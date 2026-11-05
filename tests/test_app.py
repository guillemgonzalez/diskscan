import asyncio
import functools

from diskscan.app import DiskscanApp, RowList, SMALLER_KEY, split_pcts, split_cells
from diskscan.models import Kind

SIZE = (100, 30)


def sync(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        return asyncio.run(fn(*a, **k))
    return run


async def ready(app, pilot):
    for _ in range(200):
        await pilot.pause(0.02)
        if app.state == "browse" and app.query_one("#list", RowList).rows:
            await pilot.pause()
            return
    raise AssertionError("scan did not finish")


def names(app):
    return [r.node.name if r.node else "<smaller>" for r in app.query_one("#list", RowList).rows]


def lst(app):
    return app.query_one("#list", RowList)


def make():
    return DiskscanApp("/demo/Projects", demo=True, demo_delay=0)


@sync
async def test_drill_in_out_and_cursor_restore():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        top = names(app)
        assert top[0] == "ml-lab"            # biggest first
        await pilot.press("j", "j")
        assert lst(app).cursor == 2
        picked = lst(app).rows[2].node.name
        await pilot.press("enter")
        await pilot.pause()
        assert app.current.name == picked
        assert lst(app).cursor == 0
        await pilot.press("down")
        await pilot.press("backspace")
        await pilot.pause()
        assert app.current.name == "Projects"
        assert lst(app).cursor == 2          # restored
        # drill into a target (ml-lab -> .venv -> packages)
        await pilot.press("home", "right")
        await pilot.pause()
        assert app.current.name == "ml-lab"
        await pilot.press("enter")
        await pilot.pause()
        assert app.current.kind is Kind.VENV
        assert names(app)[0] == "torch"
        await pilot.press("h", "h")
        await pilot.pause()
        assert app.current.name == "Projects"


@sync
async def test_filter_changes_total():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        all_total = app.view_total
        await pilot.press("t")
        nm_total = app.view_total
        await pilot.press("t")
        venv_total = app.view_total
        assert nm_total < all_total and venv_total < all_total
        assert nm_total + venv_total == all_total
        assert "ml-lab" not in names(app) or app.filter == "venv"
        await pilot.press("t")
        assert app.view_total == all_total


@sync
async def test_smaller_objects_collapse_and_expand():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        assert "<smaller>" not in names(app)   # only 7 items at the top level
        await pilot.press(*["j"] * names(app).index("archive"))
        await pilot.press("enter")
        await pilot.pause()
        assert app.current.name == "archive"
        assert names(app)[-1] == "<smaller>"
        assert len(names(app)) < 34
        n_before = len(lst(app).rows)
        await pilot.press("end")
        assert lst(app).rows[lst(app).cursor].key == SMALLER_KEY
        await pilot.press("enter")
        await pilot.pause()
        assert "<smaller>" not in names(app)
        assert len(lst(app).rows) > n_before
        assert lst(app).rows[lst(app).cursor].node is not None


@sync
async def test_sort_toggle_and_help():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        await pilot.press("s")
        n = [x for x in names(app)]
        assert n == sorted(n, key=str.lower)
        await pilot.press("question_mark")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "HelpScreen"


@sync
async def test_narrow_80_columns_renders():
    app = make()
    async with app.run_test(size=(80, 24)) as pilot:
        await ready(app, pilot)
        w = lst(app).content_size.width
        for i, r in enumerate(lst(app).rows):
            ls = lst(app).row_lines(r, i, 5, w, i == 0)
            assert len(ls) == r.lines
            for line in ls:
                assert line.cell_len == w
    # also below the bar breakpoint
    app = make()
    async with app.run_test(size=(60, 24)) as pilot:
        await ready(app, pilot)
        w = lst(app).content_size.width
        for i, r in enumerate(lst(app).rows):
            for line in lst(app).row_lines(r, i, 5, w, False):
                assert line.cell_len == w


@sync
async def test_screenshot(tmp_path):
    app = make()
    async with app.run_test(size=(100, 30)) as pilot:
        await ready(app, pilot)
        await pilot.press("down")
        p = app.save_screenshot("shot.svg", str(tmp_path))
        assert (tmp_path / "shot.svg").exists()


def test_split_pcts_sum_to_100():
    assert split_pcts(0, 0) == (0, 0)
    assert split_pcts(5, 0) == (100, 0)
    assert split_pcts(0, 5) == (0, 100)
    for nm, py in [(1, 2), (1, 1), (1, 6), (333, 667), (1, 10**9), (7, 3), (123456, 654321)]:
        a, b = split_pcts(nm, py)
        assert a + b == 100
    assert split_pcts(1, 2) == (33, 67)
    assert split_cells(1, 10**9, 12) == (1, 11)   # tiny side keeps one cell
    assert split_cells(0, 5, 12) == (0, 12)


@sync
async def test_split_respects_filter():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        root = app.root_node
        nm, py = app.split(root)
        assert nm > 0 and py > 0 and nm + py == app.tally(root)[0]
        await pilot.press("t")   # node_modules only
        assert app.split(root) == (nm, 0)
        await pilot.press("t")   # venvs only
        assert app.split(root) == (0, py)
        # DIR rows under a single-kind filter are 100% that kind
        for r in lst(app).rows:
            assert r.nm_bytes == 0 and r.venv_bytes > 0


@sync
async def test_dir_rows_two_lines_targets_one():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        w = lst(app).content_size.width
        for i, r in enumerate(lst(app).rows):
            assert r.node.kind.value == "dir"
            ls = lst(app).row_lines(r, i, 5, w, False)
            assert len(ls) == 2
            assert "%" in ls[1].plain
        # api-server has both kinds
        r = next(r for r in lst(app).rows if r.node.name == "api-server")
        assert "npm" in lst(app).row_lines(r, 0, 5, w, False)[1].plain
        assert "py" in lst(app).row_lines(r, 0, 5, w, False)[1].plain
        await pilot.press(*["j"] * names(app).index("api-server"))
        await pilot.press("enter")
        await pilot.pause()
        for r in lst(app).rows:
            assert r.lines == 1
            assert len(lst(app).row_lines(r, 0, 5, w, False)) == 1


@sync
async def test_header_split_line():
    app = make()
    async with app.run_test(size=SIZE) as pilot:
        await ready(app, pilot)
        txt = app.query_one("#hdr-split").render().plain
        assert "npm" in txt and "py" in txt and "%" in txt
        await pilot.press("t")
        txt = app.query_one("#hdr-split").render().plain
        assert "npm 100%" in txt and "py" not in txt
        await pilot.press("t")
        assert "py 100%" in app.query_one("#hdr-split").render().plain
        await pilot.press("t")
        # inside a venv the line stays (stable header height)
        await pilot.press("home", "right", "enter")
        await pilot.pause()
        assert app.current.kind.value == "venv"
        assert "py 100%" in app.query_one("#hdr-split").render().plain
        assert app.query_one("#hdr-split").size.height == 1


@sync
async def test_cursor_visible_with_two_line_rows_short_terminal():
    app = make()
    async with app.run_test(size=(100, 14)) as pilot:
        await ready(app, pilot)
        await pilot.press("s")   # by name: no "smaller objects" collapse
        l = lst(app)
        h = l.content_size.height
        assert h < sum(r.lines for r in l.rows)
        for _ in range(len(l.rows) + 2):
            await pilot.press("j")
            assert l.top <= l.cursor
            assert sum(r.lines for r in l.rows[l.top:l.cursor + 1]) <= h
            assert len(l.render().plain.split("\n")) <= h
        for _ in range(len(l.rows) + 2):
            await pilot.press("k")
            assert l.top <= l.cursor
            assert sum(r.lines for r in l.rows[l.top:l.cursor + 1]) <= h
        await pilot.press("pagedown")
        assert sum(r.lines for r in l.rows[l.top:l.cursor + 1]) <= h
        await pilot.press("end", "pageup")
        assert l.top <= l.cursor
