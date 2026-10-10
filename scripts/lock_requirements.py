"""Regenerate the production artifact-hash lock from a reviewed clean core environment.

Install requirements.in and requirements-dev.txt first, then run this from the repo.
This update command contacts PyPI; it never reads app settings, data or credentials.
"""
from __future__ import annotations

import importlib.metadata as metadata
import json
from pathlib import Path
import urllib.request

from packaging.markers import default_environment
from packaging.requirements import Requirement


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    direct = [Requirement(line.strip()) for line in (root / "backend/requirements.in").read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    for requirement in direct:
        assert metadata.version(requirement.name) in requirement.specifier, requirement.name
    environments = []
    for platform, system, os_name in [("linux", "Linux", "posix"), ("win32", "Windows", "nt"), ("darwin", "Darwin", "posix")]:
        env = default_environment()
        env.update(sys_platform=platform, platform_system=system, os_name=os_name, extra="")
        environments.append(env)
    versions, pending = {}, [item.name for item in direct]
    while pending:
        name = pending.pop().lower().replace("_", "-")
        if name in versions:
            continue
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for value in distribution.requires or []:
            dependency = Requirement(value)
            if not dependency.marker or any(dependency.marker.evaluate(env) for env in environments):
                pending.append(dependency.name)
    assert not set(versions) & {"transformers", "torch", "sentence-transformers", "huggingface-hub"}
    blocks = []
    for name, version in sorted(versions.items()):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            artifacts = json.load(response)["urls"]
        hashes = sorted({item["digests"]["sha256"] for item in artifacts})
        assert hashes, name
        entries = [f"{name}=={version}"] + [f"    --hash=sha256:{digest}" for digest in hashes]
        blocks.append(" \\\n".join(entries))
    header = ("# Production dependency lock for Python 3.11. All artifacts verified by pip hashes.\n"
              "# Optional model/training packages are intentionally absent from the release runtime.\n"
              "# Regenerate with scripts/lock_requirements.py in a reviewed clean core environment.\n")
    (root / "backend/requirements.lock.txt").write_text(header + "\n\n".join(blocks) + "\n", encoding="utf-8")
    print(f"Wrote production hash lock for {len(versions)} packages; run advisory and release gates.")


if __name__ == "__main__":
    main()
