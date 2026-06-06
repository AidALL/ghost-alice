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
  if [ "${#ADDON_TAGS[@]}" -gt 0 ]; then
    error "$(t '--addon-tag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with --addon-source.' '--addon-tag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with --addon-source.')"
    return 1
  fi
  if [ "${#ADDON_SOURCES[@]}" -eq 0 ]; then
    error "$(t '--list-addons requires --addon-source' '--list-addons requires --addon-source')"
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
  _run_install_doctor "doctor" "$SKILLS_DIR" || rc=$?
  _run_install_hooks "status" "$PLATFORM" || rc=$?
  return "$rc"
}

uninstall() {
  local skills=("$@")
  local removed=0
  local skill targets sub_name sub_path target

  if [ "${#skills[@]}" -gt 0 ]; then
    validate_requested_skills "${skills[@]}"
    info "$(t 'Removing selected skills...' 'Removing selected skills...')"
  else
    run_full_uninstall
    return
  fi

  for skill in "${skills[@]}"; do
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

run_full_uninstall() {
  info "$(t 'Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.' 'Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets.')"
  run_uninstall_cleanup
}
