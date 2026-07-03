#!/usr/bin/env bash
# Ghost-ALICE installer library: prerequisites
# Sourced by install.sh. Do not execute directly.

_prereq_command_exists() {
  command -v "$1" >/dev/null 2>&1
}

_prereq_run_as_root_or_sudo() {
  if [ "$(id -u 2>/dev/null || printf '1')" = "0" ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

_prereq_manual_notice() {
  case "$1" in
    git)
      warn "$(t 'Git was not found and automatic setup did not complete. Install Git, then rerun the installer.' 'Git was not found and automatic setup did not complete. Install Git, then rerun the installer.')"
      warn "  macOS:   brew install git"
      warn "  Ubuntu:  sudo apt install git"
      warn "  Fedora:  sudo dnf install git"
      warn "  Arch:    sudo pacman -Sy git"
      warn "  Windows: winget install --id Git.Git --exact"
      ;;
    node)
      warn "$(t 'Node.js was not found on PATH. Ghost-ALICE can continue with a configured Codex runtime, but full capability needs Node.js installed on PATH.' 'Node.js was not found on PATH. Ghost-ALICE can continue with a configured Codex runtime, but full capability needs Node.js installed on PATH.')"
      warn "  Download: https://nodejs.org/en/download"
      warn "  macOS:    brew install node"
      warn "  Ubuntu:   sudo apt install nodejs"
      warn "  Fedora:   sudo dnf install nodejs"
      warn "  Arch:     sudo pacman -Sy nodejs"
      warn "  Windows:  winget install --id OpenJS.NodeJS.LTS --exact"
      ;;
    *)
      warn "$(t "Prerequisite '$1' is missing and automatic setup did not complete." "Prerequisite '$1' is missing and automatic setup did not complete.")"
      ;;
  esac
}

_try_install_unix_prerequisite() {
  local tool="$1"
  local brew_pkg="" apt_pkg="" dnf_pkg="" yum_pkg="" pacman_pkg=""
  local winget_id="" choco_pkg="" scoop_pkg=""

  case "$tool" in
    git)
      brew_pkg="git"
      apt_pkg="git"
      dnf_pkg="git"
      yum_pkg="git"
      pacman_pkg="git"
      winget_id="Git.Git"
      choco_pkg="git"
      scoop_pkg="git"
      ;;
    node)
      brew_pkg="node"
      apt_pkg="nodejs"
      dnf_pkg="nodejs"
      yum_pkg="nodejs"
      pacman_pkg="nodejs"
      winget_id="OpenJS.NodeJS.LTS"
      choco_pkg="nodejs-lts"
      scoop_pkg="nodejs-lts"
      ;;
    *)
      _prereq_manual_notice "$tool"
      return 1
      ;;
  esac

  if _prereq_command_exists "$tool"; then
    return 0
  fi

  info "$(t "${tool} not found; trying automatic prerequisite setup." "${tool} not found; trying automatic prerequisite setup.")"

  if _prereq_command_exists brew; then
    info "brew install $brew_pkg"
    if brew install "$brew_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists apt-get; then
    info "apt-get install -y $apt_pkg"
    if _prereq_run_as_root_or_sudo apt-get update && _prereq_run_as_root_or_sudo apt-get install -y "$apt_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists dnf; then
    info "dnf install -y $dnf_pkg"
    if _prereq_run_as_root_or_sudo dnf install -y "$dnf_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists yum; then
    info "yum install -y $yum_pkg"
    if _prereq_run_as_root_or_sudo yum install -y "$yum_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists pacman; then
    info "pacman -Sy --noconfirm $pacman_pkg"
    if _prereq_run_as_root_or_sudo pacman -Sy --noconfirm "$pacman_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists winget.exe; then
    info "winget.exe install --id $winget_id --exact"
    if winget.exe install --id "$winget_id" --exact --accept-package-agreements --accept-source-agreements; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists choco.exe; then
    info "choco.exe install $choco_pkg -y"
    if choco.exe install "$choco_pkg" -y; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  if _prereq_command_exists scoop; then
    info "scoop install $scoop_pkg"
    if scoop install "$scoop_pkg"; then
      return 0
    fi
    _prereq_manual_notice "$tool"
    return 1
  fi

  _prereq_manual_notice "$tool"
  return 1
}

install_prerequisites() {
  local missing=0

  if ! _try_install_unix_prerequisite git; then
    missing=1
  fi

  if ! _try_install_unix_prerequisite node; then
    missing=1
  fi

  if [ "$missing" = "1" ]; then
    warn "$(t 'Continuing after prerequisite guidance. A later install step may still require the missing tool.' 'Continuing after prerequisite guidance. A later install step may still require the missing tool.')"
  fi

  return 0
}
