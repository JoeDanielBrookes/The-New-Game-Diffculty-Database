"""Core logic for finding and removing duplicate images.

This module has no GUI code so it can be tested and reused on its own.

There are two ways of matching:

**Identical files** (:func:`find_duplicates`): contents are byte-for-byte the
same. File names, dates and locations are ignored. Files are compared in
three steps so most never need to be read in full:

1. Group files by size (a file with a unique size can't have a duplicate).
2. Hash the first 64 KB of each remaining file.
3. Fully hash (SHA-256) only the files whose first 64 KB match.

**Similar images** (:func:`find_similar_images`): pictures that look the
same even though the files differ, for example a resized copy, a re-saved
JPEG, a PNG converted to WebP, or a slightly brightened version. Each image
is shrunk to a tiny greyscale thumbnail and turned into a 128-bit
"perceptual hash" (a horizontal and a vertical difference hash). Images
whose hashes differ in only a few bits, and whose average colours are close,
are grouped together. This needs the Pillow package.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import (Callable, Dict, Iterable, Iterator, List, Optional,
                    Sequence, Tuple)

try:  # Optional: lets removed files go to the Recycle Bin / Trash.
    from send2trash import send2trash
except ImportError:  # pragma: no cover - depends on the environment
    send2trash = None

try:  # Optional: needed for similar-image detection.
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - depends on the environment
    Image = ImageOps = None

try:  # Optional: lets Pillow open HEIC/HEIF photos from phones.
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:  # pragma: no cover - depends on the environment
    pass

HAS_SIMILAR_SUPPORT = Image is not None


IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".tif",
        ".tiff", ".webp", ".heic", ".heif", ".avif", ".ico", ".svg", ".psd",
        ".raw", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2",
        ".raf",
    }
)

PARTIAL_HASH_BYTES = 64 * 1024
CHUNK_SIZE = 1024 * 1024

MODE_IDENTICAL = "identical"
MODE_SIMILAR = "similar"

HASH_BITS = 128
DEFAULT_SIMILARITY = 90  # Percent of perceptual-hash bits that must agree.
# Images whose average colour differs by more than this (0-255, any channel)
# are never grouped. This stops e.g. plain red and plain blue images, which
# have identical difference hashes, from being treated as similar.
MAX_COLOUR_DIFFERENCE = 40

METHOD_TRASH = "trash"
METHOD_MOVE = "move"
METHOD_DELETE = "delete"
REMOVAL_METHODS = (METHOD_TRASH, METHOD_MOVE, METHOD_DELETE)

ProgressFn = Callable[[str, int, int], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    """Raised when the caller asks for a running scan to stop."""


@dataclass
class FileEntry:
    path: str
    size: int
    mtime: float
    # Position of the scanned folder this file belongs to. Lower numbers have
    # higher priority: copies in those folders are the ones kept.
    root_index: int
    # SHA-256 of the contents (identical-file scans only).
    digest: Optional[str] = None
    # Filled in by similar-image scans.
    width: int = 0
    height: int = 0
    similarity: Optional[float] = None  # 0-100, compared with the group's best image

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass
class DuplicateGroup:
    files: List[FileEntry]
    kind: str = MODE_IDENTICAL

    @property
    def wasted_bytes(self) -> int:
        """Space freed by keeping only the largest file."""
        sizes = [f.size for f in self.files]
        return sum(sizes) - max(sizes)

    @property
    def digest(self) -> Optional[str]:
        return self.files[0].digest if self.kind == MODE_IDENTICAL else None


@dataclass
class ScanResult:
    groups: List[DuplicateGroup]
    files_scanned: int
    errors: List[Tuple[str, str]] = field(default_factory=list)
    mode: str = MODE_IDENTICAL


@dataclass
class RemovalItem:
    keeper: FileEntry
    remove: List[FileEntry]


@dataclass
class RemovalResult:
    removed: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def parse_extensions(text: str) -> frozenset:
    """Turn "jpg, .PNG webp" into {".jpg", ".png", ".webp"}."""
    parts = text.replace(",", " ").replace(";", " ").split()
    return frozenset(
        ("." + p.lstrip(".")).lower() for p in parts if p.strip(".")
    )


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def root_index_for(path: str, roots: Sequence[str]) -> int:
    """Return the index of the most specific root folder containing *path*.

    If folders overlap (e.g. both "Photos" and "Photos/Holiday" were added)
    the deeper folder wins, so its position in the list decides priority.
    """
    target = _norm(path)
    best, best_len = len(roots), -1
    for i, root in enumerate(roots):
        r = _norm(root)
        try:
            inside = os.path.commonpath([target, r]) == r
        except ValueError:  # Different drives on Windows.
            inside = False
        if inside and len(r) > best_len:
            best, best_len = i, len(r)
    return best


def hash_file(path: str, limit: Optional[int] = None,
              cancel: Optional[CancelFn] = None) -> str:
    """SHA-256 of a file, or of its first *limit* bytes."""
    h = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as fh:
        while True:
            if cancel and cancel():
                raise Cancelled()
            size = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            if size <= 0:
                break
            block = fh.read(size)
            if not block:
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    return h.hexdigest()


def iter_files(roots: Iterable[str], extensions: Optional[frozenset],
               include_hidden: bool = False,
               cancel: Optional[CancelFn] = None) -> Iterator[str]:
    """Yield every matching file under the given folders (recursively)."""
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if cancel and cancel():
                raise Cancelled()
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            dirnames.sort()
            for name in sorted(filenames):
                if not include_hidden and name.startswith("."):
                    continue
                if extensions is not None:
                    if os.path.splitext(name)[1].lower() not in extensions:
                        continue
                yield os.path.join(dirpath, name)


def collect_files(roots: Sequence[str], extensions: Optional[frozenset],
                  include_hidden: bool = False,
                  progress: Optional[ProgressFn] = None,
                  cancel: Optional[CancelFn] = None
                  ) -> Tuple[List[FileEntry], List[Tuple[str, str]]]:
    """List the files to compare. *roots* must already be absolute."""
    entries: List[FileEntry] = []
    errors: List[Tuple[str, str]] = []
    seen: set = set()
    for path in iter_files(roots, extensions, include_hidden, cancel):
        try:
            st = os.lstat(path)
        except OSError as exc:
            errors.append((path, str(exc)))
            continue
        if not stat.S_ISREG(st.st_mode):  # Skips symlinks and special files.
            continue
        if st.st_size == 0:  # Empty files aren't useful duplicates.
            continue
        # The same file reached twice (overlapping folders or hard links).
        file_id = (st.st_dev, st.st_ino) if st.st_ino else _norm(path)
        if file_id in seen:
            continue
        seen.add(file_id)
        entries.append(FileEntry(path, st.st_size, st.st_mtime,
                                 root_index_for(path, roots)))
        if progress and len(entries) % 250 == 0:
            progress("Finding files", len(entries), 0)
    if progress:
        progress("Finding files", len(entries), 0)
    return entries, errors


def _sort_groups(groups: List[DuplicateGroup]) -> List[DuplicateGroup]:
    for g in groups:
        g.files.sort(key=lambda f: (f.root_index, f.path.lower()))
    groups.sort(key=lambda g: (-g.wasted_bytes, g.files[0].path.lower()))
    return groups


# --------------------------------------------------------------------------
# Identical files
# --------------------------------------------------------------------------

def find_duplicates(roots: Sequence[str],
                    extensions: Optional[frozenset] = IMAGE_EXTENSIONS,
                    include_hidden: bool = False,
                    progress: Optional[ProgressFn] = None,
                    cancel: Optional[CancelFn] = None) -> ScanResult:
    """Find groups of byte-identical files under *roots*.

    *roots* is in priority order. Pass ``extensions=None`` to check every file
    type. ``progress(stage, done, total)`` is called as work proceeds (total
    is 0 while the total isn't known yet). Raises :class:`Cancelled` if
    ``cancel()`` returns True.
    """
    roots = [os.path.abspath(r) for r in roots]

    def report(stage: str, done: int, total: int) -> None:
        if progress:
            progress(stage, done, total)

    entries, errors = collect_files(roots, extensions, include_hidden,
                                    progress, cancel)
    by_size: Dict[int, List[FileEntry]] = {}
    for e in entries:
        by_size.setdefault(e.size, []).append(e)

    same_size = [g for g in by_size.values() if len(g) > 1]
    total = sum(len(g) for g in same_size)
    done = 0

    # Step 2: compare the start of each file.
    partial_groups = []
    for group in same_size:
        by_partial: dict = {}
        for entry in group:
            try:
                key = hash_file(entry.path, PARTIAL_HASH_BYTES, cancel)
            except OSError as exc:
                errors.append((entry.path, str(exc)))
            else:
                by_partial.setdefault(key, []).append(entry)
            done += 1
            report("Comparing files", done, total)
        for key, matches in by_partial.items():
            if len(matches) > 1:
                partial_groups.append((key, matches))

    # Step 3: fully hash files whose beginnings match.
    total = sum(len(e) for _, e in partial_groups)
    done = 0
    groups: List[DuplicateGroup] = []
    for partial_key, matches in partial_groups:
        if matches[0].size <= PARTIAL_HASH_BYTES:
            # The partial hash already covered the whole file.
            for e in matches:
                e.digest = partial_key
            groups.append(DuplicateGroup(list(matches), MODE_IDENTICAL))
            done += len(matches)
            report("Checking contents", done, total)
            continue
        by_full: dict = {}
        for entry in matches:
            try:
                entry.digest = hash_file(entry.path, None, cancel)
            except OSError as exc:
                errors.append((entry.path, str(exc)))
            else:
                by_full.setdefault(entry.digest, []).append(entry)
            done += 1
            report("Checking contents", done, total)
        for same in by_full.values():
            if len(same) > 1:
                groups.append(DuplicateGroup(same, MODE_IDENTICAL))

    return ScanResult(_sort_groups(groups), len(entries), errors,
                      MODE_IDENTICAL)


# --------------------------------------------------------------------------
# Similar images
# --------------------------------------------------------------------------

def _popcount(x: int) -> int:
    return bin(x).count("1")


try:  # int.bit_count() is much faster but needs Python 3.10+.
    (0).bit_count()
    _popcount = int.bit_count  # type: ignore[assignment]  # noqa: F811
except AttributeError:  # pragma: no cover
    pass


@dataclass
class ImageFingerprint:
    hash: int            # 128-bit perceptual hash
    colour: Tuple[int, int, int]  # Average RGB colour
    width: int
    height: int


def _resample(name: str):
    resampling = getattr(Image, "Resampling", Image)
    return getattr(resampling, name)


def image_fingerprint(path: str) -> ImageFingerprint:
    """Compute the perceptual fingerprint of an image file.

    Raises an exception if the file can't be opened as an image.
    """
    if Image is None:
        raise RuntimeError("Similar-image detection needs the Pillow package.")
    with Image.open(path) as im:
        width, height = im.size
        try:
            orientation = im.getexif().get(0x0112, 1)
        except Exception:
            orientation = 1
        if orientation in (5, 6, 7, 8):  # Rotated 90° by its EXIF data.
            width, height = height, width
        try:
            im.draft("RGB", (64, 64))  # Much faster JPEG decoding.
        except Exception:
            pass
        im.load()
        im = ImageOps.exif_transpose(im)

        if im.mode in ("RGBA", "LA", "PA") or (
                im.mode == "P" and "transparency" in im.info):
            # Put transparent images on white so they compare sensibly.
            rgba = im.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            rgb = background.convert("RGB")
        else:
            try:
                rgb = im.convert("RGB")
            except (ValueError, OSError):
                rgb = im.convert("L").convert("RGB")

    small = rgb.resize((32, 32), _resample("BOX"))
    r, g, b = small.resize((1, 1), _resample("BOX")).getpixel((0, 0))[:3]
    grey = small.convert("L")

    horiz = grey.resize((9, 8), _resample("LANCZOS")).tobytes()
    vert = grey.resize((8, 9), _resample("LANCZOS")).tobytes()
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (horiz[row * 9 + col] > horiz[row * 9 + col + 1])
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (vert[row * 8 + col] > vert[(row + 1) * 8 + col])
    return ImageFingerprint(value, (r, g, b), width, height)


def similarity_percent(a: ImageFingerprint, b: ImageFingerprint) -> float:
    return 100.0 * (1 - _popcount(a.hash ^ b.hash) / HASH_BITS)


class _BKTree:
    """Finds hashes within a Hamming distance without comparing every pair."""

    def __init__(self) -> None:
        self.root = None  # [hash, [indexes], {distance: child}]

    def add(self, value: int, index: int) -> None:
        if self.root is None:
            self.root = [value, [index], {}]
            return
        node = self.root
        while True:
            d = _popcount(value ^ node[0])
            if d == 0:
                node[1].append(index)
                return
            child = node[2].get(d)
            if child is None:
                node[2][d] = [value, [index], {}]
                return
            node = child

    def query(self, value: int, max_distance: int) -> List[int]:
        found: List[int] = []
        stack = [self.root] if self.root is not None else []
        while stack:
            node = stack.pop()
            d = _popcount(value ^ node[0])
            if d <= max_distance:
                found.extend(node[1])
            lo, hi = d - max_distance, d + max_distance
            for k, child in node[2].items():
                if lo <= k <= hi:
                    stack.append(child)
        return found


def find_similar_images(roots: Sequence[str],
                        extensions: Optional[frozenset] = IMAGE_EXTENSIONS,
                        include_hidden: bool = False,
                        similarity: float = DEFAULT_SIMILARITY,
                        progress: Optional[ProgressFn] = None,
                        cancel: Optional[CancelFn] = None,
                        workers: Optional[int] = None) -> ScanResult:
    """Find groups of images that look alike under *roots*.

    *similarity* (50-100) is the percentage of perceptual-hash bits that must
    agree: 100 only matches images that look the same, ~90 also catches
    resized, re-compressed and lightly edited copies. Lower values find more
    matches but also more false matches.
    """
    if Image is None:
        raise RuntimeError("Similar-image detection needs the Pillow package "
                           "(pip install Pillow).")
    roots = [os.path.abspath(r) for r in roots]
    max_distance = int(HASH_BITS * (100 - similarity) / 100 + 1e-9)

    entries, errors = collect_files(roots, extensions, include_hidden,
                                    progress, cancel)

    # Step 1: fingerprint every image (in parallel, Pillow releases the GIL
    # while decoding).
    prints: List[Tuple[FileEntry, ImageFingerprint]] = []
    total = len(entries)
    workers = workers or min(8, (os.cpu_count() or 2))
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(image_fingerprint, e.path): e for e in entries}
        for done, fut in enumerate(as_completed(futures), 1):
            if cancel and cancel():
                raise Cancelled()
            entry = futures[fut]
            try:
                fp = fut.result()
            except Exception as exc:
                errors.append((entry.path, f"Couldn't read as an image ({exc})"))
            else:
                entry.width, entry.height = fp.width, fp.height
                prints.append((entry, fp))
            if progress:
                progress("Reading images", done, total)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    prints.sort(key=lambda p: p[0].path)

    # Step 2: link images whose hashes and colours are close enough.
    tree = _BKTree()
    for i, (_, fp) in enumerate(prints):
        tree.add(fp.hash, i)

    parent = list(range(len(prints)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_, fp) in enumerate(prints):
        if cancel and i % 200 == 0 and cancel():
            raise Cancelled()
        for j in tree.query(fp.hash, max_distance):
            if j <= i:
                continue
            other = prints[j][1]
            if max(abs(x - y) for x, y in zip(fp.colour, other.colour)) \
                    > MAX_COLOUR_DIFFERENCE:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
        if progress and (i % 100 == 0 or i == len(prints) - 1):
            progress("Comparing images", i + 1, len(prints))

    clusters: Dict[int, List[int]] = {}
    for i in range(len(prints)):
        clusters.setdefault(find(i), []).append(i)

    groups: List[DuplicateGroup] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        # Compare each image with the best (highest-resolution) one.
        best = max(members, key=lambda k: (prints[k][0].pixels, prints[k][0].size))
        for k in members:
            prints[k][0].similarity = similarity_percent(prints[k][1],
                                                         prints[best][1])
        groups.append(DuplicateGroup([prints[k][0] for k in members],
                                     MODE_SIMILAR))
    return ScanResult(_sort_groups(groups), len(entries), errors, MODE_SIMILAR)


# --------------------------------------------------------------------------
# Folder priority rules
# --------------------------------------------------------------------------

RULE_KEEP_FIRST = "keep_first"
RULE_NEVER_REMOVE = "never_remove"
RULE_REMOVE_FIRST = "remove_first"
RULE_LABELS = {
    RULE_KEEP_FIRST: "Keep first",
    RULE_NEVER_REMOVE: "Never remove",
    RULE_REMOVE_FIRST: "Remove first",
}
# Lower ranks are kept in preference to higher ones. Copies in Never remove
# folders are always kept anyway, so when choosing the one copy to keep, a
# Keep first copy is preferred: both then survive.
_RULE_RANK = {RULE_KEEP_FIRST: 0, RULE_NEVER_REMOVE: 1, None: 2,
              RULE_REMOVE_FIRST: 3}


@dataclass
class FolderRule:
    """A priority rule for every file inside *folder* (and its subfolders).

    * ``keep_first``: copies here are kept in preference to all others.
    * ``never_remove``: copies here are never marked or removed (protected).
    * ``remove_first``: copies here are removed in preference to all others.

    The folder doesn't need to be one of the scanned folders; it can be any
    folder, e.g. a single subfolder deep inside one. When rules overlap, the
    most specific (deepest) folder wins.
    """
    folder: str
    rule: str

    def __post_init__(self) -> None:
        if self.rule not in RULE_LABELS:
            raise ValueError(f"Unknown folder rule: {self.rule}")


def is_inside(path: str, folder: str) -> bool:
    """True if *path* is *folder* itself or anywhere inside it."""
    p, f = _norm(path), _norm(folder)
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:  # Different drives on Windows.
        return False


def rule_for(path: str, rules: Sequence[FolderRule]) -> Optional[str]:
    """The rule that applies to *path*: the deepest matching folder wins."""
    best, best_len = None, -1
    for r in rules:
        if is_inside(path, r.folder):
            length = len(_norm(r.folder))
            if length > best_len:
                best, best_len = r.rule, length
    return best


def is_protected(path: str, rules: Sequence[FolderRule]) -> bool:
    return rule_for(path, rules) == RULE_NEVER_REMOVE


# --------------------------------------------------------------------------
# Choosing which copy to keep
# --------------------------------------------------------------------------

TIEBREAKS = {
    "Oldest file": lambda f: (f.mtime, f.path.lower()),
    "Newest file": lambda f: (-f.mtime, f.path.lower()),
    "Highest resolution": lambda f: (-f.pixels, -f.size, f.path.lower()),
    "Largest file": lambda f: (-f.size, f.path.lower()),
    "Shortest path": lambda f: (len(f.path), f.path.lower()),
    "Alphabetical path": lambda f: (f.path.lower(),),
}
DEFAULT_TIEBREAK = "Oldest file"
DEFAULT_SIMILAR_TIEBREAK = "Highest resolution"


def choose_keeper(group: DuplicateGroup,
                  tiebreak: str = DEFAULT_TIEBREAK,
                  prefer_quality: bool = False,
                  rules: Sequence[FolderRule] = ()) -> FileEntry:
    """Pick the copy to keep.

    Copies are ranked by, in order:

    1. folder rules (*Keep first*, then *Never remove*, then folders with no
       rule, then *Remove first*),
    2. the order of the scanned folders,
    3. *tiebreak*, for copies that are still equal (e.g. in the same folder).

    With *prefer_quality*, the highest-resolution (then largest) image is
    kept wherever it is, and the steps above only break ties.
    """
    key = TIEBREAKS[tiebreak]

    def rank(f: FileEntry) -> tuple:
        folder_rank = (_RULE_RANK[rule_for(f.path, rules)], f.root_index)
        if prefer_quality:
            return (-f.pixels, -f.size) + folder_rank + tuple(key(f))
        return folder_rank + tuple(key(f))

    return min(group.files, key=rank)


def files_to_remove(group: DuplicateGroup, tiebreak: str = DEFAULT_TIEBREAK,
                    prefer_quality: bool = False,
                    rules: Sequence[FolderRule] = ()) -> List[FileEntry]:
    """Every copy except the keeper and any in *Never remove* folders."""
    keeper = choose_keeper(group, tiebreak, prefer_quality, rules)
    return [f for f in group.files
            if f is not keeper and not is_protected(f.path, rules)]


def auto_plan(groups: Sequence[DuplicateGroup],
              tiebreak: str = DEFAULT_TIEBREAK,
              prefer_quality: bool = False,
              rules: Sequence[FolderRule] = ()) -> List[RemovalItem]:
    """Keep one copy per group (plus protected copies); remove the rest."""
    plan = []
    for g in groups:
        keeper = choose_keeper(g, tiebreak, prefer_quality, rules)
        remove = files_to_remove(g, tiebreak, prefer_quality, rules)
        if remove:
            plan.append(RemovalItem(keeper, remove))
    return plan


# --------------------------------------------------------------------------
# Removing
# --------------------------------------------------------------------------

def _move_destination(entry: FileEntry, move_to: str,
                      roots: Sequence[str]) -> str:
    """Where a removed file goes when using the "move to folder" option.

    The folder structure is preserved under a subfolder named after the
    scanned folder, so nothing is overwritten and files are easy to restore.
    """
    if entry.root_index < len(roots):
        root = os.path.abspath(roots[entry.root_index])
        rel = os.path.relpath(entry.path, root)
        label = os.path.basename(root.rstrip("\\/")) or "drive"
        label = label.replace(":", "")
    else:
        rel = os.path.basename(entry.path)
        label = "other"
    dest = os.path.join(move_to, label, rel)
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base} ({n}){ext}"
        n += 1
    return dest


def _unchanged(entry: FileEntry, verify: bool) -> bool:
    """Is the file still there and the same as when it was scanned?

    Identical-file scans re-check the SHA-256. Similar-image scans (which
    don't hash whole files) check the size and modification time.
    """
    try:
        st = os.stat(entry.path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_size != entry.size:
        return False
    if not verify:
        return True
    if entry.digest:
        try:
            return hash_file(entry.path) == entry.digest
        except OSError:
            return False
    return abs(st.st_mtime - entry.mtime) < 1e-3


def remove_duplicates(items: Sequence[RemovalItem], method: str,
                      move_to: Optional[str] = None,
                      roots: Sequence[str] = (),
                      verify: bool = True,
                      progress: Optional[ProgressFn] = None,
                      rules: Sequence[FolderRule] = ()) -> RemovalResult:
    """Remove the copies listed in *items*.

    With *verify* on (the default), each group's kept copy is checked first
    and nothing in that group is removed if it's missing or has changed. Each
    copy to be removed is also checked, so a file edited since the scan is
    never removed. Files in *Never remove* folders (see *rules*) are always
    left alone, even if they were listed.
    """
    if method not in REMOVAL_METHODS:
        raise ValueError(f"Unknown removal method: {method}")
    if method == METHOD_TRASH and send2trash is None:
        raise RuntimeError("Recycle Bin support needs the 'send2trash' package.")
    if method == METHOD_MOVE:
        if not move_to:
            raise ValueError("A destination folder is needed to move files.")
        os.makedirs(move_to, exist_ok=True)

    result = RemovalResult()
    total = sum(len(i.remove) for i in items)
    done = 0

    def report() -> None:
        if progress:
            progress("Removing duplicates", done, total)

    for item in items:
        if not _unchanged(item.keeper, verify):
            for f in item.remove:
                result.skipped.append(
                    (f.path, "The copy being kept is missing or has changed "
                             "since the scan, so this copy was left alone."))
            done += len(item.remove)
            report()
            continue

        keeper_norm = _norm(os.path.realpath(item.keeper.path))
        for f in item.remove:
            done += 1
            try:
                if _norm(os.path.realpath(f.path)) == keeper_norm:
                    result.skipped.append((f.path, "This is the kept copy."))
                    continue
                if is_protected(f.path, rules):
                    result.skipped.append(
                        (f.path, "It's in a folder marked Never remove."))
                    continue
                if not _unchanged(f, verify):
                    result.skipped.append(
                        (f.path, "The file is missing or has changed since "
                                 "the scan."))
                    continue
                if method == METHOD_TRASH:
                    send2trash(f.path)
                elif method == METHOD_MOVE:
                    dest = _move_destination(f, move_to, roots)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(f.path, dest)
                else:
                    os.remove(f.path)
                result.removed.append(f.path)
            except Exception as exc:  # Keep going; report the problem.
                result.skipped.append((f.path, str(exc)))
            finally:
                report()
    return result
