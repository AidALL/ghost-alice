#!/usr/bin/env bash
# Ghost-ALICE installer library: targets
# Sourced by install.sh. Do not execute directly.

expand_skill_targets() {
  local skill_name="$1"
  local skill_root="${SCRIPT_DIR}/${skill_name}"

  [ -d "$skill_root" ] || return 0

  if [ -f "${skill_root}/SKILL.md" ]; then
    printf "%s|%s\n" "$skill_name" "$skill_root"
    return 0
  fi

  # Family search: only direct sub-directories containing SKILL.md
  local sub
  for sub in "$skill_root"/*/; do
    [ -d "$sub" ] || continue
    if [ -f "${sub}SKILL.md" ]; then
      local sub_name
      sub_name=$(basename "$sub")
      printf "%s|%s\n" "$sub_name" "${sub%/}"
    fi
  done
}

_addon_source_is_git_url() {
  local source="$1"
  case "$source" in
    http://*|https://*|ssh://*|file://*|git@*:*) return 0 ;;
    *) return 1 ;;
  esac
}

_addon_selected_ref() {
  if [ "${#ADDON_TAGS[@]}" -eq 0 ]; then
    printf '%s' ""
    return 0
  fi
  if [ "${#ADDON_TAGS[@]}" -gt 1 ]; then
    error "$(t '--addon-tag accepts one branch/tag for git URL addon sources' '--addon-tag accepts one branch/tag for git URL addon sources')" >&2
    return 1
  fi
  printf '%s' "${ADDON_TAGS[0]}"
}

_addon_source_cache_key() {
  local source="$1" ref="$2" py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; cannot prepare addon git source cache' 'Python 3.11+ not found; cannot prepare addon git source cache')" >&2
    return 1
  fi
  "$py" - "$source" "$ref" <<'PY'
import hashlib
import sys

payload = (sys.argv[1] + "\0" + sys.argv[2]).encode("utf-8")
print(hashlib.sha256(payload).hexdigest()[:24])
PY
}

_clone_addon_git_source() {
  local source="$1" ref="$2" cache_root key dest tmp
  if ! command -v git >/dev/null 2>&1; then
    error "$(t 'git not found; cannot clone addon source URL' 'git not found; cannot clone addon source URL')" >&2
    return 1
  fi
  key="$(_addon_source_cache_key "$source" "$ref")" || return 1
  cache_root="${GHOST_ALICE_ADDON_SOURCE_CACHE_DIR:-${HOME}/.ghost-alice/addon-source-cache}"
  dest="${cache_root}/${key}"
  tmp="${dest}.tmp.$$"
  mkdir -p "$cache_root"
  rm -rf "$tmp"
  if [ -n "$ref" ]; then
    info "$(t "Cloning addon source: ${source} (${ref})" "Cloning addon source: ${source} (${ref})")" >&2
    if ! git clone --quiet --depth 1 --branch "$ref" "$source" "$tmp"; then
      rm -rf "$tmp"
      error "$(t "Addon source clone failed: ${source}" "Addon source clone failed: ${source}")" >&2
      return 1
    fi
  else
    info "$(t "Cloning addon source: ${source}" "Cloning addon source: ${source}")" >&2
    if ! git clone --quiet --depth 1 "$source" "$tmp"; then
      rm -rf "$tmp"
      error "$(t "Addon source clone failed: ${source}" "Addon source clone failed: ${source}")" >&2
      return 1
    fi
  fi
  rm -rf "$dest"
  mv "$tmp" "$dest"
  printf '%s\n' "$dest"
}

prepare_addon_sources() {
  if [ "${ADDON_SOURCE_PREPARED:-0}" = "1" ]; then
    return 0
  fi
  if [ "$ADDON_SKIP" = "1" ] || [ "${#ADDON_SOURCES[@]}" -eq 0 ]; then
    ADDON_SOURCE_PREPARED=1
    return 0
  fi
  local ref source resolved
  ref="$(_addon_selected_ref)" || return 1
  local prepared=()
  for source in "${ADDON_SOURCES[@]}"; do
    if _addon_source_is_git_url "$source"; then
      resolved="$(_clone_addon_git_source "$source" "$ref")" || return 1
      prepared+=("$resolved")
    else
      if [ -n "$ref" ]; then
        error "$(t '--addon-tag can only be used with git URL addon sources; check out local sources yourself' '--addon-tag can only be used with git URL addon sources; check out local sources yourself')"
        return 1
      fi
      prepared+=("$source")
    fi
  done
  ADDON_SOURCES=("${prepared[@]}")
  ADDON_SOURCE_PREPARED=1
}

collect_addon_targets() {
  if [ "$ADDON_SKIP" = "1" ] || [ "${#ADDON_SOURCES[@]}" -eq 0 ]; then
    return 0
  fi
  prepare_addon_sources || return 1
  local py source skill
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; cannot read addon manifests' 'Python 3.11+ not found; cannot read addon manifests')"
    return 1
  fi
  local args=()
  for source in "${ADDON_SOURCES[@]}"; do
    args+=(--source "$source")
  done
  for skill in "${ALL_SKILLS[@]}"; do
    args+=(--core-skill "$skill")
    local core_targets core_name core_path
    core_targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r core_name core_path; do
      [ -n "$core_name" ] || continue
      args+=(--core-skill "$core_name")
    done <<< "$core_targets"
  done
  "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" "${args[@]}" --platform "$PLATFORM" --format shell
}

_auto_platform_target_keys() {
  local target_platform="$1"
  shift
  local saved_platform="$PLATFORM"
  local skill targets sub_name sub_path addon_targets_text addon_target a_name a_path a_id

  PLATFORM="$target_platform"
  [ -d "${SCRIPT_DIR}/_shared" ] && printf '%s\n' "support:_shared"

  for skill in "$@"; do
    targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      printf 'skill:%s\n' "$sub_name"
    done <<< "$targets"
  done

  if [ "$ADDON_SKIP" != "1" ] && [ "${#ADDON_SOURCES[@]}" -gt 0 ]; then
    if ! addon_targets_text="$(collect_addon_targets)"; then
      PLATFORM="$saved_platform"
      return 1
    fi
    while IFS='|' read -r a_name a_path a_id; do
      [ -n "$a_name" ] || continue
      printf 'skill:%s\n' "$a_name"
    done <<< "$addon_targets_text"
  fi

  PLATFORM="$saved_platform"
}

count_auto_common_targets() {
  local platforms=()
  while [ "$#" -gt 0 ] && [ "${1:-}" != "--" ]; do
    platforms+=("$1")
    shift
  done
  [ "${1:-}" = "--" ] && shift

  if [ "${#platforms[@]}" -eq 0 ]; then
    printf '0\n'
    return 0
  fi

  local common_file keys_file next_file
  common_file="$(mktemp "${TMPDIR:-/tmp}/ghost-alice-common-targets.XXXXXX")" || return 1
  keys_file="$(mktemp "${TMPDIR:-/tmp}/ghost-alice-platform-targets.XXXXXX")" || {
    rm -f "$common_file"
    return 1
  }
  next_file="$(mktemp "${TMPDIR:-/tmp}/ghost-alice-next-targets.XXXXXX")" || {
    rm -f "$common_file" "$keys_file"
    return 1
  }

  local first=1 platform
  for platform in "${platforms[@]}"; do
    if ! _auto_platform_target_keys "$platform" "$@" | LC_ALL=C sort -u >"$keys_file"; then
      rm -f "$common_file" "$keys_file" "$next_file"
      return 1
    fi
    if [ "$first" = "1" ]; then
      cp "$keys_file" "$common_file"
      first=0
    else
      comm -12 "$common_file" "$keys_file" >"$next_file"
      mv "$next_file" "$common_file"
      next_file="$(mktemp "${TMPDIR:-/tmp}/ghost-alice-next-targets.XXXXXX")" || {
        rm -f "$common_file" "$keys_file"
        return 1
      }
    fi
  done

  local count
  count="$(wc -l <"$common_file" | tr -d '[:space:]')"
  rm -f "$common_file" "$keys_file" "$next_file"
  printf '%s\n' "$count"
}

iter_install_targets() {
  local skill targets sub_name sub_path addon_target a_name a_path a_id
  for skill in "$@"; do
    targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      printf "%s|%s\n" "$sub_name" "$sub_path"
    done <<< "$targets"
  done
  # INSTALL_ADDON_TARGETS entries are name|source|addon_id; emit only name|source
  # so generic two-field consumers stay correct (addon_id is consumed elsewhere).
  for addon_target in ${INSTALL_ADDON_TARGETS[@]+"${INSTALL_ADDON_TARGETS[@]}"}; do
    [ -n "$addon_target" ] || continue
    IFS='|' read -r a_name a_path a_id <<< "$addon_target"
    [ -n "$a_name" ] && printf "%s|%s\n" "$a_name" "$a_path"
  done
}

install_shared() {
  local skills_dir="$1"
  local copy_only="${2:-0}"
  local shared_src="${SCRIPT_DIR}/_shared"
  local shared_dest="${skills_dir}/_shared"

  [ -d "$shared_src" ] || return 0

  if [ "$copy_only" != "1" ] && [ -L "$shared_dest" ]; then
    local existing
    existing=$(readlink "$shared_dest" 2>/dev/null || echo "")
    if [ "$existing" = "$shared_src" ]; then
      warn "$(t '_shared. Already installed (skipped)' '_shared. Already installed (skipped)')"
      return 0
    fi
    rm -f "$shared_dest"
  elif [ -e "$shared_dest" ]; then
    warn "$(t '_shared. Overwriting existing copy' '_shared. Overwriting existing copy')"
    if [ "$copy_only" != "1" ]; then
      rm -rf "$shared_dest"
    fi
  fi

  if [ "$copy_only" = "1" ]; then
    _install_copy_target "$shared_src" "$shared_dest"
    ok "$(t '_shared → copied' '_shared → copied')"
  elif ln -s "$shared_src" "$shared_dest" 2>/dev/null; then
    ok "$(t '_shared → symlinked' '_shared → symlinked')"
  else
    _install_copy_target "$shared_src" "$shared_dest"
    ok "$(t '_shared → copied (symlink not supported)' '_shared → copied (symlink not supported)')"
  fi
}

validate_requested_skills() {
  local skill targets
  for skill in "$@"; do
    targets=$(expand_skill_targets "$skill")
    if [ -z "$targets" ]; then
      error "$(t "Skill not found: ${skill}" "Skill not found: ${skill}")"
      error "$(t 'Run bash install.sh --list to see available skills.' 'Run bash install.sh --list to see available skills.')"
      exit 1
    fi
  done
}

remove_installed_target() {
  local label="$1"
  local target="$2"

  if [ -L "$target" ] || [ -d "$target" ]; then
    rm -rf "$target"
    ok "$(t "${label} removed" "${label} removed")"
    return 0
  fi
  return 1
}

has_managed_installs() {
  local skills_root="$1"
  local skill targets sub_name sub_path target

  for skill in "${ALL_SKILLS[@]}"; do
    targets=$(expand_skill_targets "$skill")
    if [ -z "$targets" ]; then
      target="${skills_root}/${skill}"
      if [ -L "$target" ] || [ -d "$target" ]; then
        return 0
      fi
      continue
    fi

    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      target="${skills_root}/${sub_name}"
      if [ -L "$target" ] || [ -d "$target" ]; then
        return 0
      fi
    done <<< "$targets"
  done

  return 1
}

cleanup_shared_if_unused() {
  local skills_root="$1"
  local shared_target="${skills_root}/_shared"

  if has_managed_installs "$skills_root"; then
    return 1
  fi

  if [ -L "$shared_target" ] || [ -d "$shared_target" ]; then
    rm -rf "$shared_target"
    ok "$(t '_shared removed' '_shared removed')"
    return 0
  fi

  return 1
}

cleanup_deprecated_installed_skills() {
  local skills_root="$1"
  local skill target backup_root stamp quarantine suffix

  [ -d "$skills_root" ] || return 0

  for skill in "${DEPRECATED_INSTALLED_SKILLS[@]}"; do
    target="${skills_root}/${skill}"
    [ -e "$target" ] || [ -L "$target" ] || continue

    backup_root="${HOME}/.ghost-alice/deprecated-skill-backups"
    stamp="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%Y%m%dT%H%M%S)"
    quarantine="${backup_root}/${skill}-${stamp}"
    suffix=1
    mkdir -p "$backup_root"
    while [ -e "$quarantine" ]; do
      quarantine="${backup_root}/${skill}-${stamp}-${suffix}"
      suffix=$((suffix + 1))
    done

    if mv "$target" "$quarantine"; then
      warn "$(t "Deprecated installed skill moved out of discovery path: ${skill} -> ${quarantine}" "Deprecated installed skill moved out of discovery path: ${skill} -> ${quarantine}")"
    else
      error "$(t "Failed to move deprecated installed skill: ${target}" "Failed to move deprecated installed skill: ${target}")"
      return 1
    fi
  done

  return 0
}

cleanup_pending_false_positives() {
  local py pending manifest
  _ensure_python_runtime_for_install || return 1
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    _python_required_notice
    return 1
  fi

  pending="${HOME}/.ghost-alice/pending-merges/${PLATFORM}"
  manifest="${pending}/manifest.json"
  "$py" "${SCRIPT_DIR}/merge-companion/scripts/cleanup_false_positive_legacy.py" \
    --platform "$PLATFORM" \
    --pending "$pending" \
    --manifest "$manifest" \
    --repo-root "$SCRIPT_DIR" \
    --apply
}
