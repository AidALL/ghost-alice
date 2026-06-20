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

function Get-CoreSkillNamesForAddonValidation {
    $seen = @{}
    $names = @()

    foreach ($skill in $AllSkills) {
        if (-not $seen.ContainsKey($skill)) {
            $seen[$skill] = $true
            $names += $skill
        }
        foreach ($target in @(Expand-SkillTargets $skill)) {
            if ($target.Name -and -not $seen.ContainsKey($target.Name)) {
                $seen[$target.Name] = $true
                $names += $target.Name
            }
        }
    }
    return $names
}

function Resolve-OfficialAddonSource {
    param([string]$Name)
    switch ($Name) {
        "autopilot" { return $(if ($env:GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE) { $env:GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE } else { "https://github.com/AidALL/ghost-alice-autopilot.git" }) }
        "autopilot-mode" { return $(if ($env:GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE) { $env:GHOST_ALICE_OFFICIAL_ADDON_AUTOPILOT_SOURCE } else { "https://github.com/AidALL/ghost-alice-autopilot.git" }) }
        default {
            Write-Err ("Unknown official addon: {0} (supported: autopilot)" -f $Name) ("Unknown official addon: {0} (supported: autopilot)" -f $Name)
            throw "unknown official addon - aborting installation"
        }
    }
}

function Resolve-OfficialAddonShortcuts {
    if ($AddonSkip -or -not $Addon -or $Addon.Count -eq 0) {
        return
    }

    $resolved = @()
    foreach ($name in @($Addon)) {
        $resolved += Resolve-OfficialAddonSource -Name $name
    }
    $current = @()
    foreach ($source in @($AddonSource)) {
        if ($source) {
            $current += $source
        }
    }
    $script:AddonSource = $current + $resolved
}

function Test-AddonSourceIsGitUrl {
    param([string]$Source)
    return (
        $Source -like "http://*" -or
        $Source -like "https://*" -or
        $Source -like "ssh://*" -or
        $Source -like "file://*" -or
        $Source -like "git@*:*"
    )
}

function Get-AddonSelectedRef {
    if (-not $AddonTag -or $AddonTag.Count -eq 0) {
        return ""
    }
    if ($AddonTag.Count -gt 1) {
        Write-Err "-AddonTag accepts one branch/tag for git URL addon sources."
        throw "addon tag selection failed - aborting installation"
    }
    return [string]$AddonTag[0]
}

function Get-AddonSourceCacheKey {
    param(
        [string]$Source,
        [string]$Ref
    )
    $payload = $Source + [char]0 + $Ref
    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    return (($hash | ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 24)
}

function Copy-AddonGitSourceToCache {
    param(
        [string]$Source,
        [string]$Ref
    )
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Err "git not found; cannot clone addon source URL"
        throw "git not found - aborting addon source preparation"
    }

    $cacheRoot = if ($env:GHOST_ALICE_ADDON_SOURCE_CACHE_DIR) {
        $env:GHOST_ALICE_ADDON_SOURCE_CACHE_DIR
    } else {
        Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "addon-source-cache"
    }
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    $key = Get-AddonSourceCacheKey -Source $Source -Ref $Ref
    $dest = Join-Path $cacheRoot $key
    $tmp = "$dest.tmp.$PID"
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

    $cloneArgs = @("clone", "--quiet", "--depth", "1")
    if ($Ref) {
        $cloneArgs += @("--branch", $Ref)
    }
    $cloneArgs += @($Source, $tmp)
    & git @cloneArgs
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Write-Err ("Addon source clone failed: {0}" -f $Source) ("Addon source clone failed: {0}" -f $Source)
        throw "addon source clone failed - aborting installation"
    }

    Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $tmp -Destination $dest
    return $dest
}

function Prepare-AddonSources {
    if ($script:AddonSourcesPrepared) {
        return
    }
    if ($AddonSkip -or -not $AddonSource -or $AddonSource.Count -eq 0) {
        $script:AddonSourcesPrepared = $true
        return
    }

    $ref = Get-AddonSelectedRef
    $prepared = @()
    foreach ($source in @($AddonSource)) {
        if (-not $source) {
            continue
        }
        if (Test-AddonSourceIsGitUrl -Source $source) {
            $prepared += Copy-AddonGitSourceToCache -Source $source -Ref $ref
        } else {
            if ($ref) {
                Write-Err "-AddonTag can only be used with git URL addon sources; check out local sources yourself."
                throw "addon tag cannot be applied to local source - aborting installation"
            }
            $prepared += $source
        }
    }
    $script:AddonSource = $prepared
    $script:AddonSourcesPrepared = $true
}

function Get-AddonTargets {
    param([string]$TargetPlatform = "")

    if ($AddonSkip -or -not $AddonSource -or $AddonSource.Count -eq 0) {
        return @()
    }
    Prepare-AddonSources
    if (-not $TargetPlatform) {
        $TargetPlatform = $Platform
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
    foreach ($skill in (Get-CoreSkillNamesForAddonValidation)) {
        $addonArgs += @("--core-skill", $skill)
    }

    $jsonText = @(& $py (Join-Path $ScriptDir "_shared/addon_installer.py") @addonArgs --platform $TargetPlatform --format json)
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
            AddonId  = [string]$target.addon_id
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
