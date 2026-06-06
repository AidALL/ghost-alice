# Ghost-ALICE installer library: bootstrap
# Dot-sourced by install.ps1. Do not run directly.

function Assert-SessionGateContract {
    if (-not (Test-Path $SessionGateContractSource)) {
        Write-Err "Session gate contract missing: $SessionGateContractSource" "Session gate contract missing: $SessionGateContractSource"
        throw "Session gate contract not found at $SessionGateContractSource"
    }
}

function Get-CodexHooklessFallbackBlock {
    return @'
## Codex Hookless Fallback Mode

This installation is in hookless/manual fallback mode for environments where Codex hook evidence cannot be trusted. If hooks are disabled, review/trust has not completed, or no hook payload has been observed in the current session, this bootstrap directly enforces the procedure below.

- Immediately after each user turn starts, check the pending-merge precheck first, apply the session-intent-analyzer intake, let jailbreak-detector derive the downstream gate, and then run task-router before downstream work or tool calls. tool-checkpoint is a tool-stage retry checkpoint, not user-input intake.
- Do not store the raw user input. The session-intent-analyzer ledger records only digest and compressed intent summary.
- session-intent-analyzer intent-state.json is update-plus-accumulate state. Scalar intent is corrected to the latest delta, while list-like constraints/non-goals/open questions/criteria/decisions accumulate by dedupe or id-based merge.
- The pending-merge precheck completes in the pre-routing/session-start layer before the user-input governance graph begins.
- After this precheck is clean, or after surfacing ends through an explicit user defer/skip, the runtime hook graph is: user input -> session-intent-analyzer digest/ledger/current-session pointer write and allow -> skill-evolution report-only consumption and jailbreak-detector downstream-gates.json decision -> task-router reminder release when no current-lineage block exists -> tool-stage tool-checkpoint.
- jailbreak-detector deterministic hard-block rules are a narrow regression guard for explicit, high-confidence attack signals. Gradual multi-turn jailbreak resistance depends on session-intent summary quality and cumulative constraint comparison quality.
- tool-checkpoint looks only at the opened/decision bit of the jailbreak-detector downstream gate. If opened=false or decision=block, it denies; every other state is silent allow. It does not use tool-call identity, payload content, or audit/log/correlation metadata as decision input; audit/log/correlation metadata stays outside the decision body.
- If task-router outputs `boundary-contract: required`, apply `boundary-contract` before any other tool call.
- Leave a `[gate-state]` block in the first commentary.
- Leave a `[tool-checkpoint]` block immediately before every tool call. Required fields are intent, why, procedure, contract-ref, contract-check, localized-human-note, rejected-alternatives, unverified-premises, and failure-mode-if-wrong. Add recovery-action only when a mismatch, scope reopen, external side effect, or hard-to-recover action needs a concrete next step.
- Gate schemas such as `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, and `[io-trace]` follow English canonical narrative + English control surface.
- Immediately before a completion claim, leave a `[completion-check]` block with fresh evidence.
- Leave an `[io-trace]` block at the end of the response.
- If any required step was missed, self-correct immediately before continuing.

This mode is not a substitute for hook guarantees; it is the fallback when hook evidence is absent. When hook payload is observed, use that evidence first. When it is absent, apply the prose gates.
'@
}

function Get-CodexBootstrapContent {
    if (-not (Test-Path $CodexBootstrapSource)) {
        Write-Err "Codex bootstrap source missing: $CodexBootstrapSource" "Codex bootstrap source missing: $CodexBootstrapSource"
        throw "Codex bootstrap source not found at $CodexBootstrapSource"
    }
    $content = Get-Content -LiteralPath $CodexBootstrapSource -Raw -Encoding UTF8
    if (-not (Test-CodexHooksSupported)) {
        $content = $content.TrimEnd("`r", "`n") + "`n`n" + (Get-CodexHooklessFallbackBlock)
    }
    return $content
}

function Set-CodexBootstrap {
    $codexHome = Resolve-CodexHome
    if (-not (Test-Path $codexHome)) {
        New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    }

    $agentsPath = Join-Path $codexHome "AGENTS.md"
    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because Codex AGENTS.md block merge cannot run." "Python 3.11+ not found; aborting because Codex AGENTS.md block merge cannot run."
        throw "Codex AGENTS.md block merge cannot run - aborting installation"
    }

    $blockArgs = @(
        (Join-Path $script:GhostAliceRoot "_shared/global_rule_blocks.py"),
        "codex-merge",
        "--source", $CodexBootstrapSource,
        "--dest", $agentsPath,
        "--proposed", "$agentsPath.ghost-alice-proposed"
    )
    if (-not (Test-CodexHooksSupported)) {
        $blockArgs += "--hookless-fallback"
    }

    $result = & $py @blockArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Codex AGENTS.md block merge failed; aborting install." "Codex AGENTS.md block merge failed; aborting install."
        throw "Codex AGENTS.md block merge failed - aborting installation"
    }

    $resultText = ($result | Select-Object -First 1)
    if ($resultText -like "proposed:*") {
        $proposedPath = $resultText.Substring("proposed:".Length)
        Write-Warn "Codex AGENTS.md is user-owned; wrote proposed file instead: $proposedPath" "Codex AGENTS.md is user-owned; wrote proposed file instead: $proposedPath"
        return
    }

    Write-Ok "Codex AGENTS.md bootstrap block updated" "Codex AGENTS.md bootstrap block updated"
}

function Remove-CodexBootstrapIfUnused {
    param([string]$SkillsRoot)

    if ((Get-InstalledManagedTargetCount $SkillsRoot) -gt 0) {
        return $false
    }

    $agentsPath = Join-Path (Resolve-CodexHome) "AGENTS.md"
    if (-not (Test-Path $agentsPath)) {
        return $false
    }

    $py = Find-PythonExe
    if (-not $py) {
        Write-Warn "Python 3.11+ not found; skipping Codex AGENTS.md block removal." "Python 3.11+ not found; skipping Codex AGENTS.md block removal."
        return $false
    }

    $result = & $py (Join-Path $script:GhostAliceRoot "_shared/global_rule_blocks.py") "codex-remove" --dest $agentsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Codex AGENTS.md block removal failed." "Codex AGENTS.md block removal failed."
        return $false
    }

    $resultText = ($result | Select-Object -First 1)
    if ($resultText -like "removed:*") {
        Write-Ok "Codex AGENTS.md bootstrap removed" "Codex AGENTS.md bootstrap removed"
        return $true
    }
    if ($resultText -like "updated:*") {
        Write-Ok "Codex AGENTS.md Ghost-ALICE block removed; user content preserved" "Codex AGENTS.md Ghost-ALICE block removed; user content preserved"
        return $true
    }
    return $false
}
