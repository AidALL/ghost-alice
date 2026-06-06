#!/usr/bin/env bash
# Ghost-ALICE installer library: bootstrap
# Sourced by install.sh. Do not execute directly.

assert_session_gate_contract() {
  if [ ! -f "$SESSION_GATE_CONTRACT_SOURCE" ]; then
    error "$(t "Session gate contract file missing: $SESSION_GATE_CONTRACT_SOURCE" "Session gate contract file missing: $SESSION_GATE_CONTRACT_SOURCE")"
    exit 1
  fi
}

get_codex_bootstrap_content() {
  if [ ! -f "$CODEX_BOOTSTRAP_SOURCE" ]; then
    error "$(t "Codex bootstrap source missing: $CODEX_BOOTSTRAP_SOURCE" "Codex bootstrap source missing: $CODEX_BOOTSTRAP_SOURCE")"
    return 1
  fi
  cat "$CODEX_BOOTSTRAP_SOURCE"
  if ! codex_hooks_supported "codex"; then
    cat <<'EOF'

## Codex Hook Enforcement And Hookless Fallback

When Codex hooks are trusted and a `PreToolUse` tool-checkpoint payload is observed, treat tool-checkpoint as a hook-enforced retry point. After a hook denial, the agent emits a `[tool-checkpoint]` and retries the same tool call. Hook timing enforcement does not weaken the semantic gate. Required decision fields are `intent`, `why`, `procedure`, `contract-ref`, `contract-check`, `localized-human-note`, `rejected-alternatives`, `unverified-premises`, and `failure-mode-if-wrong`. Add `recovery-action` only when a mismatch, scope reopen, external side effect, or hard-to-recover action needs a concrete next step.

If hooks are disabled, review/trust has not completed, or no hook payload has been observed in the current session, the session is in hookless/manual fallback mode. Only in that case, the bootstrap directly enforces the procedure below.

- Immediately after each user turn starts, check the pending-merge precheck first, apply the session-intent-analyzer intake, let jailbreak-detector derive the downstream gate, and then run task-router before downstream work or tool calls. tool-checkpoint is a tool-stage retry checkpoint, not user-input intake.
- Do not store the raw user input. The session-intent-analyzer ledger records only digest and compressed intent summary.
- session-intent-analyzer intent-state.json is update-plus-accumulate state. Scalar intent is corrected to the latest delta, while list-like constraints/non-goals/open questions/criteria/decisions accumulate by dedupe or id-based merge.
- The pending-merge precheck completes in the pre-routing/session-start layer before the user-input governance graph begins.
- After this precheck is clean, or after surfacing ends through an explicit user defer/skip, the runtime hook graph is: user input -> session-intent-analyzer digest/ledger/current-session pointer write and allow -> skill-evolution report-only consumption and jailbreak-detector downstream-gates.json decision -> task-router reminder release when no current-lineage block exists -> tool-stage tool-checkpoint.
- jailbreak-detector deterministic hard-block rules are a narrow regression guard for explicit, high-confidence attack signals. Gradual multi-turn jailbreak resistance depends on session-intent summary quality and cumulative constraint comparison quality.
- tool-checkpoint looks only at the opened/decision bit of the jailbreak-detector downstream gate. If `opened=false` or `decision=block`, it denies; every other state is silent allow. It does not use tool-call identity, payload content, or audit/log/correlation metadata as decision input; audit/log/correlation metadata stays outside the decision body.
- If task-router outputs `boundary-contract: required`, apply `boundary-contract` before any other tool call.
- Leave a `[gate-state]` block in the first commentary.
- Leave a `[tool-checkpoint]` block immediately before every new tool action.
- A routine inspection batch explicitly declared by the previous full gate may be referenced briefly as `[tool-checkpoint:batch]`; output polling for an already-started process may be referenced briefly as `[tool-checkpoint:continuation]`.
- Do not infer whether an action is safe from tool-call identity or payload content. The decision depends only on downstream gate state (`opened`/`decision`).
- Gate schemas such as `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, and `[io-trace]` follow English canonical narrative + English control surface.
- Immediately before a completion claim, leave a `[completion-check]` block that connects acceptance criteria to fresh evidence.
- Leave an `[io-trace]` block at the end of the response.
- If any required step was missed, repair it immediately and then continue.

This mode is not a substitute for hook guarantees; it is the fallback when hook evidence is absent. When hook payload is observed, use that evidence first. When it is absent, apply the prose gates.
EOF
  fi
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
  if ! codex_hooks_supported "codex"; then
    args+=(--hookless-fallback)
  fi

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
