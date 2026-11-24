"""Build a portable zip: Python's official embeddable runtime, Tcl/Tk, Pillow and the overlay."""

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "EVE Pressure"
VERSION = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
TAG = f"{sys.version_info.major}{sys.version_info.minor}"
EMBED_URL = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"
LAUNCHER = '@start "" "%~dp0python\\pythonw.exe" -m pressure_overlay\r\n'


def embeddable():
    cache = BUILD / "cache" / Path(EMBED_URL).name
    if not cache.exists():
        print(f"Downloading {EMBED_URL}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(EMBED_URL) as response:
            cache.write_bytes(response.read())
    return cache


def main():
    if sys.platform != "win32" or sys.maxsize < 2**32:
        raise SystemExit("Build with 64-bit Python on Windows.")
    base = Path(sys.base_prefix)
    stage = BUILD / NAME
    shutil.rmtree(stage, ignore_errors=True)
    python = stage / "python"
    with zipfile.ZipFile(embeddable()) as archive:
        archive.extractall(python)

    # The embeddable runtime leaves out Tcl/Tk, so take it from this install.
    for dll in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        if (base / "DLLs" / dll).exists():
            shutil.copy2(base / "DLLs" / dll, python)
    ignore = shutil.ignore_patterns("__pycache__", "test*", "idle_test")
    shutil.copytree(base / "Lib" / "tkinter", python / "Lib" / "tkinter", ignore=ignore)
    shutil.copytree(base / "tcl", python / "tcl", ignore=ignore)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--only-binary=:all:",
            "--target",
            str(python / "Lib"),
            "pillow",
        ],
        check=True,
    )
    # ".." puts the app folder on the path, so -m finds the overlay from anywhere.
    (python / f"python{TAG}._pth").write_text(f"python{TAG}.zip\n.\nLib\n..\n", encoding="utf-8")

    shutil.copytree(ROOT / "pressure_overlay", stage / "pressure_overlay", ignore=ignore)
    for file in ("README.md", "LICENSE"):
        shutil.copy2(ROOT / file, stage)
    (stage / f"{NAME}.cmd").write_text(LAUNCHER, encoding="ascii")

    check = "import tkinter, PIL, pressure_overlay.overlay; tkinter.Tk().destroy()"
    subprocess.run([str(python / "python.exe"), "-c", check], check=True, cwd=BUILD)

    DIST.mkdir(exist_ok=True)
    target = DIST / f"{NAME.replace(' ', '-')}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(BUILD))
    target.write_bytes(buffer.getvalue())
    print(f"{target} ({target.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
