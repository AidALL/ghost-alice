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
#   bash install.sh --platform codex --uninstall hwpx  # Remove selected skills from one platform
#   bash install.sh --list       # List available skills
#   bash install.sh --addon-source ./ghost-alice-addons --list-addons
#   bash install.sh --status     # Check current install status

if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  printf '%s\n' "[ERROR] install.sh requires bash. Run it as: bash install.sh" >&2
  exit 1
fi

set -euo pipefail

ORIGINAL_LOCALE="${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}"

# ── Force UTF-8 locale ────────────────────────────────────
# Ensures output is not garbled on macOS/Linux/WSL.
# If UTF-8 locale is already set, leave it; otherwise find and apply an available UTF-8 locale.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Source installer_lib modules. Function definitions live in installer_lib/*.sh and
# are sourced here (before any top-level call) so install.sh stays a thin entrypoint.
for _lib in "${SCRIPT_DIR}"/installer_lib/*.sh; do [ -f "$_lib" ] && source "$_lib"; done; unset _lib
ensure_utf8_locale
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
PROJECT_DISPLAY_NAME="Ghost-ALICE"
PLATFORM="claude"
PLATFORM_EXPLICIT=0
PROMPT_PLATFORM=0
AUTO_DETECT=0
SKIP_SOURCE_HEALTH=0
UPDATE_SOURCE=0
CLEANUP_PENDING=0
ADDON_SKIP=0
LIST_ADDONS=0
AGENT_VISIBILITY=""
VERBOSE=0
INSTALL_REPORT_CHILD="${GHOST_ALICE_INSTALL_REPORT_CHILD:-0}"
INSTALL_REPORT_LOG_FILE="${GHOST_ALICE_INSTALL_LOG_FILE:-}"
INSTALL_REPORT_EVENT_FILE="${GHOST_ALICE_INSTALL_EVENT_FILE:-}"
INSTALL_LOCK_PATH=""
INSTALL_LOCK_HELD=0
SOURCE_REPO_HOOK_CHANGED=0
SOURCE_REPO_HOOK_BEFORE_PRESENT=0
SOURCE_REPO_HOOK_BEFORE=""
SOURCE_REPO_HOOK_AFTER="hooks"
ADDON_SOURCES=()
ADDON_TAGS=()
INSTALL_ADDON_TARGETS=()
# ── Platform path resolution ──────────────────────────────
# Environment variable takes precedence; falls back to default path.
#   Claude Code: CLAUDE_CONFIG_DIR → ~/.claude
#   Codex:       CODEX_HOME → ~/.codex










SKILLS_DIR="$(resolve_claude_home)/skills"






# ── Git hooks (post-merge auto-refresh) ───────────────────
# Point this repo's Git hook path to the tracked hooks/ directory so
# `git pull --ff-only` automatically triggers hooks/post-merge, which in turn
# re-runs install.sh --platform codex only on copy-mode runtimes.

ALL_SKILLS=(
  adversarial-verification
  boundary-contract
  coding-convention
  skill-evolution
  agent-security-scan
  jailbreak-detector
  merge-companion
  necessity-gate
  session-intent-analyzer
  compact-handoff
  task-router
)

DEPRECATED_INSTALLED_SKILLS=(
  harness-security-scan
  session-intent-guard
)

# ── Skill target expansion ────────────────────────────────
# Equivalent to Expand-SkillTargets in install.ps1.
# Regular skill (root SKILL.md exists) → one "<name>|<source_path>" line
# Family skill (no root SKILL.md, SKILL.md in direct sub-directories) → N sub-skill lines
# If not found or no SKILL.md present → no output



# ── _shared install (symlink preferred, copy fallback) ────






# ── Colors when the terminal supports them ─────────────────
if [ -t 1 ]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi
































# ── Message helper ───────────────────────────────
# Installer messages are English-only.

CODEX_BOOTSTRAP_MARKER="# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN="<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END="<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CODEX_BOOTSTRAP_SOURCE="${SCRIPT_DIR}/platforms/codex/AGENTS.md"
SESSION_GATE_CONTRACT_SOURCE="${SCRIPT_DIR}/skill-catalog/session-gates.json"

# ── Command sync (skills.json SSOT, multi-platform) ────────
# Validate platform command files from skills.json and generate missing entries.
#   Claude Code: .claude/commands/*.md  (@path + $ARGUMENTS)
#   Codex:       Not needed (skills are discovered directly through /skills or $ references)
# Runs automatically during install; can also run independently with --sync-commands.



assert_session_gate_contract






# ── Description extraction (multibyte-safe through perl) ───

# ── Skill list output ──────────────────────────────────────


# ── Single-target status line ──────────────────────────────


# ── Install status check ───────────────────────────────────


# ── Uninstall ──────────────────────────────────────────────



# ── UserPromptSubmit hook install/remove ──────────────────
# Require Python 3.11+. Partial fallback violates the freshness contract.
# Any failure blocks the whole install.





# Python bootstrap and guidance when Python is missing (v1.3.0 removed node.js/bash fallbacks).
# Keep install.sh-level bootstrap messages ASCII English to avoid encoding corruption.
# User-facing messages are emitted by install_hooks.py under Python UTF-8.























# ── Install helpers ────────────────────────────────────────

# ── Install ────────────────────────────────────────────────
# ── Argument parsing ───────────────────────────────────────
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform|-p)
      if [[ $# -lt 2 ]]; then
        error "$(t '--platform requires a value (claude|codex)' '--platform requires a value (claude|codex)')"
        exit 1
      fi
      PLATFORM="$2"
      PLATFORM_EXPLICIT=1
      AUTO_DETECT=0
      if [[ "$PLATFORM" != "claude" && "$PLATFORM" != "codex" ]]; then
        error "$(t "Unknown platform: $PLATFORM (must be claude or codex)" "Unknown platform: $PLATFORM (must be claude or codex)")"
        exit 1
      fi
      shift 2
      ;;
    --prompt-platform)
      PROMPT_PLATFORM=1
      AUTO_DETECT=0
      shift
      ;;
    --auto|-a)
      # Backward-compatible no-op. Plain install is auto-detect by default.
      shift
      ;;
    --skip-source-health)
      SKIP_SOURCE_HEALTH=1
      shift
      ;;
    --update-source)
      UPDATE_SOURCE=1
      AUTO_DETECT=0
      shift
      ;;
    --cleanup-pending)
      CLEANUP_PENDING=1
      shift
      ;;
    --addon-source)
      if [[ $# -lt 2 ]]; then
        error "$(t '--addon-source requires a value' '--addon-source requires a value')"
        exit 1
      fi
      ADDON_SOURCES+=("$2")
      shift 2
      ;;
    --addon-tag)
      if [[ $# -lt 2 ]]; then
        error "$(t '--addon-tag requires a value' '--addon-tag requires a value')"
        exit 1
      fi
      ADDON_TAGS+=("$2")
      shift 2
      ;;
    --addon-skip)
      ADDON_SKIP=1
      shift
      ;;
    --list-addons)
      LIST_ADDONS=1
      shift
      ;;
    --verbose|-v)
      VERBOSE=1
      shift
      ;;
    --visibility|--agent-visibility)
      flag_name="$1"
      if [[ $# -lt 2 ]]; then
        error "$(t "${flag_name} requires a value (strict|dynamic|minimal)" "${flag_name} requires a value (strict|dynamic|minimal)")"
        exit 1
      fi
      AGENT_VISIBILITY="$2"
      if [[ "$AGENT_VISIBILITY" != "strict" && "$AGENT_VISIBILITY" != "dynamic" && "$AGENT_VISIBILITY" != "minimal" ]]; then
        error "$(t "Unknown agent visibility profile: $AGENT_VISIBILITY (must be strict, dynamic, or minimal)" "Unknown agent visibility profile: $AGENT_VISIBILITY (must be strict, dynamic, or minimal)")"
        exit 1
      fi
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

# Default install: when no platform is specified, install to all detected tools.
# Plain full uninstall also detects platform homes and install-state manifests for full cleanup.
# Read-only, diagnostics, and partial removal commands keep the single-platform default (claude).
if [ "$PLATFORM_EXPLICIT" -eq 0 ] && [ "$PROMPT_PLATFORM" -eq 0 ] && [ "$CLEANUP_PENDING" -eq 0 ] && [ "$LIST_ADDONS" -eq 0 ] && [ "$UPDATE_SOURCE" -eq 0 ]; then
  case "${ARGS[0]:-}" in
    ""|--help|-h|--list|-l|--status|-s|--doctor|--uninstall|--remove|-u|--sync-commands|--check-commands|--*)
      ;;
    *)
      AUTO_DETECT=1
      ;;
  esac
  case "${ARGS[0]:-}" in
    "")
      AUTO_DETECT=1
      ;;
  esac
fi

plain_full_uninstall_args=0
case "${ARGS[0]:-}" in
  --uninstall|--remove|-u)
    [ "${#ARGS[@]}" -eq 1 ] && plain_full_uninstall_args=1
    ;;
esac

if [ "$plain_full_uninstall_args" -eq 1 ] && [ "$PLATFORM_EXPLICIT" -eq 0 ] && [ "$PROMPT_PLATFORM" -eq 0 ]; then
  detected=()
  while IFS= read -r plat; do
    [ -n "$plat" ] && detected+=("$plat")
  done < <(detect_uninstall_platforms)
  if [ ${#detected[@]} -eq 0 ]; then
    warn "$(t 'No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json.' 'No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json.')"
    exit 0
  fi
  rc=0
  platform_index=0
  info "$(t "Detected uninstall targets: ${detected[*]}" "Detected uninstall targets: ${detected[*]}")"
  for plat in "${detected[@]}"; do
    platform_index=$((platform_index + 1))
    echo ""
    info "$(t "[uninstall] (${platform_index}/${#detected[@]}) Starting ${plat} full cleanup" "[uninstall] (${platform_index}/${#detected[@]}) Starting ${plat} full cleanup")"
    if ! bash "${BASH_SOURCE[0]}" --platform "$plat" --uninstall; then
      rc=1
    fi
  done
  exit "$rc"
fi

if [ "$UPDATE_SOURCE" -eq 1 ]; then
  update_source_checkout
  exit $?
fi

# auto/default: detect ~/.claude and ~/.codex, then recurse per platform.
# Auto child installs are separate install path operations, not a duplicate install.
# Legacy-readable log shape: [auto] (${platform_index}/${#detected[@]}) <platform>
if [ "$AUTO_DETECT" -eq 1 ]; then
  detected=()
  while IFS= read -r plat; do
    [ -n "$plat" ] && detected+=("$plat")
  done < <(detect_present_platforms)
  if [ ${#detected[@]} -eq 0 ]; then
    warn "$(t 'No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.' 'No AI platform home directories detected. At least one of ~/.claude, ~/.codex is required.')"
    exit 0
  fi
  source_health_args=()
  [ "$SKIP_SOURCE_HEALTH" -eq 1 ] && source_health_args+=(--skip-source-health)
  agent_visibility_args=()
  [ -n "$AGENT_VISIBILITY" ] && agent_visibility_args+=(--visibility "$AGENT_VISIBILITY")
  verbose_args=()
  [ "$VERBOSE" -eq 1 ] && verbose_args+=(--verbose)
  addon_args=()
  for source in ${ADDON_SOURCES[@]+"${ADDON_SOURCES[@]}"}; do
    addon_args+=(--addon-source "$source")
  done
  for tag in ${ADDON_TAGS[@]+"${ADDON_TAGS[@]}"}; do
    addon_args+=(--addon-tag "$tag")
  done
  [ "$ADDON_SKIP" -eq 1 ] && addon_args+=(--addon-skip)

  if ! install_compact_output_enabled; then
    rc=0
    platform_index=0
    info "$(t "Detected platforms: ${detected[*]}" "Detected platforms: ${detected[*]}")"
    info "$(t "Auto install will process ${#detected[@]} detected platform target(s) in sequence; each has a separate install path (not a duplicate install)." "Auto install will process ${#detected[@]} detected platform target(s) in sequence; each has a separate install path (not a duplicate install).")"
    for plat in "${detected[@]}"; do
      platform_index=$((platform_index + 1))
      echo ""
      info "$(t "[auto] (${platform_index}/${#detected[@]}) Starting install for ${plat}" "[auto] (${platform_index}/${#detected[@]}) Starting install for ${plat}")"
      if bash "${BASH_SOURCE[0]}" --platform "$plat" "${source_health_args[@]+"${source_health_args[@]}"}" "${agent_visibility_args[@]+"${agent_visibility_args[@]}"}" "${verbose_args[@]+"${verbose_args[@]}"}" "${addon_args[@]+"${addon_args[@]}"}" "${ARGS[@]+"${ARGS[@]}"}"; then
        :
      else
        child_rc=$?
        rc=1
        warn "$(t "[auto] Install failed for ${plat} (exit code $child_rc)" "[auto] Install failed for ${plat} (exit code $child_rc)")"
      fi
    done
    echo ""
    if [ "$rc" -eq 0 ]; then
      ok "$(t "[auto] Install complete for all ${#detected[@]} detected install target(s)" "[auto] Install complete for all ${#detected[@]} detected install target(s)")"
    else
      error "$(t '[auto] Some platform installs failed. Check the log above.' '[auto] Some platform installs failed. Check the log above.')"
    fi
    exit "$rc"
  fi

  install_log_init
  auto_event_file="${INSTALL_REPORT_LOG_FILE%.log}.events.jsonl"
  : >"$auto_event_file"
  rc=0
  auto_current=0
  auto_updated=0
  auto_new=0
  auto_synced_targets=0
  auto_skills=(${ARGS[@]+"${ARGS[@]}"})
  if [ "${#auto_skills[@]}" -eq 0 ]; then
    auto_skills=("${ALL_SKILLS[@]}")
  fi
  auto_common_targets="$(count_skill_targets "${auto_skills[@]}")"
  [ -d "${SCRIPT_DIR}/_shared" ] && auto_common_targets=$((auto_common_targets + 1))
  auto_platform_count="${#detected[@]}"
  auto_total_operations=$((auto_common_targets * auto_platform_count))
  auto_completed_operations=0
  auto_displayed_operations=0
  auto_platform_label="$(join_by ', ' "${detected[@]}")"
  auto_visibility="$(resolve_effective_visibility)"
  if live_counter_enabled; then
    report_print_auto_start "$auto_platform_label" "$auto_common_targets" "$auto_visibility" "$auto_platform_count"
  fi
  platform_index=0
  for plat in "${detected[@]}"; do
    platform_index=$((platform_index + 1))
    printf '\n[auto] (%s/%s) Starting install for %s (separate install path; not a duplicate install)\n' "$platform_index" "${#detected[@]}" "$plat" >>"$INSTALL_REPORT_LOG_FILE"
    GHOST_ALICE_INSTALL_REPORT_CHILD=1 \
      GHOST_ALICE_INSTALL_LOG_FILE="$INSTALL_REPORT_LOG_FILE" \
      GHOST_ALICE_INSTALL_EVENT_FILE="$auto_event_file" \
      bash "${BASH_SOURCE[0]}" --platform "$plat" "${source_health_args[@]+"${source_health_args[@]}"}" "${agent_visibility_args[@]+"${agent_visibility_args[@]}"}" "${verbose_args[@]+"${verbose_args[@]}"}" "${addon_args[@]+"${addon_args[@]}"}" "${ARGS[@]+"${ARGS[@]}"}" >>"$INSTALL_REPORT_LOG_FILE" 2>&1 &
    child_pid=$!
    if live_counter_enabled; then
      while jobs -pr | grep -q "^${child_pid}$"; do
        auto_completed_operations="$(report_read_target_operation_progress "$auto_event_file")"
        report_auto_animate_target_operation_progress_line \
          "$auto_displayed_operations" \
          "$auto_completed_operations" \
          "$auto_total_operations" \
          "${plat} ${platform_index}/${auto_platform_count}"
        auto_displayed_operations="$auto_completed_operations"
        sleep 0.1
      done
    fi
    if wait "$child_pid"; then
      :
    else
      child_rc=$?
      rc=1
      warn "$(t "[auto] Install failed for ${plat} (exit code $child_rc)" "[auto] Install failed for ${plat} (exit code $child_rc)")"
    fi
    if live_counter_enabled; then
      auto_completed_operations="$(report_read_target_operation_progress "$auto_event_file")"
      report_auto_animate_target_operation_progress_line \
        "$auto_displayed_operations" \
        "$auto_completed_operations" \
        "$auto_total_operations" \
        "${plat} ${platform_index}/${auto_platform_count}"
      auto_displayed_operations="$auto_completed_operations"
    fi
  done
  auto_synced_targets="$(report_read_all_common_target_progress "$auto_event_file" "$auto_platform_count")"
  if [ "$rc" -eq 0 ]; then
    if live_counter_enabled; then
      auto_completed_operations="$(report_read_target_operation_progress "$auto_event_file")"
      report_auto_animate_target_operation_progress_line \
        "$auto_displayed_operations" \
        "$auto_completed_operations" \
        "$auto_total_operations" \
        "done"
      [ "$auto_completed_operations" -gt "$auto_total_operations" ] && auto_completed_operations="$auto_total_operations"
      auto_displayed_operations="$auto_completed_operations"
      report_auto_update_target_operation_progress_line \
        "$auto_displayed_operations" \
        "$auto_total_operations" \
        "done"
      printf '\n'
      report_print_tail "$auto_platform_label" "$auto_visibility"
    else
      report_print_auto_full "$auto_platform_label" "$auto_common_targets" "$auto_visibility" "$auto_synced_targets"
    fi
  else
    live_counter_enabled && printf '\n'
    error "$(t '[auto] Some platform installs failed. Check the log above.' '[auto] Some platform installs failed. Check the log above.')"
    error "$(t "Details log: ${INSTALL_REPORT_LOG_FILE}" "Details log: ${INSTALL_REPORT_LOG_FILE}")"
  fi
  exit $rc
fi

if [ "$PROMPT_PLATFORM" -eq 1 ] && [ "$PLATFORM_EXPLICIT" -eq 0 ]; then
  prompt_platform
elif [ "$PROMPT_PLATFORM" -eq 1 ] && [ "$PLATFORM_EXPLICIT" -eq 1 ]; then
  warn "$(t '--prompt-platform ignored because --platform is already specified.' '--prompt-platform ignored because --platform is already specified.')"
fi

# Platform-specific skill directory setup.
if [ "$PLATFORM" = "codex" ]; then
  SKILLS_DIR="$(resolve_codex_skills_dir)"
fi

if [ "$CLEANUP_PENDING" -eq 1 ]; then
  cleanup_pending_false_positives
  exit $?
fi

if [ "$LIST_ADDONS" -eq 1 ]; then
  list_addons
  exit $?
fi

# ── Main ───────────────────────────────────────────────────
case "${ARGS[0]:-}" in
  --list|-l)
    list_skills
    ;;
  --status|-s)
    check_status
    ;;
  --doctor)
    run_doctor
    ;;
  "")
    setup_git_hooks
    _ensure_python_runtime_for_install || exit 1
    _acquire_install_lock
    trap _release_install_lock EXIT
    install "${ARGS[@]+"${ARGS[@]}"}"
    _release_install_lock
    trap - EXIT
    ;;
  --uninstall|--remove|-u)
    uninstall "${ARGS[@]:1}"
    ;;
  --sync-commands)
    sync_commands "sync" "$PLATFORM"
    ;;
  --check-commands)
    sync_commands "check" "$PLATFORM"
    ;;
  --help|-h)
    echo "Usage:"
    echo "  bash install.sh                              # $(t 'Install to detected AI tools' 'Install to detected AI tools')"
    echo "  bash install.sh --platform claude            # $(t 'Install only to Claude Code' 'Install only to Claude Code')"
    echo "  bash install.sh --platform codex             # $(t 'Install to Codex' 'Install to Codex')"
    echo ""
    echo "$(t 'Common commands:' 'Common commands:')"
    echo "  --platform, -p PLAT    $(t 'Choose claude | codex' 'Choose claude | codex')"
    echo "  --prompt-platform      $(t 'Ask which AI tool to install' 'Ask which AI tool to install')"
    echo "  --status, -s           $(t 'Show install status' 'Show install status')"
    echo "  --visibility strict|dynamic|minimal"
    echo "                          $(t 'Set user-facing governance message visibility profile' 'Set user-facing governance message visibility profile')"
    echo "  --agent-visibility strict|dynamic|minimal"
    echo "                          $(t 'Alias for --visibility' 'Alias for --visibility')"
    echo "  --doctor               $(t 'Run install diagnostics' 'Run install diagnostics')"
    echo "  --verbose, -v          $(t 'Show per-target install details' 'Show per-target install details')"
    echo ""
    echo "$(t 'Removal commands:' 'Removal commands:')"
    echo "  --uninstall, -u        $(t 'Full uninstall: remove managed hooks, bootstrap, support state, and install targets' 'Full uninstall: remove managed hooks, bootstrap, support state, and install targets')"
    echo "  --platform PLAT --uninstall SKILL_NAMES"
    echo "                          $(t 'Remove selected skills from one platform' 'Remove selected skills from one platform')"
    echo ""
    echo "$(t 'Advanced/operator commands:' 'Advanced/operator commands:')"
    echo "  SKILL_NAMES            $(t 'Install specified skills only' 'Install specified skills only')"
    echo "  --list, -l             $(t 'List available skills' 'List available skills')"
    echo "  --cleanup-pending      $(t 'Clean false-positive legacy pending entries' 'Clean false-positive legacy pending entries')"
    echo "  --update-source        $(t 'Stash source checkout local changes and run git pull --ff-only' 'Stash source checkout local changes and run git pull --ff-only')"
    echo "  --addon-source PATH    $(t 'Add addon repo or local manifest path' 'Add addon repo or local manifest path')"
    echo "  --addon-tag TAG        $(t 'Reserved: check out tag locally for now' 'Reserved: check out tag locally for now')"
    echo "  --addon-skip           $(t 'Disable addon installation' 'Disable addon installation')"
    echo "  --list-addons          $(t 'List addon manifest targets' 'List addon manifest targets')"
    echo "  --skip-source-health   $(t 'Skip source health gate' 'Skip source health gate')"
    echo "  --sync-commands        $(t 'Sync workspace commands' 'Sync workspace commands')"
    echo "  --check-commands       $(t 'Check workspace commands only' 'Check workspace commands only')"
    echo "  --help, -h             $(t 'Show this help' 'Show this help')"
    ;;
  --*)
    error "$(t "Unknown option: ${ARGS[0]}" "Unknown option: ${ARGS[0]}")"
    echo "$(t 'See bash install.sh --help' 'See bash install.sh --help')"
    exit 1
    ;;
  *)
    _ensure_python_runtime_for_install || exit 1
    setup_git_hooks
    _acquire_install_lock
    trap _release_install_lock EXIT
    install "${ARGS[@]+"${ARGS[@]}"}"
    _release_install_lock
    trap - EXIT
    ;;
esac
