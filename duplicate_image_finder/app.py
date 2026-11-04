"""Duplicate Image Finder: a desktop app for finding identical images.

Run with:  python app.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import core

try:  # Optional: previews for JPEG, WebP and other formats.
    from PIL import Image, ImageOps, ImageTk
except ImportError:  # pragma: no cover - depends on the environment
    Image = ImageOps = ImageTk = None


APP_TITLE = "Duplicate Image Finder"
PREVIEW_SIZE = 170


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_in_file_manager(path: str) -> None:
    """Show *path* in Explorer / Finder / the Linux file manager."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1150x820")
        self.minsize(900, 600)

        self.folders: list[str] = []
        self.scan_roots: list[str] = []
        self.scan_mode = core.MODE_IDENTICAL
        self.groups: list[core.DuplicateGroup] = []
        self.marked: set[tuple[int, int]] = set()  # (group, file) to remove
        self.item_map: dict[str, tuple[int, int]] = {}  # tree id -> indexes
        self.busy = False
        self.cancel_event = threading.Event()
        self.messages: queue.Queue = queue.Queue()
        self._preview_image = None

        self._build_ui()
        self.after(100, self._poll_messages)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # --- Folders and options --------------------------------------
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="nsew", **pad)
        top.columnconfigure(0, weight=1)

        folders = ttk.LabelFrame(top, text="Folders to scan (highest priority first)")
        folders.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        folders.columnconfigure(0, weight=1)
        self.folder_list = tk.Listbox(folders, height=6, activestyle="none",
                                      selectmode="extended")
        self.folder_list.grid(row=0, column=0, rowspan=5, sticky="nsew",
                              padx=(6, 0), pady=6)
        fsb = ttk.Scrollbar(folders, orient="vertical",
                            command=self.folder_list.yview)
        fsb.grid(row=0, column=1, rowspan=5, sticky="ns", pady=6)
        self.folder_list.configure(yscrollcommand=fsb.set)
        self.folder_buttons = []
        for row, (label, cmd) in enumerate((
            ("Add folder…", self.add_folder),
            ("Remove", self.remove_folder),
            ("Move up", lambda: self.move_folder(-1)),
            ("Move down", lambda: self.move_folder(1)),
        )):
            b = ttk.Button(folders, text=label, command=cmd, width=12)
            b.grid(row=row, column=2, sticky="ew", padx=6, pady=(6 if row == 0 else 2, 2))
            self.folder_buttons.append(b)
        ttk.Label(
            folders, wraplength=520, foreground="#555",
            text="When the same image is in more than one folder, the copy in "
                 "the folder highest in this list is kept and the others are "
                 "marked for removal. Put the folder you want cleaned out at "
                 "the bottom.",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))

        options = ttk.LabelFrame(top, text="Options")
        options.grid(row=0, column=1, sticky="nsew")
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="Find:").grid(row=0, column=0, sticky="w",
                                             padx=6, pady=(6, 0))
        self.mode = tk.StringVar(value=core.MODE_IDENTICAL)
        ttk.Radiobutton(options, text="Identical files (exact copies)",
                        value=core.MODE_IDENTICAL, variable=self.mode,
                        command=self._update_mode_state).grid(
            row=0, column=1, columnspan=2, sticky="w", pady=(6, 0))
        similar_text = "Similar images (resized, re-saved, edited)"
        if not core.HAS_SIMILAR_SUPPORT:
            similar_text = "Similar images (install Pillow)"
        self.similar_radio = ttk.Radiobutton(
            options, text=similar_text, value=core.MODE_SIMILAR,
            variable=self.mode, command=self._update_mode_state,
            state="normal" if core.HAS_SIMILAR_SUPPORT else "disabled")
        self.similar_radio.grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(options, text="Similarity:").grid(row=2, column=0, sticky="w",
                                                   padx=6, pady=(2, 0))
        self.similarity = tk.IntVar(value=core.DEFAULT_SIMILARITY)
        self.similarity_text = tk.StringVar(value=f"{core.DEFAULT_SIMILARITY}%")

        def on_scale(value: str) -> None:
            self.similarity.set(round(float(value)))
            self.similarity_text.set(f"{self.similarity.get()}%")

        self.similarity_scale = ttk.Scale(options, from_=70, to=100,
                                          orient="horizontal", command=on_scale)
        self.similarity_scale.set(core.DEFAULT_SIMILARITY)
        self.similarity_scale.grid(row=2, column=1, sticky="ew", pady=(2, 0))
        self.similarity_label = ttk.Label(options, width=5,
                                          textvariable=self.similarity_text)
        self.similarity_label.grid(row=2, column=2, sticky="w", padx=(4, 6))
        self.similarity_hint = ttk.Label(
            options, foreground="#555", wraplength=360,
            text="Higher is stricter. 90 catches resized and re-compressed "
                 "copies. Lower values find more but may group different photos.")
        self.similarity_hint.grid(row=3, column=0, columnspan=3, sticky="w", padx=6)

        ttk.Separator(options).grid(row=4, column=0, columnspan=3, sticky="ew",
                                    padx=6, pady=6)
        self.only_images = tk.BooleanVar(value=True)
        ttk.Radiobutton(options, text="Images only", value=True,
                        variable=self.only_images,
                        command=self._update_ext_state).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=6)
        self.all_types_radio = ttk.Radiobutton(
            options, text="All file types", value=False,
            variable=self.only_images, command=self._update_ext_state)
        self.all_types_radio.grid(row=5, column=1, columnspan=2, sticky="e", padx=6)
        self.ext_var = tk.StringVar(
            value=" ".join(sorted(e.lstrip(".") for e in core.IMAGE_EXTENSIONS)))
        self.ext_entry = ttk.Entry(options, textvariable=self.ext_var, width=44)
        self.ext_entry.grid(row=6, column=0, columnspan=3, sticky="ew", padx=6,
                            pady=(2, 0))
        self.include_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Include hidden files and folders",
                        variable=self.include_hidden).grid(
            row=7, column=0, columnspan=3, sticky="w", padx=6, pady=6)

        # --- Scan bar ------------------------------------------------
        scanbar = ttk.Frame(self)
        scanbar.grid(row=1, column=0, sticky="ew", **pad)
        scanbar.columnconfigure(2, weight=1)
        self.scan_button = ttk.Button(scanbar, text="Scan for duplicates",
                                      command=self.start_scan)
        self.scan_button.grid(row=0, column=0)
        self.cancel_button = ttk.Button(scanbar, text="Cancel",
                                        command=self.cancel_event.set,
                                        state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(scanbar, mode="determinate")
        self.progress.grid(row=0, column=2, sticky="ew", padx=6)
        self.status = tk.StringVar(value="Add one or more folders, then scan.")
        ttk.Label(scanbar, textvariable=self.status, width=48).grid(
            row=0, column=3, sticky="e")

        # --- Results -------------------------------------------------
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew", **pad)

        tree_frame = ttk.Frame(panes)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("action", "match", "folder", "dimensions", "size", "modified"),
            show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Duplicate group / file name")
        self.tree.heading("action", text="Action")
        self.tree.heading("match", text="Match")
        self.tree.heading("folder", text="Folder")
        self.tree.heading("dimensions", text="Dimensions")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.column("#0", width=230, stretch=False)
        self.tree.column("action", width=70, anchor="center", stretch=False)
        self.tree.column("match", width=80, anchor="center", stretch=False)
        self.tree.column("folder", width=280)
        self.tree.column("dimensions", width=95, anchor="center", stretch=False)
        self.tree.column("size", width=75, anchor="e", stretch=False)
        self.tree.column("modified", width=125, stretch=False)
        self.tree.tag_configure("group", font=("TkDefaultFont", 9, "bold"))
        self.tree.tag_configure("keep", foreground="#1b6e2a")
        self.tree.tag_configure("remove", foreground="#b3261e",
                                background="#fdecea")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self.toggle_selected())
        self.tree.bind("<space>", lambda e: self.toggle_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-2>", self._show_context_menu)  # macOS
        ttk.Label(tree_frame, foreground="#555",
                  text="Double-click a file (or press Space) to switch it "
                       "between Keep and Remove. Right-click for more options.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        panes.add(tree_frame, weight=3)

        preview = ttk.LabelFrame(panes, text="Preview")
        preview.columnconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview, anchor="center",
                                       text="Select a file to preview it.")
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.preview_info = tk.StringVar()
        ttk.Label(preview, textvariable=self.preview_info, wraplength=PREVIEW_SIZE,
                  justify="left").grid(row=1, column=0, sticky="nw", padx=6, pady=6)
        panes.add(preview, weight=1)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Keep only this copy",
                              command=self.keep_only_selected)
        self.menu.add_command(label="Switch Keep / Remove",
                              command=self.toggle_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Show in folder", command=self.show_selected)

        # --- Removal -------------------------------------------------
        bottom = ttk.LabelFrame(self, text="Remove duplicates")
        bottom.grid(row=3, column=0, sticky="ew", **pad)
        bottom.columnconfigure(5, weight=1)

        ttk.Label(bottom, text="If copies are in equally ranked folders, keep the:").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))
        self.tiebreak = tk.StringVar(value=core.DEFAULT_TIEBREAK)
        tiebreak_box = ttk.Combobox(bottom, textvariable=self.tiebreak,
                                    state="readonly", values=list(core.TIEBREAKS),
                                    width=18)
        tiebreak_box.grid(row=0, column=2, sticky="w", pady=(6, 2))
        tiebreak_box.bind("<<ComboboxSelected>>", lambda e: self.auto_select())
        self.auto_button = ttk.Button(bottom, text="Auto-select by folder priority",
                                      command=self.auto_select)
        self.auto_button.grid(row=0, column=3, padx=6, pady=(6, 2))
        self.keep_all_button = ttk.Button(bottom, text="Keep all",
                                          command=self.keep_all)
        self.keep_all_button.grid(row=0, column=4, pady=(6, 2))
        self.prefer_quality = tk.BooleanVar(value=False)
        self.prefer_quality_check = ttk.Checkbutton(
            bottom, variable=self.prefer_quality, command=self.auto_select,
            text="Similar images: always keep the highest-resolution copy, "
                 "even if it's in a lower-priority folder")
        self.prefer_quality_check.grid(row=3, column=0, columnspan=7, sticky="w",
                                       padx=6, pady=(0, 6))

        ttk.Label(bottom, text="Removed files go to:").grid(
            row=1, column=0, sticky="w", padx=6, pady=(4, 6))
        has_trash = core.send2trash is not None
        self.method = tk.StringVar(
            value=core.METHOD_TRASH if has_trash else core.METHOD_MOVE)
        trash_label = "Recycle Bin / Trash" if has_trash else \
            "Recycle Bin (install send2trash)"
        ttk.Radiobutton(bottom, text=trash_label, value=core.METHOD_TRASH,
                        variable=self.method, command=self._update_method_state,
                        state="normal" if has_trash else "disabled").grid(
            row=1, column=1, sticky="w", pady=(4, 6))
        ttk.Radiobutton(bottom, text="A folder:", value=core.METHOD_MOVE,
                        variable=self.method,
                        command=self._update_method_state).grid(
            row=1, column=2, sticky="w", pady=(4, 6))
        move_frame = ttk.Frame(bottom)
        move_frame.grid(row=1, column=3, columnspan=3, sticky="ew", pady=(4, 6))
        move_frame.columnconfigure(0, weight=1)
        self.move_to = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Removed duplicates"))
        self.move_entry = ttk.Entry(move_frame, textvariable=self.move_to)
        self.move_entry.grid(row=0, column=0, sticky="ew")
        self.move_browse = ttk.Button(move_frame, text="Browse…",
                                      command=self.choose_move_folder)
        self.move_browse.grid(row=0, column=1, padx=6)
        ttk.Radiobutton(bottom, text="Delete permanently",
                        value=core.METHOD_DELETE, variable=self.method,
                        command=self._update_method_state).grid(
            row=1, column=6, sticky="w", padx=6, pady=(4, 6))

        self.summary = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.summary,
                  font=("TkDefaultFont", 10, "bold")).grid(
            row=2, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 6))
        self.remove_button = ttk.Button(bottom, text="Remove marked files",
                                        command=self.remove_marked,
                                        state="disabled")
        self.remove_button.grid(row=2, column=5, columnspan=2, sticky="e",
                                padx=6, pady=(0, 6))
        self._update_method_state()
        self._update_mode_state()

    def _update_ext_state(self) -> None:
        self.ext_entry.configure(
            state="normal" if self.only_images.get() else "disabled")

    def _update_mode_state(self) -> None:
        similar = self.mode.get() == core.MODE_SIMILAR
        if similar:
            # Similar matching only makes sense for pictures.
            self.only_images.set(True)
            if self.tiebreak.get() == core.DEFAULT_TIEBREAK:
                self.tiebreak.set(core.DEFAULT_SIMILAR_TIEBREAK)
        elif self.tiebreak.get() == core.DEFAULT_SIMILAR_TIEBREAK:
            self.tiebreak.set(core.DEFAULT_TIEBREAK)
        self.all_types_radio.configure(state="disabled" if similar else "normal")
        self.similarity_scale.configure(state="normal" if similar else "disabled")
        self.similarity_label.configure(state="normal" if similar else "disabled")
        self._update_ext_state()

    @property
    def results_are_similar(self) -> bool:
        return self.scan_mode == core.MODE_SIMILAR

    def _update_method_state(self) -> None:
        state = "normal" if self.method.get() == core.METHOD_MOVE else "disabled"
        self.move_entry.configure(state=state)
        self.move_browse.configure(state=state)

    # ------------------------------------------------------------------
    # Folder list
    # ------------------------------------------------------------------

    def _refresh_folders(self, select: int | None = None) -> None:
        self.folder_list.delete(0, "end")
        for i, f in enumerate(self.folders, 1):
            self.folder_list.insert("end", f"{i}.  {f}")
        if select is not None and 0 <= select < len(self.folders):
            self.folder_list.selection_set(select)
            self.folder_list.see(select)

    def add_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Choose a folder to scan",
                                       mustexist=True)
        if not path:
            return
        path = os.path.normpath(path)
        if any(os.path.normcase(path) == os.path.normcase(f) for f in self.folders):
            messagebox.showinfo(APP_TITLE, "That folder is already in the list.")
            return
        self.folders.append(path)
        self._refresh_folders(len(self.folders) - 1)

    def remove_folder(self) -> None:
        for i in sorted(self.folder_list.curselection(), reverse=True):
            del self.folders[i]
        self._refresh_folders()

    def move_folder(self, step: int) -> None:
        sel = self.folder_list.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + step
        if 0 <= j < len(self.folders):
            self.folders[i], self.folders[j] = self.folders[j], self.folders[i]
            self._refresh_folders(j)

    def choose_move_folder(self) -> None:
        path = filedialog.askdirectory(parent=self,
                                       title="Choose where removed files go")
        if path:
            self.move_to.set(os.path.normpath(path))

    # ------------------------------------------------------------------
    # Background work
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        normal = "disabled" if busy else "normal"
        self.scan_button.configure(state=normal)
        self.auto_button.configure(state=normal)
        self.keep_all_button.configure(state=normal)
        for b in self.folder_buttons:
            b.configure(state=normal)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self._update_summary()

    def _run_in_background(self, func, *args) -> None:
        self.cancel_event.clear()
        self._set_busy(True)
        progress = lambda stage, done, total: self.messages.put(
            ("progress", stage, done, total))

        def work():
            try:
                self.messages.put(("done", func, func(*args, progress=progress)))
            except core.Cancelled:
                self.messages.put(("cancelled",))
            except Exception as exc:  # Show unexpected errors to the user.
                self.messages.put(("error", exc))

        threading.Thread(target=work, daemon=True).start()

    def _poll_messages(self) -> None:
        try:
            while True:
                msg = self.messages.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, stage, done, total = msg
                    if total:
                        self.progress.configure(mode="determinate",
                                                maximum=total, value=done)
                        self.status.set(f"{stage}… {done:,} of {total:,}")
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.step(5)
                        self.status.set(f"{stage}… {done:,} found")
                elif kind == "done":
                    self._set_busy(False)
                    self.progress.configure(mode="determinate", value=0)
                    if msg[1] == self._scan_job:
                        self._scan_finished(msg[2])
                    else:
                        self._removal_finished(msg[2])
                elif kind == "cancelled":
                    self._set_busy(False)
                    self.progress.configure(mode="determinate", value=0)
                    self.status.set("Cancelled.")
                elif kind == "error":
                    self._set_busy(False)
                    self.progress.configure(mode="determinate", value=0)
                    self.status.set("Something went wrong.")
                    messagebox.showerror(APP_TITLE, str(msg[1]))
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _scan_job(self, mode, roots, extensions, include_hidden, similarity,
                  progress):
        if mode == core.MODE_SIMILAR:
            return core.find_similar_images(roots, extensions, include_hidden,
                                            similarity=similarity,
                                            progress=progress,
                                            cancel=self.cancel_event.is_set)
        return core.find_duplicates(roots, extensions, include_hidden,
                                    progress=progress,
                                    cancel=self.cancel_event.is_set)

    def start_scan(self) -> None:
        if self.busy:
            return
        if not self.folders:
            messagebox.showinfo(APP_TITLE, "Add at least one folder to scan first.")
            return
        missing = [f for f in self.folders if not os.path.isdir(f)]
        if missing:
            messagebox.showerror(APP_TITLE, "These folders can't be found:\n\n"
                                 + "\n".join(missing))
            return
        mode = self.mode.get()
        extensions = None
        if self.only_images.get() or mode == core.MODE_SIMILAR:
            extensions = core.parse_extensions(self.ext_var.get())
            if not extensions:
                messagebox.showinfo(APP_TITLE, "Enter at least one file extension.")
                return
        self.scan_roots = list(self.folders)
        self._clear_results()
        self.scan_mode = mode
        self._scan_started = time.monotonic()
        self.status.set("Starting scan…")
        self._run_in_background(self._scan_job, mode, self.scan_roots,
                                extensions, self.include_hidden.get(),
                                self.similarity.get())

    def _scan_finished(self, result: core.ScanResult) -> None:
        self.groups = result.groups
        self.scan_mode = result.mode
        elapsed = time.monotonic() - self._scan_started
        copies = sum(len(g.files) - 1 for g in self.groups)
        found = "similar" if self.results_are_similar else "identical"
        self.status.set(
            f"Scanned {result.files_scanned:,} files in {elapsed:.1f}s: "
            f"{len(self.groups):,} {found} groups, {copies:,} extra copies.")
        self.auto_select()  # Pre-mark copies using the folder priority.
        if result.errors:
            self._show_problems("Some files couldn't be read and were skipped:",
                                result.errors)
        if not self.groups:
            messagebox.showinfo(APP_TITLE, f"No {found} images were found.")

    # ------------------------------------------------------------------
    # Results list
    # ------------------------------------------------------------------

    def _clear_results(self) -> None:
        self.groups = []
        self.marked.clear()
        self.item_map.clear()
        self.tree.delete(*self.tree.get_children())
        self._clear_preview()
        self._update_summary()

    @staticmethod
    def _match_text(group: core.DuplicateGroup, f: core.FileEntry) -> str:
        if group.kind == core.MODE_IDENTICAL:
            return "Identical"
        if f.similarity is None:
            return ""
        return f"{f.similarity:.0f}%"

    @staticmethod
    def _dimensions_text(f: core.FileEntry) -> str:
        return f"{f.width}×{f.height}" if f.pixels else ""

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.item_map.clear()
        for gi, g in enumerate(self.groups):
            gid = f"g{gi}"
            if g.kind == core.MODE_SIMILAR:
                label = f"{len(g.files)} similar images"
                size = ""
            else:
                label = f"{len(g.files)} identical copies"
                size = human_size(g.files[0].size)
            self.tree.insert(
                "", "end", gid, open=True, tags=("group",), text=label,
                values=("", "", f"Up to {human_size(g.wasted_bytes)} can be freed",
                        "", size, ""))
            for fi, f in enumerate(g.files):
                iid = f"g{gi}f{fi}"
                self.item_map[iid] = (gi, fi)
                self.tree.insert(gid, "end", iid, text=os.path.basename(f.path),
                                 values=("", self._match_text(g, f),
                                         os.path.dirname(f.path),
                                         self._dimensions_text(f),
                                         human_size(f.size),
                                         time.strftime("%Y-%m-%d %H:%M",
                                                       time.localtime(f.mtime))))
                self._refresh_row(gi, fi)
        self._update_summary()

    def _refresh_row(self, gi: int, fi: int) -> None:
        iid = f"g{gi}f{fi}"
        remove = (gi, fi) in self.marked
        self.tree.set(iid, "action", "Remove" if remove else "Keep")
        self.tree.item(iid, tags=("remove" if remove else "keep",))

    def _update_summary(self) -> None:
        count = len(self.marked)
        size = sum(self.groups[gi].files[fi].size for gi, fi in self.marked)
        if count:
            self.summary.set(f"{count:,} files marked for removal "
                             f"({human_size(size)} will be freed).")
        elif self.groups:
            self.summary.set("No files marked for removal.")
        else:
            self.summary.set("")
        self.remove_button.configure(
            state="normal" if count and not self.busy else "disabled")

    def auto_select(self) -> None:
        if self.busy:
            return
        self.marked.clear()
        prefer_quality = self.results_are_similar and self.prefer_quality.get()
        for gi, g in enumerate(self.groups):
            keeper = core.choose_keeper(g, self.tiebreak.get(), prefer_quality)
            for fi, f in enumerate(g.files):
                if f is not keeper:
                    self.marked.add((gi, fi))
        self._populate_tree()

    def keep_all(self) -> None:
        self.marked.clear()
        self._populate_tree()

    def _selected_file(self):
        sel = self.tree.selection()
        return self.item_map.get(sel[0]) if sel else None

    def toggle_selected(self) -> None:
        idx = self._selected_file()
        if idx is None or self.busy:
            return
        gi, fi = idx
        if idx in self.marked:
            self.marked.discard(idx)
        else:
            group_size = len(self.groups[gi].files)
            marked_in_group = sum(1 for g, _ in self.marked if g == gi)
            if marked_in_group + 1 >= group_size:
                self.bell()
                self.status.set("At least one copy of each image must be kept.")
                return
            self.marked.add(idx)
        self._refresh_row(gi, fi)
        self._update_summary()

    def keep_only_selected(self) -> None:
        idx = self._selected_file()
        if idx is None or self.busy:
            return
        gi, keep_fi = idx
        for fi in range(len(self.groups[gi].files)):
            if fi == keep_fi:
                self.marked.discard((gi, fi))
            else:
                self.marked.add((gi, fi))
            self._refresh_row(gi, fi)
        self._update_summary()

    def show_selected(self) -> None:
        idx = self._selected_file()
        if idx is not None:
            path = self.groups[idx[0]].files[idx[1]].path
            try:
                open_in_file_manager(path)
            except OSError as exc:
                messagebox.showerror(APP_TITLE, str(exc))

    def _show_context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid in self.item_map:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _clear_preview(self) -> None:
        self._preview_image = None
        self.preview_label.configure(image="", text="Select a file to preview it.")
        self.preview_info.set("")

    def _on_select(self, _event=None) -> None:
        idx = self._selected_file()
        if idx is None:
            self._clear_preview()
            return
        g = self.groups[idx[0]]
        f = g.files[idx[1]]
        lines = [os.path.basename(f.path), human_size(f.size)]
        if f.pixels:
            lines.append(f"{f.width} × {f.height} pixels")
        lines.append("Modified "
                     + time.strftime("%Y-%m-%d %H:%M", time.localtime(f.mtime)))
        if g.kind == core.MODE_SIMILAR and f.similarity is not None:
            lines.append(f"{f.similarity:.0f}% similar to the best image "
                         "in this group")
        elif f.digest:
            lines.append(f"SHA-256: {f.digest[:16]}…")
        self.preview_info.set("\n".join(lines))
        image = None
        try:
            if Image is not None:
                with Image.open(f.path) as im:
                    im.draft("RGB", (PREVIEW_SIZE, PREVIEW_SIZE))
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
                    image = ImageTk.PhotoImage(im)
            elif os.path.splitext(f.path)[1].lower() in (".png", ".gif"):
                image = tk.PhotoImage(file=f.path)
                factor = max(1, -(-max(image.width(), image.height()) // PREVIEW_SIZE))
                image = image.subsample(factor, factor)
        except Exception:
            image = None
        self._preview_image = image
        if image is not None:
            self.preview_label.configure(image=image, text="")
        else:
            self.preview_label.configure(
                image="", text="No preview available."
                + ("" if Image else "\n(Install Pillow, with Tk support, "
                                    "for previews of more formats.)"))

    # ------------------------------------------------------------------
    # Removal
    # ------------------------------------------------------------------

    def _removal_job(self, plan, method, move_to, roots, progress):
        return core.remove_duplicates(plan, method, move_to, roots,
                                      progress=progress)

    def remove_marked(self) -> None:
        if self.busy or not self.marked:
            return
        plan = []
        for gi, g in enumerate(self.groups):
            remove = [f for fi, f in enumerate(g.files) if (gi, fi) in self.marked]
            keep = [f for fi, f in enumerate(g.files) if (gi, fi) not in self.marked]
            if remove and keep:
                plan.append(core.RemovalItem(keep[0], remove))
        count = sum(len(p.remove) for p in plan)
        size = sum(f.size for p in plan for f in p.remove)

        method = self.method.get()
        move_to = None
        if method == core.METHOD_MOVE:
            move_to = self.move_to.get().strip()
            if not move_to:
                messagebox.showinfo(APP_TITLE, "Choose a folder to move files to.")
                return
            move_to = os.path.abspath(os.path.expanduser(move_to))
            where = f"moved to:\n{move_to}"
        elif method == core.METHOD_TRASH:
            where = "moved to the Recycle Bin / Trash"
        else:
            where = "PERMANENTLY DELETED. This can't be undone"

        question = f"{count:,} files ({human_size(size)}) will be {where}.\n\n"
        if self.results_are_similar:
            question += ("These are similar images, not exact copies. Please "
                         "check the groups before continuing.\n\n")
        question += ("One image in every group is always kept, and each file "
                     "is checked again before it's removed.\n\nContinue?")
        icon = "warning" if (method == core.METHOD_DELETE
                             or self.results_are_similar) else "question"
        if not messagebox.askyesno(APP_TITLE, question, icon=icon, parent=self):
            return
        self.status.set("Removing files…")
        self._run_in_background(self._removal_job, plan, method, move_to,
                                list(self.scan_roots))

    def _removal_finished(self, result: core.RemovalResult) -> None:
        removed = {os.path.normcase(p) for p in result.removed}
        new_groups = []
        for g in self.groups:
            g.files = [f for f in g.files
                       if os.path.normcase(f.path) not in removed]
            if len(g.files) > 1:
                new_groups.append(g)
        self.groups = new_groups
        self.marked.clear()
        self._populate_tree()
        self._clear_preview()
        self.status.set(f"Removed {len(result.removed):,} files.")
        if result.skipped:
            self._show_problems(
                f"Removed {len(result.removed):,} files. "
                f"{len(result.skipped):,} were left in place:", result.skipped)
        else:
            messagebox.showinfo(APP_TITLE,
                                f"Removed {len(result.removed):,} files.")

    def _show_problems(self, heading: str, problems) -> None:
        win = tk.Toplevel(self)
        win.title(APP_TITLE)
        win.geometry("760x360")
        win.transient(self)
        ttk.Label(win, text=heading).pack(anchor="w", padx=10, pady=(10, 4))
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        text = tk.Text(frame, wrap="word", height=12)
        sb = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for path, reason in problems:
            text.insert("end", f"{path}\n    {reason}\n")
        text.configure(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=10)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
