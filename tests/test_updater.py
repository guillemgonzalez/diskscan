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
