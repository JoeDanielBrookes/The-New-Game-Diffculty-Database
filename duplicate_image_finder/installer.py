"""Install Duplicate Image Finder for the current user, with a desktop shortcut.

Usually run through "Install on Windows.bat", "Install on Mac.command" or
"install_linux.sh". It can also be run directly:

    python installer.py              # install or update
    python installer.py --uninstall  # remove the app and its shortcuts

The installer needs no administrator rights. It:

1. copies the app into a per-user folder,
2. creates a private Python environment there and installs the optional
   extras (Pillow, send2trash, pillow-heif) into it,
3. adds a shortcut to the desktop and the Start menu / Applications /
   app launcher.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import venv
from pathlib import Path

APP_NAME = "Duplicate Image Finder"
APP_ID = "duplicate-image-finder"
HERE = Path(__file__).resolve().parent
APP_FILES = ["app.py", "core.py", "requirements.txt", "README.md"]
EXTRAS = ["Pillow>=9.0", "send2trash>=1.8", "pillow-heif>=0.10"]

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def say(msg: str = "") -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

def install_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "Programs" / APP_NAME
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return _xdg_data_home() / APP_ID


def _xdg_data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    path = Path(value) if value else Path.home() / ".local" / "share"
    return path if path.is_absolute() else Path.home() / ".local" / "share"


def windows_folder(name: str) -> Path:
    """Real Desktop / Programs folder (handles OneDrive-redirected desktops)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"[Environment]::GetFolderPath('{name}')"],
        capture_output=True, text=True)
    path = out.stdout.strip()
    if out.returncode == 0 and path:
        return Path(path)
    if name == "Desktop":
        return Path.home() / "Desktop"
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"


def linux_desktop() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                             text=True)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return Path.home() / "Desktop"


def venv_python(target: Path, windowed: bool = False) -> Path:
    if IS_WINDOWS:
        return target / "venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return target / "venv" / "bin" / "python"


def shortcut_paths() -> list:
    """Every shortcut the installer may create (used by uninstall)."""
    if IS_WINDOWS:
        return [windows_folder("Desktop") / f"{APP_NAME}.lnk",
                windows_folder("Programs") / f"{APP_NAME}.lnk"]
    if IS_MAC:
        return [Path.home() / "Desktop" / f"{APP_NAME}.app",
                Path.home() / "Applications" / f"{APP_NAME}.app"]
    return [linux_desktop() / f"{APP_ID}.desktop",
            _xdg_data_home() / "applications" / f"{APP_ID}.desktop"]


# --------------------------------------------------------------------------
# Install steps
# --------------------------------------------------------------------------

def check_python() -> None:
    if sys.version_info < (3, 9):
        sys.exit(f"Python 3.9 or newer is needed (this is {sys.version.split()[0]}). "
                 "Get it from https://www.python.org/downloads/")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        if IS_MAC:
            hint = ("Install Python from https://www.python.org/downloads/macos/ "
                    "(it includes Tkinter), or with Homebrew run: "
                    "brew install python-tk")
        elif IS_WINDOWS:
            hint = ("Re-run the Python installer from python.org, choose "
                    "'Modify' and make sure 'tcl/tk and IDLE' is ticked.")
        else:
            hint = "Install it with your package manager, e.g. sudo apt install python3-tk"
        sys.exit("This Python doesn't include Tkinter, which the app needs "
                 "for its window.\n" + hint)


def copy_app(target: Path) -> Path:
    app_dir = target / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    for name in APP_FILES:
        shutil.copy2(HERE / name, app_dir / name)
    shutil.copytree(HERE / "assets", app_dir / "assets", dirs_exist_ok=True)
    return app_dir


def make_environment(target: Path) -> Path:
    """Create a private virtual environment and install the extras."""
    python = venv_python(target)
    if not python.exists():
        say("Creating a private Python environment...")
        try:
            venv.create(target / "venv", with_pip=True, clear=True)
        except Exception as exc:
            say(f"  Couldn't create it ({exc}).")
            if not IS_WINDOWS and not IS_MAC:
                say("  On Debian/Ubuntu: sudo apt install python3-venv")
            say("  The app will use your normal Python, without the extras.")
            return Path(sys.executable)
    say("Installing extras (Pillow, send2trash, pillow-heif). This can take a "
        "minute...")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--upgrade", "pip"],
                   check=False)
    for package in EXTRAS:
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "--upgrade", package])
        name = package.split(">")[0]
        if result.returncode == 0:
            say(f"  OK  {name}")
        else:
            say(f"  !!  {name} couldn't be installed. The app still works, "
                "but without that feature.")
    return python


def create_windows_shortcuts(target: Path, app_dir: Path) -> list:
    pythonw = venv_python(target, windowed=True)
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    created = []
    for link in shortcut_paths():
        link.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, DIF_LINK=str(link), DIF_TARGET=str(pythonw),
                   DIF_ARGS=f'"{app_dir / "app.py"}"', DIF_DIR=str(app_dir),
                   DIF_ICON=str(app_dir / "assets" / "icon.ico"))
        script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DIF_LINK);"
                  "$s.TargetPath = $env:DIF_TARGET; $s.Arguments = $env:DIF_ARGS;"
                  "$s.WorkingDirectory = $env:DIF_DIR; $s.IconLocation = $env:DIF_ICON;"
                  "$s.Description = 'Find duplicate and similar images'; $s.Save()")
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                env=env)
        if result.returncode == 0:
            created.append(link)
    return created


def create_mac_apps(target: Path, app_dir: Path, python: Path) -> list:
    created = []
    for bundle in shortcut_paths():
        if bundle.exists():
            shutil.rmtree(bundle)
        macos = bundle / "Contents" / "MacOS"
        resources = bundle / "Contents" / "Resources"
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)
        shutil.copy2(app_dir / "assets" / "icon.icns", resources / "icon.icns")
        launcher = macos / APP_ID
        launcher.write_text(
            "#!/bin/sh\n"
            f'cd "{app_dir}"\n'
            f'exec "{python}" "{app_dir / "app.py"}"\n')
        launcher.chmod(0o755)
        with open(bundle / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({
                "CFBundleName": APP_NAME,
                "CFBundleDisplayName": APP_NAME,
                "CFBundleIdentifier": "local.duplicate-image-finder",
                "CFBundleVersion": "1.0",
                "CFBundleShortVersionString": "1.0",
                "CFBundlePackageType": "APPL",
                "CFBundleExecutable": APP_ID,
                "CFBundleIconFile": "icon.icns",
                "NSHighResolutionCapable": True,
            }, fh)
        created.append(bundle)
    return created


def create_linux_launchers(target: Path, app_dir: Path, python: Path) -> list:
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Find duplicate and similar images\n"
        f'Exec="{python}" "{app_dir / "app.py"}"\n'
        f"Path={app_dir}\n"
        f"Icon={app_dir / 'assets' / 'icon.png'}\n"
        "Terminal=false\n"
        "Categories=Graphics;Utility;\n")
    created = []
    for path in shortcut_paths():
        if not path.parent.exists() and path.parent.name != "applications":
            continue  # No desktop folder (e.g. a server); skip it.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry)
        path.chmod(0o755)
        # GNOME only launches desktop files that are marked as trusted.
        if shutil.which("gio"):
            subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
        created.append(path)
    return created


def install() -> None:
    check_python()
    target = install_dir()
    say(f"Installing {APP_NAME} to:\n  {target}\n")
    app_dir = copy_app(target)
    python = make_environment(target)
    say("Creating shortcuts...")
    if IS_WINDOWS:
        created = create_windows_shortcuts(target, app_dir)
    elif IS_MAC:
        created = create_mac_apps(target, app_dir, python)
    else:
        created = create_linux_launchers(target, app_dir, python)
    for path in created:
        say(f"  OK  {path}")
    say(f"\nDone! Open {APP_NAME} from the shortcut on your desktop.")
    say("You can delete the downloaded folder now. To uninstall later, run "
        "this installer again with --uninstall.")


def uninstall() -> None:
    for path in shortcut_paths():
        if path.is_dir():
            shutil.rmtree(path)
            say(f"Removed {path}")
        elif path.exists():
            path.unlink()
            say(f"Removed {path}")
    target = install_dir()
    if target.exists():
        shutil.rmtree(target)
        say(f"Removed {target}")
    say(f"{APP_NAME} has been uninstalled.")


if __name__ == "__main__":
    if "--uninstall" in sys.argv[1:]:
        uninstall()
    else:
        install()
