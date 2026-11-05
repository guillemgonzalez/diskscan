"""Shared data contract between the scanner (scanner.py) and the TUI (app.py).

DO NOT change these signatures without updating both sides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Kind(str, Enum):
    DIR = "dir"                    # ordinary directory that *contains* targets somewhere below
    NODE_MODULES = "node_modules"  # a node_modules directory (top-most one only)
    VENV = "venv"                  # a Python virtualenv (pyvenv.cfg / conda-meta / bin+site-packages)
    INNER = "inner"                # a sub-folder *inside* a target (e.g. a package in node_modules)
    FILE = "file"                  # a file inside a target (only produced by children_of)


@dataclass
class Node:
    name: str                      # basename for display
    path: str                      # absolute path
    kind: Kind
    size: int = 0                  # bytes on disk. For DIR: sum of all target sizes beneath it.
                                   # For NODE_MODULES/VENV/INNER: real on-disk usage of the subtree.
    children: list[Node] = field(default_factory=list)
                                   # DIR: only children that contain/are targets, sorted size desc.
                                   # NODE_MODULES/VENV/INNER: empty until loaded via children_of().
    children_loaded: bool = False  # True once children are populated (always True for DIR after scan)
    target_count: int = 0          # DIR: number of targets beneath. Targets: 1.
    detail: str = ""               # optional extra info, e.g. "uv venv · python 3.13" or "conda env"
    error: str | None = None       # set if the directory could not be read


@dataclass
class ScanProgress:
    dirs_scanned: int = 0
    targets_found: int = 0
    bytes_found: int = 0           # running total of target bytes sized so far
    current_path: str = ""
    done: bool = False


ProgressCallback = Callable[[ScanProgress], None]

# --- Functions implemented in diskscan/scanner.py -------------------------------
#
# def scan(root: str, progress: ProgressCallback | None = None,
#          cancel: threading.Event | None = None) -> Node
#     Walk `root`, return a Kind.DIR Node for root whose subtree contains only the
#     paths leading to targets (pruned), with sizes aggregated. Thread-safe to call
#     from a worker thread; progress is called periodically (throttled, ~10/s) and
#     once at the end with done=True.
#
# def children_of(node: Node) -> list[Node]
#     For a NODE_MODULES / VENV / INNER node: list its immediate entries (sub-dirs as
#     Kind.INNER, files grouped as Kind.FILE), each with on-disk size, sorted desc.
#     Sets node.children and node.children_loaded=True and returns the list.
#     Cached: calling twice does not rescan. For Kind.DIR, returns node.children.
#
# def detect_venv(path: str) -> str | None
#     Returns a short description ("venv", "uv venv", "conda env", ...) if `path`
#     is a Python environment, else None.
