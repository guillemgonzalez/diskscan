# diskscan

Find Python virtualenvs and `node_modules` folders under a path and see how much disk they use,
with drill-down navigation in a terminal UI.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/guillemgonzalez/diskscan/main/install.sh | sh
```

The installer sets up [uv](https://docs.astral.sh/uv/) if you don't have it, then installs diskscan as an isolated tool
(uv fetches its own Python, so nothing else is needed). macOS and Linux.

Already have uv or pipx? Install directly:

```sh
uv tool install diskscan-tui     # or: pipx install diskscan-tui
```

The PyPI package is `diskscan-tui`; the command is `diskscan`.

Installer options (environment variables):

| variable | effect |
| --- | --- |
| `DISKSCAN_SOURCE` | what to install instead of PyPI: a git URL (`git+https://github.com/guillemgonzalez/diskscan`) or a local checkout path |
| `DISKSCAN_NO_MODIFY_PATH=1` | don't edit shell profiles to add the tool bin dir to `PATH` |

## Usage

```sh
diskscan [PATH]     # scan a folder (default: current directory)
diskscan --demo     # browse a synthetic tree, nothing is scanned or deleted
diskscan --version
diskscan update     # upgrade to the latest version
```

## Updating

Run `diskscan update`. It upgrades in place with `uv tool upgrade` (or `pipx upgrade` if you installed with pipx).

diskscan also checks PyPI for a newer version at most once a day, in the background, when you open it. If one exists you'll see an
"Update available" notice. The check never blocks or fails the app (offline is fine). It is cached in `~/.cache/diskscan/update.json`.
Turn it off with `DISKSCAN_NO_UPDATE_CHECK=1`.

## Uninstall

```sh
uv tool uninstall diskscan-tui
```

## Development

```sh
uv sync
uv run diskscan ~/Projects
uv run pytest -q
DISKSCAN_SOURCE="$PWD" sh install.sh   # test the installer against this checkout
```

### Releasing (manual for now)

1. Bump `version` in `pyproject.toml`.
2. `uv build` then `uv publish` (needs a PyPI token, `UV_PUBLISH_TOKEN`).
3. Users get it with `diskscan update`.

## Keys

| key | action |
| --- | --- |
| `↑` `↓` / `j` `k` | move |
| `⏎` `→` / `l` | open folder (or expand "smaller objects…") |
| `⌫` `←` / `h` | go up |
| `space` | select / unselect row, move down |
| `a` | select all targets in view |
| `A` / `u` | clear selection |
| `d` / `delete` | delete selection (type `DELETE` to confirm) |
| `t` | filter: all / node_modules / venvs |
| `s` | sort: size / name |
| `o` | reveal in Finder |
| `c` | copy path |
| `r` | rescan |
| `esc` | cancel scan |
| `?` | help |
| `q` / `ctrl+c` | quit |

Folder rows show a second line with a stacked bar and `npm 62% · py 38%` (by on-disk bytes, respecting the `t` filter), and the header shows the split for the current view. Inside a `node_modules` or venv, drilling in shows the heaviest packages.

Selected targets can be deleted **permanently** (not moved to the Trash) after typing `DELETE` in a confirmation dialog; `--demo` only simulates this. Full details, including detection rules, size accounting and safety checks: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).
