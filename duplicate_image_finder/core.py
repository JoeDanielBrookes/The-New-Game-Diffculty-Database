"""Core logic for finding and removing identical files.

This module has no GUI code so it can be tested and reused on its own.

Two files are treated as identical when their contents are byte-for-byte
the same; file names, dates and locations are ignored. To keep scans fast,
files are compared in three steps:

1. Group files by size (files with a unique size can't have a duplicate).
2. Hash the first 64 KB of each remaining file.
3. Fully hash (SHA-256) only the files whose first 64 KB match.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

try:  # Optional: lets removed files go to the Recycle Bin / Trash.
    from send2trash import send2trash
except ImportError:  # pragma: no cover - depends on the environment
    send2trash = None


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


@dataclass
class DuplicateGroup:
    digest: str
    size: int
    files: List[FileEntry]

    @property
    def wasted_bytes(self) -> int:
        return self.size * (len(self.files) - 1)


@dataclass
class ScanResult:
    groups: List[DuplicateGroup]
    files_scanned: int
    errors: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class RemovalItem:
    keeper: FileEntry
    digest: str
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


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def find_duplicates(roots: Sequence[str],
                    extensions: Optional[frozenset] = IMAGE_EXTENSIONS,
                    include_hidden: bool = False,
                    progress: Optional[ProgressFn] = None,
                    cancel: Optional[CancelFn] = None) -> ScanResult:
    """Find groups of identical files under *roots*.

    *roots* is in priority order. Pass ``extensions=None`` to check every file
    type. ``progress(stage, done, total)`` is called as work proceeds (total
    is 0 while the total isn't known yet). Raises :class:`Cancelled` if
    ``cancel()`` returns True.
    """
    roots = [os.path.abspath(r) for r in roots]
    errors: List[Tuple[str, str]] = []

    def report(stage: str, done: int, total: int) -> None:
        if progress:
            progress(stage, done, total)

    # Step 1: collect files and group them by size.
    by_size: dict = {}
    seen: set = set()
    scanned = 0
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
        entry = FileEntry(path, st.st_size, st.st_mtime,
                          root_index_for(path, roots))
        by_size.setdefault(st.st_size, []).append(entry)
        scanned += 1
        if scanned % 250 == 0:
            report("Finding files", scanned, 0)
    report("Finding files", scanned, 0)

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
        for key, entries in by_partial.items():
            if len(entries) > 1:
                partial_groups.append((key, entries))

    # Step 3: fully hash files whose beginnings match.
    total = sum(len(e) for _, e in partial_groups)
    done = 0
    groups: List[DuplicateGroup] = []
    for partial_key, entries in partial_groups:
        size = entries[0].size
        if size <= PARTIAL_HASH_BYTES:
            # The partial hash already covered the whole file.
            groups.append(DuplicateGroup(partial_key, size, list(entries)))
            done += len(entries)
            report("Checking contents", done, total)
            continue
        by_full: dict = {}
        for entry in entries:
            try:
                key = hash_file(entry.path, None, cancel)
            except OSError as exc:
                errors.append((entry.path, str(exc)))
            else:
                by_full.setdefault(key, []).append(entry)
            done += 1
            report("Checking contents", done, total)
        for key, matches in by_full.items():
            if len(matches) > 1:
                groups.append(DuplicateGroup(key, size, matches))

    for g in groups:
        g.files.sort(key=lambda f: (f.root_index, f.path.lower()))
    groups.sort(key=lambda g: (-g.wasted_bytes, g.files[0].path.lower()))
    return ScanResult(groups, scanned, errors)


# --------------------------------------------------------------------------
# Choosing which copy to keep
# --------------------------------------------------------------------------

TIEBREAKS = {
    "Oldest file": lambda f: (f.mtime, f.path.lower()),
    "Newest file": lambda f: (-f.mtime, f.path.lower()),
    "Shortest path": lambda f: (len(f.path), f.path.lower()),
    "Alphabetical path": lambda f: (f.path.lower(),),
}
DEFAULT_TIEBREAK = "Oldest file"


def choose_keeper(group: DuplicateGroup,
                  tiebreak: str = DEFAULT_TIEBREAK) -> FileEntry:
    """Pick the copy to keep: the one in the highest-priority folder.

    When several copies are in equally ranked folders (e.g. the same folder),
    *tiebreak* decides.
    """
    key = TIEBREAKS[tiebreak]
    return min(group.files, key=lambda f: (f.root_index,) + tuple(key(f)))


def auto_plan(groups: Sequence[DuplicateGroup],
              tiebreak: str = DEFAULT_TIEBREAK) -> List[RemovalItem]:
    """Keep one copy per group (by folder priority) and remove the rest."""
    plan = []
    for g in groups:
        keeper = choose_keeper(g, tiebreak)
        plan.append(RemovalItem(keeper, g.digest,
                                [f for f in g.files if f is not keeper]))
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


def remove_duplicates(items: Sequence[RemovalItem], method: str,
                      move_to: Optional[str] = None,
                      roots: Sequence[str] = (),
                      verify: bool = True,
                      progress: Optional[ProgressFn] = None) -> RemovalResult:
    """Remove the duplicate copies listed in *items*.

    With *verify* on (the default), each group's kept copy is re-hashed first
    and nothing in that group is removed if it's missing or has changed. Each
    copy to be removed is also re-hashed, so a file edited since the scan is
    never removed.
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
        keeper_ok = os.path.isfile(item.keeper.path)
        if keeper_ok and verify:
            try:
                keeper_ok = hash_file(item.keeper.path) == item.digest
            except OSError:
                keeper_ok = False
        if not keeper_ok:
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
                if verify and hash_file(f.path) != item.digest:
                    result.skipped.append(
                        (f.path, "The file has changed since the scan."))
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
