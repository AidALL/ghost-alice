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
    if (-not $AddonSource -or $AddonSource.Count -eq 0) {
        Write-Err "-ListAddons requires at least one -AddonSource path."
        exit 1
    }
    Prepare-AddonSources

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ runtime not found for addon manifest inspection."
        exit 1
    }

    $addonArgs = @()
    foreach ($source in $AddonSource) {
        $addonArgs += @("--source", $source)
    }
    foreach ($skill in (Get-CoreSkillNamesForAddonValidation)) {
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
        "--install-state-manifest", $installStateManifest,
        "--skills-root", $SkillsRoot
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

function Test-AddonDependentsForSkill {
    param([string]$SkillName)

    if ($Force) {
        return $true
    }

    $py = Find-PythonExe
    if (-not $py) {
        Write-Warn ("Dependency check skipped for {0}: Python 3.11+ not found" -f $SkillName) ("Dependency check skipped for {0}: Python 3.11+ not found" -f $SkillName)
        return $true
    }

    $addonsDir = Join-Path (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "addons") $Platform
    $helper = Join-Path $script:GhostAliceRoot "_shared/addon_uninstall.py"
    & $py $helper --dependents $SkillName --addons-dir $addonsDir 2>$null | Out-Null
    if ($LASTEXITCODE -eq 2) {
        Write-Warn ("Skipping {0}: an installed addon depends on it (use -Force to override)" -f $SkillName) ("Skipping {0}: an installed addon depends on it (use -Force to override)" -f $SkillName)
        return $false
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn ("Dependency check failed for {0}; proceeding with removal" -f $SkillName) ("Dependency check failed for {0}; proceeding with removal" -f $SkillName)
    }
    return $true
}

function Test-InstalledAddonIdForUninstall {
    param([string]$AddonId, [string]$AddonsDir)

    if (-not $AddonId -or $AddonId -notmatch '^[a-z][a-z0-9-]*$') {
        return $false
    }
    return (
        (Test-Path -LiteralPath (Join-Path $AddonsDir "$AddonId.json")) -or
        (Test-Path -LiteralPath (Join-Path $AddonsDir "$AddonId.json.removing"))
    )
}

function Add-UniqueUninstallAddonId {
    param(
        [System.Collections.Generic.List[string]]$AddonIds,
        [string]$AddonId
    )

    if ($AddonId -and -not $AddonIds.Contains($AddonId)) {
        [void]$AddonIds.Add($AddonId)
    }
}

function Split-UninstallSelection {
    param(
        [string[]]$SkillNames,
        [string[]]$ExplicitAddonIds,
        [string]$AddonsDir
    )

    $addonIds = [System.Collections.Generic.List[string]]::new()
    foreach ($addonId in @($ExplicitAddonIds)) {
        Add-UniqueUninstallAddonId -AddonIds $addonIds -AddonId $addonId
    }

    $keptSkills = @()
    foreach ($skill in @($SkillNames)) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0 -and (Test-InstalledAddonIdForUninstall -AddonId $skill -AddonsDir $AddonsDir)) {
            Add-UniqueUninstallAddonId -AddonIds $addonIds -AddonId $skill
        } else {
            $keptSkills += $skill
        }
    }

    return [PSCustomObject]@{
        SkillNames = @($keptSkills)
        AddonIds   = @($addonIds.ToArray())
    }
}

function Invoke-ResumePendingAddonUninstalls {
    param(
        [string]$PythonExe,
        [string]$AddonsDir,
        [string]$Helper,
        [string]$CommandsDir,
        [string]$ResourcesDir
    )

    if (-not $PythonExe -or -not (Test-Path -LiteralPath $AddonsDir)) {
        return
    }
    & $PythonExe $Helper --resume-pending --addons-dir $AddonsDir --skills-dir $SkillsDir `
        --skills-dir $CommandsDir --skills-dir $ResourcesDir --platform $Platform --confirm 2>$null | Out-Null
}

function Invoke-Uninstall {
    param([string[]]$SkillNames, [string[]]$AddonIds)

    $userHome = Resolve-UserHome
    $addonsDir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "addons") $Platform
    $selection = Split-UninstallSelection -SkillNames $SkillNames -ExplicitAddonIds $AddonIds -AddonsDir $addonsDir
    $SkillNames = @($selection.SkillNames)
    $AddonIds = @($selection.AddonIds)

    $py = Find-PythonExe
    $helper = Join-Path $script:GhostAliceRoot "_shared/addon_uninstall.py"
    $commandsDir = Join-Path (Resolve-ClaudeHome) "commands"
    $resourcesDir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "resources") $Platform

    $knownBefore = @{}
    foreach ($addonId in @($AddonIds)) {
        if (Test-InstalledAddonIdForUninstall -AddonId $addonId -AddonsDir $addonsDir) {
            $knownBefore[$addonId] = $true
        }
    }

    Invoke-ResumePendingAddonUninstalls -PythonExe $py -AddonsDir $addonsDir `
        -Helper $helper -CommandsDir $commandsDir -ResourcesDir $resourcesDir

    $removed = 0
    $failed = 0

    if ($AddonIds -and $AddonIds.Count -gt 0) {
        if (-not $py) {
            Write-Err "Python 3.11+ not found; cannot uninstall addons" "Python 3.11+ not found; cannot uninstall addons"
            throw "addon uninstall cannot run - Python 3.11+ is required"
        }
        foreach ($addonId in @($AddonIds)) {
            & $py $helper --addon-id $addonId --addons-dir $addonsDir --skills-dir $SkillsDir `
                --skills-dir $commandsDir --skills-dir $resourcesDir --platform $Platform --confirm
            $rc = $LASTEXITCODE
            if ($rc -eq 0) {
                $removed++
            } elseif ($rc -eq 1 -and $knownBefore.ContainsKey($addonId)) {
                $removed++
            } else {
                $failed++
                if ($rc -eq 1) {
                    Write-Warn ("Addon {0}: not installed (nothing to remove)" -f $addonId) ("Addon {0}: not installed (nothing to remove)" -f $addonId)
                } else {
                    Write-Warn ("Addon {0}: uninstall left items for manual review" -f $addonId) ("Addon {0}: uninstall left items for manual review" -f $addonId)
                }
            }
        }
    }

    if ($SkillNames -and $SkillNames.Count -gt 0) {
        Assert-SkillNames $SkillNames
        Write-Info "Removing selected skills..." "Removing selected skills..."
    } else {
        if ($AddonIds -and $AddonIds.Count -gt 0) {
            if ($removed -gt 0) { Write-Ok ("{0} addon(s) removed." -f $removed) ("{0} addon(s) removed." -f $removed) }
            else { Write-Info "No addon targets to remove." "No addon targets to remove." }
            if ($failed -gt 0) {
                throw "one or more addon uninstalls failed"
            }
            return
        }
        Invoke-FullUninstall
        return
    }

    foreach ($skill in $SkillNames) {
        if (-not (Test-AddonDependentsForSkill -SkillName $skill)) {
            continue
        }
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

    if ($failed -gt 0) {
        throw "one or more addon uninstalls failed"
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

function Invoke-AllAddonUninstallsBeforeFull {
    # PowerShell mirror of bash _uninstall_all_addons_before_full (uninstall.sh):
    # finish any interrupted addon uninstall, then remove every installed addon via
    # the hash-gated per-addon path (commands + resources allowed roots) BEFORE the
    # cleanup wipes the sidecar registry that points at them. Returns $true only if
    # all addons cleared; $false if any was preserved for manual review (drift /
    # user-modified target), so the full uninstall must halt instead of clobbering.
    $py = Find-PythonExe
    if (-not $py) { return $true }
    $userHome = Resolve-UserHome
    $adir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "addons") $Platform
    if (-not (Test-Path -LiteralPath $adir)) { return $true }
    $helper = Join-Path $script:GhostAliceRoot "_shared/addon_uninstall.py"
    $commandsDir = Join-Path (Resolve-ClaudeHome) "commands"
    $resourcesDir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "resources") $Platform

    # 1) Finish any interrupted addon uninstall (leftover .removing markers).
    & $py $helper --resume-pending --addons-dir $adir --skills-dir $SkillsDir `
        --skills-dir $commandsDir --skills-dir $resourcesDir --platform $Platform --confirm 2>$null | Out-Null

    # 2) Hash-gated per-addon uninstall for every recorded sidecar.
    $allClear = $true
    foreach ($sidecar in (Get-ChildItem -LiteralPath $adir -Filter "*.json" -File -ErrorAction SilentlyContinue)) {
        $aid = $sidecar.BaseName
        if ($aid -notmatch '^[a-z][a-z0-9-]*$') { continue }  # skip non-addon json (e.g. _migration-report)
        & $py $helper --addon-id $aid --addons-dir $adir --skills-dir $SkillsDir `
            --skills-dir $commandsDir --skills-dir $resourcesDir --platform $Platform --confirm 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $allClear = $false
            Write-Warn ("Full uninstall preserved addon {0}; manual review is required before sidecar cleanup" -f $aid) ("Full uninstall preserved addon {0}; manual review is required before sidecar cleanup" -f $aid)
        }
    }
    return $allClear
}

function Invoke-FullUninstall {
    Write-Info "Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets." "Full uninstall: removing Ghost-ALICE managed hooks, bootstrap, support state, and install targets."
    if (-not (Invoke-AllAddonUninstallsBeforeFull)) {
        Write-Err "Full uninstall stopped because one or more addon targets need manual review." "Full uninstall stopped because one or more addon targets need manual review."
        throw "full uninstall halted: addon manual review required"
    }
    Invoke-UninstallCleanup
}

function Show-Help {
    if ($LegacyAsciiConsole) {
        Write-Host @"
Usage:
  .\install.cmd                          # Install to detected AI tools
  .\install.cmd --addon autopilot        # Install official autopilot addon to detected AI tools
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
  -Platform PLAT -Uninstall -Addon id    Remove selected addon from one platform
  -Force                                 Override selected-skill addon dependency guard

Advanced/operator commands:
  -Skills name1,name2                    Install only selected skills
  -List                                  List available skills
  -Addon autopilot                       Install official autopilot addon; -Platform is optional
  -AddonSource PATH                      Add addon repo or local manifest path
  -AddonTag TAG                          Checkout branch/tag for git URL addon sources
  -AddonSkip                             Disable addon installation
  -ListAddons                            List addon manifest targets
  -CleanupPending                        Clean false-positive legacy pending entries
  -UpdateSource                          Stash source checkout local changes and fast-forward
  -SkipSourceHealth                      Skip source health gate
  -Help                                  Show this help
"@
        return
    }

    Write-Host @"
Usage:
  .\install.cmd                          # Install to detected AI tools
  .\install.cmd --addon autopilot        # Install official autopilot addon to detected AI tools
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
  -Platform PLAT -Uninstall -Addon id    Remove selected addon from one platform
  -Force                                 Override selected-skill addon dependency guard

Advanced/operator commands:
  -Skills name1,name2                    Install only selected skills
  -List                                  List available skills
  -Addon autopilot                       Install official autopilot addon; -Platform is optional
  -AddonSource PATH                      Add addon repo or local manifest path
  -AddonTag TAG                          Checkout branch/tag for git URL addon sources
  -AddonSkip                             Disable addon installation
  -ListAddons                            List addon manifest targets
  -CleanupPending                        Clean false-positive legacy pending entries
  -UpdateSource                          Stash source checkout local changes and fast-forward
  -SkipSourceHealth                      Skip source health gate
  -Help                                  Show this help
"@
}
