#!/usr/bin/env bash
# Ghost-ALICE OS Installer (Mac / Linux / WSL / Git Bash)
# Supports: Claude Code, Codex
# Usage:
#   bash install.sh              # Install to all detected platforms (~/.claude, ~/.codex)
#   bash install.sh --prompt-platform  # Select AI tool interactively before installing
#   bash install.sh task-router verification-before-completion  # Install selected core skills only
#   bash install.sh --platform claude       # Install all skills to Claude Code
#   bash install.sh --platform codex       # Install all skills to Codex
#   bash install.sh --platform codex --visibility dynamic
#   bash install.sh --update-source  # Safely stash local source edits and fast-forward this checkout
#   bash install.sh --uninstall  # Full uninstall for all detected Ghost-ALICE managed footprint
#   bash install.sh --platform codex --uninstall skill-evolution  # Remove selected core skills from one platform
#   bash install.sh --list       # List available skills
#   bash install.sh --addon-source ./ghost-alice-addons --list-addons
#   bash install.sh --status     # Check current install status
#
# This shell only finds (or installs) Python 3.11+ and runs the installer.
# _shared/installer/cli.py owns the options, the commands and the install order.

if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  printf '%s\n' "[ERROR] install.sh requires bash. Run it as: bash install.sh" >&2
  exit 1
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Messages for the Python runtime step only ─────────────
if [ -t 1 ]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi
info()  { printf "${CYAN}[INFO]${NC} %s\n" "$1"; }
ok()    { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

# ── Force UTF-8 locale ────────────────────────────────────
# If a UTF-8 locale is already set, leave it; otherwise apply an available one.
ensure_utf8_locale() {
  local current="${LC_ALL:-${LANG:-}}"
  case "$current" in
    *UTF-8*|*utf8*|*utf-8*) return 0 ;;
  esac
  local loc
  if command -v locale >/dev/null 2>&1; then
    for loc in C.UTF-8 en_US.UTF-8 ko_KR.UTF-8 en_GB.UTF-8; do
      if locale -a 2>/dev/null | grep -qix "$loc"; then
        export LC_ALL="$loc" LANG="$loc"
        return 0
      fi
    done
  fi
  # Last fallback. There is no effect on shell output even without a locale DB.
  export LC_ALL="C.UTF-8" LANG="C.UTF-8"
}

# ── Python 3.11+ runtime ──────────────────────────────────
_is_working_python() {
  local cmd="$1"
  case "$cmd" in
    */*) [ -x "$cmd" ] || return 1 ;;
    *) command -v "$cmd" >/dev/null 2>&1 || return 1 ;;
  esac
  "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || return 1
}

_python_version_key() {
  local cmd="$1"
  "$cmd" -c 'import sys; print(f"{sys.version_info.major:03d}.{sys.version_info.minor:03d}.{sys.version_info.micro:03d}")' 2>/dev/null || return 1
}

_find_python_runtime() {
  local cmd
  for cmd in python3 python; do
    if _is_working_python "$cmd"; then
      printf '%s\n' "$cmd"
      return 0
    fi
  done

  local search_dirs=()
  local path_dir candidate key best_key="" best_cmd=""
  local old_ifs="$IFS"
  IFS=:
  for path_dir in $PATH; do
    [ -n "$path_dir" ] || path_dir="."
    [ -d "$path_dir" ] || continue
    search_dirs+=("$path_dir")
  done
  IFS="$old_ifs"

  if [ "${GHOST_ALICE_TEST_SKIP_COMMON_PYTHON_PATHS:-0}" != "1" ]; then
    search_dirs+=("/opt/homebrew/bin" "/usr/local/bin" "/usr/bin" "/bin")
  fi

  for path_dir in "${search_dirs[@]}"; do
    [ -n "$path_dir" ] || continue
    [ -d "$path_dir" ] || continue
    for candidate in "$path_dir"/python3.[0-9]* "$path_dir"/python3 "$path_dir"/python; do
      [ -e "$candidate" ] || continue
      [ -x "$candidate" ] || continue
      if _is_working_python "$candidate"; then
        key="$(_python_version_key "$candidate" || true)"
        if [ -n "$key" ] && { [ -z "$best_key" ] || [ "$key" \> "$best_key" ]; }; then
          best_key="$key"
          best_cmd="$candidate"
        fi
      fi
    done
  done

  if [ -n "$best_cmd" ]; then
    printf '%s\n' "$best_cmd"
    return 0
  fi

  return 1
}

_run_as_root_or_sudo() {
  if [ "$(id -u 2>/dev/null || printf '1')" = "0" ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

_try_install_python_runtime() {
  info "Python 3.11+ not found; trying to install Python automatically."

  if command -v brew >/dev/null 2>&1; then
    info "brew install python3"
    brew install python3
    return $?
  fi
  if command -v apt-get >/dev/null 2>&1; then
    info "apt-get install -y python3"
    _run_as_root_or_sudo apt-get update && _run_as_root_or_sudo apt-get install -y python3
    return $?
  fi
  if command -v dnf >/dev/null 2>&1; then
    info "dnf install -y python3"
    _run_as_root_or_sudo dnf install -y python3
    return $?
  fi
  if command -v yum >/dev/null 2>&1; then
    info "yum install -y python3"
    _run_as_root_or_sudo yum install -y python3
    return $?
  fi
  if command -v pacman >/dev/null 2>&1; then
    info "pacman -Sy --noconfirm python"
    _run_as_root_or_sudo pacman -Sy --noconfirm python
    return $?
  fi
  if command -v winget.exe >/dev/null 2>&1; then
    info "winget.exe install --id Python.Python.3 --exact"
    winget.exe install --id Python.Python.3 --exact --accept-package-agreements --accept-source-agreements
    return $?
  fi

  warn "No supported Python installer was found."
  return 1
}

_ensure_python_runtime_for_install() {
  local py
  py="$(_find_python_runtime || true)"
  if [ -n "$py" ]; then
    return 0
  fi

  if _try_install_python_runtime; then
    py="$(_find_python_runtime || true)"
    if [ -n "$py" ]; then
      ok "Python is ready"
      return 0
    fi
  fi

  _python_required_notice
  return 1
}

_python_required_notice() {
  echo ""
  error "Python 3.11+ is required."
  error "The installer tried automatic setup but could not find a working Python 3.11+ runtime."
  echo ""
  echo "  Install Python:"
  echo "    macOS:   brew install python3"
  echo "    Ubuntu:  sudo apt install python3"
  echo "    Windows: winget install --id Python.Python.3 --exact"
  echo ""
  echo "  Then re-run: ./install.sh"
  echo ""
}

# ── Run the installer ─────────────────────────────────────
ensure_utf8_locale
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if ! _ensure_python_runtime_for_install; then
  exit 1
fi
py="$(_find_python_runtime)"
exec "$py" "${SCRIPT_DIR}/_shared/installer/cli.py" "$@"
