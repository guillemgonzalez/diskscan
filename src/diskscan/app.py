"""Textual TUI for diskscan."""

from __future__ import annotations

import os
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .deleter import TARGET_KINDS, DeleteOutcome, delete_targets, prune_tree
from .models import Kind, Node, ScanProgress

try:  # scanner.py is written separately; the demo works without it
    from .scanner import format_size  # type: ignore
except Exception:  # pragma: no cover
    def format_size(n: int) -> str:
        v = float(n)
        for unit in ("bytes", "KB", "MB", "GB", "TB"):
            if v < 1000 or unit == "TB":
                return f"{int(v)} bytes" if unit == "bytes" else f"{v:.1f} {unit}"
            v /= 1000
        return f"{n} bytes"

ACCENT = "#D97757"
DIM = "#777777"
PATH_COLOR = "#7DD3FC"  # header full-path highlight
SUBTLE = "#3a3a3a"
SEL_BG = "#2a2523"
GRADIENT = ["#A3E635", "#22D3EE", "#3B82F6", "#6366F1", "#8B5CF6", "#D946EF", "#EC4899"]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

FILTERS = ["all", "node_modules", "venv"]
FILTER_LABEL = {"all": "All", "node_modules": "node_modules only", "venv": "venvs only"}
SMALLER_KEY = "\0smaller"

NPM_COLOR = "#8CC84B"   # node_modules badge / split colour
PY_COLOR = "#5B9BD5"    # python env badge / split colour
HDR_BAR_W = 22


# --------------------------------------------------------------------------- helpers

def _lerp_color(t: float) -> str:
    t = min(max(t, 0.0), 1.0) * (len(GRADIENT) - 1)
    i = min(int(t), len(GRADIENT) - 2)
    f = t - i
    a, b = GRADIENT[i], GRADIENT[i + 1]
    c = [round(int(a[k:k + 2], 16) * (1 - f) + int(b[k:k + 2], 16) * f) for k in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(c)


def mid_trunc(s: str, w: int) -> str:
    if w <= 0:
        return ""
    if len(s) <= w:
        return s
    if w == 1:
        return "…"
    keep = w - 1
    a = (keep + 1) // 2
    return s[:a] + "…" + s[len(s) - (keep - a):]


def tilde(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def split_pcts(nm: int, py: int) -> tuple[int, int]:
    """Whole-number percentages of (nm, py) by bytes that always sum to 100
    (largest-remainder rounding). (0, 0) when there are no bytes at all."""
    total = nm + py
    if total <= 0:
        return 0, 0
    a = nm * 100 // total
    rem_a = nm * 100 % total
    b = py * 100 // total
    rem_b = py * 100 % total
    if a + b < 100:
        if rem_a >= rem_b:
            a += 1
        else:
            b += 1
    return a, b


def split_cells(nm: int, py: int, width: int) -> tuple[int, int]:
    """Cells of the (nm, py) segments in a bar of `width` cells; a non-zero side keeps >= 1 cell."""
    total = nm + py
    if total <= 0 or width <= 0:
        return 0, 0
    if not nm:
        return 0, width
    if not py:
        return width, 0
    a = min(max(round(nm / total * width), 1), width - 1)
    return a, width - a


def pct_label(pct: int, nbytes: int) -> str:
    return "<1%" if nbytes and pct == 0 else f"{pct}%"


def split_bar(nm: int, py: int, width: int, bg: str = "") -> Text:
    """Two-colour stacked bar; dim thin track when there is nothing to show."""
    a, b = split_cells(nm, py, width)
    t = Text(no_wrap=True)
    if a + b == 0:
        t.append("─" * width, style="#333333" + bg)
        return t
    t.append("━" * a, style=NPM_COLOR + bg)
    t.append("━" * b, style=PY_COLOR + bg)
    return t


@dataclass
class Row:
    key: str
    node: Node | None          # None for the "smaller objects" row
    size: int
    nm: int = 0
    venv: int = 0
    hidden: list["Row"] = field(default_factory=list)
    check: int | None = None   # None = not selectable, 0 = unchecked, 1 = checked, 2 = partial
    nm_bytes: int = 0          # on-disk bytes of node_modules beneath (filter applied)
    venv_bytes: int = 0        # on-disk bytes of venvs beneath (filter applied)

    @property
    def lines(self) -> int:
        """Rendered height: DIR rows with targets get a second (split) line."""
        n = self.node
        return 2 if (n is not None and n.kind is Kind.DIR and (self.nm or self.venv)) else 1


# --------------------------------------------------------------------------- demo data

def build_demo_tree() -> Node:
    rnd = random.Random(7)
    MB = 1_000_000

    def pkg_children(path: str, names: list[tuple[str, int]]) -> list[Node]:
        out = [Node(n, f"{path}/{n}", Kind.INNER, s, children_loaded=True) for n, s in names]
        out.append(Node("(files)", f"{path}/(files)", Kind.FILE, 40_000))
        return sorted(out, key=lambda x: -x.size)

    def target(parent: str, kind: Kind, name: str, size: int, detail: str = "",
               kids: list[tuple[str, int]] | None = None) -> Node:
        path = f"{parent}/{name}"
        n = Node(name, path, kind, size, detail=detail, target_count=1, children_loaded=True)
        if kids:
            n.children = pkg_children(path, kids)
        return n

    def dirnode(parent: str, name: str, kids: list[Node]) -> Node:
        path = f"{parent}/{name}"
        kids = sorted(kids, key=lambda x: -x.size)
        return Node(name, path, Kind.DIR, sum(k.size for k in kids), kids,
                    children_loaded=True, target_count=sum(k.target_count for k in kids))

    root_path = "/demo/Projects"
    web = f"{root_path}/web-app"
    web_nm = target(web, Kind.NODE_MODULES, "node_modules", 612 * MB, kids=[
        ("next", 142 * MB), ("@swc", 96 * MB), ("typescript", 63 * MB), ("webpack", 41 * MB),
        ("@babel", 38 * MB), ("lodash", 5 * MB), ("react-dom", 6 * MB), ("eslint", 14 * MB)])
    web_fe = dirnode(web, "frontend", [target(f"{web}/frontend", Kind.NODE_MODULES, "node_modules", 355 * MB)])
    web_dir = dirnode(root_path, "web-app", [web_nm, web_fe])

    ml = f"{root_path}/ml-lab"
    ml_venv = target(ml, Kind.VENV, ".venv", 4_100 * MB, "uv venv · py 3.13", kids=[
        ("torch", 1_800 * MB), ("nvidia", 1_200 * MB), ("numpy", 90 * MB), ("scipy", 110 * MB),
        ("pandas", 70 * MB), ("sklearn", 35 * MB)])
    ml_nb = dirnode(ml, "notebooks", [target(f"{ml}/notebooks", Kind.VENV, "env", 1_250 * MB, "conda env")])
    ml_dir = dirnode(root_path, "ml-lab", [ml_venv, ml_nb])

    api = f"{root_path}/api-server"
    api_dir = dirnode(root_path, "api-server", [
        target(api, Kind.VENV, "venv", 640 * MB, "venv · py 3.12"),
        target(api, Kind.NODE_MODULES, "node_modules", 210 * MB)])

    dash = f"{root_path}/dashboard"
    dash_dir = dirnode(root_path, "dashboard", [
        target(dash, Kind.NODE_MODULES, "node_modules", 1_450 * MB)])

    tools = f"{root_path}/tools"
    tools_dir = dirnode(root_path, "tools", [
        dirnode(tools, f"cli-{i}", [target(f"{tools}/cli-{i}", Kind.VENV, ".venv", 180 * MB - i * 12 * MB, "venv")])
        for i in range(5)])

    arch = f"{root_path}/archive"
    arch_kids = []
    for i in range(34):
        p = f"{arch}/old-{i:02d}"
        kind = Kind.NODE_MODULES if i % 3 else Kind.VENV
        arch_kids.append(dirnode(arch, f"old-{i:02d}", [
            target(p, kind, "node_modules" if kind is Kind.NODE_MODULES else ".venv",
                   rnd.randint(2, 38) * MB, "" if kind is Kind.NODE_MODULES else "venv")]))
    arch_dir = dirnode(root_path, "archive", arch_kids)

    sites = f"{root_path}/a-really-long-folder-name-for-the-marketing-site-redesign-2024-final-v2"
    long_dir = dirnode(root_path, "a-really-long-folder-name-for-the-marketing-site-redesign-2024-final-v2", [
        target(sites, Kind.NODE_MODULES, "node_modules", 520 * MB)])

    return dirnode("/demo", "Projects",
                   [web_dir, ml_dir, api_dir, dash_dir, tools_dir, arch_dir, long_dir])


def demo_scan(root: str, progress=None, cancel: threading.Event | None = None, delay: float = 1.6) -> Node:
    tree = build_demo_tree()
    steps = 24
    for i in range(steps):
        if cancel is not None and cancel.is_set():
            break
        if delay:
            time.sleep(delay / steps)
        if progress:
            progress(ScanProgress(i * 310, i * 3, i * 640_000_000 // 3,
                                  f"{tree.path}/archive/old-{i:02d}/node_modules/.bin"))
    if progress:
        progress(ScanProgress(7440, 49, tree.size, tree.path, done=True))
    return tree


# --------------------------------------------------------------------------- widgets

class RowList(Widget, can_focus=False):
    """Hand-rendered row list (1 line per row; folder rows get a 2nd split line)."""

    DEFAULT_CSS = "RowList { height: 1fr; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.rows: list[Row] = []
        self.cursor = 0
        self.top = 0
        self.total = 0
        self.empty_msg = ""

    def set_rows(self, rows: list[Row], cursor: int, total: int, empty_msg: str = "") -> None:
        self.rows, self.total, self.empty_msg = rows, total, empty_msg
        self.set_cursor(cursor)

    def set_cursor(self, i: int) -> None:
        self.cursor = min(max(i, 0), max(len(self.rows) - 1, 0))
        h = max(self.content_size.height, 1)
        if self.cursor < self.top:
            self.top = self.cursor
        else:
            # scroll down until the cursor row (all of its lines) fits
            while self.top < self.cursor and sum(r.lines for r in self.rows[self.top:self.cursor + 1]) > h:
                self.top += 1
        # don't leave blank space at the bottom when scrolled past the end
        while self.top > 0 and sum(r.lines for r in self.rows[self.top - 1:]) <= h:
            self.top -= 1
        self.top = max(0, min(self.top, max(len(self.rows) - 1, 0)))
        if sum(r.lines for r in self.rows) > h:
            self.border_subtitle = f"{self.cursor + 1}/{len(self.rows)}"
        else:
            self.border_subtitle = ""
        self.refresh()

    def page_rows(self) -> int:
        """How many rows fit on one page (by lines), at least 1."""
        h = max(self.content_size.height, 1)
        used = n = 0
        for r in self.rows[self.top:]:
            used += r.lines
            if used > h:
                break
            n += 1
        return max(n, 1)

    def render(self) -> Text:
        w, h = self.content_size.width, self.content_size.height
        if not self.rows:
            return Text("\n  " + self.empty_msg, style=DIM, no_wrap=True)
        n_color = sum(1 for r in self.rows if r.node is not None)
        lines: list[Text] = []
        for idx in range(self.top, len(self.rows)):
            if len(lines) >= h:
                break
            lines.extend(self.row_lines(self.rows[idx], idx, n_color, w, idx == self.cursor))
        return Text("\n").join(lines[:h])

    def row_lines(self, row: Row, idx: int, n_color: int, width: int, selected: bool) -> list[Text]:
        out = [self._line(row, idx, n_color, width, selected)]
        if row.lines == 2:
            out.append(self._split_line(row, width, selected))
        return out

    def _split_line(self, row: Row, width: int, selected: bool) -> Text:
        """Second line of a folder row: stacked npm/py bar under the size bar, percentages to its left.
        Below the bar breakpoint only the dim percentages are shown, right-aligned."""
        bg = f" on {SEL_BG}" if selected else ""
        size_w = 10
        bar_w = 12 if width >= 72 else 0
        a, b = split_pcts(row.nm_bytes, row.venv_bytes)
        parts: list[tuple[str, str]] = []
        if row.nm_bytes:
            parts.append((f"npm {pct_label(a, row.nm_bytes)}", NPM_COLOR if bar_w else DIM))
        if row.venv_bytes:
            parts.append((f"py {pct_label(b, row.venv_bytes)}", PY_COLOR if bar_w else DIM))
        if not parts:
            parts.append(("—", DIM))
        label = Text(no_wrap=True)
        for k, (txt, col) in enumerate(parts):
            if k:
                label.append(" · ", style=DIM)
            label.append(txt, style=col)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("▌" if selected else " ", style=ACCENT + bg)
        tail = (2 + bar_w + size_w + 1) if bar_w else 1
        room = max(width - 1 - tail, 0)
        label.truncate(room, overflow="ellipsis")
        t.append(" " * (room - label.cell_len), style=bg)
        label.stylize(bg.replace(" on ", "on ") if bg else "")
        t.append_text(label)
        if bar_w:
            t.append("  ", style=bg)
            t.append_text(split_bar(row.nm_bytes, row.venv_bytes, bar_w, bg))
            t.append(" " * (size_w + 1), style=bg)
        else:
            t.append(" ", style=bg)
        return t

    def _line(self, row: Row, idx: int, n_color: int, width: int, selected: bool) -> Text:
        node = row.node
        bg = f" on {SEL_BG}" if selected else ""
        size_txt = format_size(row.size)
        size_w = 10
        bar_w = 12 if width >= 72 else 0
        badge_w = 4
        chk_w = 4  # "[x] "
        fixed = 1 + 1 + chk_w + 1 + 1 + badge_w + 1 + (bar_w + 2 if bar_w else 0) + size_w + 1
        flex = max(width - fixed, 4)

        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("▌" if selected else " ", style=ACCENT + bg)
        t.append(" ", style=bg)
        if row.check is None:
            t.append(" " * chk_w, style=bg)
        else:
            glyph = ("[ ]", "[x]", "[-]")[row.check]
            t.append(glyph, style=(f"bold {ACCENT}" if row.check else "#555555") + bg)
            t.append(" ", style=bg)
        if node is None:
            dot = "#555555"
            t.append("●", style=dot + bg)
            t.append(" ", style=bg)
            t.append(" " * badge_w + " ", style=bg)
            label = Text(f"smaller objects… ({len(row.hidden)})", style=DIM + (" bold" if selected else ""))
        else:
            dot = _lerp_color(idx / max(n_color - 1, 1))
            t.append("●", style=dot + bg)
            t.append(" ", style=bg)
            if node.kind is Kind.NODE_MODULES:
                t.append("npm ", style=f"bold {NPM_COLOR}" + bg)
            elif node.kind is Kind.VENV:
                t.append("py  ", style=f"bold {PY_COLOR}" + bg)
            else:
                t.append(" " * badge_w, style=bg)
            t.append(" ", style=bg)
            name_style = ("bold " + ACCENT) if selected else "#e6e6e6"
            label = Text(node.name, style=name_style)
            if node.kind in (Kind.DIR, Kind.INNER):
                label.append("/", style=DIM)
            detail = self._detail(row)
            if detail:
                label.append("  " + detail, style="#6e6e6e")
        label.truncate(flex, overflow="ellipsis", pad=True)
        label.stylize(bg.replace(" on ", "on ") if bg else "")
        t.append_text(label)
        if bar_w:
            t.append("  ", style=bg)
            share = (row.size / self.total) if self.total else 0
            filled = round(share * bar_w)
            if row.size and not filled:
                filled = 1
            bar_color = "#555555" if node is None else dot
            t.append("━" * filled, style=bar_color + bg)
            t.append("─" * (bar_w - filled), style="#333333" + bg)
        t.append(size_txt.rjust(size_w), style=("#ffffff" if selected else "#bdbdbd") + bg)
        t.append(" ", style=bg)
        return t

    @staticmethod
    def _detail(row: Row) -> str:
        n = row.node
        assert n is not None
        if n.error:
            return "unreadable"
        if n.kind is Kind.DIR:
            parts = []
            if row.nm:
                parts.append(f"{row.nm} node_modules")
            if row.venv:
                parts.append(plural(row.venv, "venv"))
            return " · ".join(parts)
        if n.detail:
            return n.detail
        if n.kind is Kind.NODE_MODULES:
            return "node_modules"
        if n.kind is Kind.VENV:
            return "venv"
        return ""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,q,enter", "dismiss_help", "close")]
    DEFAULT_CSS = f"""
    HelpScreen {{ align: center middle; background: #000000 60%; }}
    #help-box {{ width: 62; height: auto; border: round {ACCENT}; background: #181818; padding: 1 2; }}
    """

    def compose(self) -> ComposeResult:
        rows = [
            ("↑ ↓  j k", "move"),
            ("⏎  →  l", "open folder / expand smaller objects"),
            ("⌫  ←  h", "go up one level"),
            ("space", "select / unselect, then move down"),
            ("a", "select all targets in this view"),
            ("A  u", "clear the selection"),
            ("d  delete", "delete selected (asks you to type DELETE)"),
            ("t", "cycle filter: all / node_modules / venvs"),
            ("s", "toggle sort: size / name"),
            ("o", "reveal in Finder"),
            ("c", "copy path to clipboard"),
            ("r", "rescan"),
            ("esc", "cancel a running scan"),
            ("?", "this help"),
            ("q  ctrl+c", "quit"),
        ]
        t = Text()
        t.append("diskscan\n\n", style=f"bold {ACCENT}")
        for k, d in rows:
            t.append(f"{k:<12}", style="bold #e6e6e6")
            t.append(d + "\n", style=DIM)
        t.append("\npress esc to close", style="#555555")
        yield Static(t, id="help-box")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


CONFIRM_WORD = "DELETE"


def _kind_badge(kind: Kind) -> tuple[str, str]:
    return ("npm", f"bold {NPM_COLOR}") if kind is Kind.NODE_MODULES else ("py ", f"bold {PY_COLOR}")


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Always-shown confirmation: the Delete button only works if the input is exactly DELETE."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("pageup", "scroll_list(-1)", show=False),
        Binding("pagedown", "scroll_list(1)", show=False),
    ]
    DEFAULT_CSS = f"""
    ConfirmDeleteScreen {{ align: center middle; background: #000000 70%; }}
    #del-box {{ width: 90%; max-width: 100; height: auto; max-height: 95%; border: round #E5484D;
                background: #181818; padding: 1 2; }}
    #del-title {{ height: auto; }}
    #del-list {{ height: auto; max-height: 12; margin: 1 0; border: round {SUBTLE}; }}
    #del-list-text {{ height: auto; }}
    #del-input {{ margin: 0 0 1 0; }}
    #del-buttons {{ height: 3; align-horizontal: right; }}
    #del-buttons Button {{ margin-left: 2; }}
    """

    def __init__(self, nodes: list[Node], demo: bool, root: str = "") -> None:
        super().__init__()
        self.nodes = nodes
        self.demo = demo
        self.root = root

    def compose(self) -> ComposeResult:
        total = sum(n.size for n in self.nodes)
        t = Text()
        if self.demo:
            t.append("(demo — nothing will be deleted)\n", style="bold #E5C07B")
        t.append("Delete forever?\n", style="bold #E5484D")
        t.append(f"This permanently deletes {plural(len(self.nodes), 'folder')} "
                 f"({format_size(total)}). This cannot be undone.", style="bold #f0f0f0")
        with Vertical(id="del-box"):
            yield Static(t, id="del-title")
            with VerticalScroll(id="del-list"):
                yield Static(self._list_text(), id="del-list-text")
            yield Input(placeholder=f"type {CONFIRM_WORD} to confirm", id="del-input")
            with Horizontal(id="del-buttons"):
                yield Button("Cancel", id="del-cancel")
                yield Button("Delete", id="del-ok", variant="error", disabled=True)

    def _list_text(self) -> Text:
        width = max(min(int(self.app.size.width * 0.9), 100) - 6 - 4 - 12 - 3, 16)
        out = Text(no_wrap=True, overflow="ellipsis")
        for i, n in enumerate(self.nodes):
            if i:
                out.append("\n")
            label, style = _kind_badge(n.kind)
            out.append(label + " ", style=style)
            path = n.path
            if self.root and path.startswith(self.root.rstrip(os.sep) + os.sep):
                path = path[len(self.root.rstrip(os.sep)) + 1:]
            else:
                path = tilde(path)
            out.append(mid_trunc(path, width).ljust(width), style="#e6e6e6")
            out.append(format_size(n.size).rjust(11), style="#bdbdbd")
        return out

    def on_mount(self) -> None:
        self.query_one("#del-input", Input).focus()

    def _ok(self) -> bool:
        return self.query_one("#del-input", Input).value == CONFIRM_WORD

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#del-ok", Button).disabled = event.value != CONFIRM_WORD

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._ok():
            self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "del-ok" and self._ok():
            self.dismiss(True)
        elif event.button.id == "del-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_scroll_list(self, d: int) -> None:
        lst = self.query_one("#del-list", VerticalScroll)
        lst.scroll_page_down() if d > 0 else lst.scroll_page_up()


class DeleteProgressScreen(ModalScreen[None]):
    DEFAULT_CSS = f"""
    DeleteProgressScreen {{ align: center middle; background: #000000 70%; }}
    #prog-box {{ width: 80%; max-width: 90; height: auto; border: round {ACCENT}; background: #181818; padding: 1 2; }}
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="prog-box")

    def update_progress(self, frame: int, i: int, n: int, path: str, demo: bool) -> None:
        t = Text()
        t.append(SPINNER[frame % len(SPINNER)] + " ", style=f"bold {ACCENT}")
        t.append(f"Deleting {i}/{n}", style="bold #f0f0f0")
        t.append(" · ", style=DIM)
        t.append(mid_trunc(tilde(path), 60) + "\n\n", style="#e6e6e6")
        t.append("(demo — simulated, nothing touched on disk)" if demo else "please wait — this cannot be interrupted",
                 style=DIM)
        self.query_one("#prog-box", Static).update(t)


class DeleteResultScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,enter,space,q", "close", "close")]
    DEFAULT_CSS = f"""
    DeleteResultScreen {{ align: center middle; background: #000000 70%; }}
    #res-box {{ width: 90%; max-width: 100; height: auto; max-height: 90%; border: round {ACCENT};
                background: #181818; padding: 1 2; }}
    #res-fail {{ height: auto; max-height: 12; }}
    """

    def __init__(self, freed: int, outcome: DeleteOutcome, demo: bool) -> None:
        super().__init__()
        self.freed, self.outcome, self.demo = freed, outcome, demo

    def compose(self) -> ComposeResult:
        o = self.outcome
        t = Text()
        t.append(f"Freed {format_size(self.freed)} · deleted {len(o.deleted)}", style=f"bold {ACCENT}")
        if self.demo:
            t.append("\n(demo — nothing was deleted on disk)", style="bold #E5C07B")
        if o.failed:
            t.append(f"\n{plural(len(o.failed), 'failure')} (left untouched or partly removed):", style="bold #E5484D")
        with Vertical(id="res-box"):
            yield Static(t)
            if o.failed:
                f = Text()
                for path, reason in o.failed:
                    f.append(tilde(path) + "\n", style="#e6e6e6")
                    f.append("   " + reason + "\n", style=DIM)
                with VerticalScroll(id="res-fail"):
                    yield Static(f)
            yield Static(Text("\npress esc to close", style="#555555"))

    def action_close(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- app

class DiskscanApp(App[None]):
    CSS = f"""
    Screen {{ background: #121212; color: #d4d4d4; }}
    #browse {{ height: 1fr; }}
    #hdr {{ height: auto; border: round {SUBTLE}; padding: 0 1; }}
    #hdr-top, #hdr-crumb-row {{ height: 1; }}
    #hdr-name {{ width: 1fr; text-style: bold; color: #f0f0f0; }}
    #hdr-size {{ width: auto; color: {ACCENT}; text-style: bold; }}
    #crumb {{ width: 1fr; color: {DIM}; }}
    #status {{ width: auto; color: {ACCENT}; }}
    #hdr-sum-row {{ height: 1; }}
    #hdr-summary {{ width: 1fr; height: 1; color: {DIM}; }}
    #hdr-split {{ height: 1; width: 1fr; }}
    #selinfo {{ width: auto; height: 1; color: {ACCENT}; text-style: bold; }}
    #list {{ height: 1fr; border: round {SUBTLE}; border-subtitle-color: {DIM}; }}
    #total {{ height: 1; padding: 0 1; color: #9a9a9a; }}
    #footer {{ height: 1; padding: 0 1; color: #6b6b6b; }}
    #scan {{ display: none; height: 1fr; align: center middle; }}
    #scan-box {{ width: 72; max-width: 100%; height: auto; border: round {ACCENT}; padding: 1 2; }}
    """

    BINDINGS = [
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home,g", "move(-100000)", show=False),
        Binding("end,G", "move(100000)", show=False),
        Binding("enter,right,l", "open", show=False),
        Binding("backspace,left,h", "back", show=False),
        Binding("space", "toggle_select", show=False),
        Binding("a", "select_all", show=False),
        Binding("A,u", "clear_selection", show=False),
        Binding("d,delete", "delete", show=False),
        Binding("t", "cycle_filter", show=False),
        Binding("s", "toggle_sort", show=False),
        Binding("o", "reveal", show=False),
        Binding("c", "copy_path", show=False),
        Binding("r", "rescan", show=False),
        Binding("escape", "cancel", show=False),
        Binding("question_mark", "help", show=False),
        Binding("q", "quit_app", show=False),
        Binding("ctrl+c", "quit_app", show=False, priority=True),
    ]

    def __init__(self, root: str, demo: bool = False, demo_delay: float = 1.6) -> None:
        super().__init__()
        self.root_path = root
        self.demo = demo
        self.demo_delay = demo_delay
        self.root_node: Node | None = None
        self.stack: list[Node] = []
        self.filter = "all"
        self.sort = "size"
        self.cursors: dict[str, str] = {}
        self.expanded: set[str] = set()
        self.state = "scanning"      # scanning | browse
        self.busy = False            # loading children of a target
        self.selected: dict[str, Node] = {}   # path -> target node (global selection)
        self._deleting = False
        self._del_state = (0, 0, "")
        self._del_screen: DeleteProgressScreen | None = None
        self.progress = ScanProgress()
        self.view_total = 0
        self._cancel = threading.Event()
        self._tally_memo: dict[tuple, tuple] = {}
        self._frame = 0
        self._flash_timer = None
        self._flash_text = ""
        self.title = "diskscan"

    # ---- layout
    def compose(self) -> ComposeResult:
        with Vertical(id="browse"):
            with Vertical(id="hdr"):
                with Horizontal(id="hdr-top"):
                    yield Static("", id="hdr-name")
                    yield Static("", id="hdr-size")
                with Horizontal(id="hdr-crumb-row"):
                    yield Static("", id="crumb")
                    yield Static("", id="status")
                with Horizontal(id="hdr-sum-row"):
                    yield Static("", id="hdr-summary")
                    yield Static("", id="selinfo")
                yield Static("", id="hdr-split")
            yield RowList(id="list")
            yield Static("", id="total")
        with Vertical(id="scan"):
            yield Static("", id="scan-box")
        yield Static("", id="footer")

    def on_mount(self) -> None:
        self.query_one("#list").border_title = Text("diskscan", style=f"bold {ACCENT}")
        self.query_one("#browse").display = False
        self.query_one("#scan").display = True
        self.set_interval(0.09, self._tick)
        self._update_footer()
        self.start_scan()
        if not self.demo:
            threading.Thread(target=self._check_update, daemon=True).start()

    def _check_update(self) -> None:
        from .updater import check_for_update, current_version

        latest = check_for_update()
        if latest:
            self.call_from_thread(
                self.notify,
                f"diskscan {latest} is available (you have {current_version()}).\n"
                "Run `diskscan update` to upgrade.",
                title="Update available",
                timeout=8,
            )

    def on_resize(self, event: events.Resize) -> None:
        self._update_footer()
        if self.state == "browse":
            self.call_after_refresh(self.refresh_view)

    # ---- scanning
    def start_scan(self) -> None:
        self._cancel = threading.Event()
        self.progress = ScanProgress()
        self.state = "scanning"
        self.query_one("#browse").display = False
        self.query_one("#scan").display = True
        self._render_scan()
        self._update_footer()
        self.run_worker(self._scan_thread, thread=True, exclusive=True, group="scan", name="scan")

    def _scan_thread(self) -> None:
        cancel = self._cancel

        def cb(p: ScanProgress) -> None:
            try:
                self.call_from_thread(self._on_progress, p)
            except Exception:
                pass

        node, err = None, None
        try:
            if self.demo:
                node = demo_scan(self.root_path, cb, cancel, self.demo_delay)
            else:
                from .scanner import scan
                node = scan(self.root_path, progress=cb, cancel=cancel)
        except Exception as e:  # noqa: BLE001
            err = e
        try:
            self.call_from_thread(self._scan_done, node, cancel.is_set(), err)
        except Exception:
            pass

    def _on_progress(self, p: ScanProgress) -> None:
        self.progress = p

    def _scan_done(self, node: Node | None, cancelled: bool, err: Exception | None) -> None:
        if err is not None:
            self._show_scan_error(err)
            return
        if cancelled and (node is None or not node.children):
            if self.root_node is None:
                self.exit()
                return
            node = self.root_node  # keep the old results
        if node is None:
            self.exit()
            return
        self.root_node = node
        self._tally_memo.clear()
        self.selected.clear()
        self.stack = [node]
        self.cursors.clear()
        self.expanded.clear()
        self.state = "browse"
        self.query_one("#scan").display = False
        self.query_one("#browse").display = True
        self._update_footer()
        self.call_after_refresh(self.refresh_view)
        if cancelled:
            self.flash("scan cancelled · showing partial results")

    def _show_scan_error(self, err: Exception) -> None:
        t = Text()
        t.append("Scan failed\n\n", style=f"bold {ACCENT}")
        t.append(f"{type(err).__name__}: {err}\n\n", style="#e6e6e6")
        t.append("press r to retry · q to quit", style=DIM)
        self.query_one("#scan-box", Static).update(t)
        self.state = "error"

    def _render_scan(self) -> None:
        p = self.progress
        box = self.query_one("#scan-box", Static)
        width = max(self.size.width - 4, 20) if self.size.width else 60
        inner = min(72, width) - 6
        t = Text()
        t.append(SPINNER[self._frame % len(SPINNER)] + " ", style=f"bold {ACCENT}")
        t.append("Scanning ", style="bold #f0f0f0")
        t.append(mid_trunc(tilde(self.root_path), max(inner - 12, 8)) + "\n\n", style="#e6e6e6")
        t.append(f"{p.dirs_scanned:,}", style="bold #e6e6e6")
        t.append(" dirs  ·  ", style=DIM)
        t.append(f"{p.targets_found:,}", style=f"bold {ACCENT}")
        t.append(" targets  ·  ", style=DIM)
        t.append(format_size(p.bytes_found), style=f"bold {ACCENT}")
        t.append("\n\n")
        t.append(mid_trunc(tilde(p.current_path) or " ", max(inner, 8)), style="#666666")
        box.update(t)

    def _tick(self) -> None:
        self._frame += 1
        try:
            if self.state == "scanning":
                self._render_scan()
            elif self._deleting and self._del_screen is not None:
                self._render_delete()
            elif self.busy:
                self._render_status()
        except NoMatches:
            pass  # widgets already torn down (app is exiting)

    # ---- data helpers
    def tally(self, node: Node, filt: str | None = None) -> tuple[int, int, int]:
        """(size, node_modules count, venv count) for targets under `node` given the filter."""
        filt = filt or self.filter
        k = (id(node), filt)
        hit = self._tally_memo.get(k)
        if hit is not None:
            return hit
        if node.kind is Kind.NODE_MODULES:
            r = (node.size, 1, 0) if filt in ("all", "node_modules") else (0, 0, 0)
        elif node.kind is Kind.VENV:
            r = (node.size, 0, 1) if filt in ("all", "venv") else (0, 0, 0)
        elif node.kind is Kind.DIR:
            s = a = b = 0
            for c in node.children:
                cs, ca, cb = self.tally(c, filt)
                s, a, b = s + cs, a + ca, b + cb
            r = (s, a, b)
        else:
            r = (node.size, 0, 0)
        self._tally_memo[k] = r
        return r

    def split(self, node: Node, filt: str | None = None) -> tuple[int, int]:
        """(node_modules bytes, venv bytes) of the targets under `node` given the filter.
        Memoised alongside tally(); the memo is cleared whenever the tree changes."""
        filt = filt or self.filter
        k = (id(node), filt, "split")
        hit = self._tally_memo.get(k)
        if hit is not None:
            return hit
        if node.kind is Kind.NODE_MODULES:
            r = (node.size, 0) if filt in ("all", "node_modules") else (0, 0)
        elif node.kind is Kind.VENV:
            r = (0, node.size) if filt in ("all", "venv") else (0, 0)
        elif node.kind is Kind.DIR:
            a = b = 0
            for c in node.children:
                ca, cb = self.split(c, filt)
                a, b = a + ca, b + cb
            r = (a, b)
        else:
            r = (0, 0)
        self._tally_memo[k] = r
        return r

    def _view_split(self, node: Node) -> tuple[int, int]:
        """Split for the header: the folder's own split, or 100% one kind inside a target."""
        if node.kind is Kind.DIR:
            return self.split(node)
        for anc in reversed(self.stack):
            if anc.kind is Kind.NODE_MODULES:
                return (node.size, 0)
            if anc.kind is Kind.VENV:
                return (0, node.size)
        return (0, 0)

    def _split_header(self, nm: int, py: int) -> Text:
        width = max(self.size.width - 4, 20)
        bar_w = min(HDR_BAR_W, max(width - 22, 6))
        t = Text(no_wrap=True)
        if nm + py == 0:
            t.append("no targets ", style=DIM)
            t.append("░" * bar_w, style="#333333")
        else:
            a, b = split_pcts(nm, py)
            if nm:
                t.append(f"npm {pct_label(a, nm)} ", style=f"bold {NPM_COLOR}")
            t.append_text(split_bar(nm, py, bar_w))
            if py:
                t.append(f" py {pct_label(b, py)}", style=f"bold {PY_COLOR}")
        # right-align by padding (the panel's inner width is the terminal width minus border+padding)
        pad = max(width - t.cell_len, 0)
        return Text(" " * pad, no_wrap=True) + t

    @property
    def current(self) -> Node:
        return self.stack[-1]

    def _list_height(self) -> int:
        h = self.query_one("#list").content_size.height
        return h if h > 0 else 20

    def build_rows(self, node: Node) -> tuple[list[Row], int]:
        items: list[Row] = []
        if node.kind is Kind.DIR:
            for c in node.children:
                s, a, b = self.tally(c)
                if self.filter != "all" and s == 0 and c.kind is Kind.DIR:
                    continue
                if self.filter != "all" and c.kind is not Kind.DIR and s == 0:
                    continue
                nb, vb = self.split(c)
                items.append(Row(c.path, c, s, a, b, nm_bytes=nb, venv_bytes=vb))
        else:
            for c in node.children:
                items.append(Row(c.path, c, c.size))
        if self.sort == "name":
            items.sort(key=lambda r: r.node.name.lower())  # type: ignore[union-attr]
        else:
            items.sort(key=lambda r: -r.size)
        total = sum(r.size for r in items)

        if self.sort != "size" or node.path in self.expanded or not items:
            return items, total
        cap = max(self._list_height(), 3)   # budget in screen lines, not rows
        thresh = total * 0.01
        lines = [r.lines for r in items]
        tail = len(items)
        for i, r in enumerate(items):
            if r.size < thresh:
                tail = i
                break

        def fit(t: int) -> int:
            # largest t' <= t whose rows plus the one-line "smaller objects" row fit
            while t > 0 and sum(lines[:t]) + 1 > cap:
                t -= 1
            return t

        if tail < len(items):
            tail = fit(tail)
        if len(items) - tail < 2:
            tail = len(items) if sum(lines) <= cap else fit(len(items))
        if tail >= len(items):
            return items, total
        rest = items[tail:]
        smaller = Row(SMALLER_KEY, None, sum(r.size for r in rest),
                      sum(r.nm for r in rest), sum(r.venv for r in rest), hidden=rest,
                      nm_bytes=sum(r.nm_bytes for r in rest), venv_bytes=sum(r.venv_bytes for r in rest))
        return items[:tail] + [smaller], total

    # ---- rendering
    def refresh_view(self) -> None:
        if self.state != "browse" or not self.stack:
            return
        lst = self.query_one("#list", RowList)
        node = self.current
        rows, total = self.build_rows(node)
        self._annotate(rows)
        want = self.cursors.get(node.path)
        idx = next((i for i, r in enumerate(rows) if r.key == want), 0)
        if want is None and not lst.rows:
            idx = 0
        empty = ("No matching targets at this level." if node.kind is Kind.DIR
                 else "Empty folder.")
        lst.set_rows(rows, idx, total, empty)
        if rows:
            self.cursors[node.path] = rows[lst.cursor].key
        self.view_total = total if node.kind is Kind.DIR else (node.size or total)

        # header
        name = node.name or node.path
        self.query_one("#hdr-name", Static).update(Text(name, no_wrap=True, overflow="ellipsis"))
        self.query_one("#hdr-size", Static).update(format_size(self.view_total))
        # full path of the current folder: parents bold blue, current folder bold accent
        full = tilde(node.path)
        w = max(self.size.width - 8 - 24, 20)
        shown = mid_trunc(full, w)
        head, sep, tail = shown.rpartition("/")
        crumb = Text(no_wrap=True)
        if sep:
            crumb.append(head + sep, style=f"bold {PATH_COLOR}")
        crumb.append(tail, style=f"bold {ACCENT}")
        self.query_one("#crumb", Static).update(crumb)
        if node.kind is Kind.DIR:
            _, a, b = self.tally(node)
            parts = []
            if a:
                parts.append(f"{a} node_modules")
            if b:
                parts.append(plural(b, "venv"))
            summary = " · ".join(parts) or "no matching targets"
        else:
            folders = sum(1 for c in node.children if c.kind is not Kind.FILE)
            files = sum(1 for c in node.children if c.kind is Kind.FILE)
            summary = f"{plural(folders, 'folder')} · {plural(files, 'file group')}"
        self.query_one("#hdr-summary", Static).update(summary)
        self.query_one("#hdr-split", Static).update(self._split_header(*self._view_split(node)))
        self._render_status()
        self._render_selinfo()

        # bottom line
        if self.root_node is not None:
            s, a, b = self.tally(self.root_node, "all")
            tot = Text()
            tot.append("Total reclaimable: ", style="#9a9a9a")
            tot.append(format_size(s), style=f"bold {ACCENT}")
            tot.append(f" across {plural(a + b, 'target')}", style="#9a9a9a")
            if self._flash_text:
                tot.append("   " + self._flash_text, style=DIM)
            self.query_one("#total", Static).update(tot)

    # ---- selection
    def _targets_under(self, node: Node) -> list[Node]:
        """Selectable targets at/below `node`, respecting the active filter."""
        out: list[Node] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n.kind is Kind.NODE_MODULES:
                if self.filter in ("all", "node_modules"):
                    out.append(n)
            elif n.kind is Kind.VENV:
                if self.filter in ("all", "venv"):
                    out.append(n)
            elif n.kind is Kind.DIR:
                stack.extend(n.children)
        return out

    def _row_targets(self, row: Row) -> list[Node]:
        if row.node is None:
            return [t for h in row.hidden for t in self._row_targets(h)]
        return self._targets_under(row.node)

    def _annotate(self, rows: list[Row]) -> None:
        for r in rows:
            if r.node is not None and r.node.kind not in (*TARGET_KINDS, Kind.DIR):
                r.check = None
                continue
            ts = self._row_targets(r)
            if not ts:
                r.check = None
                continue
            n = sum(1 for t in ts if t.path in self.selected)
            r.check = 0 if n == 0 else (1 if n == len(ts) else 2)

    def _render_selinfo(self) -> None:
        w = self.query_one("#selinfo", Static)
        if self.selected:
            size = sum(n.size for n in self.selected.values())
            w.update(Text(f"{len(self.selected)} selected · {format_size(size)}", no_wrap=True))
        else:
            w.update("")

    def _modal_open(self) -> bool:
        return isinstance(self.screen, ModalScreen)

    def action_toggle_select(self) -> None:
        if self.state != "browse" or self.busy or self._deleting or self._modal_open():
            return
        row = self._selected()
        if row is None:
            return
        ts = self._row_targets(row) if (row.node is None or row.node.kind in (*TARGET_KINDS, Kind.DIR)) else []
        if not ts:
            self.flash("only node_modules and venv folders can be selected")
            return
        if all(t.path in self.selected for t in ts):
            for t in ts:
                self.selected.pop(t.path, None)
        else:
            for t in ts:
                self.selected[t.path] = t
        self.action_move(1)
        self.refresh_view()

    def action_select_all(self) -> None:
        if self.state != "browse" or self.busy or self._deleting or self._modal_open():
            return
        if self.current.kind is not Kind.DIR:
            self.flash("nothing to select here; go up to the folder list")
            return
        ts = self._targets_under(self.current)
        for t in ts:
            self.selected[t.path] = t
        self.refresh_view()
        self.flash(f"selected {plural(len(ts), 'target')} in this view")

    def action_clear_selection(self) -> None:
        if self.state != "browse" or self._deleting or self._modal_open():
            return
        self.selected.clear()
        self.refresh_view()

    # ---- deletion
    def action_delete(self) -> None:
        if self.state != "browse" or self.busy or self._deleting or self._modal_open():
            return
        nodes = list(self.selected.values())
        if not nodes:
            row = self._selected()
            if row is not None and row.node is not None and row.node.kind in TARGET_KINDS:
                nodes = [row.node]
            else:
                self.flash("nothing selected; press space on a node_modules or venv first")
                return
        nodes.sort(key=lambda n: (-n.size, n.path))
        self.push_screen(ConfirmDeleteScreen(nodes, self.demo, self.root_path),
                         lambda ok: self._start_delete(nodes) if ok else None)

    def _start_delete(self, nodes: list[Node]) -> None:
        self._deleting = True
        self._del_state = (0, len(nodes), nodes[0].path)
        self._del_screen = DeleteProgressScreen()
        self.push_screen(self._del_screen)
        self.run_worker(lambda: self._delete_thread(nodes), thread=True, group="delete", name="delete")

    def _delete_thread(self, nodes: list[Node]) -> None:
        def cb(i: int, n: int, path: str) -> None:
            self.call_from_thread(self._delete_progress, i, n, path)

        try:
            out = delete_targets([n.path for n in nodes], self.root_path, cb,
                                 dry_run=self.demo, dry_run_delay=0.03 if self.demo else 0.0)
        except Exception as e:  # noqa: BLE001
            out = DeleteOutcome([], [(n.path, f"{type(e).__name__}: {e}") for n in nodes])
        try:
            self.call_from_thread(self._delete_done, nodes, out)
        except Exception:
            pass

    def _delete_progress(self, i: int, n: int, path: str) -> None:
        self._del_state = (i, n, path)
        self._render_delete()

    def _render_delete(self) -> None:
        if self._del_screen is not None and self._del_screen.is_mounted:
            i, n, path = self._del_state
            self._del_screen.update_progress(self._frame, i, n, path, self.demo)

    def _delete_done(self, nodes: list[Node], out: DeleteOutcome) -> None:
        self._deleting = False
        if self._del_screen is not None:
            self._del_screen.dismiss(None)
            self._del_screen = None
        deleted = set(out.deleted)
        freed = sum(n.size for n in nodes if n.path in deleted)
        if self.root_node is not None and deleted:
            prune_tree(self.root_node, deleted)
        for p in deleted:
            self.selected.pop(p, None)
            self.cursors.pop(p, None)
        self._tally_memo.clear()
        # drop stack levels that were removed from the tree
        keep = 1
        while keep < len(self.stack) and any(c is self.stack[keep] for c in self.stack[keep - 1].children):
            keep += 1
        del self.stack[keep:]
        self.refresh_view()
        msg = f"Freed {format_size(freed)} · deleted {len(out.deleted)}"
        if out.failed:
            msg += f" · {len(out.failed)} failed"
        self.flash(msg)
        self.push_screen(DeleteResultScreen(freed, out, self.demo))

    def _render_status(self) -> None:
        st = self.query_one("#status", Static)
        if self.busy:
            st.update(Text(SPINNER[self._frame % len(SPINNER)] + " reading…"))
            return
        bits = []
        if self.filter != "all":
            bits.append(FILTER_LABEL[self.filter])
        if self.sort == "name":
            bits.append("by name")
        st.update(Text(" · ".join(bits)))

    def _update_footer(self) -> None:
        wide = self.size.width >= 104
        if self.state == "scanning":
            txt = "esc cancel · q quit"
        elif wide:
            txt = ("↑↓ navigate · ⏎ open · ⌫ back · space select · d delete · t filter · s sort · "
                   "o reveal · c copy · r rescan · ? help · q quit")
        else:
            txt = "↑↓ ⏎ ⌫ · space select · d delete · t filter · o reveal · ? help · q quit"
        self.query_one("#footer", Static).update(Text(txt, no_wrap=True, overflow="ellipsis"))

    def flash(self, msg: str) -> None:
        self._flash_text = msg
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = self.set_timer(2.5, self._clear_flash)
        self.refresh_view()

    def _clear_flash(self) -> None:
        self._flash_text = ""
        self.refresh_view()

    # ---- actions
    def _selected(self) -> Row | None:
        if self.state != "browse":
            return None
        lst = self.query_one("#list", RowList)
        return lst.rows[lst.cursor] if lst.rows else None

    def action_move(self, delta: int) -> None:
        if self.state != "browse" or self.busy:
            return
        lst = self.query_one("#list", RowList)
        lst.set_cursor(lst.cursor + delta)
        if lst.rows:
            self.cursors[self.current.path] = lst.rows[lst.cursor].key

    def action_page(self, direction: int) -> None:
        lst = self.query_one("#list", RowList)
        self.action_move(direction * max(lst.page_rows() - 1, 1))

    def action_open(self) -> None:
        row = self._selected()
        if row is None or self.busy:
            return
        if row.node is None:  # expand smaller objects
            self.cursors[self.current.path] = row.hidden[0].key
            self.expanded.add(self.current.path)
            self.refresh_view()
            return
        node = row.node
        if node.kind is Kind.FILE:
            return
        if node.kind is Kind.DIR or node.children_loaded:
            self._push(node)
            return
        self.busy = True
        self._render_status()
        self.run_worker(lambda: self._load_children(node), thread=True, group="children", name="children")

    def _load_children(self, node: Node) -> None:
        err = None
        try:
            from .scanner import children_of
            children_of(node)
        except Exception as e:  # noqa: BLE001
            err = e
        try:
            self.call_from_thread(self._children_done, node, err)
        except Exception:
            pass

    def _children_done(self, node: Node, err: Exception | None) -> None:
        self.busy = False
        if err is not None:
            self.flash(f"could not read {node.name}: {err}")
            return
        self._push(node)

    def _push(self, node: Node) -> None:
        self.stack.append(node)
        self.refresh_view()

    def action_back(self) -> None:
        if self.state != "browse" or self.busy or len(self.stack) < 2:
            return
        self.expanded.discard(self.current.path)
        self.stack.pop()
        self.refresh_view()

    def action_cycle_filter(self) -> None:
        if self.state != "browse":
            return
        self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
        self.refresh_view()

    def action_toggle_sort(self) -> None:
        if self.state != "browse":
            return
        self.sort = "name" if self.sort == "size" else "size"
        self.refresh_view()

    def action_reveal(self) -> None:
        row = self._selected()
        if row is None:
            return
        path = row.node.path if row.node else self.current.path
        if self.demo:
            self.flash("demo: would reveal " + tilde(path))
            return
        try:
            subprocess.Popen(["open", "-R", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.flash("revealed in Finder")
        except OSError as e:
            self.flash(f"open failed: {e}")

    def action_copy_path(self) -> None:
        row = self._selected()
        if row is None:
            return
        path = row.node.path if row.node else self.current.path
        try:
            subprocess.run(["pbcopy"], input=path.encode(), check=True, timeout=3)
            self.flash("copied " + mid_trunc(tilde(path), 50))
        except (OSError, subprocess.SubprocessError) as e:
            self.flash(f"copy failed: {e}")

    def action_rescan(self) -> None:
        if self.state == "scanning":
            return
        self._cancel.set()
        self.start_scan()

    def action_cancel(self) -> None:
        if self.state == "scanning":
            self._cancel.set()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        if self._deleting:
            return
        self._cancel.set()
        self.exit()
