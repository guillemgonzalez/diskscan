"""OS integration: reveal a path in the file manager and copy text to the clipboard.

macOS uses Finder (`open -R`) and `pbcopy`. Linux uses the freedesktop FileManager1
D-Bus interface (Nautilus, Dolphin, Nemo, Thunar, ...) falling back to `xdg-open` on
the parent folder, and wl-copy / xclip / xsel for the clipboard. When no clipboard
tool exists (SSH, headless), callers can fall back to the terminal's OSC 52 clipboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from urllib.parse import quote

IS_MAC = sys.platform == "darwin"
FILE_MANAGER = "Finder" if IS_MAC else "file manager"

_QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def reveal(path: str) -> str:
    """Show `path` selected in the OS file manager. Returns a short status message.

    Raises OSError if nothing suitable is available.
    """
    if IS_MAC:
        subprocess.Popen(["open", "-R", path], **_QUIET)
        return "revealed in Finder"

    uri = "file://" + quote(os.path.abspath(path))
    if shutil.which("dbus-send") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        rc = subprocess.call(
            ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
             "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
             f"array:string:{uri}", "string:"],
            timeout=5, **_QUIET,
        )
        if rc == 0:
            return "revealed in file manager"
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", os.path.dirname(path) or path], **_QUIET)
        return "opened containing folder"
    raise OSError("no file manager found (install xdg-utils)")


def _clipboard_cmd() -> list[str] | None:
    if IS_MAC:
        return ["pbcopy"]
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    return None


def copy_to_clipboard(text: str) -> bool:
    """Copy with a native clipboard tool. Returns False if none is available."""
    cmd = _clipboard_cmd()
    if cmd is None:
        return False
    subprocess.run(cmd, input=text.encode(), check=True, timeout=3, **_QUIET)
    return True
