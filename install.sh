#!/bin/sh
# diskscan installer
#
#   curl -fsSL https://raw.githubusercontent.com/guillemgonzalez/diskscan/main/install.sh | sh
#
# Installs `uv` (if missing) and then diskscan as an isolated uv tool.
# uv downloads a suitable Python by itself, so no Python is needed beforehand.
#
# Environment overrides:
#   DISKSCAN_SOURCE   what to install. Default: diskscan-tui from PyPI if it is
#                     published there, otherwise the latest code from GitHub.
#                     Can be a git URL ("git+https://github.com/guillemgonzalez/diskscan"),
#                     a tag ("...diskscan@v0.1.0") or a local path to a checkout.
#   DISKSCAN_NO_MODIFY_PATH=1   don't touch shell profiles.
set -eu

PYPI_NAME="diskscan-tui"
REPO="https://github.com/guillemgonzalez/diskscan"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '\033[38;2;217;119;87m›\033[0m %s\n' "$*"; }
err()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) err "unsupported OS: $(uname -s) (macOS and Linux only)";;
esac

# 1. uv ----------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
  info "using uv at $UV"
else
  info "installing uv (https://docs.astral.sh/uv)…"
  command -v curl >/dev/null 2>&1 || err "curl is required"
  if [ "${DISKSCAN_NO_MODIFY_PATH:-}" = "1" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  for d in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    if [ -n "$d" ] && [ -x "$d/uv" ]; then UV="$d/uv"; break; fi
  done
  [ -n "${UV:-}" ] || err "uv installed but not found; open a new terminal and re-run"
fi

# 2. diskscan ----------------------------------------------------------------
if [ -n "${DISKSCAN_SOURCE:-}" ]; then
  SOURCE="$DISKSCAN_SOURCE"
elif curl -fsS -o /dev/null "https://pypi.org/pypi/$PYPI_NAME/json" 2>/dev/null; then
  SOURCE="$PYPI_NAME"
elif command -v git >/dev/null 2>&1; then
  SOURCE="git+$REPO"           # not on PyPI yet: install from GitHub
else
  SOURCE="$PYPI_NAME @ $REPO/archive/refs/heads/main.tar.gz"   # no git: use the source archive
fi
info "installing diskscan from: $SOURCE"
"$UV" tool install --force --upgrade "$SOURCE"

# 3. PATH --------------------------------------------------------------------
BIN_DIR="$("$UV" tool dir --bin)"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    if [ "${DISKSCAN_NO_MODIFY_PATH:-}" != "1" ]; then
      "$UV" tool update-shell >/dev/null 2>&1 || true
    fi
    info "add $BIN_DIR to your PATH (restart your terminal if it was just added)"
    ;;
esac

echo
bold "✓ diskscan installed"
echo "  diskscan ~/Projects     scan a folder"
echo "  diskscan --demo         try it on sample data"
echo "  diskscan update         upgrade later"
