#!/usr/bin/env bash
# Ghost-ALICE installer library: bootstrap
# Sourced by install.sh. Do not execute directly.

assert_session_gate_contract() {
  if [ ! -f "$SESSION_GATE_CONTRACT_SOURCE" ]; then
    error "$(t "Session gate contract file missing: $SESSION_GATE_CONTRACT_SOURCE" "Session gate contract file missing: $SESSION_GATE_CONTRACT_SOURCE")"
    exit 1
  fi
}

ensure_claude_bootstrap() {
  local skills_root="$1"
  local claude_home rules_path py result args
  claude_home="$(resolve_claude_home)"
  rules_path="${claude_home}/CLAUDE.md"

  mkdir -p "$claude_home"
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because Claude CLAUDE.md block merge cannot run' 'Python 3.11+ not found; aborting because Claude CLAUDE.md block merge cannot run')"
    return 1
  fi

  args=(claude-merge --source "$CLAUDE_BOOTSTRAP_SOURCE" --dest "$rules_path" --proposed "${rules_path}.ghost-alice-proposed")
  if ! result="$("$py" "${SCRIPT_DIR}/_shared/global_rule_blocks.py" "${args[@]}")"; then
    error "$(t 'Claude CLAUDE.md block merge failed; aborting install' 'Claude CLAUDE.md block merge failed; aborting install')"
    return 1
  fi

  case "$result" in
    proposed:*)
      warn "$(t 'Claude CLAUDE.md is user-owned; wrote proposed file instead' 'Claude CLAUDE.md is user-owned; wrote proposed file instead'): ${result#proposed:}"
      ;;
    *)
      ok "$(t 'Claude CLAUDE.md bootstrap block updated' 'Claude CLAUDE.md bootstrap block updated')"
      ;;
  esac
}

remove_claude_bootstrap() {
  local require_change="${1:-0}"
  local claude_home rules_path py result

  claude_home="$(resolve_claude_home)"
  rules_path="${claude_home}/CLAUDE.md"
  if [ ! -f "$rules_path" ]; then
    [ "$require_change" = "1" ] && return 1
    return 0
  fi

  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    warn "$(t 'Python 3.11+ not found; skipping Claude CLAUDE.md block removal' 'Python 3.11+ not found; skipping Claude CLAUDE.md block removal')"
    return 1
  fi

  if ! result="$("$py" "${SCRIPT_DIR}/_shared/global_rule_blocks.py" claude-remove --dest "$rules_path")"; then
    warn "$(t 'Claude CLAUDE.md block removal failed' 'Claude CLAUDE.md block removal failed')"
    return 1
  fi

  case "$result" in
    removed:*)
      ok "$(t 'Claude CLAUDE.md bootstrap removed' 'Claude CLAUDE.md bootstrap removed')"
      return 0
      ;;
    updated:*)
      ok "$(t 'Claude CLAUDE.md Ghost-ALICE block removed; user content preserved' 'Claude CLAUDE.md Ghost-ALICE block removed; user content preserved')"
      return 0
      ;;
    *)
      [ "$require_change" = "1" ] && return 1
      return 0
      ;;
  esac
}

remove_claude_bootstrap_if_unused() {
  local skills_root="$1"
  if has_managed_installs "$skills_root"; then
    return 1
  fi
  remove_claude_bootstrap 1
}

ensure_codex_bootstrap() {
  local skills_root="$1"
  local codex_home agents_path py result args
  codex_home="$(resolve_codex_home)"
  agents_path="${codex_home}/AGENTS.md"

  mkdir -p "$codex_home"
  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    error "$(t 'Python 3.11+ not found; aborting because Codex AGENTS.md block merge cannot run' 'Python 3.11+ not found; aborting because Codex AGENTS.md block merge cannot run')"
    return 1
  fi

  args=(codex-merge --source "$CODEX_BOOTSTRAP_SOURCE" --dest "$agents_path" --proposed "${agents_path}.ghost-alice-proposed")
  if ! result="$("$py" "${SCRIPT_DIR}/_shared/global_rule_blocks.py" "${args[@]}")"; then
    error "$(t 'Codex AGENTS.md block merge failed; aborting install' 'Codex AGENTS.md block merge failed; aborting install')"
    return 1
  fi

  case "$result" in
    proposed:*)
      warn "$(t "Codex AGENTS.md is user-owned; wrote proposed file instead: ${result#proposed:}" "Codex AGENTS.md is user-owned; wrote proposed file instead: ${result#proposed:}")"
      ;;
    *)
      ok "$(t 'Codex AGENTS.md bootstrap block updated' 'Codex AGENTS.md bootstrap block updated')"
      ;;
  esac
}

remove_codex_bootstrap_if_unused() {
  local skills_root="$1"
  local codex_home agents_path py result

  if has_managed_installs "$skills_root"; then
    return 1
  fi

  codex_home="$(resolve_codex_home)"
  agents_path="${codex_home}/AGENTS.md"
  [ -f "$agents_path" ] || return 1

  py="$(_find_python_runtime || true)"
  if [ -z "$py" ]; then
    warn "$(t 'Python 3.11+ not found; skipping Codex AGENTS.md block removal' 'Python 3.11+ not found; skipping Codex AGENTS.md block removal')"
    return 1
  fi

  if ! result="$("$py" "${SCRIPT_DIR}/_shared/global_rule_blocks.py" codex-remove --dest "$agents_path")"; then
    warn "$(t 'Codex AGENTS.md block removal failed' 'Codex AGENTS.md block removal failed')"
    return 1
  fi

  case "$result" in
    removed:*)
      ok "$(t 'Codex AGENTS.md bootstrap removed' 'Codex AGENTS.md bootstrap removed')"
      return 0
      ;;
    updated:*)
      ok "$(t 'Codex AGENTS.md Ghost-ALICE block removed; user content preserved' 'Codex AGENTS.md Ghost-ALICE block removed; user content preserved')"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
