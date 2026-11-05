# Duplicate Image Finder

A desktop app that finds duplicate images across folders, even when the copies
have different names, and can remove the extra copies automatically based on
which folder they're in.

## Two ways of matching

**Identical files (exact copies).** The contents are exactly the same, byte for
byte. Names, dates and locations don't matter. This mode is fast and has no
false matches, and it can also check non-image files.

**Similar images.** Pictures that look the same even though the files differ.
For example:

- resized copies and thumbnails
- re-saved or re-compressed JPEGs
- format changes (PNG → JPEG → WebP)
- small brightness or contrast tweaks, light blur, black-and-white versions
- photos that are only rotated by their camera orientation (EXIF) data

Crops, mirror images and other bigger edits are treated as different pictures.
The **Similarity** slider sets how close images must be. At the default of 90%,
the edits above scored 94–100% in testing and unrelated photos scored at most
about 65%. Raise the slider if unrelated images get grouped. Lower it to catch
heavier edits. This mode needs Pillow.

The Match column shows *Identical* for exact copies. For similar images it
shows how close each image is to the highest-resolution image in its group,
and the Dimensions column shows each image's size in pixels.

## Windows: download the ready-made app (easiest)

A standalone `DuplicateImageFinder.exe` is built automatically on GitHub's
Windows build servers whenever the app changes. It needs no Python and no
installation.

1. Open the repository's **Releases** page on GitHub and pick the newest
   *Duplicate Image Finder for Windows* release.
2. Download **`DuplicateImageFinder.exe`** and put it wherever you like, for
   example in Documents or on your desktop.
3. Double-click it. Because the program isn't code-signed, Windows may say
   *"Windows protected your PC"*. Click **More info → Run anyway**. You only
   need to do this once.

To pin it, right-click the .exe and choose *Show more options → Pin to Start*
or *Send to → Desktop (create shortcut)*. It starts a few seconds slower than
the installed version because it unpacks itself each time it opens.

Every build also runs the tests on Windows and checks that the .exe starts,
before publishing it. To build it by hand on a Windows PC:

```bat
cd duplicate_image_finder
pip install -r requirements.txt pyinstaller
python build_exe.py
```

The .exe is written to `dist\DuplicateImageFinder.exe`.

## Installing it on your computer (with a desktop shortcut)

1. **Install Python 3.9 or newer** if you don't already have it, from
   [python.org](https://www.python.org/downloads/). On Windows, tick
   **"Add python.exe to PATH"** during setup.
2. Download this `duplicate_image_finder` folder, for example as a zip from
   GitHub, and unzip it.
3. Run the installer for your system:
   - **Windows:** double-click **`Install on Windows.bat`**. If Windows
     SmartScreen warns you, click *More info → Run anyway*.
   - **macOS:** double-click **`Install on Mac.command`**. If macOS says it
     can't be opened, right-click it, choose *Open*, then *Open* again.
   - **Linux:** run `sh install_linux.sh`. It needs `python3-tk` and
     `python3-venv`, e.g. `sudo apt install python3-tk python3-venv`.
4. Open **Duplicate Image Finder** from the new shortcut on your desktop. The
   installer also adds it to the Start menu (Windows), `~/Applications`
   (macOS) or your app launcher (Linux).

The installer doesn't need administrator rights. It copies the app to a
folder in your user account and installs the extras (Pillow, send2trash,
pillow-heif) into a private Python environment, so your other Python
programs aren't affected:

| System  | Installed to |
|---------|--------------|
| Windows | `%LOCALAPPDATA%\Programs\Duplicate Image Finder` |
| macOS   | `~/Library/Application Support/Duplicate Image Finder` |
| Linux   | `~/.local/share/duplicate-image-finder` |

Afterwards you can delete the downloaded folder. To **update**, download the
new version and run the installer again. To **uninstall**, run the installer
with `--uninstall` (for example `py installer.py --uninstall` on Windows or
`python3 installer.py --uninstall` on macOS or Linux), or delete the folder
above and the shortcuts.

## Running it without installing

```bash
cd duplicate_image_finder
pip install -r requirements.txt   # optional, see below
python app.py
```

Tkinter ships with the python.org installers for Windows and macOS. On Linux,
install it with `sudo apt install python3-tk`.

## How to use it

1. **Add folders** and put them in priority order with *Move up* / *Move down*.
   Folders are scanned recursively.
2. Choose **Identical files** or **Similar images**, then click
   **Scan for duplicates**. Each group of matching files is listed.
3. **Copies are marked automatically by folder location.** In each group, the
   copy in the highest folder in the list is kept (green) and the others are
   marked *Remove* (red). Put the folder you want cleaned out at the bottom
   of the list, or use [folder priority rules](#folder-priority-rules) for
   more control.
   - If several copies are in equally ranked folders (for example two copies in
     the same folder), the *"keep the"* setting decides: oldest file, newest
     file, highest resolution, largest file, shortest path or alphabetical path.
     In *Similar images* mode it defaults to highest resolution.
   - For similar images, you can tick *Always keep the highest-resolution
     copy* so the best-quality version is never removed, even when it's in a
     lower-priority folder. Folder priority then only breaks ties.
   - Double-click a file (or press Space) to switch it between Keep and Remove.
     You can also right-click it and choose *Keep only this copy* or *Show in
     folder*.
4. Choose where removed files go, then click **Remove marked files**:
   - **Recycle Bin / Trash** (needs send2trash)
   - **A folder** (the default without send2trash). Files are moved there with
     their folder structure kept, so they're easy to put back.
   - **Delete permanently**

## Folder priority rules

The order of the scanned folders is a simple way to say which copies to keep.
For finer control, open the **Priority rules** tab next to the folder list and
add rules for specific folders:

| Rule | What it does |
|------|--------------|
| **Keep first** | Copies here are kept in preference to copies anywhere else. |
| **Never remove** | Copies here are protected. They're never marked for removal (even by hand) and are never removed. |
| **Remove first** | Copies here are removed in preference to copies anywhere else. |

- A rule covers a folder **and everything inside it**. It can be any folder,
  including one subfolder deep inside a scanned folder, e.g. mark
  `Pictures/Favourites` *Keep first* while scanning all of `Pictures`.
- When rules overlap, the **deepest folder wins**. So you can mark
  `Backup` *Remove first* but `Backup/Originals` *Never remove*.
- Rules come before the folder order. The order then breaks ties between
  folders with the same rule, then the *"keep the"* setting decides.
- If a group has a *Keep first* copy and copies in *Never remove* folders,
  all of them are kept.
- **Shortcut:** right-click any file in the results and choose
  **Rule for this file's folder**.
- Changing a rule re-selects the files straight away. Protected files show
  as **Protected** in blue.

Your folders, rules and options are saved automatically and restored the next
time you open the app.

## Safety

- At least one copy of every image is always kept. You can't mark the last
  copy for removal.
- Files in *Never remove* folders are never removed, even if something else
  marks them.
- Just before removal, the kept copy and every copy being removed are checked
  again. Exact copies are re-hashed. For similar images, the size and
  modification time are compared. If anything is missing or has changed since
  the scan, that file is left alone and reported.
- Similar images aren't exact copies, so the confirmation message asks you to
  check the groups before continuing.
- Nothing is removed until you confirm.
- Symbolic links, empty files and hidden files and folders (unless you choose
  to include them) are skipped. A file reached twice through overlapping
  folders or hard links is counted only once.

## How it works

**Identical files:** files are grouped by size first, then by a hash of their
first 64 KB, and only then fully hashed with SHA-256. Most files are ruled out
without being read in full, so scans stay fast even on large photo libraries.

**Similar images:** each image is decoded at low resolution, which is fast for
JPEGs. It is shrunk to a tiny greyscale thumbnail and turned into a 128-bit
*perceptual hash* (horizontal and vertical difference hashes). Images whose
hashes differ by only a few bits are linked into groups. A BK-tree avoids
comparing every pair, and images are read in parallel. Images whose average
colours are very different are never grouped. This stops, for example, a plain
red image matching a plain blue one.

Scanning runs in the background, so the window stays responsive and you can
cancel.

`core.py` holds all the scanning and removal logic, with no GUI code.
`app.py` is the Tkinter interface.

## Tests

```bash
cd duplicate_image_finder
python -m unittest discover -s tests -v
```

## Making a standalone program for macOS or Linux

`build_exe.py` also works on macOS and Linux. Run it on the system you want a
program for, because PyInstaller can't build for other systems:

```bash
pip install -r requirements.txt pyinstaller
python build_exe.py
```
