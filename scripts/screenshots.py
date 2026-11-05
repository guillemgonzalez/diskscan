"""Regenerate the README screenshots from demo data (no real files are scanned).

    uv run python scripts/screenshots.py
"""

from __future__ import annotations

import asyncio
import os

from diskscan.app import DiskscanApp

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "assets")
SIZE = (110, 30)


async def wait_browse(app, pilot) -> None:
    for _ in range(500):
        await pilot.pause(0.02)
        if app.state == "browse":
            await pilot.pause(0.2)
            return
    raise RuntimeError("demo scan did not finish")


def shot(app, name: str) -> None:
    app.save_screenshot(f"{name}.svg", OUT)
    print("wrote", os.path.normpath(os.path.join(OUT, f"{name}.svg")))


async def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    os.environ["DISKSCAN_NO_UPDATE_CHECK"] = "1"

    # 1. scanning in progress
    app = DiskscanApp("/demo/Projects", demo=True, demo_delay=6)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(2.5)
        shot(app, "scanning")

    app = DiskscanApp("/demo/Projects", demo=True, demo_delay=0)
    async with app.run_test(size=SIZE) as pilot:
        await wait_browse(app, pilot)
        # 2. overview
        shot(app, "overview")
        # 3. drill into ml-lab, then into its .venv to see heavy packages
        await pilot.press("enter")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.6)
        shot(app, "packages")
        await pilot.press("backspace", "backspace")
        await pilot.pause(0.3)
        # 4. select a few targets
        await pilot.press("space", "j", "space", "j", "space")
        await pilot.pause(0.3)
        shot(app, "selection")
        # 5. delete confirmation with DELETE typed
        await pilot.press("d")
        await pilot.pause(0.3)
        await pilot.press(*"DELETE")
        await pilot.pause(0.3)
        shot(app, "delete")


if __name__ == "__main__":
    asyncio.run(main())
