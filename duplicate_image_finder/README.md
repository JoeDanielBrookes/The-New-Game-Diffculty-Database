# Duplicate Image Finder

A desktop app that finds identical images across folders, even when the copies
have different names, and can remove the extra copies automatically based on
which folder they're in.

## What counts as identical

Two files are identical when their contents are exactly the same, byte for
byte. Names, dates and locations don't matter. A resized or re-saved copy of a
picture is **not** treated as identical. (A "similar images" mode could be
added later.)

## Running it

You need Python 3.8 or newer. Tkinter ships with the python.org installers for
Windows and macOS. On Linux, install it with `sudo apt install python3-tk`.

```bash
cd duplicate_image_finder
pip install -r requirements.txt   # optional, see below
python app.py
```

Optional extras in `requirements.txt`:

- **send2trash**: enables "Recycle Bin / Trash" as a removal option.
- **Pillow**: shows previews for JPEG, WebP and other formats. PNG and GIF
  previews work without it.

## How to use it

1. **Add folders** and put them in priority order with *Move up* / *Move down*.
   Folders are scanned recursively.
2. Click **Scan for duplicates**. Each group of identical files is listed.
3. **Copies are marked automatically by folder location.** In each group, the
   copy in the highest folder in the list is kept (green) and the others are
   marked *Remove* (red). Put the folder you want cleaned out at the bottom
   of the list.
   - If several copies are in equally ranked folders (for example two copies in
     the same folder), the *"keep the"* setting decides: oldest file, newest
     file, shortest path or alphabetical path. Click *Auto-select by folder
     priority* after changing it.
   - Double-click a file (or press Space) to switch it between Keep and Remove.
     You can also right-click it and choose *Keep only this copy* or *Show in
     folder*.
4. Choose where removed files go, then click **Remove marked files**:
   - **Recycle Bin / Trash** (needs send2trash)
   - **A folder** (the default without send2trash). Files are moved there with
     their folder structure kept, so they're easy to put back.
   - **Delete permanently**

## Safety

- At least one copy of every image is always kept. You can't mark the last
  copy for removal.
- Just before removal, the kept copy and every copy being removed are hashed
  again. If anything is missing or has changed since the scan, that file is
  left alone and reported.
- Nothing is removed until you confirm.
- Symbolic links, empty files and hidden files and folders (unless you choose
  to include them) are skipped. A file reached twice through overlapping
  folders or hard links is counted only once.

## How it works

Files are grouped by size first, then by a hash of their first 64 KB, and only
then fully hashed with SHA-256. Most files are ruled out without being read in
full, so scans stay fast even on large photo libraries. Scanning runs in the
background, so the window stays responsive and you can cancel.

`core.py` holds all the scanning and removal logic, with no GUI code.
`app.py` is the Tkinter interface.

## Tests

```bash
cd duplicate_image_finder
python -m unittest discover -s tests -v
```

## Making a standalone .exe / .app (optional)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "Duplicate Image Finder" app.py
```

The program is written to `dist/`.
