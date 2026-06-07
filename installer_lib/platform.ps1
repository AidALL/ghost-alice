# Ghost-ALICE installer library: platform
# Dot-sourced by install.ps1. Do not run directly.

function Resolve-UserHome {
    if ($env:HOME) { return $env:HOME }
    return $HOME
}

function Resolve-ClaudeHome {
    if ($env:CLAUDE_CONFIG_DIR) { return $env:CLAUDE_CONFIG_DIR }
    return (Join-Path (Resolve-UserHome) ".claude")
}

function Resolve-CodexHome {
    if ($env:CODEX_HOME) { return $env:CODEX_HOME }
    return (Join-Path (Resolve-UserHome) ".codex")
}

function Resolve-CodexSkillsDir {
    return (Join-Path (Join-Path (Resolve-UserHome) ".agents") "skills")
}

function Test-CodexHooksSupported {
    return $true
}

function Test-Windows10OrNewer {
    if (-not $IsWindows -and ($PSVersionTable.PSVersion.Major -ge 6)) {
        return $false
    }
    $version = [Environment]::OSVersion.Version
    return ($version.Major -ge 10)
}

function Get-DetectedPlatforms {
    $detected = @()
    if (Test-Path (Resolve-ClaudeHome)) { $detected += "claude" }
    if (Test-Path (Resolve-CodexHome))  { $detected += "codex" }
    return $detected
}

function Get-DetectedUninstallPlatformHomes {
    $detected = @()
    if (Test-Path (Resolve-ClaudeHome)) { $detected += "claude" }
    if (Test-Path (Resolve-CodexHome))  { $detected += "codex" }
    return $detected
}

function Get-DetectedUninstallPlatforms {
    $seen = [ordered]@{}
    foreach ($plat in (Get-DetectedUninstallPlatformHomes)) {
        $seen[$plat] = $true
    }
    $stateRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state"
    foreach ($plat in @("claude", "codex")) {
        if (Test-Path (Join-Path $stateRoot "$plat.json")) {
            $seen[$plat] = $true
        }
    }
    return @($seen.Keys)
}

function Resolve-SkillsDir {
    param([string]$TargetPlatform)
    if ($TargetPlatform -eq "codex") {
        return Resolve-CodexSkillsDir
    }
    return (Join-Path (Resolve-ClaudeHome) "skills")
}
