# Changelog

All notable changes to diskscan are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-05

First release.

### Added

- Scanner that finds `node_modules` folders and Python environments (venv, virtualenv, uv, poetry, pdm, tox/nox via `pyvenv.cfg`; conda via `conda-meta/`; a `bin/python` + `site-packages` fallback) under any folder, whatever they're named.
- Accurate on-disk sizes: block-based (`st_blocks × 512`), symlinks never followed, hard links counted once across the scan (uv cache, pnpm store).
- Drill-down terminal UI: per-folder totals, colored rows with size bars, `npm`/`py` badges, "smaller objects…" grouping, drill into a venv or `node_modules` to see its heaviest packages.
- npm vs py split bars, in the header and under every folder row.
- Filters (all / node_modules / venvs), sort by size or name, reveal in Finder, copy path, rescan.
- Multi-select with checkboxes and permanent deletion behind a typed `DELETE` confirmation, with per-path safety checks right before each delete.
- `--demo` mode with synthetic data; nothing is scanned or deleted.
- `curl | sh` installer (`install.sh`) built on `uv tool`.
- `diskscan update` self-update and a once-a-day "update available" notice (opt out with `DISKSCAN_NO_UPDATE_CHECK=1`).
- `diskscan --version`.

[Unreleased]: https://github.com/guillemgonzalez/diskscan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/guillemgonzalez/diskscan/releases/tag/v0.1.0
