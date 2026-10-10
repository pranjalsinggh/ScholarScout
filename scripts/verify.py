"""Run local release gates with the selected Python runtime.

Docker build/scanning/startup run in GitHub CI. Live Render TLS/edge checks
remain part of first deployment; this command never claims to verify them.
"""
from __future__ import annotations

import compileall
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(label, args, cwd=ROOT):
    print(f"{label} ...", flush=True)
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        print(f"FAILED ({type(exc).__name__})")
        return False
    print("PASSED" if result.returncode == 0 else "FAILED", flush=True)
    if result.returncode:
        print((result.stdout + result.stderr)[-3000:])
    return result.returncode == 0


def main():
    if not compileall.compile_dir(ROOT / "backend" / "app", quiet=2):
        return 1
    npm = shutil.which("npm")
    if npm is None:
        print("Node.js/npm is required for the frontend gates.")
        return 1
    checks = [
        ("Secret/artifact scan", [sys.executable, "scripts/check_secrets.py"], ROOT),
        ("Deployment configuration", [sys.executable, "scripts/validate_deployment.py"], ROOT),
        ("Backend lint", [sys.executable, "-m", "ruff", "check", "backend/app", "--select", "F821,F811,E722"], ROOT),
        ("Backend regressions", [sys.executable, "-m", "pytest", "tests/", "-q", "-o", "faulthandler_timeout=45"], ROOT / "backend"),
        ("Frontend advisories", [npm, "audit", "--audit-level=moderate"], ROOT / "frontend"),
        ("Frontend production build", [npm, "run", "build"], ROOT / "frontend"),
        ("Rendering regressions", [npm, "run", "verify:render"], ROOT / "frontend"),
        ("Production application smoke", [sys.executable, "scripts/release_gate.py"], ROOT),
    ]
    results = [run(label, args, cwd) for label, args, cwd in checks]
    success = all(results)
    print("LOCAL RELEASE CHECKS PASSED" if success else "Release checks failed; see above.")
    print("Docker and live Render checks require CI and the first deployment.")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
