#!/usr/bin/env bash
# Ghost-ALICE installer library: install
# Sourced by install.sh. Do not execute directly.

sync_commands() {
  local mode="${1:-sync}"        # sync | check
  local target="${2:-all}"       # all | claude | codex
  local catalog="${SCRIPT_DIR}/skill-catalog/skills.json"

  if [ ! -f "$catalog" ]; then
    warn "$(t "skills.json not found: ${catalog}" "skills.json not found: ${catalog}")"
    return 1
  fi

  local py_cmd=""
  py_cmd="$(_find_python_runtime || true)"

  if [ -z "$py_cmd" ]; then
    warn "$(t 'Python 3.11+ not found; skipping commands sync' 'Python 3.11+ not found; skipping commands sync')"
    return 0
  fi

  local claude_dir="${SCRIPT_DIR}/.claude/commands"

  "$py_cmd" "${SCRIPT_DIR}/_shared/commands_sync.py" "$catalog" "$claude_dir" "$mode" "$target"
}

_log() {
  echo "  [install_hooks] $*"
}

_platform_dir_for_state() {
  case "$PLATFORM" in
    claude|codex) printf '%s\n' "$PLATFORM" ;;
    *) return 1 ;;
  esac
}

_install_lock_path() {
  printf '%s\n' "${HOME}/.ghost-alice/install.lock"
}

_acquire_install_lock() {
  local py lock_path stale_seconds
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because install lock cannot be acquired' 'Python 3.11+ not found; aborting because install lock cannot be acquired')"
    return 1
  fi

  lock_path="$(_install_lock_path)"
  stale_seconds="${GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS:-1800}"
  if ! "$py" "${SCRIPT_DIR}/_shared/install_lock.py" acquire \
    --lock "$lock_path" \
    --stale-seconds "$stale_seconds" \
    --owner "install.sh:${PLATFORM}:$$"; then
    error "$(t "Another install is already running; aborting install: ${lock_path}" "Another install is already running; aborting install: ${lock_path}")"
    return 1
  fi

  INSTALL_LOCK_PATH="$lock_path"
  INSTALL_LOCK_HELD=1
}

_release_install_lock() {
  if [ "${INSTALL_LOCK_HELD:-0}" != "1" ] || [ -z "${INSTALL_LOCK_PATH:-}" ]; then
    return 0
  fi

  local py
  py="$(_find_python_runtime || true)"
  if [ -n "$py" ]; then
    "$py" "${SCRIPT_DIR}/_shared/install_lock.py" release --lock "$INSTALL_LOCK_PATH" >/dev/null 2>&1 || true
  fi
  INSTALL_LOCK_HELD=0
  INSTALL_LOCK_PATH=""
}

_install_copy_target() {
  local src="$1"
  local dest="$2"
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because staged copy cannot run.' 'Python 3.11+ not found; aborting because staged copy cannot run.')"
    return 1
  fi

  "$py" "${SCRIPT_DIR}/_shared/install_transaction.py" copy-replace \
    --source "$src" \
    --dest "$dest" \
    --rollback-root "${HOME}/.ghost-alice/install-rollbacks" \
    --event-log "${HOME}/.ghost-alice/install-state/${PLATFORM}-events.jsonl" || {
      error "$(t "Staged copy replace failed: ${dest}" "Staged copy replace failed: ${dest}")"
      return 1
    }
}

_sync_runtime_shared_core() {
  local shared_src="${SCRIPT_DIR}/_shared"
  local runtime_shared
  runtime_shared="$(resolve_ghost_alice_runtime_shared_dir)"

  [ -d "$shared_src" ] || return 0
  _install_copy_target "$shared_src" "$runtime_shared" || return 1
  detail_ok "$(t 'runtime _shared → copied' 'runtime _shared -> copied')"
}

_install_copy_targets() {
  local progress_label=""
  local progress_statuses=()
  local progress_event_file=""
  local progress_platform=""
  local progress_target_ids=()
  local progress_target_kinds=()
  local progress_target_statuses=()
  while [ "$#" -gt 0 ]; do
    case "${1:-}" in
      --progress-label)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress label; aborting installation.' 'Invalid staged copy batch progress label; aborting installation.')"
          return 1
        fi
        progress_label="$2"
        shift 2
        ;;
      --progress-status)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress status; aborting installation.' 'Invalid staged copy batch progress status; aborting installation.')"
          return 1
        fi
        progress_statuses+=("$2")
        shift 2
        ;;
      --progress-event-file)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress event file; aborting installation.' 'Invalid staged copy batch progress event file; aborting installation.')"
          return 1
        fi
        progress_event_file="$2"
        shift 2
        ;;
      --progress-platform)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress platform; aborting installation.' 'Invalid staged copy batch progress platform; aborting installation.')"
          return 1
        fi
        progress_platform="$2"
        shift 2
        ;;
      --progress-target-id)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress target id; aborting installation.' 'Invalid staged copy batch progress target id; aborting installation.')"
          return 1
        fi
        progress_target_ids+=("$2")
        shift 2
        ;;
      --progress-target-kind)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress target kind; aborting installation.' 'Invalid staged copy batch progress target kind; aborting installation.')"
          return 1
        fi
        progress_target_kinds+=("$2")
        shift 2
        ;;
      --progress-target-status)
        if [ "$#" -lt 2 ]; then
          error "$(t 'Invalid staged copy batch progress target status; aborting installation.' 'Invalid staged copy batch progress target status; aborting installation.')"
          return 1
        fi
        progress_target_statuses+=("$2")
        shift 2
        ;;
      *)
        break
        ;;
    esac
  done

  if [ "$#" -eq 0 ]; then
    return 0
  fi
  if [ $(( $# % 2 )) -ne 0 ]; then
    error "$(t 'Invalid staged copy batch target arguments; aborting installation.' 'Invalid staged copy batch target arguments; aborting installation.')"
    return 1
  fi

  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because staged copy batch cannot run.' 'Python 3.11+ not found; aborting because staged copy batch cannot run.')"
    return 1
  fi

  local args=(
    copy-replace-many
    --rollback-root "${HOME}/.ghost-alice/install-rollbacks"
    --event-log "${HOME}/.ghost-alice/install-state/${PLATFORM}-events.jsonl"
  )
  if [ -n "$progress_label" ]; then
    args+=(--progress-label "$progress_label")
  fi
  if [ -n "$progress_event_file" ]; then
    args+=(--progress-event-file "$progress_event_file")
    args+=(--progress-platform "$progress_platform")
  fi
  local progress_status
  for progress_status in ${progress_statuses[@]+"${progress_statuses[@]}"}; do
    args+=(--progress-status "$progress_status")
  done
  local progress_event_index
  for ((progress_event_index = 0; progress_event_index < ${#progress_target_ids[@]}; progress_event_index++)); do
    args+=(--progress-target-id "${progress_target_ids[$progress_event_index]}")
    args+=(--progress-target-kind "${progress_target_kinds[$progress_event_index]}")
    args+=(--progress-target-status "${progress_target_statuses[$progress_event_index]}")
  done
  while [ "$#" -gt 0 ]; do
    args+=(--target "$1" "$2")
    shift 2
  done

  "$py" "${SCRIPT_DIR}/_shared/install_transaction.py" "${args[@]}" || {
    error "$(t 'Staged copy batch replace failed' 'Staged copy batch replace failed')"
    return 1
  }
}

_run_encoding_guard_before_install() {
  local skills=("$@")
  local guard_py="${SCRIPT_DIR}/_shared/encoding_guard.py"
  local py
  py="$(_find_python_runtime || true)"

  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because encoding guard cannot run' 'Python 3.11+ not found; aborting because encoding guard cannot run')"
    return 1
  fi
  if [ ! -f "$guard_py" ]; then
    error "$(t "encoding guard not found; aborting install: ${guard_py}" "encoding guard not found; aborting install: ${guard_py}")"
    return 1
  fi

  if ! "$py" "$guard_py" --repo-root "$SCRIPT_DIR"; then
    error "$(t "encoding guard failed; aborting install: ${SCRIPT_DIR}" "encoding guard failed; aborting install: ${SCRIPT_DIR}")"
    return 1
  fi

  local addon_target sub_name sub_path addon_id_unused
  for addon_target in ${INSTALL_ADDON_TARGETS[@]+"${INSTALL_ADDON_TARGETS[@]}"}; do
    IFS='|' read -r sub_name sub_path addon_id_unused <<< "$addon_target"
    [ -n "$sub_path" ] || continue
    if ! "$py" "$guard_py" --repo-root "$sub_path"; then
      error "$(t "encoding guard failed; aborting install: ${sub_path}" "encoding guard failed; aborting install: ${sub_path}")"
      return 1
    fi
  done

  [ -d "$SKILLS_DIR" ] || return 0

  local target_args=(--repo-root "$SKILLS_DIR")
  if [ -e "${SKILLS_DIR}/_shared" ]; then
    target_args+=(--exclude-root "${SKILLS_DIR}/_shared")
  fi

  while IFS='|' read -r sub_name sub_path; do
    [ -n "$sub_name" ] || continue
    target_args+=(--exclude-root "${SKILLS_DIR}/${sub_name}")
  done < <(iter_install_targets "${skills[@]}")

  if ! "$py" "$guard_py" "${target_args[@]}"; then
    error "$(t "encoding guard failed; aborting install: ${SKILLS_DIR}" "encoding guard failed; aborting install: ${SKILLS_DIR}")"
    return 1
  fi
}

_run_preflight_before_install() {
  local skills=("$@")
  local platform_dir
  platform_dir="$(_platform_dir_for_state || true)"
  [ -n "$platform_dir" ] || return 0

  local pending_root="${HOME}/.ghost-alice/pending-merges/${platform_dir}"
  local manifest="${pending_root}/manifest.json"
  local snapshot="${pending_root}/snapshot.json"
  local py
  py="$(_find_python_runtime || true)"

  mkdir -p "$pending_root"

  _is_clean_ghost_alice_managed_target() {
    local target_name="$1"
    local target_path="$2"
    local python_exe="$3"
    [ -n "$python_exe" ] || return 1
    "$python_exe" "${SCRIPT_DIR}/_shared/installer_assets_cli.py" classify-clean \
      --asset-id "$target_name" \
      --path "$target_path" \
      --repo-root "$SCRIPT_DIR" >/dev/null 2>&1
  }

  if [ ! -f "$snapshot" ]; then
    local legacy_args=()
    if [ -e "${SKILLS_DIR}/_shared" ] && [ ! -L "${SKILLS_DIR}/_shared" ]; then
      if ! _is_clean_ghost_alice_managed_target "_shared" "${SKILLS_DIR}/_shared" "$py"; then
        legacy_args+=("_shared" "${SKILLS_DIR}/_shared")
      fi
    fi

    local sub_name sub_path dest
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      dest="${SKILLS_DIR}/${sub_name}"
      if [ -e "$dest" ] && [ ! -L "$dest" ]; then
        if ! _is_clean_ghost_alice_managed_target "$sub_name" "$dest" "$py"; then
          legacy_args+=("$sub_name" "$dest")
        fi
      fi
    done < <(iter_install_targets "${skills[@]}")

    if [ "${#legacy_args[@]}" -gt 0 ] && [ -z "$py" ]; then
      error "$(t 'Python 3.11+ not found; aborting because legacy target quarantine cannot run' 'Python 3.11+ not found; aborting because legacy target quarantine cannot run')"
      return 1
    fi

    local idx target_name target_path
    for ((idx = 0; idx < ${#legacy_args[@]}; idx += 2)); do
      target_name="${legacy_args[idx]}"
      target_path="${legacy_args[idx + 1]}"
      if ! "$py" "${SCRIPT_DIR}/merge-companion/scripts/quarantine_legacy_cli.py" \
        --target "$target_path" \
        --target-name "$target_name" \
        --pending "$pending_root" \
        --manifest "$manifest" \
        --platform "$platform_dir"; then
        error "$(t "legacy target quarantine failed; aborting install: ${target_name}" "legacy target quarantine failed; aborting install: ${target_name}")"
        return 1
      fi
    done
    return 0
  fi

  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because merge-companion preflight diff cannot run' 'Python 3.11+ not found; aborting because merge-companion preflight diff cannot run')"
    return 1
  fi

  if ! "$py" "${SCRIPT_DIR}/merge-companion/scripts/diff_collector_cli.py" \
    --snapshot "$snapshot" \
    --pending "$pending_root" \
    --manifest "$manifest" \
    --platform "$platform_dir" \
    --skills-dir "$SKILLS_DIR"; then
    error "$(t 'merge-companion preflight change detection failed; aborting install' 'merge-companion preflight change detection failed; aborting install')"
    return 1
  fi
}

_run_snapshot_after_install() {
  local platform_dir
  platform_dir="$(_platform_dir_for_state || true)"
  [ -n "$platform_dir" ] || return 0

  local pending_root="${HOME}/.ghost-alice/pending-merges/${platform_dir}"
  local snapshot="${pending_root}/snapshot.json"
  local py
  py="$(_find_python_runtime || true)"

  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because merge-companion snapshot cannot run' 'Python 3.11+ not found; aborting because merge-companion snapshot cannot run')"
    return 1
  fi

  if ! mkdir -p "$pending_root"; then
    error "$(t "merge-companion snapshot directory cannot be created; aborting install: ${pending_root}" "merge-companion snapshot directory cannot be created; aborting install: ${pending_root}")"
    return 1
  fi

  if ! "$py" "${SCRIPT_DIR}/merge-companion/scripts/snapshot_cli.py" \
    --output "$snapshot" \
    --platform "$platform_dir" \
    --skills-dir "$SKILLS_DIR"; then
    error "$(t 'merge-companion snapshot capture failed; aborting install' 'merge-companion snapshot capture failed; aborting install')"
    return 1
  fi

  _write_empty_pending_manifest_if_missing "$platform_dir" "${pending_root}/manifest.json"
}

_write_empty_pending_manifest_if_missing() {
  local platform_dir="$1"
  local manifest="$2"
  local py

  [ -f "$manifest" ] && return 0

  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because empty pending manifest cannot be written' 'Python 3.11+ not found; aborting because empty pending manifest cannot be written')"
    return 1
  fi

  "$py" "${SCRIPT_DIR}/_shared/pending_manifest_writer.py" "$manifest" "$platform_dir"
}

_detect_install_mode_for_state() {
  local dest="$1"
  local copy_only="${2:-0}"

  if [ -L "$dest" ]; then
    printf '%s\n' "symlink"
  elif [ -e "$dest" ]; then
    if [ "$copy_only" = "1" ]; then
      printf '%s\n' "copy"
    else
      printf '%s\n' "copy-fallback"
    fi
  else
    printf '%s\n' "missing"
  fi
}

_verify_install_after_copy() {
  local skills_dir="$1"
  local copy_only="${2:-0}"
  shift 2
  local skills=("$@")
  local platform_dir
  platform_dir="$(_platform_dir_for_state || true)"
  [ -n "$platform_dir" ] || return 0

  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because post-install verification cannot run' 'Python 3.11+ not found; aborting because post-install verification cannot run')"
    return 1
  fi

  local target_args=()
  if [ -d "${SCRIPT_DIR}/_shared" ]; then
    target_args+=(
      --target "_shared" "${SCRIPT_DIR}/_shared" "${skills_dir}/_shared"
      "$(_detect_install_mode_for_state "${skills_dir}/_shared" "$copy_only")"
    )
  fi

  local sub_name sub_path
  while IFS='|' read -r sub_name sub_path; do
    [ -n "$sub_name" ] || continue
    target_args+=(
      --target "$sub_name" "$sub_path" "${skills_dir}/${sub_name}"
      "$(_detect_install_mode_for_state "${skills_dir}/${sub_name}" "$copy_only")"
    )
  done < <(iter_install_targets "${skills[@]}")

  [ "${#target_args[@]}" -gt 0 ] || return 0

  if ! "$py" "${SCRIPT_DIR}/merge-companion/scripts/install_verifier.py" \
    --platform "$platform_dir" \
    --state-root "${HOME}/.ghost-alice/install-state" \
    "${target_args[@]}"; then
    error "$(t 'Post-install source/destination verification failed; aborting before snapshot' 'Post-install source/destination verification failed; aborting before snapshot')"
    return 1
  fi
}

# Echo the addon_id that owns target NAME (empty + non-zero rc for core targets).
# INSTALL_ADDON_TARGETS entries are name|source|addon_id so copied targets can
# carry the addon owner in their install marker.
_addon_id_for_target() {
  local name="$1" line a_name a_path a_id
  for line in ${INSTALL_ADDON_TARGETS[@]+"${INSTALL_ADDON_TARGETS[@]}"}; do
    IFS='|' read -r a_name a_path a_id <<< "$line"
    if [ "$a_name" = "$name" ] && [ -n "$a_id" ]; then
      printf '%s' "$a_id"
      return 0
    fi
  done
  return 1
}

_write_ownership_markers_after_install() {
  local skills_dir="$1"
  local copy_only="${2:-0}"
  shift 2
  local skills=("$@")
  local platform_dir
  platform_dir="$(_platform_dir_for_state || true)"
  [ -n "$platform_dir" ] || return 0

  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because ownership markers cannot be written' 'Python 3.11+ not found; aborting because ownership markers cannot be written')"
    return 1
  fi

  local source_root source_head
  source_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$SCRIPT_DIR")"
  source_head="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf '%s' "unknown")"

  local target_args=()
  if [ -e "${skills_dir}/_shared" ]; then
    target_args+=(
      --target "_shared" "${skills_dir}/_shared"
      "$(_detect_install_mode_for_state "${skills_dir}/_shared" "$copy_only")"
    )
  fi

  local sub_name sub_path sub_mode sub_addon_id
  while IFS='|' read -r sub_name sub_path; do
    [ -n "$sub_name" ] || continue
    sub_mode="$(_detect_install_mode_for_state "${skills_dir}/${sub_name}" "$copy_only")"
    if sub_addon_id="$(_addon_id_for_target "$sub_name")"; then
      target_args+=(--addon-target "$sub_name" "${skills_dir}/${sub_name}" "$sub_mode" "$sub_addon_id")
    else
      target_args+=(--target "$sub_name" "${skills_dir}/${sub_name}" "$sub_mode")
    fi
  done < <(iter_install_targets "${skills[@]}")

  [ "${#target_args[@]}" -gt 0 ] || return 0

  if ! "$py" "${SCRIPT_DIR}/_shared/installer_assets_cli.py" \
    --platform "$platform_dir" \
    --source-repo "$source_root" \
    --source-commit "$source_head" \
    "${target_args[@]}"; then
    error "$(t 'Ownership marker write failed; aborting before snapshot' 'Ownership marker write failed; aborting before snapshot')"
    return 1
  fi
}

_install_state_resolve_source() {
  _IS_SRC_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$SCRIPT_DIR")"
  _IS_SRC_BRANCH="$(git -C "$SCRIPT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || printf '%s' "DETACHED")"
  _IS_SRC_HEAD="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf '%s' "unknown")"
  if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [ -n "$(git -C "$SCRIPT_DIR" status --porcelain=v1 --untracked-files=all 2>/dev/null)" ]; then
      _IS_SRC_DIRTY="dirty"
    else
      _IS_SRC_DIRTY="clean"
    fi
  else
    _IS_SRC_DIRTY="unknown"
  fi
}

_install_state_collect_target_args() {
  local skills_dir="$1"
  local copy_only="$2"
  shift 2
  local skills=("$@")

  _IS_TARGET_ARGS=()
  if [ -e "${SCRIPT_DIR}/_shared" ]; then
    _IS_TARGET_ARGS+=("_shared" "${SCRIPT_DIR}/_shared" "${skills_dir}/_shared" "$(_detect_install_mode_for_state "${skills_dir}/_shared" "$copy_only")")
  fi

  local sub_name sub_path
  while IFS='|' read -r sub_name sub_path; do
    [ -n "$sub_name" ] || continue
    _IS_TARGET_ARGS+=("$sub_name" "$sub_path" "${skills_dir}/${sub_name}" "$(_detect_install_mode_for_state "${skills_dir}/${sub_name}" "$copy_only")")
  done < <(iter_install_targets "${skills[@]}")
}

write_install_state_manifest() {
  local skills_dir="$1"
  local copy_only="${2:-0}"
  shift 2
  local skills=("$@")
  local py state_root state_path source_root source_branch source_head source_dirty_state

  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because install-state manifest cannot be written' 'Python 3.11+ not found; aborting because install-state manifest cannot be written')"
    return 1
  fi

  state_root="${HOME}/.ghost-alice/install-state"
  if ! mkdir -p "$state_root"; then
    error "$(t "Could not create install-state directory; aborting install: ${state_root}" "Could not create install-state directory; aborting install: ${state_root}")"
    return 1
  fi
  state_path="${state_root}/${PLATFORM}.json"

  _install_state_resolve_source
  source_root="$_IS_SRC_ROOT"
  source_branch="$_IS_SRC_BRANCH"
  source_head="$_IS_SRC_HEAD"
  source_dirty_state="$_IS_SRC_DIRTY"

  _install_state_collect_target_args "$skills_dir" "$copy_only" "${skills[@]}"

  if ! GHOST_ALICE_SOURCE_REPO_HOOK_CHANGED="$SOURCE_REPO_HOOK_CHANGED" \
    GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE_PRESENT="$SOURCE_REPO_HOOK_BEFORE_PRESENT" \
    GHOST_ALICE_SOURCE_REPO_HOOK_BEFORE="$SOURCE_REPO_HOOK_BEFORE" \
    GHOST_ALICE_SOURCE_REPO_HOOK_AFTER="$SOURCE_REPO_HOOK_AFTER" \
    "$py" "${SCRIPT_DIR}/_shared/install_state_writer.py" "$PLATFORM" "$source_root" "$source_branch" "$source_head" "$source_dirty_state" "$state_path" "${_IS_TARGET_ARGS[@]}"
  then
    error "$(t "Install-state manifest write failed; aborting install: ${state_path}" "Install-state manifest write failed; aborting install: ${state_path}")"
    return 1
  fi

  info "Install-state manifest: $state_path"
}

write_addon_sidecars_after_install() {
  local skills_dir="$1"
  [ "${#ADDON_SOURCES[@]}" -gt 0 ] || return 0
  local py installed_at
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; cannot write addon sidecars; aborting install' 'Python 3.11+ not found; cannot write addon sidecars; aborting install')"
    return 1
  fi
  installed_at="$("$py" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())' 2>/dev/null || printf '%s' "unknown")"
  local source_args=()
  local source
  for source in "${ADDON_SOURCES[@]}"; do
    source_args+=(--source "$source")
  done
  if "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" write-sidecars \
      "${source_args[@]}" --platform "$PLATFORM" \
      --addons-dir "${HOME}/.ghost-alice/addons/${PLATFORM}" \
      --skills-dir "$skills_dir" --installed-at "$installed_at" \
      --claude-commands-dir "$(resolve_claude_home)/commands" \
      --resources-dir "${HOME}/.ghost-alice/resources/${PLATFORM}"; then
    info "$(t 'Addon sidecars written' 'Addon sidecars written')"
  else
    error "$(t 'Addon sidecar write failed; aborting install' 'Addon sidecar write failed; aborting install')"
    return 1
  fi
}

_repair_reprovision_target() {
  local name="$1" src="$2" dest="$3" copy_only="$4"
  if [ "$copy_only" = "1" ]; then
    _install_copy_target "$src" "$dest"
  elif ln -s "$src" "$dest" 2>/dev/null; then
    :
  else
    _install_copy_target "$src" "$dest"
  fi
  ok "$(t "repair: re-provisioned ${name}" "repair: re-provisioned ${name}")"
}

_repair_addon_targets() {
  local py="$1"
  local addons_dir="${HOME}/.ghost-alice/addons/${PLATFORM}"
  [ -d "$addons_dir" ] || return 0
  "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" repair-missing \
    --platform "$PLATFORM" \
    --addons-dir "$addons_dir" \
    --skills-dir "$SKILLS_DIR" \
    --claude-commands-dir "$(resolve_claude_home)/commands" \
    --resources-dir "${HOME}/.ghost-alice/resources/${PLATFORM}" >/dev/null
}

run_repair() {
  # The mutating reconciliation path. Unlike --doctor (read-only), it
  # re-provisions MISSING managed targets, but it classifies ownership before ever
  # replacing anything: an occupied slot that is not a clean Ghost-ALICE managed
  # target (a user/domain dir, or drift) is LEFT UNTOUCHED, never clobbered.
  echo ""
  info "$(t 'Installer repair: re-provisioning missing managed targets...' 'Installer repair: re-provisioning missing managed targets...')"
  local py copy_only=0 repaired=0 kept=0
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; repair cannot run' 'Python 3.11+ not found; repair cannot run')"
    exit 1
  fi
  if codex_prefers_copy_install || shared_skills_prefers_copy_install; then
    copy_only=1
  fi

  # _shared is the most critical target -- every managed skill resolves shared
  # modules through it. A dangling symlink ([ ! -e ] is true, [ -L ] is true) is
  # functionally missing, so it must be re-provisioned too (addon review), while an
  # occupied non-clean _shared (user dir / drift) is classified and left untouched.
  local shared="${SKILLS_DIR}/_shared"
  if [ ! -e "$shared" ]; then
    install_shared "${SKILLS_DIR}" "$copy_only"  # absent or dangling -> restore
    repaired=$((repaired + 1))
  elif "$py" "${SCRIPT_DIR}/_shared/installer_assets_cli.py" classify-clean \
         --asset-id "_shared" --path "$shared" --repo-root "$SCRIPT_DIR" >/dev/null 2>&1; then
    : # clean managed _shared; nothing to repair
  else
    kept=$((kept + 1))
    warn "$(t 'repair: _shared is occupied and not cleanly managed; left untouched' 'repair: _shared is occupied and not cleanly managed; left untouched')"
  fi

  local sub_name sub_path dest
  while IFS='|' read -r sub_name sub_path; do
    [ -n "$sub_name" ] || continue
    dest="${SKILLS_DIR}/${sub_name}"
    if [ ! -e "$dest" ] && [ ! -L "$dest" ]; then
      _repair_reprovision_target "$sub_name" "$sub_path" "$dest" "$copy_only"
      repaired=$((repaired + 1))
    elif "$py" "${SCRIPT_DIR}/_shared/installer_assets_cli.py" classify-clean \
           --asset-id "$sub_name" --path "$dest" --repo-root "$SCRIPT_DIR" >/dev/null 2>&1; then
      : # already a clean managed target; nothing to repair
    else
      kept=$((kept + 1))
      warn "$(t "repair: ${sub_name} is occupied and not cleanly managed; left untouched" "repair: ${sub_name} is occupied and not cleanly managed; left untouched")"
    fi
  done < <(iter_install_targets "${ALL_SKILLS[@]}")

  if ! _repair_addon_targets "$py"; then
    error "$(t 'repair: addon target repair failed; see addon registry sidecars' 'repair: addon target repair failed; see addon registry sidecars')"
    return 1
  fi

  ok "$(t "Repair complete: ${repaired} re-provisioned, ${kept} left untouched" "Repair complete: ${repaired} re-provisioned, ${kept} left untouched")"
}

_check_addon_collisions() {
  [ "${#ADDON_SOURCES[@]}" -gt 0 ] || return 0
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    # Addons were requested but collision detection cannot run: fail closed
    # rather than installing over an unknown owner.
    error "$(t 'Python 3.11+ not found; cannot check addon collisions; aborting install' 'Python 3.11+ not found; cannot check addon collisions; aborting install')"
    exit 1
  fi
  local source_args=() core_args=() source skill
  for source in "${ADDON_SOURCES[@]}"; do
    source_args+=(--source "$source")
  done
  for skill in ${ALL_SKILLS[@]+"${ALL_SKILLS[@]}"}; do
    core_args+=(--core-skill "$skill")
    local core_targets core_name core_path
    core_targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r core_name core_path; do
      [ -n "$core_name" ] || continue
      core_args+=(--core-skill "$core_name")
    done <<< "$core_targets"
  done
  local rc=0
  "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" detect-collisions \
    "${source_args[@]}" --platform "$PLATFORM" --skills-dir "$SKILLS_DIR" \
    --addons-dir "${HOME}/.ghost-alice/addons/${PLATFORM}" \
    --claude-commands-dir "$(resolve_claude_home)/commands" \
    --resources-dir "${HOME}/.ghost-alice/resources/${PLATFORM}" \
    ${core_args[@]+"${core_args[@]}"} || rc=$?
  if [ "$rc" -eq 2 ]; then
    error "$(t 'Addon install collision detected; aborting install' 'Addon install collision detected; aborting install')"
    exit 1
  elif [ "$rc" -ne 0 ]; then
    error "$(t 'Addon manifest error; aborting install' 'Addon manifest error; aborting install')"
    exit 1
  fi
}

migrate_addon_state_once() {
  local py state_path
  py="$(_find_python_runtime || true)"
  [ -n "$py" ] || return 0
  state_path="${HOME}/.ghost-alice/install-state/${PLATFORM}.json"
  [ -f "$state_path" ] || return 0
  local core_args=()
  local skill
  for skill in ${ALL_SKILLS[@]+"${ALL_SKILLS[@]}"}; do
    core_args+=(--core-skill "$skill")
  done
  "$py" "${SCRIPT_DIR}/_shared/addon_migration.py" \
    --platform "$PLATFORM" --install-state "$state_path" \
    --addons-dir "${HOME}/.ghost-alice/addons/${PLATFORM}" \
    ${core_args[@]+"${core_args[@]}"} >/dev/null 2>&1 || true
}

_run_install_hooks() {
  local action="${1:-install}"   # install | uninstall | status
  local platform="${2:-$PLATFORM}"

  local action_ko
  case "$action" in
    install)   action_ko="install" ;;
    uninstall) action_ko="uninstall" ;;
    status)    action_ko="status" ;;
    *)         action_ko="$action" ;;
  esac
  echo ""
  info "$(t "Agent hook ${action}... (pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace)" "Agent hook ${action}... (pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace)")"

  if ! codex_hooks_supported "$platform"; then
    info "$(t "Codex hooks are unavailable in this runtime; skipping." "Codex hooks are unavailable in this runtime; skipping.")"
    return 0
  fi

  local runtime_info
  runtime_info="$(_find_runtime || true)"
  if [ -z "$runtime_info" ]; then
    _python_required_notice
    return 1
  fi
  local runtime_type="${runtime_info%%:*}"
  local runtime_cmd="${runtime_info#*:}"

  case "$runtime_type" in
    python)
      local hook_py="${SCRIPT_DIR}/_shared/install_hooks.py"
      if [ ! -f "$hook_py" ]; then
        error "$(t "install_hooks.py not found: ${hook_py}" "install_hooks.py not found: ${hook_py}")"
        return 1
      fi
      local args=(--platform "$platform" --hook-shared-dir "$(resolve_ghost_alice_runtime_shared_dir)" --skills-dir "$SKILLS_DIR")
      local visibility="${AGENT_VISIBILITY:-}"
      if [ "$action" = "install" ] && [ -z "$visibility" ]; then
        visibility="dynamic"
      fi
      if [ -n "$visibility" ]; then
        args+=(--visibility "$visibility")
      fi
      case "$action" in
        uninstall) args+=(--uninstall) ;;
        status)    args+=(--status) ;;
      esac
      # Forward addon sources so observational addon hooks install after core hooks
      # (plan Phase 4). Empty on a core-only run -> args[] stays byte-identical.
      if [ "$action" = "install" ]; then
        local _addon_src
        for _addon_src in ${ADDON_SOURCES[@]+"${ADDON_SOURCES[@]}"}; do
          args+=(--addon-source "$_addon_src")
        done
      fi
      "$runtime_cmd" "$hook_py" "${args[@]}" || {
        error "$(t "Hook ${action} failed" "Hook ${action} failed")"
        return 1
      }
      ;;
    *)
      _python_required_notice
      return 1
      ;;
  esac
}

_install_collect_targets() {
  # Args: skill names (same as passed to install()).
  # Outputs (globals set by plain assignment. Bash 3.2 safe):
  #   INSTALL_ADDON_TARGETS       (global array, already declared globally)
  #   _INSTALL_ALL_TARGET_LINES   (global array)
  #   _INSTALL_SKILL_TARGET_TOTAL (global scalar)
  #   _INSTALL_SUPPORT_TARGET_TOTAL (global scalar)
  #   _INSTALL_TOTAL_TARGET_COUNT (global scalar)
  validate_requested_skills "$@"
  INSTALL_ADDON_TARGETS=()
  local addon_targets_text addon_target_line
  if ! addon_targets_text="$(collect_addon_targets)"; then
    exit 1
  fi
  while IFS= read -r addon_target_line; do
    [ -n "$addon_target_line" ] && INSTALL_ADDON_TARGETS+=("$addon_target_line")
  done <<< "$addon_targets_text"

  _INSTALL_ALL_TARGET_LINES=()
  local install_target_line
  while IFS= read -r install_target_line; do
    [ -n "$install_target_line" ] && _INSTALL_ALL_TARGET_LINES+=("$install_target_line")
  done < <(iter_install_targets "$@")
  _INSTALL_SKILL_TARGET_TOTAL="${#_INSTALL_ALL_TARGET_LINES[@]}"
  _INSTALL_SUPPORT_TARGET_TOTAL=0
  if [ -d "${SCRIPT_DIR}/_shared" ]; then
    _INSTALL_SUPPORT_TARGET_TOTAL=1
  fi
  _INSTALL_TOTAL_TARGET_COUNT=$((_INSTALL_SKILL_TARGET_TOTAL + _INSTALL_SUPPORT_TARGET_TOTAL))
}

install() {
  local skills=("$@")
  if [ "${#skills[@]}" -eq 0 ]; then
    skills=("${ALL_SKILLS[@]}")
  fi

  _install_collect_targets "${skills[@]}"
  local all_target_lines=("${_INSTALL_ALL_TARGET_LINES[@]}")
  local skill_target_total="$_INSTALL_SKILL_TARGET_TOTAL"
  local support_target_total="$_INSTALL_SUPPORT_TARGET_TOTAL"
  local total_target_count="$_INSTALL_TOTAL_TARGET_COUNT"
  local shared_src="${SCRIPT_DIR}/_shared"
  local shared_dest="${SKILLS_DIR}/_shared"
  local visibility
  visibility="$(resolve_effective_visibility)"

  run_logged_if_compact check_source_health
  mkdir -p "${SKILLS_DIR}"
  run_logged_if_compact cleanup_deprecated_installed_skills "${SKILLS_DIR}"
  if ! run_logged_if_compact _run_encoding_guard_before_install "${skills[@]}"; then
    error "$(t "encoding guard failed; invalid-utf8 or semantic asset details: ${INSTALL_REPORT_LOG_FILE}" "encoding guard failed; invalid-utf8 or semantic asset details: ${INSTALL_REPORT_LOG_FILE}")"
    exit 1
  fi
  run_logged_if_compact _run_preflight_before_install "${skills[@]}"
  if install_report_enabled && live_counter_enabled; then
    report_print_start "$PLATFORM" "$total_target_count" "$visibility"
  elif ! install_compact_output_enabled; then
    info "$(t '[1/5] Preflight: ok' '[1/5] Preflight: ok')"
  fi

  local copy_only=0
  if codex_prefers_copy_install || shared_skills_prefers_copy_install; then
    copy_only=1
  fi

  local copy_target_args=()
  local copy_target_labels=()
  local copy_target_kinds=()
  local copy_target_statuses=()
  local sync_installed=0
  local sync_updated=0
  local sync_skipped=0
  if [ "$copy_only" = "1" ]; then
    if [ -d "$shared_src" ]; then
      if [ -e "$shared_dest" ]; then
        sync_updated=$((sync_updated + 1))
        copy_target_statuses+=("updated")
        detail_warn "$(t '_shared. Overwriting existing copy' '_shared. Overwriting existing copy')"
      else
        sync_installed=$((sync_installed + 1))
        copy_target_statuses+=("new")
      fi
      copy_target_args+=("$shared_src" "$shared_dest")
      copy_target_labels+=("_shared")
      copy_target_kinds+=("support")
    fi
  else
    local shared_report_status=""
    if [ -d "$shared_src" ]; then
      if [ -L "$shared_dest" ] && [ "$(readlink "$shared_dest" 2>/dev/null || true)" = "$shared_src" ]; then
        sync_skipped=$((sync_skipped + 1))
        shared_report_status="current"
      elif [ -e "$shared_dest" ]; then
        sync_updated=$((sync_updated + 1))
        shared_report_status="updated"
      else
        sync_installed=$((sync_installed + 1))
        shared_report_status="new"
      fi
      install_shared "${SKILLS_DIR}" "$copy_only"
      report_write_target_event "$PLATFORM" "_shared" "support" "$shared_report_status"
    fi
  fi

  # Finish any interrupted prior uninstall BEFORE installing. If a leftover
  # <addon>.json.removing marker is resumed AFTER install, it deletes the addon we
  # just installed, leaving sidecar-present/skill-missing. Running it first
  # completes the old removal and frees the name before collision detection.
  run_logged_if_compact _resume_pending_addon_uninstalls || true

  _check_addon_collisions

  local installed=0
  local skipped=0
  local sub_name sub_path src dest existing target_status
  local py
  py="$(_find_python_runtime || true)"

  for install_target_line in "${all_target_lines[@]}"; do
    IFS='|' read -r sub_name sub_path <<< "$install_target_line"
    [ -n "$sub_name" ] || continue
    src="$sub_path"
    dest="${SKILLS_DIR}/${sub_name}"

    if [ "$copy_only" = "1" ]; then
      if [ -e "$dest" ]; then
        sync_updated=$((sync_updated + 1))
        target_status="updated"
        copy_target_statuses+=("updated")
        detail_warn "$(t "${sub_name}. Overwriting existing install" "${sub_name}. Overwriting existing install")"
      else
        sync_installed=$((sync_installed + 1))
        target_status="new"
        copy_target_statuses+=("new")
      fi
      copy_target_args+=("$src" "$dest")
      copy_target_labels+=("$sub_name")
      copy_target_kinds+=("skill")
      installed=$((installed + 1))
      continue
    fi

    if [ -L "$dest" ]; then
      existing=$(readlink "$dest" 2>/dev/null || echo "")
      if [ "$existing" = "$src" ]; then
        sync_skipped=$((sync_skipped + 1))
        detail_warn "$(t "${sub_name}. Already installed (skipped)" "${sub_name}. Already installed (skipped)")"
        report_write_target_event "$PLATFORM" "$sub_name" "skill" "current"
        skipped=$((skipped + 1))
        continue
      fi
    fi

    if [ -e "$dest" ]; then
      sync_updated=$((sync_updated + 1))
      target_status="updated"
      detail_warn "$(t "${sub_name}. Overwriting existing install" "${sub_name}. Overwriting existing install")"
    else
      sync_installed=$((sync_installed + 1))
      target_status="new"
    fi

    if [ -n "$py" ] && "$py" "${SCRIPT_DIR}/_shared/install_transaction.py" symlink-replace --source "$src" --dest "$dest" --event-log "${HOME}/.ghost-alice/install-state/${PLATFORM}-events.jsonl" >/dev/null 2>&1; then
      detail_ok "$(t "${sub_name} → symlinked" "${sub_name} → symlinked")"
    elif ln -s "$src" "$dest" 2>/dev/null; then
      detail_ok "$(t "${sub_name} → symlinked" "${sub_name} → symlinked")"
    else
      _install_copy_target "$src" "$dest"
      detail_ok "$(t "${sub_name} → copied (symlink not supported)" "${sub_name} → copied (symlink not supported)")"
    fi
    report_write_target_event "$PLATFORM" "$sub_name" "skill" "$target_status"
    installed=$((installed + 1))
  done

  if [ "$copy_only" = "1" ] && [ "${#copy_target_args[@]}" -gt 0 ]; then
    local copy_progress_args=()
    local copy_events_during_batch=0
    if install_report_enabled && live_counter_enabled; then
      copy_progress_args+=(--progress-label "  [2/5] Skill sync         ")
      local copy_status
      for copy_status in "${copy_target_statuses[@]}"; do
        copy_progress_args+=(--progress-status "$copy_status")
      done
    fi
    if [ -n "${INSTALL_REPORT_EVENT_FILE:-}" ]; then
      copy_events_during_batch=1
      copy_progress_args+=(--progress-event-file "$INSTALL_REPORT_EVENT_FILE")
      copy_progress_args+=(--progress-platform "$PLATFORM")
      local copy_progress_event_index
      for ((copy_progress_event_index = 0; copy_progress_event_index < ${#copy_target_labels[@]}; copy_progress_event_index++)); do
        copy_progress_args+=(--progress-target-id "${copy_target_labels[$copy_progress_event_index]}")
        copy_progress_args+=(--progress-target-kind "${copy_target_kinds[$copy_progress_event_index]}")
        copy_progress_args+=(--progress-target-status "${copy_target_statuses[$copy_progress_event_index]}")
      done
    fi
    _install_copy_targets ${copy_progress_args[@]+"${copy_progress_args[@]}"} "${copy_target_args[@]}" || exit 1
    local copy_event_index
    if [ "$copy_events_during_batch" != "1" ]; then
      for ((copy_event_index = 0; copy_event_index < ${#copy_target_labels[@]}; copy_event_index++)); do
        report_write_target_event \
          "$PLATFORM" \
          "${copy_target_labels[$copy_event_index]}" \
          "${copy_target_kinds[$copy_event_index]}" \
          "${copy_target_statuses[$copy_event_index]}"
      done
    fi
    local copy_label
    for copy_label in "${copy_target_labels[@]}"; do
      if [ "$copy_label" = "_shared" ]; then
        detail_ok "$(t '_shared → copied' '_shared → copied')"
      else
        detail_ok "$(t "${copy_label} → copied (copy-only compatibility mode)" "${copy_label} → copied (copy-only compatibility mode)")"
      fi
    done
  fi

  local sync_mode_label=""
  if [ "$copy_only" = "1" ]; then
    sync_mode_label="$(t 'copy-only compatibility mode' 'copy-only compatibility mode')"
  fi
  if ! install_compact_output_enabled; then
    print_skill_sync_summary "$skill_target_total" "$support_target_total" "$sync_installed" "$sync_updated" "$sync_skipped" "$sync_mode_label"
  fi

  if [ "$PLATFORM" = "codex" ]; then
    run_logged_if_compact ensure_codex_bootstrap "${SKILLS_DIR}"
  fi
  if ! install_compact_output_enabled; then
    info "$(t "[3/5] Runtime config: platform=${PLATFORM}, visibility=${visibility}" "[3/5] Runtime config: platform=${PLATFORM}, visibility=${visibility}")"

    echo ""
    ok "$(t "Done: ${installed} installed, ${skipped} skipped" "Done: ${installed} installed, ${skipped} skipped")"
    echo ""
    info "$(t "Install path: ${SKILLS_DIR}" "Install path: ${SKILLS_DIR}")"
    if [ "$PLATFORM" = "codex" ] && [ "$copy_only" = "1" ]; then
      info "$(t "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source, then re-run bash install.sh" "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source, then re-run bash install.sh")"
    elif [ "$PLATFORM" = "codex" ]; then
      info "$(t "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source" "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source")"
    else
      info "$(t "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source" "To update skills safely: cd ${SCRIPT_DIR} && bash install.sh --update-source")"
    fi
    if [ "$installed" -gt 0 ] && [ "$copy_only" != "1" ]; then
      info "$(t 'With symlink install, safe source updates refresh linked skills after the checkout fast-forwards.' 'With symlink install, safe source updates refresh linked skills after the checkout fast-forwards.')"
    fi
  fi

  run_logged_if_compact _sync_runtime_shared_core || {
    error "$(t 'Runtime shared core sync failed. Aborting hook install' 'Runtime shared core sync failed. Aborting hook install')"
    [ -n "${INSTALL_REPORT_LOG_FILE:-}" ] && error "$(t "Details log: ${INSTALL_REPORT_LOG_FILE}" "Details log: ${INSTALL_REPORT_LOG_FILE}")"
    exit 1
  }

  run_logged_if_compact _run_install_hooks "install" "$PLATFORM" || {
    error "$(t 'Hook install failed. Aborting skill install' 'Hook install failed. Aborting skill install')"
    [ -n "${INSTALL_REPORT_LOG_FILE:-}" ] && error "$(t "Details log: ${INSTALL_REPORT_LOG_FILE}" "Details log: ${INSTALL_REPORT_LOG_FILE}")"
    exit 1
  }
  if ! install_compact_output_enabled; then
    info "$(t '[4/5] Hooks: install complete' '[4/5] Hooks: install complete')"
  fi

  run_logged_if_compact sync_commands "sync" "$PLATFORM" || warn "$(t 'Commands sync failed (continuing)' 'Commands sync failed (continuing)')"

  run_logged_if_compact _verify_install_after_copy "$SKILLS_DIR" "$copy_only" "${skills[@]}" || exit 1
  run_logged_if_compact _write_ownership_markers_after_install "$SKILLS_DIR" "$copy_only" "${skills[@]}" || exit 1
  run_logged_if_compact _run_snapshot_after_install
  run_logged_if_compact migrate_addon_state_once || true
  run_logged_if_compact write_addon_sidecars_after_install "$SKILLS_DIR" || exit 1
  run_logged_if_compact write_install_state_manifest "$SKILLS_DIR" "$copy_only" "${skills[@]}" || exit 1

  local report_current="$sync_skipped"
  local report_updated="$sync_updated"
  local report_new="$sync_installed"
  report_write_event "$PLATFORM" "$total_target_count" "$report_current" "$report_updated" "$report_new"

  if install_report_enabled; then
    if live_counter_enabled; then
      if [ "$copy_only" != "1" ]; then
        printf '\r'
        report_skill_sync_line "$report_current" "$report_updated" "$report_new"
        printf '\n'
      fi
      report_print_tail "$PLATFORM" "$visibility"
    else
      report_print_full "$PLATFORM" "$total_target_count" "$report_current" "$report_updated" "$report_new" "$visibility"
    fi
  else
    ok "$(t '[5/5] Verification: install state recorded' '[5/5] Verification: install state recorded')"

    echo ""
    info "$(t '[Ghost-ALICE] User: when local changes are detected during the agent tool update, they are backed up instead of overwritten. Next time you open Claude/Codex, please ask the line below.' '[Ghost-ALICE] User: when local changes are detected during the agent tool update, they are backed up instead of overwritten. Next time you open Claude/Codex, please ask the line below.')"
    echo ""
    echo "    $(t 'Please review backed-up changes.' 'Please review backed-up changes.')"
    echo ""
    info "$(t '[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. Missing, empty, or unparsable manifests pass silently.' '[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. Missing, empty, or unparsable manifests pass silently.')"
  fi
}
