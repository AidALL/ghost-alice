#!/usr/bin/env bash
# Ghost-ALICE installer library: report
# Sourced by install.sh. Do not execute directly.

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }

ok()    { echo -e "${GREEN}[OK]${NC} $*"; }

warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

error() { echo -e "${RED}[ERROR]${NC} $*"; }

resolve_effective_visibility() {
  # Report the visibility the runtime will actually use: an explicit --visibility
  # flag wins, else the existing config profile, else the dynamic default.
  if [ -n "${AGENT_VISIBILITY:-}" ]; then
    printf '%s' "$AGENT_VISIBILITY"
    return 0
  fi
  local py prof
  py="$(_find_python_runtime || true)"
  if [ -n "$py" ]; then
    prof="$("$py" -c 'import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "_shared"))
import runtime_config
import pathlib
home = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
print(runtime_config.load_config(home=home)["agent_visibility"]["profile"])' "$SCRIPT_DIR" "${HOME:-}" 2>/dev/null)"
    case "$prof" in
      strict|dynamic|minimal) printf '%s' "$prof"; return 0 ;;
    esac
  fi
  printf 'dynamic'
}

detail_ok() {
  [ "${VERBOSE:-0}" = "1" ] && ok "$*"
  return 0
}

detail_warn() {
  [ "${VERBOSE:-0}" = "1" ] && warn "$*"
  return 0
}

live_counter_enabled() {
  case "${GHOST_ALICE_INSTALL_PROGRESS:-auto}" in
    0|false|False|FALSE|off|OFF|no|NO) return 1 ;;
  esac
  [ "${VERBOSE:-0}" = "1" ] && return 1
  [ -t 1 ]
}

count_label() {
  local count="$1"
  local singular="$2"
  local plural="$3"
  if [ "$count" = "1" ]; then
    printf '%s %s' "$count" "$singular"
  else
    printf '%s %s' "$count" "$plural"
  fi
}

print_skill_sync_summary() {
  local skill_targets="$1"
  local support_targets="$2"
  local installed_count="$3"
  local updated_count="$4"
  local skipped_count="$5"
  local mode_label="${6:-}"
  local skill_label support_label message

  skill_label="$(count_label "$skill_targets" "$(t 'skill target' 'skill target')" "$(t 'skill targets' 'skill targets')")"
  support_label="$(count_label "$support_targets" "$(t 'support target' 'support target')" "$(t 'support targets' 'support targets')")"
  message="$(t "[2/5] Skill sync: ${skill_label}, ${support_label}; ${installed_count} installed, ${updated_count} updated, ${skipped_count} skipped" "[2/5] Skill sync: ${skill_label}, ${support_label}; ${installed_count} installed, ${updated_count} updated, ${skipped_count} skipped")"
  if [ -n "$mode_label" ]; then
    message="${message} (${mode_label})"
  fi
  info "$message"
}

count_skill_targets() {
  local total=0
  local skill targets sub_name sub_path
  for skill in "$@"; do
    targets="$(expand_skill_targets "$skill")"
    while IFS='|' read -r sub_name sub_path; do
      [ -n "$sub_name" ] || continue
      total=$((total + 1))
    done <<< "$targets"
  done
  printf '%s\n' "$total"
}

install_compact_output_enabled() {
  [ "${VERBOSE:-0}" != "1" ]
}

install_report_enabled() {
  install_compact_output_enabled && [ "${INSTALL_REPORT_CHILD:-0}" != "1" ]
}

install_log_init() {
  if [ -n "${INSTALL_REPORT_LOG_FILE:-}" ]; then
    export GHOST_ALICE_INSTALL_LOG_FILE="$INSTALL_REPORT_LOG_FILE"
    return 0
  fi
  local log_root timestamp
  log_root="${HOME}/.ghost-alice/install"
  mkdir -p "$log_root"
  timestamp="$(date '+%Y-%m-%d-%H%M%S')"
  INSTALL_REPORT_LOG_FILE="${log_root}/${timestamp}.log"
  export GHOST_ALICE_INSTALL_LOG_FILE="$INSTALL_REPORT_LOG_FILE"
}

run_logged_if_compact() {
  if install_compact_output_enabled; then
    install_log_init
    "$@" >>"$INSTALL_REPORT_LOG_FILE" 2>&1
    return $?
  fi
  "$@"
}

join_by() {
  local sep="$1"
  shift
  local first=1 item
  for item in "$@"; do
    if [ "$first" = "1" ]; then
      printf '%s' "$item"
      first=0
    else
      printf '%s%s' "$sep" "$item"
    fi
  done
}

hook_suite_label() {
  printf '%s\n' "prompt, session-intent, web-search-first, tool-checkpoint, completion, session-start, io-trace"
}

report_skill_sync_line() {
  local current_count="$1" updated_count="$2" new_count="$3"
  printf '  [2/5] Skill sync          [%s] [Current], [%s] [updated], [%s] [newly added]' \
    "$current_count" "$updated_count" "$new_count"
}

report_common_skill_sync_line() {
  local common_targets="$1"
  printf '  [2/5] Skill sync          [%s] common targets' "$common_targets"
}

report_progress_bar() {
  local done_count="${1:-0}" total_count="${2:-0}" width="${3:-20}"
  local filled=0 i
  if [ "$total_count" -gt 0 ]; then
    filled=$((done_count * width / total_count))
  fi
  [ "$filled" -lt 0 ] && filled=0
  [ "$filled" -gt "$width" ] && filled="$width"
  for ((i = 0; i < filled; i++)); do
    printf '#'
  done
  for ((i = filled; i < width; i++)); do
    printf '-'
  done
}

report_common_target_progress_line() {
  local done_count="$1" total_count="$2" suffix="${3:-common targets synced}"
  printf '        Common targets      ['
  report_progress_bar "$done_count" "$total_count" 30
  printf '] [%s/%s] %s' "$done_count" "$total_count" "$suffix"
}

report_target_operation_progress_line() {
  local done_count="$1" total_count="$2" suffix="${3:-pending}"
  printf '        Sync ['
  report_progress_bar "$done_count" "$total_count" 20
  printf '] [%s/%s] %s' "$done_count" "$total_count" "$suffix"
}

report_clear_current_line() {
  printf '\r\033[2K'
}

report_auto_update_target_operation_progress_line() {
  report_clear_current_line
  report_target_operation_progress_line "$@"
}

report_auto_animate_target_operation_progress_line() {
  local from_count="$1" to_count="$2" total_count="$3" suffix="$4"
  local completed
  [ "$from_count" -lt 0 ] && from_count=0
  [ "$to_count" -lt "$from_count" ] && to_count="$from_count"
  [ "$to_count" -gt "$total_count" ] && to_count="$total_count"
  for ((completed = from_count + 1; completed <= to_count; completed++)); do
    report_auto_update_target_operation_progress_line "$completed" "$total_count" "$suffix"
    [ "$completed" -lt "$to_count" ] && sleep 0.02
  done
  return 0
}

report_print_start() {
  local platform_label="$1" total_targets="$2" visibility="${3:-dynamic}" target_unit="${4:-targets}"
  install_log_init
  printf '%s\n\n' "Ghost-ALICE OS installation Process Report"
  printf '%s\n' "Target"
  printf '  Platform: %s\n' "$platform_label"
  printf '  Skills: [%s] %s\n' "$total_targets" "$target_unit"
  printf '%s\n' "  Hooks: enabled"
  printf '  Visibility Level: [%s]\n\n' "$visibility"
  printf '%s\n' "Progress"
  printf '%s\n' "  [1/5] Preflight           ok"
  report_skill_sync_line 0 0 0
}

report_print_tail() {
  local platform_label="$1" visibility="${2:-dynamic}"
  printf '%s\n' "  [3/5] Hooks               $(hook_suite_label) enabled"
  printf '  [4/5] Runtime config      %s hooks=true, Visibility Level=[%s]\n' "$platform_label" "$visibility"
  printf '%s\n\n' "  [5/5] Verification        ok"
  printf '%s\n' "Attention"
  printf '%s\n\n' "  - visibility can be changed later with /visibility between: dynamic | minimal | strict"
  printf '%s\n' "Details"
  printf '  log: %s\n' "$INSTALL_REPORT_LOG_FILE"
  printf '%s\n' "  rerun with --verbose to show per-skill actions"
}

report_print_full() {
  local platform_label="$1" total_targets="$2" current_count="$3" updated_count="$4" new_count="$5" visibility="${6:-dynamic}" target_unit="${7:-targets}"
  install_log_init
  printf '%s\n\n' "Ghost-ALICE OS installation Process Report"
  printf '%s\n' "Target"
  printf '  Platform: %s\n' "$platform_label"
  printf '  Skills: [%s] %s\n' "$total_targets" "$target_unit"
  printf '%s\n' "  Hooks: enabled"
  printf '  Visibility Level: [%s]\n\n' "$visibility"
  printf '%s\n' "Progress"
  printf '%s\n' "  [1/5] Preflight           ok"
  report_skill_sync_line "$current_count" "$updated_count" "$new_count"
  printf '\n'
  report_print_tail "$platform_label" "$visibility"
}

report_print_auto_start() {
  local platform_label="$1" common_targets="$2" visibility="${3:-dynamic}" platform_count="$4"
  local total_operations=$((common_targets * platform_count))
  install_log_init
  printf '%s\n\n' "Ghost-ALICE OS installation Process Report"
  printf '%s\n' "Target"
  printf '  Platform: %s\n' "$platform_label"
  printf '  Skills: [%s] common targets\n' "$common_targets"
  printf '%s\n' "  Hooks: enabled"
  printf '  Visibility Level: [%s]\n\n' "$visibility"
  printf '%s\n' "Progress"
  printf '%s\n' "  [1/5] Preflight           ok"
  report_common_skill_sync_line "$common_targets"
  printf '\n'
  report_target_operation_progress_line 0 "$total_operations"
}

report_print_auto_full() {
  local platform_label="$1" common_targets="$2" visibility="${3:-dynamic}" synced_targets="${4:-0}"
  install_log_init
  printf '%s\n\n' "Ghost-ALICE OS installation Process Report"
  printf '%s\n' "Target"
  printf '  Platform: %s\n' "$platform_label"
  printf '  Skills: [%s] common targets\n' "$common_targets"
  printf '%s\n' "  Hooks: enabled"
  printf '  Visibility Level: [%s]\n\n' "$visibility"
  printf '%s\n' "Progress"
  printf '%s\n' "  [1/5] Preflight           ok"
  report_common_skill_sync_line "$common_targets"
  printf '\n'
  report_common_target_progress_line "$synced_targets" "$common_targets" "common targets synced on all platforms"
  printf '\n'
  report_print_tail "$platform_label" "$visibility"
}

report_write_event() {
  [ -n "${INSTALL_REPORT_EVENT_FILE:-}" ] || return 0
  mkdir -p "$(dirname "$INSTALL_REPORT_EVENT_FILE")"
  printf '{"type":"platform-result","platform":"%s","total_targets":%s,"current":%s,"updated":%s,"new":%s,"verification":"ok","hooks":"enabled"}\n' \
    "$1" "$2" "$3" "$4" "$5" >>"$INSTALL_REPORT_EVENT_FILE"
}

report_write_target_event() {
  [ -n "${INSTALL_REPORT_EVENT_FILE:-}" ] || return 0
  mkdir -p "$(dirname "$INSTALL_REPORT_EVENT_FILE")"
  printf '{"type":"target-result","platform":"%s","target_id":"%s","target_kind":"%s","status":"%s"}\n' \
    "$1" "$2" "$3" "$4" >>"$INSTALL_REPORT_EVENT_FILE"
}

report_read_event_counts() {
  local event_file="$1"
  if [ ! -s "$event_file" ]; then
    printf '0|0|0|0\n'
    return 0
  fi
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    printf '0|0|0|0\n'
    return 0
  fi
  "$py" "${SCRIPT_DIR}/_shared/install_report_events.py" event-counts "$event_file"
}

report_read_platform_target_progress() {
  local event_file="$1" platform="$2"
  if [ ! -s "$event_file" ]; then
    printf '0\n'
    return 0
  fi
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    printf '0\n'
    return 0
  fi
  "$py" "${SCRIPT_DIR}/_shared/install_report_events.py" platform-target-progress "$event_file" "$platform"
}

report_read_all_common_target_progress() {
  local event_file="$1" platform_count="$2"
  if [ ! -s "$event_file" ]; then
    printf '0\n'
    return 0
  fi
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    printf '0\n'
    return 0
  fi
  "$py" "${SCRIPT_DIR}/_shared/install_report_events.py" all-common-target-progress "$event_file" "$platform_count"
}

report_read_target_operation_progress() {
  local event_file="$1"
  if [ ! -s "$event_file" ]; then
    printf '0\n'
    return 0
  fi
  local py
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    printf '0\n'
    return 0
  fi
  "$py" "${SCRIPT_DIR}/_shared/install_report_events.py" target-operation-progress "$event_file"
}
