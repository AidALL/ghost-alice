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

collect_addon_targets() {
  if [ "$ADDON_SKIP" = "1" ] || [ "${#ADDON_SOURCES[@]}" -eq 0 ]; then
    return 0
  fi
  if [ "${#ADDON_TAGS[@]}" -gt 0 ]; then
    error "$(t '--addon-tag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with --addon-source.' '--addon-tag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with --addon-source.')"
    return 1
  fi
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
  done
  "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" "${args[@]}" --platform "$PLATFORM" --format shell
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
