import asyncio
import functools
import os

from textual.widgets import Input

from diskscan.app import ConfirmDeleteScreen, DiskscanApp, RowList
from diskscan.deleter import delete_targets, prune_tree, refusal_reason
from diskscan.models import Kind
from diskscan.scanner import scan

SIZE = (100, 30)


def sync(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        return asyncio.run(fn(*a, **k))
    return run


def lst(app):
    return app.query_one("#list", RowList)


def names(app):
    return [r.node.name if r.node else "<smaller>" for r in lst(app).rows]


async def ready(app, pilot):
    for _ in range(300):
        await pilot.pause(0.02)
        if app.state == "browse" and lst(app).rows:
            await pilot.pause()
            return
    raise AssertionError("scan did not finish")


async def settle(app, pilot, cond, tries=300):
    for _ in range(tries):
        await pilot.pause(0.02)
        if cond():
            return
    raise AssertionError("condition not reached")


def demo_app():
    return DiskscanApp("/demo/Projects", demo=True, demo_delay=0)


def write(path, n=20_000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * n)


def make_tree(tmp_path):
    """root/proj/{node_modules, .venv, src/main.py}, root/other/node_modules"""
    root = tmp_path / "root"
    write(str(root / "proj" / "node_modules" / "pkg" / "index.js"))
    write(str(root / "proj" / ".venv" / "pyvenv.cfg"), 50)
    write(str(root / "proj" / ".venv" / "lib" / "x.py"))
    write(str(root / "proj" / "src" / "main.py"), 100)
    write(str(root / "proj" / "README.md"), 100)
    write(str(root / "other" / "node_modules" / "a" / "b.js"))
    return str(root)


# ----------------------------------------------------------------- UI: selection

@sync
async def test_space_toggles_and_moves_cursor():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        # drill into api-server (two direct targets)
        await pilot.press(*["j"] * names(app).index("api-server"))
        await pilot.press("enter")
        await pilot.pause()
        assert lst(app).cursor == 0
        first = lst(app).rows[0].node
        await pilot.press("space")
        assert first.path in app.selected
        assert lst(app).cursor == 1
        assert lst(app).rows[0].check == 1
        await pilot.press("up", "space")
        assert first.path not in app.selected
        assert lst(app).rows[0].check == 0


@sync
async def test_dir_toggle_selects_all_beneath_with_partial_state():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        i = names(app).index("web-app")
        await pilot.press(*["j"] * i)
        await pilot.press("space")
        assert len(app.selected) == 2  # node_modules + frontend/node_modules
        assert lst(app).rows[i].check == 1
        await pilot.press("k")  # space moved the cursor down; back to web-app
        assert lst(app).cursor == i
        await pilot.press("enter")
        await pilot.pause()
        assert app.current.name == "web-app"
        await pilot.press("space")
        assert len(app.selected) == 1
        await pilot.press("backspace")
        await pilot.pause()
        row = next(r for r in lst(app).rows if r.node and r.node.name == "web-app")
        assert row.check == 2
        # toggling a partial DIR selects everything (cursor was restored on web-app)
        assert lst(app).cursor == i
        await pilot.press("space")
        assert len(app.selected) == 2


@sync
async def test_filter_respected_and_select_all_clear():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("t")  # node_modules only
        await pilot.press("a")
        assert app.selected and all(n.kind is Kind.NODE_MODULES for n in app.selected.values())
        n_nm = len(app.selected)
        await pilot.press("A")
        assert not app.selected
        await pilot.press("t", "a")  # venvs only
        assert all(n.kind is Kind.VENV for n in app.selected.values())
        assert n_nm + len(app.selected) == app.root_node.target_count
        await pilot.press("u")
        assert not app.selected


@sync
async def test_inner_rows_not_selectable():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("home", "right", "enter")  # ml-lab -> .venv -> packages
        await pilot.pause()
        assert app.current.kind is Kind.VENV
        assert all(r.check is None for r in lst(app).rows)
        await pilot.press("space")
        assert not app.selected
        assert lst(app).cursor == 0
        assert "only node_modules" in app._flash_text


@sync
async def test_selection_survives_navigation():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("space")
        assert len(app.selected) >= 1
        await pilot.press("enter")
        await pilot.press("backspace")
        await pilot.press("s")
        assert len(app.selected) >= 1


# ----------------------------------------------------------------- UI: modal

async def open_modal(app, pilot):
    await pilot.press("d")
    await pilot.pause()
    assert isinstance(app.screen, ConfirmDeleteScreen)
    return app.screen


@sync
async def test_modal_requires_exact_delete():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("space")
        before = len(app.selected)
        scr = await open_modal(app, pilot)
        inp = scr.query_one(Input)
        for bad in ("", "delete", "DELET", "DELETE ", "Delete"):
            inp.value = bad
            await pilot.pause()
            assert scr.query_one("#del-ok").disabled
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is scr, bad
        inp.value = ""
        await pilot.pause()
        await pilot.press("D", "E", "L", "E", "T", "E")
        await pilot.pause()
        assert not scr.query_one("#del-ok").disabled
        await pilot.press("enter")
        await settle(app, pilot, lambda: not app._deleting and not isinstance(app.screen, ConfirmDeleteScreen)
                     and app.screen.__class__.__name__ == "DeleteResultScreen")
        assert before >= 1
        await pilot.press("escape")


@sync
async def test_escape_cancels_nothing_deleted(tmp_path):
    root = make_tree(tmp_path)
    app = DiskscanApp(root)
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("a")
        scr = await open_modal(app, pilot)
        scr.query_one(Input).value = "DELETE"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmDeleteScreen)
        assert len(app.selected) == 3
    assert os.path.isdir(os.path.join(root, "proj", "node_modules"))
    assert os.path.isdir(os.path.join(root, "proj", ".venv"))
    assert os.path.isdir(os.path.join(root, "other", "node_modules"))


@sync
async def test_d_without_selection_on_dir_flashes():
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmDeleteScreen)
        assert "nothing selected" in app._flash_text


@sync
async def test_demo_label_and_screenshots():
    # optional: set DISKSCAN_SCREENSHOT_DIR to save SVG screenshots for visual review
    out = os.environ.get("DISKSCAN_SCREENSHOT_DIR", "")
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("space", "space", "j", "space")
        await pilot.pause()
        if out and os.path.isdir(out):
            app.save_screenshot("selected.svg", out)
        scr = await open_modal(app, pilot)
        assert "demo" in str(scr.query_one("#del-title").render())
        if out and os.path.isdir(out):
            app.save_screenshot("delete-modal.svg", out)


# ----------------------------------------------------------------- real deletion

@sync
async def test_real_deletion_updates_tree(tmp_path):
    root = make_tree(tmp_path)
    app = DiskscanApp(root)
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        total_before = app.root_node.size
        assert app.root_node.target_count == 3
        # select proj (node_modules + .venv); leave other
        await pilot.press(*["j"] * names(app).index("proj"))
        await pilot.press("space")
        assert len(app.selected) == 2
        removed = sum(n.size for n in app.selected.values())
        scr = await open_modal(app, pilot)
        await pilot.press("D", "E", "L", "E", "T", "E", "enter")
        await settle(app, pilot, lambda: app.screen.__class__.__name__ == "DeleteResultScreen")
        await pilot.press("escape")
        await pilot.pause()
        assert not app.selected
        assert app.root_node.size == total_before - removed
        assert app.root_node.target_count == 1
        assert names(app) == ["other"]
        assert "Freed" in app._flash_text
    assert not os.path.exists(os.path.join(root, "proj", "node_modules"))
    assert not os.path.exists(os.path.join(root, "proj", ".venv"))
    assert os.path.isfile(os.path.join(root, "proj", "src", "main.py"))
    assert os.path.isfile(os.path.join(root, "proj", "README.md"))
    assert os.path.isfile(os.path.join(root, "other", "node_modules", "a", "b.js"))


@sync
async def test_demo_mode_touches_nothing_and_updates_tree(tmp_path):
    app = demo_app()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        before = app.root_node.target_count
        await pilot.press("a")
        n = len(app.selected)
        assert n == before
        await open_modal(app, pilot)
        await pilot.press("D", "E", "L", "E", "T", "E", "enter")
        await settle(app, pilot, lambda: app.screen.__class__.__name__ == "DeleteResultScreen")
        await pilot.press("escape")
        await pilot.pause()
        assert app.root_node.target_count == 0 and app.root_node.size == 0
        assert not os.path.exists("/demo")


# ----------------------------------------------------------------- safety (no UI)

def test_symlink_to_node_modules_refused(tmp_path):
    root = make_tree(tmp_path)
    real = os.path.join(root, "other", "node_modules")
    link = os.path.join(root, "proj", "link", "node_modules")
    os.makedirs(os.path.dirname(link))
    os.symlink(real, link)
    assert "symlink" in refusal_reason(link, root)
    out = delete_targets([link], root)
    assert out.deleted == [] and len(out.failed) == 1
    assert os.path.isfile(os.path.join(real, "a", "b.js"))
    assert os.path.islink(link)


def test_no_longer_a_target_refused(tmp_path):
    root = make_tree(tmp_path)
    plain = os.path.join(root, "proj", "src")
    out = delete_targets([plain], root)
    assert out.deleted == [] and "looks like" in out.failed[0][1]
    assert os.path.isfile(os.path.join(plain, "main.py"))
    # a venv whose pyvenv.cfg vanished is no longer a target
    venv = os.path.join(root, "proj", ".venv")
    os.remove(os.path.join(venv, "pyvenv.cfg"))
    assert refusal_reason(venv, root) is not None
    # missing path
    assert "exists" in refusal_reason(os.path.join(root, "gone", "node_modules"), root)


def test_root_home_outside_refused(tmp_path):
    root = make_tree(tmp_path)
    outside = tmp_path / "elsewhere" / "node_modules"
    write(str(outside / "x.js"))
    assert "outside" in refusal_reason(str(outside), root)
    # scan root itself named node_modules
    nm_root = str(tmp_path / "node_modules")
    write(os.path.join(nm_root, "y.js"))
    assert "root" in refusal_reason(nm_root, nm_root)
    # home dir
    fake_home = str(tmp_path / "home" / "node_modules")
    write(os.path.join(fake_home, "z.js"))
    assert "home" in refusal_reason(fake_home, str(tmp_path), home=fake_home)
    assert refusal_reason("/", "/") is not None
    assert os.path.isfile(str(outside / "x.js"))


def test_errors_collected_and_rest_continue(tmp_path):
    root = make_tree(tmp_path)
    good = os.path.join(root, "other", "node_modules")
    bad = os.path.join(root, "proj", "src")  # refused
    out = delete_targets([bad, good], root)
    assert out.deleted == [good] and len(out.failed) == 1
    assert not os.path.exists(good) and os.path.isdir(bad)


def test_prune_tree_updates_ancestors(tmp_path):
    root = make_tree(tmp_path)
    tree = scan(root)
    nm = os.path.join(root, "other", "node_modules")
    sz = next(c for c in tree.children if c.name == "other").children[0].size
    total = tree.size
    prune_tree(tree, {nm})
    assert [c.name for c in tree.children] == ["proj"]
    assert tree.size == total - sz and tree.target_count == 2
