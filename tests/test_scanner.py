import os
import subprocess
import threading

import pytest

from diskscan.models import Kind, Node, ScanProgress
from diskscan.scanner import children_of, detect_venv, format_size, scan

BLOCK = 4096


def write(path, nbytes=10_000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * nbytes)


def find(node: Node, name: str) -> Node | None:
    for c in node.children:
        if c.name == name:
            return c
        r = find(c, name)
        if r:
            return r
    return None


def walk(node):
    yield node
    for c in node.children:
        yield from walk(c)


def du_bytes(path) -> int:
    out = subprocess.run(["du", "-sk", path], capture_output=True, text=True, check=True)
    return int(out.stdout.split()[0]) * 1024


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(999) == "999 B"
    assert format_size(120_000) == "120 KB"
    assert format_size(1_800_000_000) == "1.8 GB"
    assert format_size(494_200_000_000) == "494.2 GB"
    assert format_size(999_960) == "1.0 MB"


def test_node_modules_nested_counted_once(tmp_path):
    nm = tmp_path / "proj" / "node_modules"
    write(str(nm / "a" / "index.js"))
    write(str(nm / "a" / "node_modules" / "b" / "index.js"))
    res = scan(str(tmp_path))
    targets = [n for n in walk(res) if n.kind is Kind.NODE_MODULES]
    assert len(targets) == 1
    assert targets[0].path == str(nm)
    assert res.target_count == 1
    assert res.size == targets[0].size
    assert targets[0].size == du_bytes(str(nm))


def test_venv_detection_variants(tmp_path):
    write(str(tmp_path / "a" / ".venv" / "pyvenv.cfg"), 10)
    (tmp_path / "a" / ".venv" / "pyvenv.cfg").write_text(
        "home = /usr/bin\nuv = 0.9.1\nversion_info = 3.13.0\n")
    (tmp_path / "b" / "env_x").mkdir(parents=True)
    (tmp_path / "b" / "env_x" / "pyvenv.cfg").write_text(
        "home = /usr/bin\nvirtualenv = 20.0\nversion_info = 3.11.4.final.0\n")
    (tmp_path / "c" / "myconda" / "conda-meta").mkdir(parents=True)
    write(str(tmp_path / "d" / "old" / "bin" / "python3"), 10)
    (tmp_path / "d" / "old" / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    write(str(tmp_path / "e" / "plain" / "readme.txt"), 10)  # no target

    res = scan(str(tmp_path))
    assert find(res, ".venv").kind is Kind.VENV
    assert find(res, ".venv").detail == "uv venv · py 3.13"
    assert find(res, "env_x").detail == "virtualenv · py 3.11"
    assert find(res, "myconda").detail == "conda env"
    assert find(res, "old").detail == "python env"
    assert find(res, "e") is None  # pruned
    assert find(res, "plain") is None
    assert res.target_count == 4
    assert detect_venv(str(tmp_path / "e" / "plain")) is None
    assert detect_venv(str(tmp_path / "a" / ".venv")) == "uv venv · py 3.13"


def test_pyvenv_plain_venv_and_no_descend(tmp_path):
    v = tmp_path / "p" / "venv"
    v.mkdir(parents=True)
    (v / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.1\n")
    write(str(v / "lib" / "node_modules" / "f.js"))  # must not be reported
    res = scan(str(tmp_path))
    assert [n.kind for n in walk(res) if n.kind is not Kind.DIR] == [Kind.VENV]
    assert find(res, "venv").detail == "venv · py 3.12"


def test_hardlinks_and_symlinks(tmp_path):
    nm = tmp_path / "node_modules"
    write(str(nm / "a" / "f.bin"), 100_000)
    os.link(nm / "a" / "f.bin", nm / "a" / "g.bin")
    big = tmp_path / "outside" / "big.bin"
    write(str(big), 5_000_000)
    os.symlink(big, nm / "link_to_big")
    os.symlink(nm, nm / "loop")  # loop back to itself
    os.symlink(tmp_path / "outside", nm / "dirlink")
    res = scan(str(tmp_path))
    t = find(res, "node_modules")
    # one copy of 100KB (+ blocks for dirs/symlinks), nowhere near 5 MB
    assert t.size < 400_000
    assert t.size >= 100_000
    assert abs(t.size - du_bytes(str(nm))) <= 1024  # du -k rounds up to KiB


def test_hardlink_shared_across_targets_counted_once(tmp_path):
    write(str(tmp_path / "p1" / "node_modules" / "f"), 200_000)
    (tmp_path / "p2" / "node_modules").mkdir(parents=True)
    os.link(tmp_path / "p1" / "node_modules" / "f", tmp_path / "p2" / "node_modules" / "f")
    res = scan(str(tmp_path))
    assert 200_000 <= res.size < 400_000


def test_size_matches_du(tmp_path):
    for i in range(20):
        write(str(tmp_path / "x" / "node_modules" / f"p{i}" / "lib" / "a.js"), 1000 * i + 1)
    res = scan(str(tmp_path))
    t = find(res, "node_modules")
    # du -k rounds a 512-byte block count up to whole KiB: allow < 1 KiB.
    assert abs(t.size - du_bytes(t.path)) <= 1024


def test_pruning_sorting_and_aggregation(tmp_path):
    write(str(tmp_path / "big" / "node_modules" / "f"), 500_000)
    write(str(tmp_path / "small" / "sub" / "node_modules" / "f"), 5_000)
    write(str(tmp_path / "empty" / "deep" / "file.txt"), 5_000)
    res = scan(str(tmp_path))
    assert [c.name for c in res.children] == ["big", "small"]
    assert res.size == sum(c.size for c in res.children)
    assert res.target_count == 2
    assert find(res, "small").target_count == 1
    assert res.children_loaded


def test_git_not_descended(tmp_path):
    write(str(tmp_path / "r" / ".git" / "node_modules" / "f"))
    assert scan(str(tmp_path)).target_count == 0


def test_progress_and_cancel(tmp_path):
    for i in range(5):
        write(str(tmp_path / f"p{i}" / "node_modules" / "f"))
    calls: list[ScanProgress] = []
    scan(str(tmp_path), progress=calls.append)
    assert calls[-1].done and calls[-1].targets_found == 5
    assert sum(c.done for c in calls) == 1
    ev = threading.Event()
    ev.set()
    res = scan(str(tmp_path), cancel=ev)
    assert res.target_count == 0


def test_unreadable_dir_sets_error(tmp_path):
    write(str(tmp_path / "node_modules" / "ok" / "f"))
    locked = tmp_path / "node_modules" / "locked"
    write(str(locked / "f"))
    os.chmod(locked, 0)
    try:
        res = scan(str(tmp_path))
        assert find(res, "node_modules").error
    finally:
        os.chmod(locked, 0o755)


def test_children_of_sorted_and_cached(tmp_path):
    nm = tmp_path / "node_modules"
    write(str(nm / "big" / "a.js"), 300_000)
    write(str(nm / "small" / "a.js"), 1_000)
    write(str(nm / "top.txt"), 50_000)
    os.symlink("big", nm / "lnk")
    res = scan(str(tmp_path))
    t = find(res, "node_modules")
    assert not t.children_loaded and t.children == []
    kids = children_of(t)
    assert t.children_loaded and kids is t.children
    sizes = [k.size for k in kids]
    assert sizes == sorted(sizes, reverse=True)
    by = {k.name: k for k in kids}
    assert by["big"].kind is Kind.INNER and by["top.txt"].kind is Kind.FILE
    assert by["lnk"].kind is Kind.FILE and by["lnk"].detail.startswith("→")
    assert by["big"].size >= 300_000
    # cached: new files are not picked up
    write(str(nm / "later.txt"), 1000)
    assert children_of(t) is kids and "later.txt" not in {k.name for k in kids}
    # drill one level deeper
    inner = children_of(by["big"])
    assert inner[0].name == "a.js"
