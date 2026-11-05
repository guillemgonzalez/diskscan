"""Safe, permanent deletion of scan targets (node_modules folders and Python envs).

Every path is re-validated *immediately before* it is removed; anything that
fails a check is skipped and reported, never deleted.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from .models import Kind, Node
from .scanner import detect_venv

TARGET_KINDS = (Kind.NODE_MODULES, Kind.VENV)

# progress(index_1_based, total, path) -- called just before each deletion
DeleteProgress = Callable[[int, int, str], None]


@dataclass
class DeleteOutcome:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def refusal_reason(path: str, root: str, home: str | None = None) -> str | None:
    """Return why `path` must NOT be deleted, or None if it is safe to delete."""
    if not os.path.isabs(path):
        return "path is not absolute"
    if os.path.islink(path):
        return "is a symlink (refusing to follow)"
    if not os.path.lexists(path):
        return "no longer exists"
    if not os.path.isdir(path):
        return "is not a directory"
    if os.path.basename(path.rstrip(os.sep)) != "node_modules" and detect_venv(path) is None:
        return "no longer looks like node_modules or a Python environment"

    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    real_home = os.path.realpath(home if home is not None else os.path.expanduser("~"))
    if real == os.sep or real == os.path.dirname(real):
        return "is a filesystem root"
    if real == real_root:
        return "is the scan root itself"
    if real == real_home:
        return "is the home directory"
    if real_home.startswith(real + os.sep):
        return "contains the home directory"
    if not real.startswith(real_root.rstrip(os.sep) + os.sep):
        return "is outside the scan root"
    return None


def delete_targets(
    paths: list[str],
    root: str,
    progress: DeleteProgress | None = None,
    dry_run: bool = False,
    home: str | None = None,
    dry_run_delay: float = 0.0,
) -> DeleteOutcome:
    """Permanently delete `paths` (shutil.rmtree). With dry_run the filesystem is never touched."""
    out = DeleteOutcome()
    n = len(paths)
    for i, path in enumerate(paths, 1):
        if progress is not None:
            try:
                progress(i, n, path)
            except Exception:  # noqa: BLE001 - UI problems must not stop the delete
                pass
        if dry_run:
            if dry_run_delay:
                import time
                time.sleep(dry_run_delay)
            out.deleted.append(path)
            continue
        try:
            reason = refusal_reason(path, root, home)
            if reason is not None:
                out.failed.append((path, reason))
                continue
            shutil.rmtree(path)
            out.deleted.append(path)
        except Exception as e:  # noqa: BLE001 - PermissionError etc.; continue with the rest
            out.failed.append((path, f"{type(e).__name__}: {e.strerror if isinstance(e, OSError) and e.strerror else e}"))
    return out


def prune_tree(root: Node, deleted: set[str]) -> tuple[int, int]:
    """Remove deleted target nodes from the in-memory tree.

    Subtracts sizes / target counts up the ancestor chain and drops DIR nodes
    that no longer contain any target. Returns (bytes_removed, targets_removed).
    """

    def prune(node: Node) -> tuple[int, int]:
        rs = rc = 0
        keep: list[Node] = []
        for c in node.children:
            if c.path in deleted and c.kind in TARGET_KINDS:
                rs += c.size
                rc += c.target_count or 1
                continue
            if c.kind is Kind.DIR:
                s, k = prune(c)
                rs += s
                rc += k
                if c.target_count <= 0:
                    continue
            keep.append(c)
        node.children = keep
        node.size -= rs
        node.target_count -= rc
        return rs, rc

    return prune(root)
