"""Version info, self-update and a lightweight "update available" check."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import re
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version as _pkg_version

PACKAGE = "diskscan-tui"  # name on PyPI (the command is still `diskscan`)
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
REPO = "https://github.com/guillemgonzalez/diskscan"
# Used when the package isn't on PyPI yet: the version on GitHub's main branch.
GITHUB_PYPROJECT_URL = "https://raw.githubusercontent.com/guillemgonzalez/diskscan/main/pyproject.toml"
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


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": f"diskscan/{current_version()}"})
    return urllib.request.urlopen(req, timeout=timeout)


def on_pypi(timeout: float = 5.0) -> bool:
    try:
        with _get(PYPI_URL, timeout):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def _fetch_latest(timeout: float = 2.0) -> str | None:
    """Latest released version: PyPI if published there, else GitHub main's pyproject."""
    try:
        with _get(PYPI_URL, timeout) as resp:
            return json.load(resp)["info"]["version"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    with _get(GITHUB_PYPROJECT_URL, timeout) as resp:
        m = re.search(r'^version\s*=\s*"([^"]+)"', resp.read().decode(), re.M)
        return m.group(1) if m else None


def check_for_update(force: bool = False) -> str | None:
    """Return the newer version string if one exists (PyPI, or GitHub before publishing), else None.

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


def find_uv() -> str | None:
    """uv on PATH, or in the places its installer puts it (PATH may not be set up yet)."""
    found = shutil.which("uv")
    if found:
        return found
    for d in (os.environ.get("XDG_BIN_HOME"), os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.cargo/bin")):
        if d and os.access(os.path.join(d, "uv"), os.X_OK):
            return os.path.join(d, "uv")
    return None


def _installed_with_uv_tool(uv: str | None) -> bool:
    if not uv:
        return False
    try:
        tool_dir = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return False
    return bool(tool_dir) and os.path.realpath(sys.prefix).startswith(os.path.realpath(tool_dir) + os.sep)


def _uv_update_cmd(uv: str) -> list[str]:
    """Same source choice as install.sh: PyPI if published, else the latest code from GitHub."""
    try:
        published = on_pypi()
    except Exception:
        published = True  # PyPI unreachable: let uv report the real error
    if published:
        # --force + explicit name also switches an earlier GitHub install over to PyPI
        return [uv, "tool", "install", "--force", "--upgrade", PACKAGE]
    source = f"git+{REPO}" if shutil.which("git") else f"{PACKAGE} @ {REPO}/archive/refs/heads/main.tar.gz"
    return [uv, "tool", "install", "--force", "--refresh-package", PACKAGE, "--reinstall-package", PACKAGE, source]


def run_update() -> int:
    """Upgrade diskscan in place. Returns a process exit code."""
    before = current_version()
    print(f"diskscan {before} — checking for updates…", flush=True)
    uv = find_uv()
    if _installed_with_uv_tool(uv):
        cmd = _uv_update_cmd(uv)
    elif shutil.which("pipx") and "pipx" in sys.prefix:
        cmd = ["pipx", "upgrade", PACKAGE]
    else:
        print(
            "diskscan wasn't installed with the installer (uv tool), so it can't update itself.\n"
            "Reinstall with:\n"
            f"  curl -fsSL {INSTALL_URL} | sh\n"
            f"or: uv tool install --force git+{REPO}"
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
