"""Filesystem scanning engine: finds Python virtualenvs and node_modules folders.

Public API (see models.py for the contract):
    scan(root, progress=None, cancel=None) -> Node
    children_of(node) -> list[Node]
    detect_venv(path) -> str | None
    format_size(n) -> str

Design notes
------------
* Everything uses ``os.scandir`` + ``lstat`` semantics. Symlinks are never
  followed (pnpm / ``node_modules/.bin`` are full of them).
* Size is *on-disk* size: ``st_blocks * 512`` (``st_size`` if unavailable),
  directories' own blocks included, which matches ``du``.
* Hard links (``st_nlink > 1`` on non-directories) are de-duplicated by
  ``(st_dev, st_ino)`` across the WHOLE scan, so a file shared between two
  venvs (uv cache) or with the pnpm store is charged once, to whichever
  target the worker threads reach first. The grand total is exact; the
  per-target attribution of shared files is therefore non-deterministic.
* The walker (scan thread) looks for targets and never descends into one;
  each target is sized by a ThreadPoolExecutor worker. Progress callbacks are
  always invoked from the thread that called ``scan``.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait

from .models import Kind, Node, ProgressCallback, ScanProgress

# Directories never descended into during *detection* (they are noise, can be
# huge, and never hold projects the user cares about).
SKIP_DIR_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".Trash",
    ".Trashes",
    ".Spotlight-V100",
    ".fseventsd",
    ".TemporaryItems",
    ".DocumentRevisions-V100",
    "$RECYCLE.BIN",
    "System Volume Information",
})
# macOS bundles that are opaque to the user (an Electron .app ships its own
# node_modules; a Photos library is not a project).
SKIP_DIR_SUFFIXES = (".app", ".photoslibrary")
# Linux pseudo-filesystems (only relevant when scanning from `/`). Matched by
# absolute path so a project folder that happens to be called `proc` is still scanned.
SKIP_ABS_PATHS = frozenset({"/proc", "/sys", "/dev", "/run"})

SCAN_WORKERS = 12
CHILDREN_WORKERS = 8
PROGRESS_INTERVAL = 0.1  # seconds -> ~10 callbacks / second


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def format_size(n: int) -> str:
    """Finder-like decimal size: '0 B', '120 KB', '1.8 GB', '494.2 GB'."""
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(max(n, 0))
    idx = 0
    while value >= 1000 and idx < len(units) - 1:
        value /= 1000
        idx += 1
    # B and KB are shown as whole numbers, larger units with one decimal.
    # Rounding may carry a value to 1000 (e.g. 999.96 KB): bump the unit.
    if idx <= 1:
        rounded = round(value)
        if rounded >= 1000 and idx < len(units) - 1:
            return f"{1.0:.1f} {units[idx + 1]}"
        return f"{rounded} {units[idx]}"
    rounded = round(value, 1)
    if rounded >= 1000 and idx < len(units) - 1:
        return f"{1.0:.1f} {units[idx + 1]}"
    return f"{rounded:.1f} {units[idx]}"


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def _parse_pyvenv_cfg(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, val = line.partition("=")
                if sep:
                    out[key.strip().lower()] = val.strip()
    except OSError:
        pass
    return out


def _short_version(cfg: dict[str, str]) -> str:
    raw = cfg.get("version_info") or cfg.get("version") or ""
    parts = raw.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return raw


def _has_glob_dir(parent: str, prefix: str) -> bool:
    try:
        with os.scandir(parent) as it:
            return any(e.name.startswith(prefix) for e in it)
    except OSError:
        return False


def _fallback_is_env(path: str, names: set[str]) -> bool:
    """bin/python* (or Scripts/python.exe) AND lib/python*/site-packages."""
    has_python = False
    if "bin" in names:
        has_python = _has_glob_dir(os.path.join(path, "bin"), "python")
    if not has_python and "Scripts" in names:
        has_python = os.path.exists(os.path.join(path, "Scripts", "python.exe"))
    if not has_python:
        return False
    if "Lib" in names and os.path.isdir(os.path.join(path, "Lib", "site-packages")):
        return True
    if "lib" in names:
        lib = os.path.join(path, "lib")
        try:
            with os.scandir(lib) as it:
                for e in it:
                    if e.name.startswith("python") and os.path.isdir(
                        os.path.join(e.path, "site-packages")
                    ):
                        return True
        except OSError:
            pass
    return False


def _detect(path: str, names: set[str]) -> str | None:
    """Classify `path` given the set of its entry names."""
    if "pyvenv.cfg" in names:
        cfg = _parse_pyvenv_cfg(os.path.join(path, "pyvenv.cfg"))
        if "uv" in cfg:
            label = "uv venv"
        elif "virtualenv" in cfg:
            label = "virtualenv"
        else:
            label = "venv"
        ver = _short_version(cfg)
        return f"{label} · py {ver}" if ver else label
    if "conda-meta" in names:
        return "conda env"
    if ("bin" in names or "Scripts" in names) and ("lib" in names or "Lib" in names):
        if _fallback_is_env(path, names):
            return "python env"
    return None


def detect_venv(path: str) -> str | None:
    """Short description if `path` is a Python environment, else None."""
    try:
        with os.scandir(path) as it:
            names = {e.name for e in it}
    except OSError:
        return None
    return _detect(path, names)


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.endswith(SKIP_DIR_SUFFIXES)


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #

def _disk_size(st: os.stat_result) -> int:
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return st.st_size
    return blocks * 512


class _Stats:
    """Counters shared between walker and workers (updated in batches)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.dirs = 0
        self.targets = 0
        self.bytes = 0
        self.current = ""

    def add_dirs(self, n: int) -> None:
        with self.lock:
            self.dirs += n


def _du(
    path: str,
    seen: set[tuple[int, int]],
    seen_lock: threading.Lock,
    cancel: threading.Event | None,
    stats: _Stats | None = None,
) -> tuple[int, str | None, bool]:
    """Iteratively size the tree at `path`.

    Returns (bytes, first_error_or_None, completed). Symlinks are counted as
    the link itself, never followed. Non-directories with st_nlink > 1 are
    charged once per (dev, ino) in `seen`.
    """
    total = 0
    error: str | None = None
    try:
        st = os.lstat(path)
    except OSError as e:
        return 0, f"{e.strerror or e}", True
    total += _disk_size(st)
    stack = [path]
    local_dirs = 0
    while stack:
        if cancel is not None and cancel.is_set():
            return total, error, False
        d = stack.pop()
        local_dirs += 1
        if stats is not None:
            stats.current = d
            if local_dirs >= 256:
                stats.add_dirs(local_dirs)
                local_dirs = 0
        try:
            it = os.scandir(d)
        except OSError as e:
            if error is None:
                error = f"{d}: {e.strerror or e}"
            continue
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as e:
                    if error is None:
                        error = f"{d}: {e.strerror or e}"
                    break
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError as e:
                    if error is None:
                        error = f"{entry.path}: {e.strerror or e}"
                    continue
                if stat.S_ISDIR(est.st_mode):
                    total += _disk_size(est)
                    stack.append(entry.path)
                    continue
                if est.st_nlink > 1:
                    key = (est.st_dev, est.st_ino)
                    with seen_lock:
                        if key in seen:
                            continue
                        seen.add(key)
                total += _disk_size(est)
    if stats is not None and local_dirs:
        stats.add_dirs(local_dirs)
    return total, error, True


# --------------------------------------------------------------------------- #
# scan()
# --------------------------------------------------------------------------- #

class _Dir:
    """Lightweight walker record; becomes a Node only if a target is below."""

    __slots__ = ("path", "name", "parent", "node")

    def __init__(self, path: str, name: str, parent: _Dir | None) -> None:
        self.path = path
        self.name = name
        self.parent = parent
        self.node: Node | None = None


def _ensure_node(item: _Dir) -> Node:
    if item.node is not None:
        return item.node
    chain = []
    cur: _Dir | None = item
    while cur is not None and cur.node is None:
        chain.append(cur)
        cur = cur.parent
    for it in reversed(chain):
        it.node = Node(
            name=it.name, path=it.path, kind=Kind.DIR, children_loaded=True
        )
        if it.parent is not None:
            assert it.parent.node is not None
            it.parent.node.children.append(it.node)
    assert item.node is not None
    return item.node


def scan(
    root: str,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Node:
    """Scan `root` for node_modules folders and Python environments.

    Returns a pruned Kind.DIR tree (only paths that lead to targets), sizes
    aggregated, children sorted by size descending. If cancelled, returns the
    tree of targets fully sized so far.
    """
    root = os.path.abspath(root)
    stats = _Stats()
    seen: set[tuple[int, int]] = set()
    seen_lock = threading.Lock()
    last_report = 0.0

    def report(done: bool = False) -> None:
        nonlocal last_report
        if progress is None:
            return
        now = time.monotonic()
        if not done and now - last_report < PROGRESS_INTERVAL:
            return
        last_report = now
        with stats.lock:
            snap = ScanProgress(
                dirs_scanned=stats.dirs,
                targets_found=stats.targets,
                bytes_found=stats.bytes,
                current_path=stats.current,
                done=done,
            )
        progress(snap)

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    root_item = _Dir(root, os.path.basename(root) or root, None)
    root_node = _ensure_node(root_item)
    root_error: str | None = None

    jobs: list[tuple[_Dir, Kind, str, Future]] = []

    def size_job(item: _Dir) -> tuple[int, str | None, bool]:
        size, err, ok = _du(item.path, seen, seen_lock, cancel, stats)
        if ok:
            with stats.lock:
                stats.bytes += size
        return size, err, ok

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS, thread_name_prefix="diskscan") as pool:

        def submit(item: _Dir, kind: Kind, detail: str) -> None:
            with stats.lock:
                stats.targets += 1
            jobs.append((item, kind, detail, pool.submit(size_job, item)))

        stack = [root_item]
        walked = 0
        while stack and not cancelled():
            item = stack.pop()
            stats.current = item.path
            walked += 1
            if walked % 64 == 0:
                stats.add_dirs(64)
            report()

            if item.name == "node_modules" and item is not root_item:
                submit(item, Kind.NODE_MODULES, "")
                continue
            try:
                with os.scandir(item.path) as it:
                    entries = list(it)
            except OSError as e:
                if item is root_item:
                    root_error = f"{e.strerror or e}"
                continue

            names = {e.name for e in entries}
            if item.name == "node_modules":  # root itself is a node_modules
                submit(item, Kind.NODE_MODULES, "")
                continue
            detail = _detect(item.path, names)
            if detail is not None:
                submit(item, Kind.VENV, detail)
                continue

            for e in entries:
                name = e.name
                if _skip_dir(name) or e.path in SKIP_ABS_PATHS:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(_Dir(e.path, name, item))
                except OSError:
                    continue
        stats.add_dirs(walked % 64)

        # Wait for sizing workers, reporting progress from this thread.
        pending = {j[3] for j in jobs}
        while pending:
            if cancelled():
                for f in pending:
                    f.cancel()
                break
            _, pending = wait(pending, timeout=PROGRESS_INTERVAL)
            report()

    # Pool has shut down (workers aborted quickly on cancel); build the tree.
    for item, kind, detail, fut in jobs:
        if fut.cancelled():
            continue
        try:
            size, err, ok = fut.result()
        except Exception as e:  # defensive: never lose the whole scan
            size, err, ok = 0, repr(e), False
        if not ok:
            continue
        node = Node(
            name=item.name,
            path=item.path,
            kind=kind,
            size=size,
            target_count=1,
            detail=detail,
            error=err,
        )
        assert item.parent is not None or item is root_item
        parent_item = item.parent if item.parent is not None else item
        if item.parent is None:
            # Scan root is itself a target: hang it under the root DIR node.
            root_node.children.append(node)
            anc = root_node
            anc.size += size
            anc.target_count += 1
            continue
        _ensure_node(parent_item).children.append(node)
        cur: _Dir | None = parent_item
        while cur is not None:
            assert cur.node is not None
            cur.node.size += size
            cur.node.target_count += 1
            cur = cur.parent

    root_node.error = root_error
    sort_stack = [root_node]
    while sort_stack:
        n = sort_stack.pop()
        n.children.sort(key=lambda c: (-c.size, c.name.lower()))
        sort_stack.extend(c for c in n.children if c.kind is Kind.DIR)

    report(done=True)
    return root_node


# --------------------------------------------------------------------------- #
# children_of()
# --------------------------------------------------------------------------- #

def children_of(node: Node) -> list[Node]:
    """Lazily list the immediate entries of a NODE_MODULES / VENV / INNER node.

    Sub-directories become Kind.INNER nodes with their full subtree size,
    other entries (files, symlinks) become Kind.FILE nodes. Sorted by size
    descending and cached via ``node.children_loaded``.

    Hard-link policy: each call uses a *fresh* seen-set (shared only among the
    entries of this one call). Using the scan-global set would charge a
    package's shared files to whichever target got there first and could show
    a package as ~0 bytes even though it is large. Consequence: the children
    of a node can sum to slightly more than the node's scan-time size when
    files are hard-linked to something outside this node (e.g. the uv cache).
    """
    if node.kind is Kind.DIR:
        return node.children
    if node.kind is Kind.FILE:
        return []
    if node.children_loaded:
        return node.children

    seen: set[tuple[int, int]] = set()
    seen_lock = threading.Lock()
    children: list[Node] = []
    subdirs: list[Node] = []
    try:
        with os.scandir(node.path) as it:
            entries = list(it)
    except OSError as e:
        node.error = f"{e.strerror or e}"
        node.children = []
        node.children_loaded = True
        return node.children

    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as e:
            children.append(Node(entry.name, entry.path, Kind.FILE,
                                 error=f"{e.strerror or e}"))
            continue
        if stat.S_ISDIR(st.st_mode):
            subdirs.append(Node(entry.name, entry.path, Kind.INNER,
                                size=_disk_size(st)))
            continue
        detail = ""
        if stat.S_ISLNK(st.st_mode):
            try:
                detail = "→ " + os.readlink(entry.path)
            except OSError:
                detail = "symlink"
        size = _disk_size(st)
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            with seen_lock:
                if key in seen:
                    size = 0
                else:
                    seen.add(key)
        children.append(Node(entry.name, entry.path, Kind.FILE, size=size,
                             detail=detail))

    def size_dir(n: Node) -> None:
        total, err, _ = _du(n.path, seen, seen_lock, None)
        n.size = total
        n.error = err

    if subdirs:
        with ThreadPoolExecutor(max_workers=CHILDREN_WORKERS) as pool:
            list(pool.map(size_dir, subdirs))
    children.extend(subdirs)
    children.sort(key=lambda c: (-c.size, c.name.lower()))
    node.children = children
    node.children_loaded = True
    return children
