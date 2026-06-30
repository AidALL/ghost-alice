#!/usr/bin/env bash
# Ghost-ALICE installer library: platform
# Sourced by install.sh. Do not execute directly.

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

resolve_claude_home() {
  printf '%s\n' "${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
}

resolve_codex_home() {
  printf '%s\n' "${CODEX_HOME:-${HOME}/.codex}"
}

resolve_shared_skills_dir() {
  printf '%s\n' "${HOME}/.agents/skills"
}

resolve_codex_skills_dir() {
  resolve_shared_skills_dir
}

resolve_ghost_alice_runtime_shared_dir() {
  printf '%s\n' "${GHOST_ALICE_RUNTIME_SHARED_DIR:-${HOME}/.ghost-alice/runtime/current/_shared}"
}

is_windows_like_runtime() {
  case "${OSTYPE:-}" in
    msys*|cygwin*|win32*) return 0 ;;
  esac

  case "$(uname -s 2>/dev/null || echo "")" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
  esac

  return 1
}

codex_prefers_copy_install() {
  if [ "$PLATFORM" != "codex" ]; then
    return 1
  fi

  # Codex uses ~/.agents/skills as user-editable semantic assets. Copy mode
  # keeps local edits in the install target so preflight diff can protect them.
  return 0
}

shared_skills_prefers_copy_install() {
  case "$PLATFORM" in
    codex) return 0 ;;
    *) return 1 ;;
  esac
}

codex_hooks_supported() {
  return 0
}

detect_present_platforms() {
  [ -d "$(resolve_claude_home)" ] && printf '%s\n' "claude"
  [ -d "$(resolve_codex_home)" ]  && printf '%s\n' "codex"
}

detect_uninstall_platform_homes() {
  [ -d "$(resolve_claude_home)" ] && printf '%s\n' "claude"
  [ -d "$(resolve_codex_home)" ]  && printf '%s\n' "codex"
}

detect_uninstall_platforms() {
  {
    detect_uninstall_platform_homes || true
    for plat in claude codex; do
      [ -f "${HOME}/.ghost-alice/install-state/${plat}.json" ] && printf '%s\n' "$plat"
    done
  } | awk 'NF && !seen[$0]++'
}
