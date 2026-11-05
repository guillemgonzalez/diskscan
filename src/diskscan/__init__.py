"""diskscan: find virtualenvs and node_modules and see what they cost you."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv

    # Subcommands (kept out of argparse so a folder can still be passed as the only positional).
    if argv[:1] == ["update"]:
        from .updater import run_update

        sys.exit(run_update())

    from .updater import current_version

    parser = argparse.ArgumentParser(
        prog="diskscan",
        description="Find Python virtualenvs and node_modules folders and show their disk usage.",
        epilog="Other commands:\n  diskscan update    upgrade diskscan to the latest version",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", default=".", help="root folder to scan (default: cwd)")
    parser.add_argument("--demo", action="store_true", help="browse a synthetic tree (no scanning)")
    parser.add_argument("-V", "--version", action="version", version=f"diskscan {current_version()}")
    args = parser.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.path))
    if not args.demo and not os.path.isdir(root):
        parser.error(f"not a directory: {root}")

    from .app import DiskscanApp

    DiskscanApp(root, demo=args.demo).run()
    sys.exit(0)
