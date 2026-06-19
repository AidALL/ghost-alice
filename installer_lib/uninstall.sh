#!/usr/bin/env bash
# Ghost-ALICE installer library: uninstall
# Sourced by install.sh. Do not execute directly.

prompt_platform() {
  local choice lowered
  if [ ! -t 0 ]; then
    error "$(t '--prompt-platform requires an interactive terminal.' '--prompt-platform requires an interactive terminal.')"
    exit 1
  fi

  while true; do
    echo "$(t 'Select AI tool to install:' 'Select AI tool to install:')"
    echo "  1) Claude Code  (~/.claude/skills)"
    echo "  2) Codex        (~/.agents/skills)"
    echo "  q) $(t 'Cancel' 'Cancel')"
    read -r -p "$(t 'Enter number or name: ' 'Enter number or name: ')" choice
    lowered=$(printf '%s' "$choice" | tr '[:upper:]' '[:lower:]')
    case "$lowered" in
      1|claude|"claude code")
        PLATFORM="claude"
        return 0
        ;;
      2|codex|"openai codex")
        PLATFORM="codex"
        return 0
        ;;
      q|quit|exit|cancel)
        info "$(t 'Installation cancelled.' 'Installation cancelled.')"
        exit 0
        ;;
      *)
        warn "$(t "Unknown input: ${choice}" "Unknown input: ${choice}")"
        echo ""
        ;;
    esac
  done
}

get_skill_description() {
  local skill_md="$1"
  [ -f "$skill_md" ] || return 0
  head -8 "$skill_md" 2>/dev/null \
    | grep 'description:' \
    | sed 's/.*description: *"\{0,1\}//' \
    | sed 's/"\{0,1\}$//' \
    | perl -CSAD -ne 'chomp; if(length($_)>80){print substr($_,0,77)."..."}else{print $_}'
}

list_skills() {
  echo "$(t 'Available skills:' 'Available skills:')"
  echo ""
  local skill targets line sub_name sub_path desc count
  for skill in "${ALL_SKILLS[@]}"; do
    targets=$(expand_skill_targets "$skill")
    if [ -z "$targets" ]; then
      printf "  %-28s %s\n" "$skill" "($(t 'source missing' 'source missing'))"
      continue
    fi
    count=$(printf "%s\n" "$targets" | wc -l | tr -d ' ')
    # Family detection: first name differs from the skill itself, or count > 1.
    local first_name
    first_name=$(printf "%s\n" "$targets" | head -1 | cut -d'|' -f1)
    if [ "$first_name" = "$skill" ] && [ "$count" = "1" ]; then
      sub_path=$(printf "%s\n" "$targets" | head -1 | cut -d'|' -f2)
      desc=$(get_skill_description "${sub_path}/SKILL.md")
      printf "  %-28s %s\n" "$skill" "$desc"
    else
      printf "  %s ($(t 'family' 'family'), %s$(t ' sub-skills' ' sub-skills'))\n" "$skill" "$count"
      while IFS='|' read -r sub_name sub_path; do
        [ -n "$sub_name" ] || continue
        desc=$(get_skill_description "${sub_path}/SKILL.md")
        printf "    └ %-26s %s\n" "$sub_name" "$desc"
      done <<< "$targets"
    fi
  done
}

list_addons() {
  if [ "$ADDON_SKIP" = "1" ]; then
    info "$(t 'Addon installation is disabled (--addon-skip)' 'Addon installation is disabled (--addon-skip)')"
    return 0
  fi
  if [ "${#ADDON_SOURCES[@]}" -eq 0 ]; then
    error "$(t '--list-addons requires --addon-source' '--list-addons requires --addon-source')"
    return 1
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
  "$py" "${SCRIPT_DIR}/_shared/addon_installer.py" "${args[@]}" --platform "$PLATFORM" --format text
}

print_target_status() {
  local label="$1"
  local install_path="$2"
  local indent="${3:-  }"
  if [ -L "$install_path" ]; then
    local target
    target=$(readlink "$install_path" 2>/dev/null || echo "?")
    echo -e "${indent}${GREEN}●${NC} ${label}  →  ${target}"
  elif [ -d "$install_path" ]; then
    echo -e "${indent}${YELLOW}■${NC} ${label}  ($(t 'copied' 'copied'))"
  else
    echo -e "${indent}${RED}○${NC} ${label}  ($(t 'not installed' 'not installed'))"
  fi
}

_run_install_doctor() {
  local mode="${1:-status}"
  local skills_dir="${2:-$SKILLS_DIR}"
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    if [ "$mode" = "doctor" ]; then
      error "$(t 'Python 3.11+ not found; installer doctor cannot run' 'Python 3.11+ not found; installer doctor cannot run')"
      return 1
    fi
    warn "$(t 'Python 3.11+ not found; skipping installer doctor diagnostics' 'Python 3.11+ not found; skipping installer doctor diagnostics')"
    return 0
  fi

  local source_root
  source_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$SCRIPT_DIR")"
  local args=(
    --platform "$PLATFORM"
    --repo-root "$source_root"
    --encoding-root "$SCRIPT_DIR"
    --encoding-root "$skills_dir"
    --ghost-alice-root "${HOME}/.ghost-alice"
    --install-state-manifest "${HOME}/.ghost-alice/install-state/${PLATFORM}.json"
    --skills-root "$skills_dir"
  )
  [ "$mode" = "doctor" ] && args+=(--strict)

  case "$PLATFORM" in
    codex)
      args+=(
        --global-rule "codex-bootstrap" "$(resolve_codex_home)/AGENTS.md"
        "$CODEX_BOOTSTRAP_MARKER" "$CODEX_MANAGED_BLOCK_BEGIN" "$CODEX_MANAGED_BLOCK_END"
      )
      ;;
  esac

  if [ -d "${SCRIPT_DIR}/_shared" ]; then
    args+=(--target "_shared" "${skills_dir}/_shared")
  fi

  local skill targets sub_name sub_path
  for skill in "${ALL_SKILLS[@]}"; do
    targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      args+=(--target "$sub_name" "${skills_dir}/${sub_name}")
    done <<< "$targets"
  done

  echo ""
  info "$(t 'Running installer doctor diagnostics...' 'Running installer doctor diagnostics...')"
  "$py" "${SCRIPT_DIR}/_shared/install_doctor.py" "${args[@]}"
}

check_status() {
  echo "$(t "Install status (${SKILLS_DIR}):" "Install status (${SKILLS_DIR}):")"
  echo ""
  print_target_status "_shared" "${SKILLS_DIR}/_shared"
  local skill targets first_name count sub_name sub_path
  for skill in "${ALL_SKILLS[@]}"; do
    targets=$(expand_skill_targets "$skill")
    if [ -z "$targets" ]; then
      echo -e "  ${RED}○${NC} ${skill}  ($(t 'source missing' 'source missing'))"
      continue
    fi
    count=$(printf "%s\n" "$targets" | wc -l | tr -d ' ')
    first_name=$(printf "%s\n" "$targets" | head -1 | cut -d'|' -f1)
    if [ "$first_name" = "$skill" ] && [ "$count" = "1" ]; then
      print_target_status "$skill" "${SKILLS_DIR}/${skill}"
    else
      echo "  ${skill} ($(t 'family' 'family'))"
      while IFS='|' read -r sub_name sub_path; do
        [ -n "$sub_name" ] || continue
        print_target_status "$sub_name" "${SKILLS_DIR}/${sub_name}" "    "
      done <<< "$targets"
    fi
  done

  _run_install_doctor "status" "$SKILLS_DIR"
  _run_install_hooks "status" "$PLATFORM"
}

run_doctor() {
  echo "$(t "Installer doctor (${SKILLS_DIR}):" "Installer doctor (${SKILLS_DIR}):")"
  local rc=0

  # Self-heal like the uninstall path: finish any interrupted addon uninstall.
  # A marker left behind afterwards means the uninstall is genuinely stuck (e.g.
  # a user-modified target kept for manual review), so doctor must surface it
  # instead of reporting overall ok.
  _resume_pending_addon_uninstalls
  local addons_dir="${HOME}/.ghost-alice/addons/${PLATFORM}"
  if [ -d "$addons_dir" ]; then
    local leftover
    leftover="$(find "$addons_dir" -maxdepth 1 -name '*.json.removing' 2>/dev/null | head -1)"
    if [ -n "$leftover" ]; then
      warn "$(t 'A pending addon uninstall could not complete (manual review needed); see .removing markers under ~/.ghost-alice/addons' 'A pending addon uninstall could not complete (manual review needed); see .removing markers under ~/.ghost-alice/addons')"
      rc=1
    fi
  fi

  _run_install_doctor "doctor" "$SKILLS_DIR" || rc=$?
  _run_install_hooks "status" "$PLATFORM" || rc=$?
  return "$rc"
}

_resume_pending_addon_uninstalls() {
  local py log
  py="$(_find_python_runtime || true)"
  [ -n "$py" ] || return 0
  [ -d "${HOME}/.ghost-alice/addons/${PLATFORM}" ] || return 0
  # Capture output: a failed pending-uninstall resume is a recovery operation
  # whose diagnostics must survive, not vanish into /dev/null.
  log="${HOME}/.ghost-alice/addon-resume.log"
  "$py" "${SCRIPT_DIR}/_shared/addon_uninstall.py" --resume-pending \
    --addons-dir "${HOME}/.ghost-alice/addons/${PLATFORM}" --skills-dir "$SKILLS_DIR" \
    --skills-dir "$(resolve_claude_home)/commands" \
    --skills-dir "${HOME}/.ghost-alice/resources/${PLATFORM}" \
    --platform "$PLATFORM" --confirm >"$log" 2>&1 || true
}

uninstall() {
  local args=("$@")
  local addon_ids=() skills=() force=0
  local arg_index=0
  while [ "$arg_index" -lt "${#args[@]}" ]; do
    case "${args[$arg_index]}" in
      --addon)
        arg_index=$((arg_index + 1))
        if [ "$arg_index" -ge "${#args[@]}" ]; then
          error "$(t '--addon requires an addon id' '--addon requires an addon id')"
          exit 1
        fi
        addon_ids+=("${args[$arg_index]}")
        ;;
      --force) force=1 ;;
      *) skills+=("${args[$arg_index]}") ;;
    esac
    arg_index=$((arg_index + 1))
  done

  # Snapshot which requested addons exist (sidecar or pending .removing marker)
  # BEFORE resume runs. An addon that resume finishes for us must count as
  # removed, not as a spurious unknown-addon failure when the explicit pass below
  # then finds nothing.
  local _adir="${HOME}/.ghost-alice/addons/${PLATFORM}"
  local known_before=" "
  local _kid
  for _kid in ${addon_ids[@]+"${addon_ids[@]}"}; do
    if [ -f "${_adir}/${_kid}.json" ] || [ -f "${_adir}/${_kid}.json.removing" ]; then
      known_before="${known_before}${_kid} "
    fi
  done

  _resume_pending_addon_uninstalls

  local removed=0
  local failed=0
  local skill targets sub_name sub_path target py aid
  py="$(_find_python_runtime || true)"

  if [ "${#addon_ids[@]}" -gt 0 ]; then
    if [ -z "$py" ]; then
      error "$(t 'Python 3.11+ not found; cannot uninstall addons' 'Python 3.11+ not found; cannot uninstall addons')"
      exit 1
    fi
    local arc
    for aid in "${addon_ids[@]}"; do
      arc=0
      "$py" "${SCRIPT_DIR}/_shared/addon_uninstall.py" --addon-id "$aid" \
          --addons-dir "${_adir}" --skills-dir "$SKILLS_DIR" \
          --skills-dir "$(resolve_claude_home)/commands" \
          --skills-dir "${HOME}/.ghost-alice/resources/${PLATFORM}" \
          --platform "$PLATFORM" --confirm || arc=$?
      if [ "$arc" -eq 0 ]; then
        removed=$((removed + 1))
      elif [ "$arc" -eq 1 ]; then
        # rc 1 = not-found. If a pending uninstall for this addon existed before
        # resume, resume already completed it -> success; otherwise it was never
        # installed -> a real failure the caller must see.
        case "$known_before" in
          *" $aid "*) removed=$((removed + 1)) ;;
          *) failed=$((failed + 1)); warn "$(t "Addon ${aid}: not installed (nothing to remove)" "Addon ${aid}: not installed (nothing to remove)")" ;;
        esac
      else
        # rc >= 2 = partial / manual-review / refused: items were preserved.
        failed=$((failed + 1))
        warn "$(t "Addon ${aid}: uninstall left items for manual review" "Addon ${aid}: uninstall left items for manual review")"
      fi
    done
  fi

  if [ "${#skills[@]}" -eq 0 ]; then
    if [ "${#addon_ids[@]}" -gt 0 ]; then
      [ "$removed" -gt 0 ] && ok "$(t "${removed} addon(s) removed." "${removed} addon(s) removed.")" || info "$(t 'No addon targets to remove.' 'No addon targets to remove.')"
      [ "$failed" -gt 0 ] && return 1
      return 0
    fi
    run_full_uninstall
    return
  fi

  validate_requested_skills "${skills[@]}"
  info "$(t 'Removing selected skills...' 'Removing selected skills...')"

  for skill in "${skills[@]}"; do
    if [ -n "$py" ] && [ "$force" -eq 0 ]; then
      local dep_rc=0
      "$py" "${SCRIPT_DIR}/_shared/addon_uninstall.py" --dependents "$skill" \
        --addons-dir "${HOME}/.ghost-alice/addons/${PLATFORM}" >/dev/null 2>&1 || dep_rc=$?
      if [ "$dep_rc" -eq 2 ]; then
        warn "$(t "Skipping ${skill}: an installed addon depends on it (use --force to override)" "Skipping ${skill}: an installed addon depends on it (use --force to override)")"
        continue
      elif [ "$dep_rc" -ne 0 ]; then
        warn "$(t "Dependency check failed for ${skill}; proceeding with removal" "Dependency check failed for ${skill}; proceeding with removal")"
      fi
    fi
    targets=$(expand_skill_targets "$skill")
    if [ -z "$targets" ]; then
      target="${SKILLS_DIR}/${skill}"
      if remove_installed_target "$skill" "$target"; then
        removed=$((removed + 1))
      fi
      continue
    fi
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      target="${SKILLS_DIR}/${sub_name}"
      if remove_installed_target "$sub_name" "$target"; then
        removed=$((removed + 1))
      fi
    done <<< "$targets"
  done

  if cleanup_shared_if_unused "${SKILLS_DIR}"; then
    removed=$((removed + 1))
  fi

  if [ "$PLATFORM" = "codex" ] && remove_codex_bootstrap_if_unused "${SKILLS_DIR}"; then
    removed=$((removed + 1))
  fi

  if [ "$removed" -eq 0 ]; then
    info "$(t 'No skills to remove.' 'No skills to remove.')"
  else
    ok "$(t "${removed} item(s) removed." "${removed} item(s) removed.")"
  fi

  if has_managed_installs "$SKILLS_DIR"; then
    info "$(t 'Managed skills remain; keeping hooks installed.' 'Managed skills remain; keeping hooks installed.')"
  else
    _run_install_hooks "uninstall" "$PLATFORM" || {
      error "$(t 'Hook removal failed. Aborting skill removal' 'Hook removal failed. Aborting skill removal')"
      exit 1
    }
  fi

  # Propagate any per-addon failure from the combined addon+skill path.
  [ "$failed" -gt 0 ] && return 1
  return 0
}

run_uninstall_cleanup() {
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; uninstall cleanup cannot run' 'Python 3.11+ not found; uninstall cleanup cannot run')"
    exit 1
  fi

  local args=(
    --platform "$PLATFORM"
    --install-state-manifest "${HOME}/.ghost-alice/install-state/${PLATFORM}.json"
    --confirm
  )

  "$py" "${SCRIPT_DIR}/_shared/uninstall_cleanup.py" "${args[@]}"
}

_uninstall_all_addons_before_full() {
  # Full uninstall must not orphan addon commands/resources (addon review): the
  # install-state cleanup classifies skill-root dirs only and leaves command/
  # resource FILES behind. Remove every installed addon via the hash-gated
  # per-addon path (with the commands + resources allowed roots) BEFORE the
  # cleanup wipes the sidecar registry that points at them.
  local py adir sidecar aid failed=0
  py="$(_find_python_runtime || true)"
  [ -n "$py" ] || return 0
  adir="${HOME}/.ghost-alice/addons/${PLATFORM}"
  [ -d "$adir" ] || return 0
  for sidecar in "$adir"/*.json; do
    [ -e "$sidecar" ] || continue
    aid="$(basename "$sidecar" .json)"
    [[ "$aid" =~ ^[a-z][a-z0-9-]*$ ]] || continue
    if ! "$py" "${SCRIPT_DIR}/_shared/addon_uninstall.py" --addon-id "$aid" \
      --addons-dir "$adir" --skills-dir "$SKILLS_DIR" \
      --skills-dir "$(resolve_claude_home)/commands" \
      --skills-dir "${HOME}/.ghost-alice/resources/${PLATFORM}" \
      --platform "$PLATFORM" --confirm >/dev/null 2>&1; then
      failed=1
      warn "$(t "Full uninstall preserved addon ${aid}; manual review is required before sidecar cleanup" "Full uninstall preserved addon ${aid}; manual review is required before sidecar cleanup")"
    fi
  done
  return "$failed"
}

run_full_uninstall() {
  info "$(t 'Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.' 'Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.')"
  _resume_pending_addon_uninstalls
  if ! _uninstall_all_addons_before_full; then
    error "$(t 'Full uninstall stopped because one or more addon targets need manual review.' 'Full uninstall stopped because one or more addon targets need manual review.')"
    return 1
  fi
  run_uninstall_cleanup
}
