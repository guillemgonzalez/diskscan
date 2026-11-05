# How diskscan works

## What diskscan does

diskscan finds the two biggest kinds of regenerable clutter on a developer machine, `node_modules` folders and Python environments (virtualenvs), and shows how much disk space each one uses. You browse the results in a terminal UI, drill down into folders, and can select and permanently delete the ones you no longer need.

### Install and run

```
curl -fsSL https://raw.githubusercontent.com/guillemgonzalez/diskscan/main/install.sh | sh
diskscan [PATH]     # scan PATH (default: the current directory)
diskscan --demo     # browse a built-in synthetic tree; nothing is scanned or deleted
diskscan update     # upgrade in place
```

See the README for installer options, the daily update check, and running from a checkout (`uv run diskscan`).

`PATH` must be an existing directory (`~` is expanded). Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

## How detection works

The scanner walks the tree from the root. For each directory it decides whether the directory is itself a target. If it is, the scanner records it and does not descend into it; otherwise it continues into sub-directories.

### node_modules

A directory named exactly `node_modules` is a target of kind `node_modules`. Only the top-most one counts: the scanner never walks into it, so any nested `node_modules` (for example `node_modules/foo/node_modules`) is simply counted as part of the parent's size. If the scan root is itself called `node_modules`, it is treated as a target too.

### Python environments

A directory is a Python environment if the first of these rules that matches applies (checked in this order):

1. **`pyvenv.cfg` exists.** This file is written by `venv`, `virtualenv`, `uv venv`, and the tools built on them (Poetry, PDM, tox, nox, ...). The file is parsed as `key = value` lines. If it has a `uv` key the label starts with `uv venv`; otherwise if it has a `virtualenv` key, `virtualenv`; otherwise plain `venv`. Poetry, PDM, tox and nox environments are created with `venv`/`virtualenv`, so they show up under those labels.
2. **`conda-meta/` exists.** Labelled `conda env`.
3. **Fallback (no `pyvenv.cfg` or `conda-meta`).** The directory has `bin/` containing an entry starting with `python` (or `Scripts/python.exe` on Windows) **and** `lib/python*/site-packages` (or `Lib/site-packages`). Labelled `python env`.

The fallback is intentionally generic, so it can also match a full Python installation that is not a virtualenv, such as a pyenv version directory (`~/.pyenv/versions/3.12.x`) or a Homebrew/conda-style prefix. Check the path before deleting those.

### The "detail" label

For `pyvenv.cfg` environments the label is `<kind> · py <major.minor>`, for example `uv venv · py 3.13`. The version is read from the `version_info` key (or `version`) in `pyvenv.cfg` and cut to major.minor; if neither key is present, only the kind is shown. Conda and fallback environments show just `conda env` / `python env`. `node_modules` rows show `node_modules`; a folder that could not be fully read shows `unreadable`; a folder row summarises what is below it, for example `3 node_modules · 2 venvs`.

## What is skipped

Directories that are never descended into while looking for targets:

- Names: `.git`, `.hg`, `.svn`, `.Trash`, `.Trashes`, `.Spotlight-V100`, `.fseventsd`, `.TemporaryItems`, `.DocumentRevisions-V100`, `$RECYCLE.BIN`, `System Volume Information`.
- Absolute paths (Linux pseudo-filesystems, only relevant when scanning from `/`): `/proc`, `/sys`, `/dev`, `/run`. Matched by full path, so a project folder named `proc` is still scanned.
- Suffixes (macOS bundles): `.app`, `.photoslibrary`. An Electron app ships its own `node_modules` and a Photos library is not a project, so these are ignored.
- Symbolic links to directories are never followed during the walk.

Unreadable directories are skipped silently (if the scan root itself cannot be read, the error is recorded on it).

## How sizes are computed

- **On-disk size, not logical size.** Each file counts `st_blocks * 512` (the space actually allocated, like `du`), falling back to `st_size` where blocks are unavailable. Directories' own blocks are included.
- **Decimal units**, like Finder: 1 KB = 1000 bytes (`120 KB`, `1.8 GB`).
- **Symlinks are never followed.** A symlink counts as the link itself.
- **Hard links are counted once.** Non-directory files with more than one link are de-duplicated by `(device, inode)` across the whole scan. This matters because uv's cache and pnpm's store hard-link files into many environments; without de-duplication the same bytes would be counted for every venv, and the grand total would exceed what you can really free.
- **Attribution caveat.** A shared file is charged to whichever target's worker thread reaches it first, so the grand total is exact but the size shown for an individual target can vary between runs. Also note that deleting a venv whose files are hard-linked to the cache frees less than the displayed size if other environments still link the same files, and the displayed size may hide files that are charged to another target.
- **Drill-down uses a fresh seen-set.** When you open a target, its children are measured separately with a new de-duplication set (shared only among that folder's entries). This avoids showing a large package as about 0 bytes just because a different target was counted first. As a result, the children of a folder can add up to slightly more than the folder's size shown in the list.

## Performance model

1. The walker thread descends the tree looking for targets and never enters one.
2. Each target found is handed to a thread pool (12 workers) that sizes it iteratively.
3. While this runs, a progress screen shows directories scanned, targets found, bytes found so far, and the current path (updated about 10 times per second).
4. When everything is sized, the result is assembled into a pruned tree containing only the paths that lead to targets, sorted by size.
5. **Esc cancels** a running scan and shows the partial results (targets fully sized so far). If nothing was found yet, diskscan exits (or keeps the previous results on a rescan).

Opening a target lists its immediate entries on demand (8 worker threads) and caches them; a spinner shows `reading…` meanwhile.

## The UI

**Header.** Folder name and total size of the current view, breadcrumb path, active filter/sort, a summary (`27 node_modules · 20 venvs`), and, when something is selected, `N selected · size` in the accent colour. The line under the list shows `Total reclaimable` for the whole scan (ignoring filters).

**Split line (header).** A right-aligned line under the summary shows how the current view divides between the two kinds: `npm 38% ━━━━━━━━━━━━ py 62%` (thin node-green and python-blue segments, matching the size bars; a dim `─` track only when there is nothing to show). Inside a `node_modules` or venv (drilling into packages) the split is meaningless, so the line stays (the header never changes height) and shows 100% of that one kind. With a single-kind filter (`t`) it shows 100% of that kind.

**Split bars (rows).** Folder rows are two lines tall. The second line has a stacked two-colour bar of the same width as the size bar, directly under it, with `npm 62% · py 38%` to its left (a folder with one kind shows e.g. a full green bar and `npm 100%`). Target, package, file and "smaller objects…" rows stay one line. Below 72 columns (no size bar) the second line is just the dim `npm 62% · py 38%`, right-aligned. The highlight and the `▌` cursor marker cover both lines.

**How the percentages are computed.** They are by on-disk bytes (the same `st_blocks`-based sizes as everywhere else, not item counts) summed over the targets beneath the folder, and they respect the active filter. They are rounded with largest-remainder so the two numbers always add up to 100; a side that is non-zero but under 1% shows `<1%` and keeps at least one bar cell. Because the sizes are the scanner's, hard-linked files are charged once per target scan (see Size accounting): a file shared between a venv and a node_modules is attributed to whichever target counted it first, and the byte sums can therefore differ slightly from what each target would show on its own.

**Rows.** Each row shows: a checkbox, a coloured dot (a gradient from green to pink down the list), a badge (`npm` for node_modules, `py` for Python environments, blank for folders), the name (folders end with `/`) with a dim detail, a proportional bar (shown when the terminal is at least 72 columns wide), and the size. The row under the cursor is highlighted with `▌`.

**"smaller objects..." rule.** When sorted by size, rows smaller than 1% of the view total are folded into one grey `smaller objects… (N)` row (so long lists stay readable and the list is capped to the visible height, measured in screen lines because folder rows take two). Press `⏎` on it to expand. It is not applied when sorting by name.

**Drill-down.** `⏎`/`→` opens a folder. Folders lead down to targets; opening a target lists its biggest packages/folders and file groups. The cursor position is remembered per level when you go back with `⌫`/`←`.

**Filters and sort.** `t` cycles all / node_modules only / venvs only; the totals, bars and selection all respect the filter. `s` toggles size / name ordering.

### Key bindings

| key | action |
| --- | --- |
| `↑` `↓` / `j` `k` | move |
| `PgUp` `PgDn`, `Home`/`g`, `End`/`G` | page / jump |
| `⏎` `→` / `l` | open folder (or expand "smaller objects…") |
| `⌫` `←` / `h` | go up one level |
| `space` | select / unselect the row, then move down |
| `a` | select all targets in this view (respects the filter) |
| `A` / `u` | clear the selection |
| `d` / `delete` | delete the selection (asks for confirmation) |
| `t` | cycle filter: all / node_modules / venvs |
| `s` | toggle sort: size / name |
| `o` | reveal in Finder (macOS) / file manager (Linux) |
| `c` | copy path to clipboard |
| `r` | rescan |
| `esc` | cancel a running scan |
| `?` | help |
| `q` / `ctrl+c` | quit |

## Selecting and deleting

### Selecting

Checkboxes: `[ ]` unselected, `[x]` selected, `[-]` some (not all) of the targets beneath a folder are selected. Only `node_modules` and Python environment rows are deletable units.

- `space` on a target toggles it. On a folder it toggles every target beneath it (respecting the current filter): if all are already selected it unselects them, otherwise it selects all. On the "smaller objects" row it applies to everything folded into it.
- Folders and files *inside* a target have no checkbox and cannot be selected; pressing `space` there shows a short message.
- The selection is global (stored by path) and survives navigation, filter and sort changes. It is cleared by a rescan.

### Deleting

Press `d` (or `delete`). If nothing is selected but the highlighted row is a target, that single target is used; otherwise a message tells you to select something first.

A confirmation dialog is **always** shown, every time, with no "don't ask again":

- it lists every path (relative to the scan root) with a `npm`/`py` badge and size, scrollable, plus the warning "This permanently deletes N folders (X). This cannot be undone.";
- you must type `DELETE` exactly (upper case, no spaces). The Delete button stays disabled and `⏎` does nothing for anything else;
- `esc` or Cancel aborts and nothing is touched.

Deletion then runs in a background thread with a progress dialog (`Deleting 3/12 · path`). It uses `shutil.rmtree`, i.e. permanent deletion, not the Trash. Quitting is blocked while it runs.

**Safety checks.** Immediately before removing each path, diskscan verifies that:

1. it is an absolute path and is **not a symlink**;
2. it still exists and is a directory;
3. it is still a target: its name is `node_modules`, or it is still detected as a Python environment (so a folder that changed since the scan is refused);
4. its real path (symlinks resolved) is inside the scan root's real path, and is not the scan root itself, not `/`, not your home directory, and does not contain your home directory.

A path that fails a check is skipped and reported with the reason. Errors on one path (for example `PermissionError`) are collected and the remaining paths are still processed.

**After deleting.** The in-memory tree is updated without rescanning: deleted targets are removed, their sizes and target counts are subtracted from every ancestor, folders left with no targets disappear, the selection and header/`Total reclaimable` are updated. A result dialog shows `Freed X · deleted N` plus any failures (path and reason). If a failure happened part-way through a folder, the folder stays in the list with its old size; rescan with `r` to see the real state.

### Demo mode

`diskscan --demo` browses a synthetic tree under `/demo/Projects`. Deleting works end to end (confirmation, progress, tree update), but it is simulated: the filesystem is never touched, the dialogs are labelled "(demo — nothing will be deleted)", and no safety checks against disk are made. Reveal and copy are also simulated/harmless. A rescan restores the demo tree.

## Limitations and FAQ

- **Deletion is permanent.** Files are not moved to the Trash and cannot be recovered.
- **How do I get things back?** Re-create environments from your project files: `npm install` (or `pnpm install`/`yarn`) for node_modules, `uv sync` or `pip install -r requirements.txt` (after recreating the venv) for Python.
- **Freed space may be less than shown** when files are hard-linked to something else that remains (uv cache, pnpm store).
- **Fallback matches can be real Python installs** (pyenv versions, etc.). Read the path before deleting.
- **External drives and protected folders** may need Full Disk Access for your terminal app (System Settings > Privacy & Security > Full Disk Access); unreadable folders are skipped or flagged `unreadable`.
- **Sizes are a snapshot.** Nothing is watched; press `r` to rescan.
- **Reveal / copy** are OS-specific. macOS: `open -R` (Finder) and `pbcopy`. Linux: the freedesktop `FileManager1.ShowItems` D-Bus call, falling back to `xdg-open` on the parent folder; clipboard via `wl-copy`, `xclip` or `xsel`, falling back to the terminal's OSC 52 clipboard when none is available (e.g. over SSH).
