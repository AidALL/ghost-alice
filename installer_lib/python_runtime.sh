#!/usr/bin/env bash
# Ghost-ALICE installer library: python_runtime
# Sourced by install.sh. Do not execute directly.

_find_runtime() {
  local py_cmd=""
  py_cmd="$(_find_python_runtime || true)"
  if [ -n "$py_cmd" ]; then
    echo "python:$py_cmd"
    return 0
  fi
  return 1
}

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
  info "$(t 'Python 3.11+ not found; trying to install Python automatically.' 'Python 3.11+ not found; trying to install Python automatically.')"

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

  warn "$(t 'No supported Python installer was found.' 'No supported Python installer was found.')"
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
      ok "$(t 'Python is ready' 'Python is ready')"
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
