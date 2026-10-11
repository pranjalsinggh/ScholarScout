"""Prepare server runtime libraries in the disposable Linux image build stage.

The final distroless image supplies glibc, OpenSSL, libgcc and libstdc++.
These additions support the remaining noninteractive Python standard library.
Each copied Debian library keeps its package record and copyright notice.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


RUNTIME_PACKAGES = (
    "libbz2-1.0", "libcrypt1", "libdb5.3t64", "libffi8",
    "libgdbm6t64", "liblzma5", "libsqlite3-0", "zlib1g",
)


def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Run only in the disposable Linux python-build stage")

    stdlib = Path(sysconfig.get_path("stdlib"))
    # ScholarScout serves HTTP and does not use terminal or GUI bindings.
    # uuid4 uses os.urandom; its standard Python implementation needs no _uuid.
    for pattern in ("_curses*.so", "readline*.so", "_tkinter*.so", "_uuid*.so"):
        for extension in (stdlib / "lib-dynload").glob(pattern):
            extension.unlink()
    shutil.rmtree(stdlib / "ensurepip")

    root = Path("/runtime-libs")
    for package in RUNTIME_PACKAGES:
        files = subprocess.check_output(
            ["dpkg-query", "-L", package], text=True,
        ).splitlines()
        libraries = [Path(name) for name in files if ".so" in Path(name).name]
        if not libraries:
            raise RuntimeError(f"No runtime libraries found for {package}")
        for source in libraries:
            target = root / source.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)

        status = root / "var/lib/dpkg/status.d" / package
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(subprocess.check_output(
            ["dpkg-query", "-s", package], text=True,
        ))
        copyright_file = Path("/usr/share/doc") / package / "copyright"
        if copyright_file.exists():
            target = root / copyright_file.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copyright_file, target)

    print(f"Prepared {len(RUNTIME_PACKAGES)} runtime packages with scanner metadata")


if __name__ == "__main__":
    main()
