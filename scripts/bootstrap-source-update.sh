#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  bash bootstrap-source-update.sh [--source-dir PATH] [--no-install] [-- INSTALL_ARGS...]

Defaults:
  source dir: $GHOST_ALICE_SOURCE_DIR or $HOME/ghost-alice
  install:    run install.sh after the source checkout is updated

Examples:
  bash bootstrap-source-update.sh
  bash bootstrap-source-update.sh --source-dir "$HOME/ghost-alice"
  bash bootstrap-source-update.sh --no-install
  bash bootstrap-source-update.sh -- --platform codex
EOF
}

info() {
  printf '[Ghost-ALICE] %s\n' "$*" >&2
}

warn() {
  printf '[Ghost-ALICE] WARN: %s\n' "$*" >&2
}

error() {
  printf '[Ghost-ALICE] ERROR: %s\n' "$*" >&2
}

source_dir="${GHOST_ALICE_SOURCE_DIR:-}"
run_install=1
install_args=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-dir)
      shift
      if [ "$#" -eq 0 ]; then
        error "--source-dir requires a path"
        usage
        exit 2
      fi
      source_dir="$1"
      ;;
    --no-install)
      run_install=0
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      install_args=("$@")
      break
      ;;
    *)
      error "Unknown argument: $1"
      usage
      exit 2
      ;;
  esac
  shift
done

if [ -z "$source_dir" ]; then
  source_dir="${HOME}/ghost-alice"
fi

if [ ! -d "$source_dir" ]; then
  error "Ghost-ALICE source checkout not found: ${source_dir}"
  error "Set GHOST_ALICE_SOURCE_DIR or pass --source-dir PATH."
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  error "git is required to update the Ghost-ALICE source checkout."
  exit 1
fi

repo_root="$(git -C "$source_dir" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$repo_root" ]; then
  error "Not a git checkout: ${source_dir}"
  exit 1
fi

upstream="$(git -C "$repo_root" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [ -z "$upstream" ]; then
  error "No upstream branch is configured for this checkout."
  error "Open the checkout owner or set the branch upstream before using bootstrap update."
  exit 1
fi

branch="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
info "Updating Ghost-ALICE source checkout: ${repo_root}"
info "Branch: ${branch}; upstream: ${upstream}"

status_output="$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)"
stash_created=0
stash_ref=""

if [ -n "$status_output" ]; then
  stash_message="ghost-alice source update backup $(date +%Y%m%d-%H%M%S)"
  info "Saving source local changes in git stash."
  if ! git -C "$repo_root" stash push -u -m "$stash_message" -- . >/dev/null; then
    error "Could not stash source local changes; aborting update."
    exit 1
  fi
  stash_created=1
  stash_ref="$(git -C "$repo_root" stash list --format='%gd' | sed -n '1p')"
  if [ -z "$stash_ref" ]; then
    stash_ref="stash@{0}"
  fi
  warn "Source local changes saved in git stash: ${stash_ref}"
  warn "Review: git -C \"${repo_root}\" stash show -p ${stash_ref}"
  warn "Reapply only if intentional: git -C \"${repo_root}\" stash pop ${stash_ref}"
fi

info "Fast-forwarding source checkout."
if ! git -C "$repo_root" pull --ff-only; then
  error "Source update failed."
  if [ "$stash_created" = "1" ]; then
    warn "Local changes remain saved in ${stash_ref}."
  fi
  exit 1
fi

if [ "$stash_created" = "1" ]; then
  warn "Review saved source changes before reapplying: git -C \"${repo_root}\" stash show -p ${stash_ref}"
fi

if [ "$run_install" = "0" ]; then
  info "Install step skipped because --no-install was supplied."
  exit 0
fi

if [ ! -f "${repo_root}/install.sh" ]; then
  error "Updated checkout does not contain install.sh: ${repo_root}/install.sh"
  exit 1
fi

info "Running installer from updated source checkout."
exec bash "${repo_root}/install.sh" "${install_args[@]}"
