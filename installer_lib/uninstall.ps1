# Ghost-ALICE installer library: uninstall
# Dot-sourced by install.ps1. Do not run directly.

function Select-TargetPlatform {
    if ([Console]::IsInputRedirected) {
        Write-Err "Interactive platform selection requires a TTY." "Interactive platform selection requires a TTY."
        exit 1
    }

    while ($true) {
        Write-Host (T "Choose the target AI tool:" "Choose the target AI tool:")
        Write-Host "  1) Claude Code  (~/.claude/skills)"
        Write-Host "  2) Codex        (~/.agents/skills)"
        Write-Host "  Q) " -NoNewline
        Write-Host (T "Cancel" "Cancel")

        $choice = (Read-Host (T "Enter a number or name" "Enter a number or name")).Trim().ToLowerInvariant()
        switch -Regex ($choice) {
            "^(1|claude|claude code)$" { return "claude" }
            "^(2|codex|openai codex)$" { return "codex" }
            "^(q|quit|exit|cancel)$" {
                Write-Info "Cancelling installation." "Cancelling installation."
                exit 0
            }
            default {
                Write-Warn "Unknown selection: $choice" "Unknown selection: $choice"
            }
        }
        Write-Host ""
    }
}

function Get-SkillDescription {
    param([string]$SkillMdPath)
    if (-not (Test-Path $SkillMdPath)) { return "" }
    $lines = Get-Content -LiteralPath $SkillMdPath -TotalCount 8 -Encoding UTF8
    $descLine = $lines | Where-Object { $_ -match "^description:" } | Select-Object -First 1
    if (-not $descLine) { return "" }
    $desc = ($descLine -replace '^description:\s*"?', '' -replace '"?\s*$', '')
    if ($desc.Length -gt 80) { $desc = $desc.Substring(0, 77) + "..." }
    return $desc
}

function Show-List {
    Write-Host (T "Available skills:`n" "Available skills:`n")
    foreach ($skill in $AllSkills) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0) {
            Write-Host ("  {0,-28} {1}" -f $skill, (T "(source missing)" "(source missing)")) -ForegroundColor DarkGray
            continue
        }
        if ($targets[0].IsFamily) {
            $familyLabel = "(family, {0} subskills)" -f $targets.Count
            Write-Host ("  {0} {1}" -f $skill, $familyLabel) -ForegroundColor Cyan
            foreach ($t in $targets) {
                if ($LegacyAsciiConsole) {
                    Write-Host ("    - {0}" -f $t.Name)
                } else {
                    $desc = Get-SkillDescription (Join-Path $t.Source "SKILL.md")
                    Write-Host ("    └ {0,-26} {1}" -f $t.Name, $desc)
                }
            }
        } else {
            if ($LegacyAsciiConsole) {
                Write-Host ("  {0}" -f $skill)
            } else {
                $desc = Get-SkillDescription (Join-Path $targets[0].Source "SKILL.md")
                Write-Host ("  {0,-28} {1}" -f $skill, $desc)
            }
        }
    }
}

function Show-Addons {
    if ($AddonSkip) {
        Write-Info "Addon installation is disabled." "Addon installation is disabled."
        return
    }
    if ($AddonTag -and $AddonTag.Count -gt 0) {
        Write-Err "-AddonTag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with -AddonSource."
        exit 1
    }
    if (-not $AddonSource -or $AddonSource.Count -eq 0) {
        Write-Err "-ListAddons requires at least one -AddonSource path."
        exit 1
    }

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ runtime not found for addon manifest inspection."
        exit 1
    }

    $addonArgs = @()
    foreach ($source in $AddonSource) {
        $addonArgs += @("--source", $source)
    }
    foreach ($skill in $AllSkills) {
        $addonArgs += @("--core-skill", $skill)
    }

    & $py (Join-Path $ScriptDir "_shared/addon_installer.py") @addonArgs --platform $Platform --format text
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Show-OneStatus {
    param(
        [string]$DisplayName,
        [string]$TargetPath,
        [string]$Indent = "  "
    )
    if (Test-Path $TargetPath) {
        $item = Get-Item $TargetPath -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $line = if ($LegacyAsciiConsole) { " {0}  ->  {1}" -f $DisplayName, $item.Target } else { " $DisplayName  →  $($item.Target)" }
            Write-Host $Indent -NoNewline; Write-Host (Mark 'linked') -ForegroundColor Green -NoNewline
            Write-Host $line
        } else {
            Write-Host $Indent -NoNewline; Write-Host (Mark 'copied') -ForegroundColor Yellow -NoNewline
            Write-Host (" {0}  {1}" -f $DisplayName, (T "(copied)" "(copied)"))
        }
    } else {
        Write-Host $Indent -NoNewline; Write-Host (Mark 'missing') -ForegroundColor Red -NoNewline
        Write-Host (" {0}  {1}" -f $DisplayName, (T "(not installed)" "(not installed)"))
    }
}

function Invoke-InstallDoctor {
    param(
        [ValidateSet("status", "doctor")]
        [string]$Mode = "status",
        [string]$SkillsRoot = $SkillsDir
    )

    $py = Find-PythonExe
    if (-not $py) {
        if ($Mode -eq "doctor") {
            Write-Err "Python 3.11+ not found; installer doctor cannot run" "Python 3.11+ not found; installer doctor cannot run"
            throw "installer doctor cannot run - Python 3.11+ is required"
        }
        Write-Warn "Python 3.11+ not found; skipping installer doctor diagnostics" "Python 3.11+ not found; skipping installer doctor diagnostics"
        return
    }

    try {
        $sourceRoot = (& git -C $ScriptDir rev-parse --show-toplevel 2>$null | Select-Object -First 1)
        if (-not $sourceRoot) { $sourceRoot = $ScriptDir }
    } catch {
        $sourceRoot = $ScriptDir
    }

    $installStateManifest = Join-Path (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state") "$Platform.json"
    $pyArgs = @(
        (Join-Path $script:GhostAliceRoot "_shared/install_doctor.py"),
        "--platform", $Platform,
        "--repo-root", $sourceRoot,
        "--encoding-root", $ScriptDir,
        "--encoding-root", $SkillsRoot,
        "--ghost-alice-root", (Join-Path (Resolve-UserHome) ".ghost-alice"),
        "--install-state-manifest", $installStateManifest
    )
    if ($Mode -eq "doctor") {
        $pyArgs += "--strict"
    }

    if ($Platform -eq "codex") {
        $codexRule = Join-Path (Resolve-CodexHome) "AGENTS.md"
        $pyArgs += @(
            "--global-rule", "codex-bootstrap", $codexRule,
            $CodexBootstrapMarker, $CodexManagedBlockBegin, $CodexManagedBlockEnd
        )
    }

    $sharedSrc = Join-Path $ScriptDir "_shared"
    $sharedDest = Join-Path $SkillsRoot "_shared"
    if (Test-Path $sharedSrc) {
        $pyArgs += @("--target", "_shared", $sharedDest)
    }

    foreach ($skill in $AllSkills) {
        foreach ($target in (Expand-SkillTargets $skill)) {
            $pyArgs += @("--target", $target.Name, (Join-Path $SkillsRoot $target.Name))
        }
    }

    Write-Host ""
    Write-Info "Running installer doctor diagnostics..." "Running installer doctor diagnostics..."
    & $py @pyArgs
    if (($Mode -eq "doctor") -and ($LASTEXITCODE -ne 0)) {
        throw "installer doctor found issues"
    }
}

function Show-Status {
    $header = "Install status ($SkillsDir):`n"
    Write-Host $header
    Show-OneStatus -DisplayName "_shared" -TargetPath (Join-Path $SkillsDir "_shared")
    foreach ($skill in $AllSkills) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0) {
            Write-Host "  " -NoNewline; Write-Host (Mark 'unknown') -ForegroundColor DarkGray -NoNewline
            Write-Host (" {0}  {1}" -f $skill, (T "(source missing)" "(source missing)"))
            continue
        }
        if ($targets[0].IsFamily) {
            $line = "  {0} {1} (family)" -f (Mark 'family'), $skill
            Write-Host $line -ForegroundColor Cyan
            foreach ($t in $targets) {
                Show-OneStatus -DisplayName $t.Name -TargetPath (Join-Path $SkillsDir $t.Name) -Indent "    "
            }
        } else {
            Show-OneStatus -DisplayName $skill -TargetPath (Join-Path $SkillsDir $skill)
        }
    }

    Invoke-InstallDoctor -Mode "status" -SkillsRoot $SkillsDir
    Invoke-InstallHooks -Action "status" -TargetPlatform $Platform
}

function Show-Doctor {
    $header = if ($LegacyAsciiConsole) { "Installer doctor ({0}):`n" -f $SkillsDir } else { "Installer doctor ($SkillsDir):`n" }
    Write-Host $header
    $doctorFailed = $false
    try {
        Invoke-InstallDoctor -Mode "doctor" -SkillsRoot $SkillsDir
    } catch {
        $doctorFailed = $true
        Write-Warn "Installer doctor found items that need attention: $_" "Installer doctor found items that need attention: $_"
    }
    Invoke-InstallHooks -Action "status" -TargetPlatform $Platform
    if ($doctorFailed) {
        throw "installer doctor found issues"
    }
}

function Invoke-Uninstall {
    param([string[]]$SkillNames)

    if ($SkillNames -and $SkillNames.Count -gt 0) {
        Assert-SkillNames $SkillNames
        Write-Info "Removing selected skills..." "Removing selected skills..."
    } else {
        Invoke-FullUninstall
        return
    }

    $removed = 0

    foreach ($skill in $SkillNames) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0) { continue }
        foreach ($t in $targets) {
            $target = Join-Path $SkillsDir $t.Name
            $label = if ($t.IsFamily) { "$($t.Family)/$($t.Name)" } else { $t.Name }
            if (Remove-InstalledTarget -DisplayName $label -TargetPath $target) {
                $removed++
            }
        }
    }

    if (Remove-SharedIfUnused -SkillsRoot $SkillsDir) {
        $removed++
    }

    if ($Platform -eq "codex" -and (Remove-CodexBootstrapIfUnused -SkillsRoot $SkillsDir)) {
        $removed++
    }

    if ($removed -eq 0) { Write-Info "Nothing to remove." "Nothing to remove." }
    else { Write-Ok ("Removed {0} skill(s)." -f $removed) ("Removed {0} skill(s)." -f $removed) }

    if ((Get-InstalledManagedTargetCount $SkillsDir) -gt 0) {
        Write-Info "Managed skills remain; keeping hooks installed." "Managed skills remain; keeping hooks installed."
    } else {
        Invoke-InstallHooks -Action "uninstall" -TargetPlatform $Platform
    }
}

function Invoke-UninstallCleanup {
    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; uninstall cleanup cannot run." "Python 3.11+ not found; uninstall cleanup cannot run."
        throw "uninstall cleanup cannot run - Python 3.11+ is required"
    }

    $manifestPath = Join-Path (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state") "$Platform.json"
    $helper = Join-Path $script:GhostAliceRoot "_shared/uninstall_cleanup.py"
    $helperArgs = @(
        $helper,
        "--platform", $Platform,
        "--install-state-manifest", $manifestPath,
        "--confirm"
    )

    & $py @helperArgs
    if ($LASTEXITCODE -ne 0) {
        throw "uninstall cleanup failed"
    }
}

function Invoke-FullUninstall {
    Write-Info "Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets." "Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets."
    Invoke-UninstallCleanup
}

function Show-Help {
    if ($LegacyAsciiConsole) {
        Write-Host @"
Usage:
  .\install.cmd                          # Install to detected AI tools
  .\install.cmd -Platform claude         # Install only to Claude Code
  .\install.cmd -Platform codex          # Install to Codex

Common commands:
  -Platform claude|codex                 Choose one platform
  -PromptPlatform                        Ask which AI tool to install
  -Status                                Show install status
  -Visibility strict|dynamic|minimal
                                         Set user-facing governance message visibility profile
  -AgentVisibility strict|dynamic|minimal
                                         Alias for -Visibility
  -Doctor                                Run install diagnostics

Removal commands:
  -Uninstall                             Full uninstall: remove managed hooks, bootstrap, support state, and install targets
  -Platform PLAT -Uninstall -Skills name Remove selected skills from one platform

Advanced/operator commands:
  -Skills name1,name2                    Install only selected skills
  -List                                  List available skills
  -AddonSource PATH                      Add addon repo or local manifest path
  -AddonTag TAG                          Reserved: check out tag locally for now
  -AddonSkip                             Disable addon installation
  -ListAddons                            List addon manifest targets
  -CleanupPending                        Clean false-positive legacy pending entries
  -SkipSourceHealth                      Skip source health gate
  -Help                                  Show this help
"@
        return
    }

    Write-Host @"
Usage:
  .\install.cmd                          # Install to detected AI tools
  .\install.cmd -Platform claude         # Install only to Claude Code
  .\install.cmd -Platform codex          # Install to Codex

Common commands:
  -Platform claude|codex                 Choose one platform
  -PromptPlatform                        Ask which AI tool to install
  -Status                                Show install status
  -Visibility strict|dynamic|minimal
                                         Set user-facing governance message visibility profile
  -AgentVisibility strict|dynamic|minimal
                                         Alias for -Visibility
  -Doctor                                Run install diagnostics

Removal commands:
  -Uninstall                             Full uninstall: remove managed hooks, bootstrap, support state, and install targets
  -Platform PLAT -Uninstall -Skills name Remove selected skills from one platform

Advanced/operator commands:
  -Skills name1,name2                    Install only selected skills
  -List                                  List available skills
  -AddonSource PATH                      Add addon repo or local manifest path
  -AddonTag TAG                          Reserved: check out tag locally for now
  -AddonSkip                             Disable addon installation
  -ListAddons                            List addon manifest targets
  -CleanupPending                        Clean false-positive legacy pending entries
  -SkipSourceHealth                      Skip source health gate
  -Help                                  Show this help
"@
}
