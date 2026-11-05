"""Build a standalone Duplicate Image Finder program with PyInstaller.

    pip install -r requirements.txt pyinstaller
    python build_exe.py

The result is a single file in ``dist/``: ``DuplicateImageFinder.exe`` on
Windows (or ``DuplicateImageFinder`` on Linux). It runs without Python
installed. PyInstaller can only build for the system it runs on, so build the
Windows .exe on Windows (the GitHub Actions workflow does this automatically).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
NAME = "DuplicateImageFinder"


def main() -> None:
    os.chdir(HERE)
    args = [
        str(HERE / "app.py"),
        "--name", NAME,
        "--onefile",
        "--windowed",  # No console window behind the app.
        "--noconfirm",
        "--clean",
        "--icon", str(HERE / "assets" / ("icon.icns" if sys.platform == "darwin"
                                         else "icon.ico")),
        "--add-data", f"{HERE / 'assets'}{os.pathsep}assets",
        # pillow-heif ships native libraries PyInstaller doesn't find alone.
        "--collect-all", "pillow_heif",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]
    version_file = HERE / "build" / "version_info.txt"
    if sys.platform.startswith("win"):
        version_file.parent.mkdir(exist_ok=True)
        version_file.write_text(_windows_version_info(), encoding="utf-8")
        args += ["--version-file", str(version_file)]
    PyInstaller.__main__.run(args + sys.argv[1:])


def _windows_version_info() -> str:
    """File details shown in the .exe's Properties dialog in Windows."""
    from app import APP_TITLE, APP_VERSION

    parts = [int(p) for p in APP_VERSION.split(".")] + [0, 0, 0, 0]
    version = tuple(parts[:4])
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version}, prodvers={version}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', '{APP_TITLE}'),
      StringStruct('ProductName', '{APP_TITLE}'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('ProductVersion', '{APP_VERSION}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('InternalName', '{NAME}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


if __name__ == "__main__":
    main()
