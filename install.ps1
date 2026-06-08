# Ghost-ALICE OS Installer (Windows PowerShell)
# Supports: Claude Code, Codex
# Usage (PowerShell execution-policy safe wrapper):
#   .\install.cmd                                  # Install to detected AI tools
#   .\install.cmd -PromptPlatform                  # Select AI tool interactively before installing
#   .\install.cmd -Skills task-router,verification-before-completion    # Install selected core skills only
#   .\install.cmd -Platform claude                 # Install all skills to Claude Code
#   .\install.cmd -Platform codex                  # Install all skills to Codex
#   .\install.cmd -Platform codex -Visibility dynamic
#   .\install.cmd -Platform codex -Skills hwpx     # Install selected Codex skills
#   .\install.cmd -Uninstall                       # Full uninstall for all detected Ghost-ALICE managed footprint
#   .\install.cmd -Platform codex -Uninstall -Skills hwpx  # Remove selected skills from one platform
#   .\install.cmd -List                            # List available skills
#   .\install.cmd -AddonSource .\ghost-alice-addons -ListAddons  # List addon manifest targets
#   .\install.cmd -Status                          # Show current install status
#   .\install.cmd -Doctor                          # Diagnose install protection state
#   .\install.cmd -CleanupPending                  # Clean false-positive legacy pending entries
#   .\install.cmd -UpdateSource                    # Stash source checkout local changes and fast-forward
# Direct .ps1 form, only when .ps1 script execution is already allowed:
#   .\install.ps1                                  # Install to detected AI tools
#   .\install.ps1 -PromptPlatform                  # Select AI tool interactively before installing
#   .\install.ps1 -Skills task-router,verification-before-completion    # Install selected core skills only
#   .\install.ps1 -Platform claude                 # Install all skills to Claude Code
#   .\install.ps1 -Platform codex                  # Install all skills to Codex
#   .\install.ps1 -Platform codex -Visibility dynamic
#   .\install.ps1 -Platform codex -Skills hwpx     # Install selected Codex skills
#   .\install.ps1 -Uninstall                       # Full uninstall for all detected Ghost-ALICE managed footprint
#   .\install.ps1 -Platform codex -Uninstall -Skills hwpx  # Remove selected skills from one platform
#   .\install.ps1 -List                            # List available skills
#   .\install.ps1 -AddonSource .\ghost-alice-addons -ListAddons  # List addon manifest targets
#   .\install.ps1 -Status                          # Show current install status
#   .\install.ps1 -Doctor                          # Diagnose install protection state
#   .\install.ps1 -CleanupPending                  # Clean false-positive legacy pending entries
#   .\install.ps1 -UpdateSource                    # Stash source checkout local changes and fast-forward

param(
    [string[]]$Skills,
    [ValidateSet("claude", "codex")]
    [string]$Platform = "claude",
    [switch]$PromptPlatform,
    [switch]$Auto,
    [switch]$List,
    [switch]$Status,
    [switch]$Doctor,
    [switch]$Uninstall,
    [switch]$CleanupPending,
    [string[]]$AddonSource,
    [string[]]$AddonTag,
    [switch]$AddonSkip,
    [switch]$ListAddons,
    [Alias("Visibility")]
    [ValidateSet("strict", "dynamic", "minimal")]
    [string]$AgentVisibility = "",
    [switch]$UpdateSource,
    [switch]$SkipSourceHealth,
    [switch]$Help
)

# Best-effort UTF-8 console output for Windows PowerShell 5.1 / legacy console hosts.
$LegacyAsciiConsole = ($PSVersionTable.PSVersion.Major -lt 6 -and $Host.Name -eq "ConsoleHost")
try {
    if ($LegacyAsciiConsole) {
        $null = & cmd /c "chcp 65001 > nul"
        $Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [Console]::InputEncoding = $Utf8NoBom
        [Console]::OutputEncoding = $Utf8NoBom
        $OutputEncoding = $Utf8NoBom
    }
} catch {
    # Do not block installation if console encoding cannot be adjusted.
    $null = $_
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Capture the installer's own directory once. install.ps1 dot-sources installer_lib/*.ps1;
# inside a dot-sourced function $PSScriptRoot resolves to installer_lib/, so functions use
# $script:GhostAliceRoot (captured here = repo root) for repo-relative paths.
$script:GhostAliceRoot = $PSScriptRoot

$ErrorActionPreference = "Stop"
if ($args.Count -gt 0) {
    [Console]::Error.WriteLine(("Unknown argument(s): {0}" -f ($args -join " ")))
    [Console]::Error.WriteLine("Run install.cmd -Help for usage.")
    exit 64
}
$PlatformWasExplicit = $PSBoundParameters.ContainsKey("Platform")
$script:InstallLockPath = $null
$script:ProjectDisplayName = "Ghost-ALICE"
$script:SourceRepoHookChange = $null
$script:PwshLtsBaselineVersion = [version]"7.6.0"
$script:PwshLtsReleaseLine = "7.6"
$script:PwshLtsEnsureChecked = $false
$script:InstallReportChild = ($env:GHOST_ALICE_INSTALL_REPORT_CHILD -eq "1")
if ($script:InstallReportChild) {
    $script:InstallReportLogFile = [string]$env:GHOST_ALICE_INSTALL_LOG_FILE
    $script:InstallReportEventFile = [string]$env:GHOST_ALICE_INSTALL_EVENT_FILE
} else {
    $script:InstallReportLogFile = ""
    $script:InstallReportEventFile = ""
}
$script:InstallCompactOutput = $false
# Installer messages are English-only.


























































$script:SessionGateContractSource = Join-Path $script:GhostAliceRoot "skill-catalog\session-gates.json"



# ── Platform path resolution ───────────────────────────────
# Environment variables take precedence; otherwise use default paths.
#   Claude Code: CLAUDE_CONFIG_DIR -> ~/.claude
#   Codex:       CODEX_HOME -> ~/.codex




































$script:CodexBootstrapSource = Join-Path $script:GhostAliceRoot "platforms\codex\AGENTS.md"
$script:CodexBootstrapMarker = "# Ghost-ALICE Codex Bootstrap"
$script:CodexManagedBlockBegin = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
$script:CodexManagedBlockEnd = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"















# ── Git hooks (post-merge auto-refresh) ───────────────────
# Point this repo's Git hook path to the tracked hooks/ directory so
# `git pull --ff-only` automatically triggers hooks/post-merge, which in turn
# re-runs install.sh --platform codex for copy-mode platforms.


$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# Dot-source installer_lib modules. Function definitions live in installer_lib/*.ps1 and are
# sourced here (before any top-level call) so install.ps1 stays a thin entrypoint.
Get-ChildItem -Path (Join-Path $script:GhostAliceRoot 'installer_lib') -Filter '*.ps1' -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { . $_.FullName }
$null = Assert-SessionGateContract
$script:SkillsDir = Resolve-SkillsDir $Platform
$AllSkills = @(
    "adversarial-verification"
    "boundary-contract"
    "coding-convention"
    "skill-evolution"
    "agent-security-scan"
    "jailbreak-detector"
    "merge-companion"
    "necessity-gate"
    "session-intent-analyzer"
    "compact-handoff"
    "task-router"
)

$script:DeprecatedInstalledSkills = @(
    "harness-security-scan"
    "session-intent-guard"
)












# ── Skill target expansion ─────────────────────────────────
# Normal skill (root SKILL.md exists) -> returns one target for itself.
# Family skill (no root SKILL.md, subdirectories have SKILL.md) -> returns N subskill targets.
# Missing skill -> returns an empty array.


# ── Skill list ─────────────────────────────────────────────









# ── Install status ─────────────────────────────────────────








# ── Uninstall ──────────────────────────────────────────────





# ── Install ────────────────────────────────────────────────


# ── UserPromptSubmit hook install/remove ───────────────────
# Require Python 3.11+. Partial fallback violates the freshness contract.
# Any failure blocks the whole install.






























































# ── Help ───────────────────────────────────────────────────

# ── Main ───────────────────────────────────────────────────
if ($Help)      { Show-Help; return }
if ($List)      { Show-List; return }
if ($ListAddons){ Show-Addons; return }
if ($UpdateSource) { Update-SourceCheckout; return }

# Default install: when no platform is specified, install to all detected tools.
# Plain full uninstall also detects platform homes and install-state manifests for full cleanup.
# Inspection, diagnostics, and partial removal commands keep the single-platform default (claude).
$Auto = $false
$hasInspectionCommand = $Help -or $List -or $ListAddons -or $Status -or $Doctor
$PlainFullUninstall = $Uninstall -and (-not $Skills -or $Skills.Count -eq 0)
if (-not $PlatformWasExplicit -and -not $PromptPlatform -and -not $hasInspectionCommand -and -not $PlainFullUninstall -and -not $CleanupPending -and -not $UpdateSource) {
    $Auto = $true
}

if ($PlainFullUninstall -and -not $PlatformWasExplicit -and -not $PromptPlatform) {
    $detected = Get-DetectedUninstallPlatforms
    if ($detected.Count -eq 0) {
        Write-Warn "No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json." "No install targets detected. Need a platform home or ~/.ghost-alice/install-state/<platform>.json."
        return
    }
    $rc = 0
    $selfPath = $MyInvocation.MyCommand.Path
    $index = 0
    Write-Info ("Detected uninstall targets: " + ($detected -join ', ')) ("Detected uninstall targets: " + ($detected -join ', '))
    foreach ($plat in $detected) {
        $index++
        Write-Host ""
        Write-Info `
            ("[uninstall] ({0}/{1}) Starting {2} full cleanup" -f $index, $detected.Count, $plat) `
            ("[uninstall] ({0}/{1}) starting {2} full cleanup" -f $index, $detected.Count, $plat)
        try {
            & $selfPath -Platform $plat -Uninstall
            $platRc = $LASTEXITCODE
        } catch {
            Write-Warn "[uninstall] $plat exception: $_" "[uninstall] $plat exception: $_"
            $platRc = 1
        }
        if ($platRc -and $platRc -ne 0) { $rc = 1 }
    }
    exit $rc
}

# auto/default: detect ~/.claude and ~/.codex, then recurse per platform.
# Auto child installs are separate install path operations, not a duplicate install.
if ($Auto) {
    $detected = Get-DetectedPlatforms
    if ($detected.Count -eq 0) {
        Write-Warn "No AI platform home directory detected. Need at least one of ~/.claude, ~/.codex." "No AI platform home directory detected. Need at least one of ~/.claude, ~/.codex."
        return
    }

    Initialize-InstallLog
    $autoEventFile = Join-Path `
        (Split-Path -Parent $script:InstallReportLogFile) `
        (([IO.Path]::GetFileNameWithoutExtension($script:InstallReportLogFile)) + ".events.jsonl")
    if (Test-Path $autoEventFile) {
        Remove-Item $autoEventFile -Force
    }
    New-Item -ItemType File -Path $autoEventFile -Force | Out-Null

    $selectedSkills = if ($Skills -and $Skills.Count -gt 0) { $Skills } else { $AllSkills }
    $fallbackTargets = @()
    foreach ($skill in $selectedSkills) {
        $fallbackTargets += Expand-SkillTargets $skill
    }
    $autoCommonTargets = Get-InstallTargetCount -Targets $fallbackTargets
    $platformLabel = Join-InstallLabels -Items $detected
    $visibility = Resolve-EffectiveVisibility -Flag $AgentVisibility
    if (Test-LiveCounterEnabled) {
        Write-InstallReportAutoStart -PlatformLabel $platformLabel -CommonTargets $autoCommonTargets -Visibility $visibility
    }

    $rc = 0
    $selfPath = $MyInvocation.MyCommand.Path
    try {
        $pwshExe = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    } catch {
        $pwshExe = ""
    }
    if (-not $pwshExe) {
        $pwshCommand = if ($PSVersionTable.PSEdition -eq "Desktop") {
            Get-Command "powershell.exe" -ErrorAction SilentlyContinue
        } else {
            Get-Command "pwsh" -ErrorAction SilentlyContinue
        }
        $pwshExe = if ($pwshCommand) { $pwshCommand.Source } else { "" }
    }
    if (-not $pwshExe) {
        $pwshExe = if ($PSVersionTable.PSEdition -eq "Desktop") { "powershell.exe" } else { "pwsh" }
    }
    $index = 0
    $autoDisplayedTargets = 0
    $oldReportChild = $env:GHOST_ALICE_INSTALL_REPORT_CHILD
    $oldReportLog = $env:GHOST_ALICE_INSTALL_LOG_FILE
    $oldReportEvent = $env:GHOST_ALICE_INSTALL_EVENT_FILE
    try {
        $env:GHOST_ALICE_INSTALL_REPORT_CHILD = "1"
        $env:GHOST_ALICE_INSTALL_LOG_FILE = $script:InstallReportLogFile
        $env:GHOST_ALICE_INSTALL_EVENT_FILE = $autoEventFile

        foreach ($plat in $detected) {
            $index++
            Write-InstallLogLine ("[INFO] [auto] ({0}/{1}) starting {2} install (separate install path; not a duplicate install)" -f $index, $detected.Count, $plat)
            $childArgs = @(
                "-Platform", $plat
            )
            if ($Uninstall) {
                $childArgs += "-Uninstall"
            }
            if ($Status) {
                $childArgs += "-Status"
            }
            if ($Doctor) {
                $childArgs += "-Doctor"
            }
            if ($SkipSourceHealth) {
                $childArgs += "-SkipSourceHealth"
            }
            if ($Skills -and $Skills.Count -gt 0) {
                $childArgs += "-Skills"
                $childArgs += $Skills
            }
            if ($AgentVisibility) {
                $childArgs += @("-Visibility", $AgentVisibility)
            }
            if ($AddonSource) {
                foreach ($source in $AddonSource) {
                    $childArgs += @("-AddonSource", $source)
                }
            }
            if ($AddonTag) {
                foreach ($tag in $AddonTag) {
                    $childArgs += @("-AddonTag", $tag)
                }
            }
            if ($AddonSkip) {
                $childArgs += "-AddonSkip"
            }

            $childOutputFile = Join-Path `
                ([IO.Path]::GetTempPath()) `
                ("ghost-alice-install-{0}-{1}.log" -f $plat, ([guid]::NewGuid().ToString("N")))
            try {
                $engineArgs = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $selfPath)
                $engineArgs += $childArgs
                & $pwshExe @engineArgs *> $childOutputFile
                $platRc = $LASTEXITCODE
                if (Test-Path $childOutputFile) {
                    Get-Content -LiteralPath $childOutputFile -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
                        Write-InstallLogLine ([string]$_)
                    }
                }
            } catch {
                Write-InstallLogLine ("[WARN] [auto] {0} install exception: {1}" -f $plat, $_)
                $platRc = 1
            } finally {
                Remove-Item -LiteralPath $childOutputFile -Force -ErrorAction SilentlyContinue
            }
            if (Test-LiveCounterEnabled) {
                $autoSyncedTargets = Read-WeightedCommonTargetProgress -EventFile $autoEventFile -PlatformCount $detected.Count -TotalCount $autoCommonTargets
                Write-AutoAnimateCommonTargetProgress -FromCount $autoDisplayedTargets -ToCount $autoSyncedTargets -TotalCount $autoCommonTargets -Suffix ("For {0} [{1}/{2}]" -f $plat, $index, $detected.Count)
                $autoDisplayedTargets = $autoSyncedTargets
            }
            if ($platRc -and $platRc -ne 0) {
                $rc = 1
                Write-InstallLogLine ("[WARN] [auto] {0} install failed (exit {1})" -f $plat, $platRc)
            }
        }
    } finally {
        if ($null -eq $oldReportChild) { Remove-Item Env:GHOST_ALICE_INSTALL_REPORT_CHILD -ErrorAction SilentlyContinue } else { $env:GHOST_ALICE_INSTALL_REPORT_CHILD = $oldReportChild }
        if ($null -eq $oldReportLog) { Remove-Item Env:GHOST_ALICE_INSTALL_LOG_FILE -ErrorAction SilentlyContinue } else { $env:GHOST_ALICE_INSTALL_LOG_FILE = $oldReportLog }
        if ($null -eq $oldReportEvent) { Remove-Item Env:GHOST_ALICE_INSTALL_EVENT_FILE -ErrorAction SilentlyContinue } else { $env:GHOST_ALICE_INSTALL_EVENT_FILE = $oldReportEvent }
    }

    $autoSyncedTargets = Read-AllCommonTargetProgress -EventFile $autoEventFile -PlatformCount $detected.Count
    if ($rc -eq 0) {
        if (Test-LiveCounterEnabled) {
            Write-AutoAnimateCommonTargetProgress -FromCount $autoDisplayedTargets -ToCount $autoSyncedTargets -TotalCount $autoCommonTargets -Suffix "common targets synced on all platforms"
            $autoDisplayedTargets = $autoSyncedTargets
            [Console]::Write("`r")
            [Console]::Write((Format-CommonTargetProgressLine -DoneCount $autoSyncedTargets -TotalCount $autoCommonTargets -Suffix "common targets synced on all platforms"))
            [Console]::WriteLine()
            Write-InstallReportTail -PlatformLabel $platformLabel -Visibility $visibility
        } else {
            Write-InstallReportAutoFull `
                -PlatformLabel $platformLabel `
                -CommonTargets $autoCommonTargets `
                -SyncedTargets $autoSyncedTargets `
                -Visibility $visibility
        }
    } else {
        Write-Err "[auto] some platforms failed. See log: $script:InstallReportLogFile" "[auto] some platforms failed. See log: $script:InstallReportLogFile"
    }
    exit $rc
}

if ($PromptPlatform -and -not $PlatformWasExplicit) {
    $Platform = Select-TargetPlatform
} elseif ($PromptPlatform -and $PlatformWasExplicit) {
    Write-Warn "-PromptPlatform is ignored because -Platform was already specified." "-PromptPlatform is ignored because -Platform was already specified."
}
$script:SkillsDir = Resolve-SkillsDir $Platform

if ($CleanupPending) {
    Invoke-CleanupPendingFalsePositives
    return
}

if ($Doctor)    { Show-Doctor; return }
if ($Status)    { Show-Status; return }
if ($Uninstall) { Invoke-Uninstall -SkillNames $Skills; return }
Initialize-GitHooks
Initialize-PythonRuntimeForInstall | Out-Null
Invoke-WithInstallLock { Invoke-Install -SkillNames $Skills }
Initialize-PwshLtsBaseline
