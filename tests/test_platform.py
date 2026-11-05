import subprocess

from diskscan import platform_utils as pu


def test_clipboard_cmd_linux_prefers_wayland(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(pu.shutil, "which", lambda c: f"/usr/bin/{c}")
    assert pu._clipboard_cmd() == ["wl-copy"]


def test_clipboard_cmd_linux_x11_fallbacks(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(pu.shutil, "which", lambda c: "/usr/bin/xsel" if c == "xsel" else None)
    assert pu._clipboard_cmd() == ["xsel", "--clipboard", "--input"]


def test_clipboard_headless_returns_false(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert pu.copy_to_clipboard("x") is False


def test_reveal_linux_dbus_then_xdg_open(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/x")
    monkeypatch.setattr(pu.shutil, "which", lambda c: f"/usr/bin/{c}")
    calls = []
    monkeypatch.setattr(pu.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 1)  # dbus fails
    monkeypatch.setattr(pu.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert pu.reveal("/home/u/p/node_modules") == "opened containing folder"
    assert calls[0][0] == "dbus-send" and "file:///home/u/p/node_modules" in calls[0][-2]
    assert calls[1] == ["xdg-open", "/home/u/p"]


def test_reveal_linux_nothing_available(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", False)
    monkeypatch.setattr(pu.shutil, "which", lambda c: None)
    try:
        pu.reveal("/x")
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")


def test_reveal_mac(monkeypatch):
    monkeypatch.setattr(pu, "IS_MAC", True)
    calls = []
    monkeypatch.setattr(pu.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert pu.reveal("/a/b") == "revealed in Finder"
    assert calls == [["open", "-R", "/a/b"]]


def test_pseudo_fs_paths_are_skipped_by_absolute_path_only():
    from diskscan.scanner import SKIP_ABS_PATHS

    assert "/proc" in SKIP_ABS_PATHS and "proc" not in SKIP_ABS_PATHS
