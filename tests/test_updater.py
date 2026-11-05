import json
import time

from diskscan import updater


def test_is_newer():
    assert updater.is_newer("0.2.0", "0.1.0")
    assert updater.is_newer("0.10.0", "0.9.3")
    assert updater.is_newer("1.0.0", "0.9.9")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("0.1.0", "0.2.0")
    assert not updater.is_newer("0.1.0", "0.1.0+local")


def test_check_disabled_by_env(monkeypatch):
    monkeypatch.setenv("DISKSCAN_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(updater, "_fetch_latest", lambda: (_ for _ in ()).throw(AssertionError("network")))
    assert updater.check_for_update() is None


def test_check_uses_fresh_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("DISKSCAN_NO_UPDATE_CHECK", raising=False)
    cache = tmp_path / "update.json"
    cache.write_text(json.dumps({"checked": time.time(), "latest": "99.0.0"}))
    monkeypatch.setattr(updater, "CACHE_FILE", str(cache))
    monkeypatch.setattr(updater, "_fetch_latest", lambda: (_ for _ in ()).throw(AssertionError("network")))
    assert updater.check_for_update() == "99.0.0"


def test_check_refreshes_stale_cache_and_survives_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("DISKSCAN_NO_UPDATE_CHECK", raising=False)
    cache = tmp_path / "sub" / "update.json"
    monkeypatch.setattr(updater, "CACHE_FILE", str(cache))

    def boom():
        raise OSError("offline")

    monkeypatch.setattr(updater, "_fetch_latest", boom)
    assert updater.check_for_update() is None  # offline -> silent
    assert json.loads(cache.read_text())["latest"] is None

    cache.write_text(json.dumps({"checked": 0, "latest": None}))  # stale
    monkeypatch.setattr(updater, "_fetch_latest", lambda: "99.0.0")
    assert updater.check_for_update() == "99.0.0"


def test_main_version_and_update_dispatch(monkeypatch, capsys):
    import pytest

    import diskscan

    with pytest.raises(SystemExit) as e:
        diskscan.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("diskscan ")

    monkeypatch.setattr(updater, "run_update", lambda: 7)
    with pytest.raises(SystemExit) as e:
        diskscan.main(["update"])
    assert e.value.code == 7


def test_update_cmd_uses_pypi_when_published(monkeypatch):
    monkeypatch.setattr(updater, "on_pypi", lambda: True)
    assert updater._uv_update_cmd("uv") == ["uv", "tool", "install", "--force", "--upgrade", "diskscan-tui"]


def test_update_cmd_falls_back_to_github(monkeypatch):
    monkeypatch.setattr(updater, "on_pypi", lambda: False)
    monkeypatch.setattr(updater.shutil, "which", lambda c: "/usr/bin/git" if c == "git" else None)
    cmd = updater._uv_update_cmd("uv")
    assert cmd[-1] == "git+https://github.com/guillemgonzalez/diskscan"
    assert "--reinstall-package" in cmd and "--refresh-package" in cmd

    monkeypatch.setattr(updater.shutil, "which", lambda c: None)  # no git -> source archive
    assert updater._uv_update_cmd("uv")[-1].endswith("/archive/refs/heads/main.tar.gz")


def test_fetch_latest_falls_back_to_github_pyproject(monkeypatch):
    import io
    import urllib.error

    def fake_get(url, timeout):
        if url == updater.PYPI_URL:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return io.BytesIO(b'[project]\nname = "diskscan-tui"\nversion = "0.3.1"\n')

    monkeypatch.setattr(updater, "_get", fake_get)
    assert updater._fetch_latest() == "0.3.1"


def test_find_uv_checks_default_install_dir(tmp_path, monkeypatch):
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    uv = bindir / "uv"
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    monkeypatch.setattr(updater.shutil, "which", lambda c: None)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert updater.find_uv() == str(uv)
