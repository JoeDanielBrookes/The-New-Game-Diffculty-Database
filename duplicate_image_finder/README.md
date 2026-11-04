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

## Running it

You need Python 3.9 or newer. Tkinter ships with the python.org installers for
Windows and macOS. On Linux, install it with `sudo apt install python3-tk`.

```bash
cd duplicate_image_finder
pip install -r requirements.txt   # optional, see below
python app.py
```

Optional extras in `requirements.txt`:

- **Pillow**: needed for *Similar images* mode. It also shows previews for
  JPEG, WebP and other formats. PNG and GIF previews work without it.
- **send2trash**: enables "Recycle Bin / Trash" as a removal option.
- **pillow-heif**: lets *Similar images* mode read HEIC/HEIF iPhone photos.

## How to use it

1. **Add folders** and put them in priority order with *Move up* / *Move down*.
   Folders are scanned recursively.
2. Choose **Identical files** or **Similar images**, then click
   **Scan for duplicates**. Each group of matching files is listed.
3. **Copies are marked automatically by folder location.** In each group, the
   copy in the highest folder in the list is kept (green) and the others are
   marked *Remove* (red). Put the folder you want cleaned out at the bottom
   of the list.
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

## Safety

- At least one copy of every image is always kept. You can't mark the last
  copy for removal.
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

## Making a standalone .exe / .app (optional)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "Duplicate Image Finder" app.py
```

The program is written to `dist/`.
