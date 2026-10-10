"""Fail the release if candidate Git files contain secrets or private artifacts.

Reports paths/line numbers only, never matched credential values. This is an
additional guard; GitHub secret scanning remains useful after publication.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"gsk_[A-Za-z0-9]{40,}"),
    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{25,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
]


def main():
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=ROOT, check=True, capture_output=True)
    problems = []
    candidates = [value.decode("utf-8") for value in result.stdout.split(b"\0") if value]
    for name in candidates:
        path = ROOT / name
        parts = set(path.relative_to(ROOT).parts)
        example = path.name.endswith(".example")
        private = (bool(parts & {"node_modules", ".venv", "data", ".git"})
                   or (path.name.startswith(".env") and not example)
                   or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".key", ".pem", ".p12", ".pfx"}
                   or (path.suffix.lower() == ".pdf" and name != "backend/demo_assets/attention-is-all-you-need.pdf"))
        if private:
            problems.append(f"Private artifact: {name}")
            continue
        if not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                problems.append(f"Credential pattern: {name}:{line_number}")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Secret/artifact gate passed: {len(candidates)} candidate Git files; no credential values printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
