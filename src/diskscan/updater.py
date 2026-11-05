"""Version info, self-update and a lightweight "update available" check."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version as _pkg_version

PACKAGE = "diskscan-tui"  # name on PyPI (the command is still `diskscan`)
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
INSTALL_URL = "https://raw.githubusercontent.com/guillemgonzalez/diskscan/main/install.sh"
CHECK_INTERVAL = 24 * 3600  # seconds between PyPI checks
CACHE_FILE = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "diskscan", "update.json"
)


def current_version() -> str:
    try:
        return _pkg_version(PACKAGE)
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _parse(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in v.split("+")[0].split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        if not num:
            break
        parts.append(int(num))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _parse(latest) > _parse(current)


def _fetch_latest(timeout: float = 2.0) -> str | None:
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": f"diskscan/{current_version()}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["info"]["version"]


def check_for_update(force: bool = False) -> str | None:
    """Return the newer version string if one exists on PyPI, else None.

    Hits the network at most once per CHECK_INTERVAL (cached in CACHE_FILE).
    Never raises; disabled with DISKSCAN_NO_UPDATE_CHECK=1.
    """
    if os.environ.get("DISKSCAN_NO_UPDATE_CHECK") and not force:
        return None
    latest: str | None = None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        if not force and time.time() - cache.get("checked", 0) < CHECK_INTERVAL:
            latest = cache.get("latest")
        else:
            raise ValueError("stale")
    except Exception:
        try:
            latest = _fetch_latest()
        except Exception:
            latest = None
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                json.dump({"checked": time.time(), "latest": latest}, f)
        except OSError:
            pass
    if latest and is_newer(latest, current_version()):
        return latest
    return None


def _installed_with_uv_tool() -> bool:
    uv = shutil.which("uv")
    if not uv:
        return False
    try:
        tool_dir = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return False
    return bool(tool_dir) and os.path.realpath(sys.prefix).startswith(os.path.realpath(tool_dir) + os.sep)


def run_update() -> int:
    """Upgrade diskscan in place. Returns a process exit code."""
    before = current_version()
    print(f"diskscan {before} — checking for updates…", flush=True)
    if _installed_with_uv_tool():
        cmd = [shutil.which("uv") or "uv", "tool", "upgrade", PACKAGE]
    elif shutil.which("pipx") and "pipx" in sys.prefix:
        cmd = ["pipx", "upgrade", PACKAGE]
    else:
        print(
            "diskscan wasn't installed with the installer (uv tool), so it can't update itself.\n"
            "Reinstall with:\n"
            f"  curl -fsSL {INSTALL_URL} | sh\n"
            f"or: uv tool install --upgrade {PACKAGE}"
        )
        return 1
    print("$ " + " ".join(cmd), flush=True)
    rc = subprocess.call(cmd)
    if rc == 0:
        try:
            os.remove(CACHE_FILE)  # forget any stale "update available" notice
        except OSError:
            pass
    return rc
