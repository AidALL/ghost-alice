# Ghost-ALICE installer library: bootstrap
# Dot-sourced by install.ps1. Do not run directly.

function Assert-SessionGateContract {
    if (-not (Test-Path $SessionGateContractSource)) {
        Write-Err "Session gate contract missing: $SessionGateContractSource" "Session gate contract missing: $SessionGateContractSource"
        throw "Session gate contract not found at $SessionGateContractSource"
    }
}

function Set-ClaudeBootstrap {
    $claudeHome = Resolve-ClaudeHome
    if (-not (Test-Path $claudeHome)) {
        New-Item -ItemType Directory -Path $claudeHome -Force | Out-Null
    }

    $rulesPath = Join-Path $claudeHome "CLAUDE.md"
    $py = Find-PythonExe
    if (-not $py) {
        $message = T "Python 3.11+ not found; aborting because Claude CLAUDE.md block merge cannot run" "Python 3.11+ not found; aborting because Claude CLAUDE.md block merge cannot run"
        Write-Err $message $message
        throw "Claude CLAUDE.md block merge cannot run - aborting installation"
    }

    $blockArgs = @(
        (Join-Path $script:GhostAliceRoot "_shared/global_rule_blocks.py"),
        "claude-merge",
        "--source", $ClaudeBootstrapSource,
        "--dest", $rulesPath,
        "--proposed", "$rulesPath.ghost-alice-proposed"
    )
    $result = & $py @blockArgs
    if ($LASTEXITCODE -ne 0) {
        $message = T "Claude CLAUDE.md block merge failed; aborting install" "Claude CLAUDE.md block merge failed; aborting install"
        Write-Err $message $message
        throw "Claude CLAUDE.md block merge failed - aborting installation"
    }

    $resultText = ($result | Select-Object -First 1)
    if ($resultText -like "proposed:*") {
        $proposedPath = $resultText.Substring("proposed:".Length)
        $message = T "Claude CLAUDE.md is user-owned; wrote proposed file instead" "Claude CLAUDE.md is user-owned; wrote proposed file instead"
        Write-Warn "${message}: $proposedPath" "${message}: $proposedPath"
        return
    }

    $message = T "Claude CLAUDE.md bootstrap block updated" "Claude CLAUDE.md bootstrap block updated"
    Write-Ok $message $message
}

function Remove-ClaudeBootstrap {
    param([switch]$RequireChange)

    $rulesPath = Join-Path (Resolve-ClaudeHome) "CLAUDE.md"
    if (-not (Test-Path $rulesPath)) {
        return (-not $RequireChange)
    }

    $py = Find-PythonExe
    if (-not $py) {
        $message = T "Python 3.11+ not found; skipping Claude CLAUDE.md block removal" "Python 3.11+ not found; skipping Claude CLAUDE.md block removal"
        Write-Warn $message $message
        return $false
    }

    $result = & $py (Join-Path $script:GhostAliceRoot "_shared/global_rule_blocks.py") "claude-remove" --dest $rulesPath
    if ($LASTEXITCODE -ne 0) {
        $message = T "Claude CLAUDE.md block removal failed" "Claude CLAUDE.md block removal failed"
        Write-Warn $message $message
        return $false
    }

    $resultText = ($result | Select-Object -First 1)
    if ($resultText -like "removed:*") {
        $message = T "Claude CLAUDE.md bootstrap removed" "Claude CLAUDE.md bootstrap removed"
        Write-Ok $message $message
        return $true
    }
    if ($resultText -like "updated:*") {
        $message = T "Claude CLAUDE.md Ghost-ALICE block removed; user content preserved" "Claude CLAUDE.md Ghost-ALICE block removed; user content preserved"
        Write-Ok $message $message
        return $true
    }
    return (-not $RequireChange)
}

function Remove-ClaudeBootstrapIfUnused {
    param([string]$SkillsRoot)

    if ((Get-InstalledManagedTargetCount $SkillsRoot) -gt 0) {
        return $false
    }
    return (Remove-ClaudeBootstrap -RequireChange)
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
