# Ghost-ALICE installer library: targets
# Dot-sourced by install.ps1. Do not run directly.

function Install-Shared {
    param(
        [string]$SkillsRoot,
        [switch]$CopyOnly
    )

    $sharedSrc = Join-Path $ScriptDir "_shared"
    $sharedDest = Join-Path $SkillsRoot "_shared"

    if (-not (Test-Path $sharedSrc)) {
        return
    }

    if (Test-Path $sharedDest) {
        $item = Get-Item $sharedDest -Force
        if ((-not $CopyOnly) -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $item.Target -eq $sharedSrc) {
            Write-Warn "_shared - already installed (skip)" "_shared - already installed (skip)"
            return
        }

        Write-Warn "_shared - replacing existing install" "_shared - replacing existing install"
        if (-not $CopyOnly) {
            Remove-Item $sharedDest -Recurse -Force
        }
    }

    if ($CopyOnly) {
        Invoke-StagedCopyReplace -Source $sharedSrc -Dest $sharedDest
        Write-Ok "_shared -> copied" "_shared -> copied"
        return
    }

    try {
        New-Item -ItemType Junction -Path $sharedDest -Target $sharedSrc -ErrorAction Stop | Out-Null
        Write-Ok "_shared → Junction" "_shared -> Junction"
    } catch {
        Invoke-StagedCopyReplace -Source $sharedSrc -Dest $sharedDest
        Write-Ok "_shared -> copied" "_shared -> copied"
    }
}

function Invoke-DeprecatedInstalledSkillCleanup {
    param([string]$SkillsRoot)

    if (-not (Test-Path -LiteralPath $SkillsRoot)) {
        return
    }

    foreach ($skill in $DeprecatedInstalledSkills) {
        $target = Join-Path $SkillsRoot $skill
        if (-not (Test-Path -LiteralPath $target)) {
            continue
        }

        $backupRoot = Join-Path (Resolve-UserHome) ".ghost-alice/deprecated-skill-backups"
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $quarantine = Join-Path $backupRoot ("{0}-{1}" -f $skill, $stamp)
        $suffix = 1
        while (Test-Path -LiteralPath $quarantine) {
            $quarantine = Join-Path $backupRoot ("{0}-{1}-{2}" -f $skill, $stamp, $suffix)
            $suffix++
        }

        Move-Item -LiteralPath $target -Destination $quarantine
        Write-Warn `
            "Deprecated installed skill moved out of discovery path: $skill -> $quarantine" `
            "Deprecated installed skill moved out of discovery path: $skill -> $quarantine"
    }
}

function Assert-SkillNames {
    param([string[]]$SkillNames)

    foreach ($skill in $SkillNames) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0) {
            Write-Err "Skill not found: $skill" "Skill not found: $skill"
            Write-Err "Run .\\install.cmd -List to see available skills." "Run .\\install.cmd -List to see available skills."
            exit 1
        }
    }
}

function Remove-InstalledTarget {
    param(
        [string]$DisplayName,
        [string]$TargetPath
    )

    if (-not (Test-Path $TargetPath)) {
        return $false
    }

    Remove-Item $TargetPath -Recurse -Force
    Write-Ok "$DisplayName removed" "$DisplayName removed"
    return $true
}

function Get-InstalledManagedTargetCount {
    param([string]$SkillsRoot)

    $count = 0
    foreach ($skill in $AllSkills) {
        $targets = Expand-SkillTargets $skill
        if ($targets.Count -eq 0) {
            $target = Join-Path $SkillsRoot $skill
            if (Test-Path $target) { $count++ }
            continue
        }

        foreach ($t in $targets) {
            $target = Join-Path $SkillsRoot $t.Name
            if (Test-Path $target) { $count++ }
        }
    }
    return $count
}

function Remove-SharedIfUnused {
    param([string]$SkillsRoot)

    if ((Get-InstalledManagedTargetCount $SkillsRoot) -gt 0) {
        return $false
    }

    $sharedTarget = Join-Path $SkillsRoot "_shared"
    if (-not (Test-Path $sharedTarget)) {
        return $false
    }

    Remove-Item $sharedTarget -Recurse -Force
    Write-Ok "_shared removed" "_shared removed"
    return $true
}

function Expand-SkillTargets {
    param([string]$SkillName)

    $skillRoot = Join-Path $ScriptDir $SkillName
    if (-not (Test-Path $skillRoot)) {
        return @()
    }

    $topSkillFile = Join-Path $skillRoot "SKILL.md"
    if (Test-Path $topSkillFile) {
        return ,([PSCustomObject]@{
            Name     = $SkillName
            Source   = $skillRoot
            IsFamily = $false
            Family   = $null
        })
    }

    # Family discovery: only direct subdirectories that contain SKILL.md.
    $targets = @()
    $subDirs = Get-ChildItem -Path $skillRoot -Directory -ErrorAction SilentlyContinue
    foreach ($sub in $subDirs) {
        $subSkillFile = Join-Path $sub.FullName "SKILL.md"
        if (Test-Path $subSkillFile) {
            $targets += [PSCustomObject]@{
                Name     = $sub.Name
                Source   = $sub.FullName
                IsFamily = $true
                Family   = $SkillName
            }
        }
    }
    return $targets
}

function Get-AddonTargets {
    if ($AddonSkip -or -not $AddonSource -or $AddonSource.Count -eq 0) {
        return @()
    }
    if ($AddonTag -and $AddonTag.Count -gt 0) {
        Write-Err "-AddonTag is not supported for local addon sources yet. Check out the desired tag locally and pass that path with -AddonSource."
        throw "addon tag checkout is not supported - aborting installation"
    }

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ runtime not found for addon manifest inspection."
        throw "addon manifest inspection cannot run - aborting installation"
    }

    $addonArgs = @()
    foreach ($source in $AddonSource) {
        $addonArgs += @("--source", $source)
    }
    foreach ($skill in $AllSkills) {
        $addonArgs += @("--core-skill", $skill)
    }

    $jsonText = @(& $py (Join-Path $ScriptDir "_shared/addon_installer.py") @addonArgs --platform $Platform --format json)
    if ($LASTEXITCODE -ne 0) {
        throw "addon manifest inspection failed - aborting installation"
    }
    $payload = ($jsonText -join "`n") | ConvertFrom-Json

    $targets = @()
    foreach ($target in @($payload.targets)) {
        $targets += [PSCustomObject]@{
            Name     = [string]$target.name
            Source   = [string]$target.source
            IsFamily = $false
            Family   = $null
            Origin   = [string]$target.origin
        }
    }
    return $targets
}

function Get-InstallTargetsForSkillNames {
    param(
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @()
    )

    $targets = @()
    foreach ($skill in @($SkillNames)) {
        $targets += Expand-SkillTargets $skill
    }
    foreach ($target in @($ExtraTargets)) {
        $targets += $target
    }
    return $targets
}

function Invoke-CleanupPendingFalsePositives {
    $py = Initialize-PythonRuntimeForInstall
    $pending = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") (Join-Path "pending-merges" $Platform)
    $manifest = Join-Path $pending "manifest.json"
    & $py (Join-Path $script:GhostAliceRoot "merge-companion/scripts/cleanup_false_positive_legacy.py") `
        --platform $Platform `
        --pending $pending `
        --manifest $manifest `
        --repo-root $script:GhostAliceRoot `
        --apply
    if ($LASTEXITCODE -ne 0) {
        throw "false-positive pending cleanup failed"
    }
}
