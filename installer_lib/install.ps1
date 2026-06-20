# Ghost-ALICE installer library: install
# Dot-sourced by install.ps1. Do not run directly.

function Install-OneTarget {
    param(
        [string]$DisplayName,
        [string]$Source,
        [string]$Dest,
        [ref]$InstalledRef,
        [ref]$SkippedRef,
        [switch]$CopyOnly
    )

    if (Test-Path $Dest) {
        $item = Get-Item $Dest -Force
        if ((-not $CopyOnly) -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            if ($item.Target -eq $Source) {
                Write-Warn "$DisplayName - already installed (skip)" "$DisplayName - already installed (skip)"
                $SkippedRef.Value++
                return
            }
        }
        Write-Warn "$DisplayName - replacing existing install" "$DisplayName - replacing existing install"
        if (-not $CopyOnly) {
            Remove-Item $Dest -Recurse -Force
        }
    }

    if ($CopyOnly) {
        Invoke-StagedCopyReplace -Source $Source -Dest $Dest
        Write-Ok "$DisplayName -> copied (Codex Windows compatibility mode)" "$DisplayName -> copied (Codex Windows compatibility mode)"
        $InstalledRef.Value++
        return
    }

    $linkType = if ($IsWindows -or $PSVersionTable.PSEdition -eq "Desktop") { "Junction" } else { "SymbolicLink" }
    try {
        New-Item -ItemType $linkType -Path $Dest -Target $Source -ErrorAction Stop | Out-Null
        if (-not (Test-Path -LiteralPath $Dest)) {
            throw "$linkType target was not created"
        }
        Write-Ok "$DisplayName -> installed as $linkType" "$DisplayName -> installed as $linkType"
    } catch {
        if (Test-Path -LiteralPath $Dest) {
            Remove-Item $Dest -Recurse -Force
        }
        Invoke-StagedCopyReplace -Source $Source -Dest $Dest
        Write-Ok "$DisplayName -> copied (link unavailable)" "$DisplayName -> copied (link unavailable)"
    }
    $InstalledRef.Value++
}

function Get-InstallLockPath {
    return (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install.lock")
}

function Enter-InstallLock {
    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because install lock cannot be acquired" "Python 3.11+ not found; aborting because install lock cannot be acquired"
        throw "install lock cannot be acquired - aborting installation"
    }

    $lockPath = Get-InstallLockPath
    $staleSeconds = if ($env:GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS) { $env:GHOST_ALICE_INSTALL_LOCK_STALE_SECONDS } else { "1800" }
    & $py (Join-Path $script:GhostAliceRoot "_shared/install_lock.py") "acquire" `
        --lock $lockPath `
        --stale-seconds $staleSeconds `
        --owner "install.ps1:${Platform}:$PID"
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Another install is already running; aborting install: $lockPath" "Another install is already running; aborting install: $lockPath"
        throw "install lock is already held - aborting installation"
    }

    $script:InstallLockPath = $lockPath
}

function Exit-InstallLock {
    if (-not $script:InstallLockPath) {
        return
    }

    $py = Find-PythonExe
    if ($py) {
        & $py (Join-Path $script:GhostAliceRoot "_shared/install_lock.py") "release" --lock $script:InstallLockPath *> $null
    }
    $script:InstallLockPath = $null
}

function Invoke-WithInstallLock {
    param([scriptblock]$Body)
    Enter-InstallLock
    try {
        & $Body
    } finally {
        Exit-InstallLock
    }
}

function Invoke-StagedCopyReplace {
    param(
        [string]$Source,
        [string]$Dest
    )

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because staged copy cannot run." "Python 3.11+ not found; aborting because staged copy cannot run."
        throw "staged copy cannot run - aborting installation"
    }

    $rollbackRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-rollbacks"
    $stateRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state"
    $eventLog = Join-Path $stateRoot "$Platform-events.jsonl"
    & $py (Join-Path $script:GhostAliceRoot "_shared/install_transaction.py") "copy-replace" `
        --source $Source `
        --dest $Dest `
        --rollback-root $rollbackRoot `
        --event-log $eventLog
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Staged copy replace failed: $Dest" "Staged copy replace failed: $Dest"
        throw "staged copy replace failed - aborting installation"
    }
}

function Invoke-StagedCopyReplaceMany {
    param(
        [object[]]$Targets,
        [string]$ProgressLabel = "",
        [string[]]$ProgressStatuses = @(),
        [string]$ProgressEventFile = "",
        [string]$ProgressPlatform = "",
        [string[]]$ProgressTargetIds = @(),
        [string[]]$ProgressTargetKinds = @(),
        [string[]]$ProgressTargetStatuses = @()
    )

    if (-not $Targets -or $Targets.Count -eq 0) {
        return
    }

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because staged copy batch cannot run." "Python 3.11+ not found; aborting because staged copy batch cannot run."
        throw "staged copy batch cannot run - aborting installation"
    }

    $rollbackRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-rollbacks"
    $stateRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state"
    $eventLog = Join-Path $stateRoot "$Platform-events.jsonl"
    $pyArgs = @(
        (Join-Path $script:GhostAliceRoot "_shared/install_transaction.py"),
        "copy-replace-many",
        "--rollback-root", $rollbackRoot,
        "--event-log", $eventLog
    )
    if ($ProgressLabel) {
        $pyArgs += @("--progress-label", $ProgressLabel)
    }
    if ($ProgressEventFile) {
        $pyArgs += @("--progress-event-file", $ProgressEventFile)
        $pyArgs += @("--progress-platform", $ProgressPlatform)
    }
    foreach ($status in @($ProgressStatuses)) {
        $pyArgs += @("--progress-status", $status)
    }
    for ($i = 0; $i -lt $ProgressTargetIds.Count; $i++) {
        $pyArgs += @("--progress-target-id", $ProgressTargetIds[$i])
        $pyArgs += @("--progress-target-kind", $ProgressTargetKinds[$i])
        $pyArgs += @("--progress-target-status", $ProgressTargetStatuses[$i])
    }
    foreach ($target in @($Targets)) {
        $pyArgs += @("--target", $target.Source, $target.Dest)
    }

    & $py @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Staged copy batch replace failed." "Staged copy batch replace failed."
        throw "staged copy batch replace failed - aborting installation"
    }
}

function Invoke-InstallHooks {
    param(
        [ValidateSet("install", "uninstall", "status")]
        [string]$Action = "install",
        [string]$TargetPlatform = "claude"
    )

    Write-Host ""
    Write-Info "Running AI agent hook ${Action}... (pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace)" "Running AI agent hook ${Action}... (pending-merge-prompt + session-intent + prompt + web-search + tool-checkpoint + completion + session-start + io-trace)"

    if (-not (Test-CodexHooksSupported)) {
        Write-Host "  [install_hooks]   Codex hooks are unavailable in this runtime; skipping."
        return
    }

    $py = Find-PythonExe
    $hookPy = Join-Path (Join-Path $ScriptDir "_shared") "install_hooks.py"

    if (-not $py) {
        Write-Err "Python 3.11+ not found; hook ${Action} cannot run" "Python 3.11+ not found; hook ${Action} cannot run"
        throw "Python 3.11+ is required for hook ${Action} - aborting installation"
    }

    if (-not (Test-Path $hookPy)) {
        Write-Err "install_hooks.py not found; hook ${Action} cannot run: $hookPy" "install_hooks.py not found; hook ${Action} cannot run: $hookPy"
        throw "install_hooks.py not found - aborting installation"
    }

    $pyArgs = @(
        $hookPy,
        "--platform", $TargetPlatform,
        "--hook-shared-dir", (Join-Path $SkillsDir "_shared"),
        "--skills-dir", $SkillsDir
    )
    switch ($Action) {
        "uninstall" { $pyArgs += "--uninstall" }
        "status"    { $pyArgs += "--status" }
    }
    $visibility = $AgentVisibility
    if ($Action -eq "install" -and -not $visibility) {
        $visibility = "dynamic"
    }
    if ($visibility) {
        $pyArgs += @("--visibility", $visibility)
    }
    if ($Action -eq "install" -and $AddonSource -and $AddonSource.Count -gt 0) {
        Prepare-AddonSources
        foreach ($source in @($AddonSource)) {
            $pyArgs += @("--addon-source", $source)
        }
    }
    & $py @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Hook ${Action} failed" "Hook ${Action} failed"
        throw "Hook ${Action} failed - aborting installation"
    }
}

function Invoke-PreflightBeforeInstall {
    param(
        [string]$TargetPlatform,
        [string]$SkillsDir,
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @()
    )
    if ($TargetPlatform -notin @("claude","codex")) { return }

    $pending = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") (Join-Path "pending-merges" $TargetPlatform)
    $manifest = Join-Path $pending "manifest.json"
    $snapshot = Join-Path $pending "snapshot.json"

    $py = Find-PythonExe
    New-Item -ItemType Directory -Path $pending -Force | Out-Null

    function Test-CleanGhostAliceManagedTarget {
        param([string]$Name, [string]$Path)
        if (-not $py) { return $false }
        & $py (Join-Path $script:GhostAliceRoot "_shared/installer_assets_cli.py") classify-clean `
            --asset-id $Name --path $Path --repo-root $script:GhostAliceRoot | Out-Null
        return ($LASTEXITCODE -eq 0)
    }

    if (-not (Test-Path $snapshot)) {
        $legacyTargets = @()
        $sharedDest = Join-Path $SkillsDir "_shared"
        $sharedItem = Get-Item -LiteralPath $sharedDest -Force -ErrorAction SilentlyContinue
        if ($sharedItem -and -not ($sharedItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            if (-not (Test-CleanGhostAliceManagedTarget -Name "_shared" -Path $sharedDest)) {
                $legacyTargets += [pscustomobject]@{ Name = "_shared"; Path = $sharedDest }
            }
        }

        foreach ($target in (Get-InstallTargetsForSkillNames -SkillNames $SkillNames -ExtraTargets $ExtraTargets)) {
            $dest = Join-Path $SkillsDir $target.Name
            $item = Get-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
            if ($item -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                if (-not (Test-CleanGhostAliceManagedTarget -Name $target.Name -Path $dest)) {
                    $legacyTargets += [pscustomobject]@{ Name = $target.Name; Path = $dest }
                }
            }
        }

        if ($legacyTargets.Count -eq 0) {
            return
        }
        if (-not $py) {
            Write-Err "Python 3.11+ not found; aborting because legacy target quarantine cannot run" "Python 3.11+ not found; aborting because legacy target quarantine cannot run"
            throw "legacy target quarantine cannot run - aborting installation"
        }

        foreach ($target in $legacyTargets) {
            & $py (Join-Path $script:GhostAliceRoot "merge-companion/scripts/quarantine_legacy_cli.py") `
                --target $target.Path --target-name $target.Name `
                --pending $pending --manifest $manifest --platform $TargetPlatform
            if ($LASTEXITCODE -ne 0) {
                Write-Err "legacy target quarantine failed; aborting install: $($target.Name)" "legacy target quarantine failed; aborting install: $($target.Name)"
                throw "legacy target quarantine failed - aborting installation"
            }
        }
        return
    }

    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because merge-companion preflight diff cannot run" "Python 3.11+ not found; aborting because merge-companion preflight diff cannot run"
        throw "merge-companion preflight cannot run - aborting installation"
    }

    & $py (Join-Path $script:GhostAliceRoot "merge-companion/scripts/diff_collector_cli.py") `
        --snapshot $snapshot --pending $pending --manifest $manifest `
        --platform $TargetPlatform --skills-dir $SkillsDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "merge-companion preflight change detection failed; aborting install" "merge-companion preflight change detection failed; aborting install"
        throw "merge-companion preflight failed - aborting installation"
    }
}

function Invoke-EncodingGuardBeforeInstall {
    param(
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @()
    )

    $py = Find-PythonExe
    $guardPy = Join-Path $script:GhostAliceRoot "_shared/encoding_guard.py"

    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because encoding guard cannot run" "Python 3.11+ not found; aborting because encoding guard cannot run"
        throw "encoding guard cannot run - aborting installation"
    }
    if (-not (Test-Path $guardPy)) {
        Write-Err "encoding guard not found; aborting install: $guardPy" "encoding guard not found; aborting install: $guardPy"
        throw "encoding guard not found - aborting installation"
    }

    & $py $guardPy --repo-root $script:GhostAliceRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Err "encoding guard failed; aborting install: $script:GhostAliceRoot" "encoding guard failed; aborting install: $script:GhostAliceRoot"
        throw "encoding guard failed - aborting installation"
    }

    foreach ($target in @($ExtraTargets)) {
        & $py $guardPy --repo-root $target.Source
        if ($LASTEXITCODE -ne 0) {
            Write-Err "encoding guard failed; aborting install: $($target.Source)" "encoding guard failed; aborting install: $($target.Source)"
            throw "encoding guard failed - aborting installation"
        }
    }

    if (Test-Path -LiteralPath $SkillsDir) {
        $targetArgs = @("--repo-root", $SkillsDir)
        $sharedDest = Join-Path $SkillsDir "_shared"
        if (Test-Path -LiteralPath $sharedDest) {
            $targetArgs += @("--exclude-root", $sharedDest)
        }

        foreach ($target in (Get-InstallTargetsForSkillNames -SkillNames $SkillNames -ExtraTargets $ExtraTargets)) {
            $targetArgs += @("--exclude-root", (Join-Path $SkillsDir $target.Name))
        }

        & $py $guardPy @targetArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Err "encoding guard failed; aborting install: $SkillsDir" "encoding guard failed; aborting install: $SkillsDir"
            throw "encoding guard failed - aborting installation"
        }
    }
}

function Invoke-SnapshotAfterInstall {
    param([string]$TargetPlatform, [string]$SkillsDir)
    if ($TargetPlatform -notin @("claude","codex")) { return }

    $pending = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") (Join-Path "pending-merges" $TargetPlatform)
    $snapshot = Join-Path $pending "snapshot.json"
    $manifest = Join-Path $pending "manifest.json"

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because merge-companion snapshot cannot run" "Python 3.11+ not found; aborting because merge-companion snapshot cannot run"
        throw "merge-companion snapshot cannot run - aborting installation"
    }

    try {
        New-Item -ItemType Directory -Path $pending -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Err "merge-companion snapshot directory cannot be created; aborting install: $pending" "merge-companion snapshot directory cannot be created; aborting install: $pending"
        throw "merge-companion snapshot directory cannot be created - aborting installation"
    }

    & $py (Join-Path $script:GhostAliceRoot "merge-companion/scripts/snapshot_cli.py") `
        --output $snapshot --platform $TargetPlatform --skills-dir $SkillsDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "merge-companion snapshot capture failed; aborting install" "merge-companion snapshot capture failed; aborting install"
        throw "merge-companion snapshot capture failed - aborting installation"
    }

    Write-EmptyPendingManifestIfMissing -TargetPlatform $TargetPlatform -Manifest $manifest
}

function Write-EmptyPendingManifestIfMissing {
    param(
        [string]$TargetPlatform,
        [string]$Manifest
    )

    if (Test-Path -LiteralPath $Manifest) {
        return
    }

    $manifestDir = Split-Path -Parent $Manifest
    New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
    $payload = [ordered]@{
        version = 1
        platform = $TargetPlatform
        entries = @()
    }
    $json = $payload | ConvertTo-Json -Depth 4
    if (-not (Test-Path -LiteralPath $Manifest)) {
        [IO.File]::WriteAllText($Manifest, $json + "`n", [System.Text.UTF8Encoding]::new($false))
    }
}

function ConvertTo-InstallStatePath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $Path
    }

    try {
        return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
    } catch {
        return $Path.Replace('\', '/')
    }
}

function Get-InstallStateStringHash {
    param([string]$Value)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-InstallStateMode {
    param(
        [string]$Path,
        [switch]$CopyOnly
    )

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) {
        return "missing"
    }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        return "junction"
    }
    if ($CopyOnly) {
        return "copy"
    }
    return "copy-fallback"
}

function Get-InstallStateManagedMarkers {
    param(
        [string]$Name,
        [string]$SourcePath,
        [string]$DestPath
    )

    if ($Name -eq "_shared") {
        return @("_shared")
    }

    $markers = @()
    if ((Test-Path -LiteralPath (Join-Path $SourcePath "SKILL.md")) -or (Test-Path -LiteralPath (Join-Path $DestPath "SKILL.md"))) {
        $markers += "SKILL.md"
    }
    return $markers
}

function Get-InstallStateTargetHash {
    param(
        [string]$Path,
        [string]$InstallMode
    )

    if ($InstallMode -eq "missing" -or -not (Test-Path -LiteralPath $Path)) {
        return "missing"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if ($InstallMode -in @("junction", "symlink")) {
        $target = if ($item.Target) { [string]$item.Target } else { ConvertTo-InstallStatePath $Path }
        return Get-InstallStateStringHash "link:$target"
    }

    if (-not $item.PSIsContainer) {
        return ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash).ToLowerInvariant()
    }

    $root = $item.FullName.TrimEnd('\', '/')
    $parts = @()
    Get-ChildItem -LiteralPath $Path -Recurse -File -Force | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
        $fileHash = ((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash).ToLowerInvariant()
        $parts += "$relative`0$fileHash"
    }
    return Get-InstallStateStringHash ($parts -join "`n")
}

function Invoke-PostflightInstallVerification {
    param(
        [string]$TargetPlatform,
        [string]$SkillsRoot,
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @(),
        [switch]$CopyOnly
    )

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because post-install verification cannot run" "Python 3.11+ not found; aborting because post-install verification cannot run"
        throw "post-install verification cannot run - aborting installation"
    }

    $stateRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state"
    $verifyArgs = @("--platform", $TargetPlatform, "--state-root", $stateRoot)
    $sharedSrc = Join-Path $ScriptDir "_shared"
    $sharedDest = Join-Path $SkillsRoot "_shared"
    if (Test-Path -LiteralPath $sharedSrc) {
        $mode = Get-InstallStateMode -Path $sharedDest -CopyOnly:$CopyOnly
        $verifyArgs += @("--target", "_shared", $sharedSrc, $sharedDest, $mode)
    }

    foreach ($target in (Get-InstallTargetsForSkillNames -SkillNames $SkillNames -ExtraTargets $ExtraTargets)) {
        $dest = Join-Path $SkillsRoot $target.Name
        $mode = Get-InstallStateMode -Path $dest -CopyOnly:$CopyOnly
        $verifyArgs += @("--target", $target.Name, $target.Source, $dest, $mode)
    }

    if ($verifyArgs.Count -le 4) {
        return
    }

    & $py (Join-Path $script:GhostAliceRoot "merge-companion/scripts/install_verifier.py") @verifyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Post-install source/destination verification failed; aborting before snapshot" "Post-install source/destination verification failed; aborting before snapshot"
        throw "post-install verification failed - aborting installation"
    }
}

function Invoke-WriteOwnershipMarker {
    param(
        [string]$TargetPlatform,
        [string]$SkillsRoot,
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @(),
        [switch]$CopyOnly
    )

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; aborting because ownership markers cannot be written" "Python 3.11+ not found; aborting because ownership markers cannot be written"
        throw "ownership markers cannot be written - aborting installation"
    }

    if (-not $SkillNames -or $SkillNames.Count -eq 0) {
        $SkillNames = $AllSkills
    }

    $gitInfo = Get-InstallStateGitInfo
    $markerArgs = @(
        "--platform", $TargetPlatform,
        "--source-repo", $gitInfo.Root,
        "--source-commit", $gitInfo.Head
    )

    $sharedDest = Join-Path $SkillsRoot "_shared"
    if (Test-Path -LiteralPath $sharedDest) {
        $mode = Get-InstallStateMode -Path $sharedDest -CopyOnly:$CopyOnly
        $markerArgs += @("--target", "_shared", $sharedDest, $mode)
    }

    foreach ($target in (Get-InstallTargetsForSkillNames -SkillNames $SkillNames -ExtraTargets $ExtraTargets)) {
        $dest = Join-Path $SkillsRoot $target.Name
        $mode = Get-InstallStateMode -Path $dest -CopyOnly:$CopyOnly
        # Addon-provided skills carry AddonId so the marker is attributed to the
        # addon (owner=addon), enabling classify (plan task T2.9/C-THREAD-1).
        $addonId = if ($target.PSObject.Properties['AddonId']) { [string]$target.AddonId } else { "" }
        if ($addonId) {
            $markerArgs += @("--addon-target", $target.Name, $dest, $mode, $addonId)
        } else {
            $markerArgs += @("--target", $target.Name, $dest, $mode)
        }
    }

    if ($markerArgs.Count -le 6) {
        return
    }

    & $py (Join-Path $script:GhostAliceRoot "_shared/installer_assets_cli.py") @markerArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Ownership marker write failed; aborting before snapshot" "Ownership marker write failed; aborting before snapshot"
        throw "ownership marker write failed - aborting installation"
    }
}

function Write-AddonSidecarsAfterInstall {
    param([string]$SkillsRoot)

    if ($AddonSkip -or -not $AddonSource -or $AddonSource.Count -eq 0) {
        return
    }
    Prepare-AddonSources

    $py = Find-PythonExe
    if (-not $py) {
        Write-Err "Python 3.11+ not found; cannot write addon sidecars; aborting install" "Python 3.11+ not found; cannot write addon sidecars; aborting install"
        throw "addon sidecar write cannot run - aborting installation"
    }

    $userHome = Resolve-UserHome
    $addonsDir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "addons") $Platform
    $resourcesDir = Join-Path (Join-Path (Join-Path $userHome ".ghost-alice") "resources") $Platform
    $commandsDir = Join-Path (Resolve-ClaudeHome) "commands"
    $installedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $pyArgs = @(
        (Join-Path $script:GhostAliceRoot "_shared/addon_installer.py"),
        "write-sidecars"
    )
    foreach ($source in @($AddonSource)) {
        $pyArgs += @("--source", $source)
    }
    $pyArgs += @(
        "--platform", $Platform,
        "--addons-dir", $addonsDir,
        "--skills-dir", $SkillsRoot,
        "--installed-at", $installedAt,
        "--claude-commands-dir", $commandsDir,
        "--resources-dir", $resourcesDir
    )

    & $py @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Addon sidecar write failed; aborting install" "Addon sidecar write failed; aborting install"
        throw "addon sidecar write failed - aborting installation"
    }
    Write-Info "Addon sidecars written" "Addon sidecars written"
}

function Get-InstallStateGitInfo {
    $info = [ordered]@{
        Root = $ScriptDir
        Branch = "unknown"
        Head = "unknown"
        DirtyState = "unknown"
    }

    try {
        $gitRoot = @(& git -C $ScriptDir rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $gitRoot.Count -gt 0) {
            $info.Root = [string]$gitRoot[0]
        }

        $gitHead = @(& git -C $ScriptDir rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $gitHead.Count -gt 0) {
            $info.Head = [string]$gitHead[0]
        }

        $gitBranch = @(& git -C $ScriptDir symbolic-ref --quiet --short HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $gitBranch.Count -gt 0) {
            $info.Branch = [string]$gitBranch[0]
        } elseif ($info.Head -ne "unknown") {
            $info.Branch = "DETACHED"
        }

        $gitStatus = @(& git -C $ScriptDir status --porcelain=v1 --untracked-files=all 2>$null)
        if ($LASTEXITCODE -eq 0) {
            $info.DirtyState = if ($gitStatus.Count -gt 0) { "dirty" } else { "clean" }
        }
    } catch {
        return $info
    }

    return $info
}

function Get-SystemEnvChangesForInstallState {
    $changes = @()
    if ($script:SourceRepoHookChange) {
        $changes += $script:SourceRepoHookChange
    }
    $codexHookFeatureChange = Join-Path (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state") "codex-hook-feature-change.json"
    if ($Platform -eq "codex" -and (Test-Path -LiteralPath $codexHookFeatureChange)) {
        try {
            $change = Get-Content -LiteralPath $codexHookFeatureChange -Raw | ConvertFrom-Json
            if ($change.kind -eq "codex_hooks_feature_flag") {
                $changes += [ordered]@{
                    kind = "codex_hooks_feature_flag"
                    path = [string]$change.path
                    before_state = [string]$change.before_state
                    after_state = [string]$change.after_state
                    applied_at = [string]$change.applied_at
                }
            }
        } catch {
            Write-Warn "Could not read Codex hooks feature flag change metadata: $codexHookFeatureChange" "Could not read Codex hooks feature flag change metadata: $codexHookFeatureChange"
        }
    }
    $codexProjectTrustChange = Join-Path (Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state") "codex-project-trust-change.json"
    if ($Platform -eq "codex" -and (Test-Path -LiteralPath $codexProjectTrustChange)) {
        try {
            $change = Get-Content -LiteralPath $codexProjectTrustChange -Raw | ConvertFrom-Json
            if ($change.kind -eq "codex_project_trust") {
                $changes += [ordered]@{
                    kind = "codex_project_trust"
                    path = [string]$change.path
                    project_path = [string]$change.project_path
                    before_state = [string]$change.before_state
                    after_state = [string]$change.after_state
                    applied_at = [string]$change.applied_at
                }
            }
        } catch {
            Write-Warn "Could not read Codex project trust change metadata: $codexProjectTrustChange" "Could not read Codex project trust change metadata: $codexProjectTrustChange"
        }
    }
    return @($changes)
}

function Write-InstallStateManifest {
    param(
        [string]$TargetPlatform,
        [string]$SkillsRoot,
        [string[]]$SkillNames,
        [object[]]$ExtraTargets = @(),
        [switch]$CopyOnly
    )

    try {
        if (-not $SkillNames -or $SkillNames.Count -eq 0) {
            $SkillNames = $AllSkills
        }

        $targets = @()
        $installedAt = [DateTimeOffset]::UtcNow.ToString("o")
        $sharedSrc = Join-Path $ScriptDir "_shared"
        $sharedDest = Join-Path $SkillsRoot "_shared"
        if (Test-Path -LiteralPath $sharedSrc) {
            $mode = Get-InstallStateMode -Path $sharedDest -CopyOnly:$CopyOnly
            $targets += [ordered]@{
                target_name = "_shared"
                source_path = ConvertTo-InstallStatePath $sharedSrc
                dest_path = ConvertTo-InstallStatePath $sharedDest
                install_mode = $mode
                target_tree_hash = Get-InstallStateTargetHash -Path $sharedDest -InstallMode $mode
                managed_markers = @(Get-InstallStateManagedMarkers -Name "_shared" -SourcePath $sharedSrc -DestPath $sharedDest)
                installed_at = $installedAt
            }
        }

        foreach ($target in (Get-InstallTargetsForSkillNames -SkillNames $SkillNames -ExtraTargets $ExtraTargets)) {
            $dest = Join-Path $SkillsRoot $target.Name
            $mode = Get-InstallStateMode -Path $dest -CopyOnly:$CopyOnly
            $entry = [ordered]@{
                target_name = $target.Name
                source_path = ConvertTo-InstallStatePath $target.Source
                dest_path = ConvertTo-InstallStatePath $dest
                install_mode = $mode
                target_tree_hash = Get-InstallStateTargetHash -Path $dest -InstallMode $mode
                managed_markers = @(Get-InstallStateManagedMarkers -Name $target.Name -SourcePath $target.Source -DestPath $dest)
                installed_at = $installedAt
            }
            $addonId = if ($target.PSObject.Properties['AddonId']) { [string]$target.AddonId } else { "" }
            if ($addonId) {
                $entry["addon_id"] = $addonId
                $entry["origin"] = "addon:$addonId"
                $entry["owner"] = "addon"
            }
            $targets += $entry
        }

        $gitInfo = Get-InstallStateGitInfo
        $stateRoot = Join-Path (Join-Path (Resolve-UserHome) ".ghost-alice") "install-state"
        New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
        $manifestPath = Join-Path $stateRoot "$TargetPlatform.json"
        $manifest = [ordered]@{
            schema_version = 1
            platform = $TargetPlatform
            installed_at = $installedAt
            source_root = ConvertTo-InstallStatePath $gitInfo.Root
            source_branch = $gitInfo.Branch
            source_head = $gitInfo.Head
            source_dirty_state = $gitInfo.DirtyState
            remote_freshness_state = "unverified"
            targets = $targets
            system_env_changes = @(Get-SystemEnvChangesForInstallState)
        }

        $json = $manifest | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText($manifestPath, $json + "`n", [System.Text.UTF8Encoding]::new($false))
        Write-Info "Install-state manifest: $manifestPath" "Install-state manifest: $manifestPath"
    } catch {
        Write-Err "Install-state manifest write failed; aborting install: $($_.Exception.Message)" "Install-state manifest write failed; aborting install: $($_.Exception.Message)"
        throw "install-state manifest write failed - aborting installation"
    }
}

function Get-InstallTargetCount {
    param([object[]]$Targets)

    $total = @($Targets).Count
    if (Test-Path (Join-Path $ScriptDir "_shared")) {
        $total++
    }
    return $total
}

function Get-InstallTargetStatus {
    param(
        [string]$Source,
        [string]$Dest,
        [switch]$CopyOnly
    )

    if (-not (Test-Path $Dest)) {
        return "new"
    }

    if (-not $CopyOnly) {
        $item = Get-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $target = [string]$item.Target
            if ($target -eq $Source) {
                return "current"
            }
        }
    }

    return "updated"
}

function Get-InstallSyncStatuses {
    param(
        [object[]]$Targets,
        [string]$SkillsRoot,
        [switch]$CopyOnly
    )

    $statuses = @()
    $sharedSrc = Join-Path $ScriptDir "_shared"
    if (Test-Path $sharedSrc) {
        $statuses += Get-InstallTargetStatus `
            -Source $sharedSrc `
            -Dest (Join-Path $SkillsRoot "_shared") `
            -CopyOnly:$CopyOnly
    }

    foreach ($target in @($Targets)) {
        $statuses += Get-InstallTargetStatus `
            -Source $target.Source `
            -Dest (Join-Path $SkillsRoot $target.Name) `
            -CopyOnly:$CopyOnly
    }
    return $statuses
}

function Invoke-Install {
    param([string[]]$SkillNames)

    $previousCompactOutput = $script:InstallCompactOutput
    $script:InstallCompactOutput = $true

    try {
        if (-not $SkillNames -or $SkillNames.Count -eq 0) {
            $SkillNames = $AllSkills
        }

        $allTargets = @()
        foreach ($skill in $SkillNames) {
            $targets = Expand-SkillTargets $skill
            if ($targets.Count -eq 0) {
                Write-Err "Skill not found: $skill" "Skill not found: $skill"
                Write-Err "Run .\\install.cmd -List to see available skills." "Run .\\install.cmd -List to see available skills."
                exit 1
            }
            $allTargets += $targets
        }
        $addonTargets = @(Get-AddonTargets)
        $allTargets += $addonTargets

        $copyOnly = ($Platform -in @("codex"))
        $skillTargetTotal = @($allTargets).Count
        $totalTargetCount = Get-InstallTargetCount -Targets $allTargets
        $supportTargetTotal = $totalTargetCount - $skillTargetTotal
        $visibility = Resolve-EffectiveVisibility -Flag $AgentVisibility

        if (-not (Test-Path $SkillsDir)) {
            New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null
        }
        Invoke-LoggedIfCompact { Test-SourceHealth }
        Invoke-LoggedIfCompact { Invoke-DeprecatedInstalledSkillCleanup -SkillsRoot $SkillsDir }
        try {
            Invoke-LoggedIfCompact { Invoke-EncodingGuardBeforeInstall -SkillNames $SkillNames -ExtraTargets $addonTargets }
        } catch {
            throw "encoding guard failed; invalid-utf8 or semantic asset details: $script:InstallReportLogFile"
        }
        Invoke-LoggedIfCompact { Invoke-PreflightBeforeInstall -TargetPlatform $Platform -SkillsDir $SkillsDir -SkillNames $SkillNames -ExtraTargets $addonTargets }

        if ((Test-InstallReportEnabled) -and (Test-LiveCounterEnabled)) {
            Write-InstallReportStart -PlatformLabel $Platform -TotalTargets $totalTargetCount -Visibility $visibility
        }

        $syncStatuses = @(Get-InstallSyncStatuses -Targets $allTargets -SkillsRoot $SkillsDir -CopyOnly:$copyOnly)
        $syncCurrent = @($syncStatuses | Where-Object { $_ -eq "current" }).Count
        $syncUpdated = @($syncStatuses | Where-Object { $_ -eq "updated" }).Count
        $syncInstalled = @($syncStatuses | Where-Object { $_ -eq "new" }).Count

        $installed = 0
        $skipped = 0

        if ($copyOnly) {
            $copyTargets = @()
            $copyStatuses = @()
            $copyStatusIndex = 0
            $sharedSrc = Join-Path $ScriptDir "_shared"
            $sharedDest = Join-Path $SkillsDir "_shared"
            if (Test-Path $sharedSrc) {
                $copyStatuses += $syncStatuses[$copyStatusIndex]
                $copyStatusIndex++
                if (Test-Path $sharedDest) {
                    Write-Warn "_shared - replacing existing install" "_shared - replacing existing install"
                }
                $copyTargets += [pscustomobject]@{
                    Source = $sharedSrc
                    Dest = $sharedDest
                    DisplayName = "_shared"
                    TargetId = "_shared"
                    TargetKind = "support"
                    IsShared = $true
                }
            }

            foreach ($t in $allTargets) {
                $dest = Join-Path $SkillsDir $t.Name
                $display = if ($t.IsFamily) { "$($t.Family)/$($t.Name)" } else { $t.Name }
                $copyStatuses += $syncStatuses[$copyStatusIndex]
                $copyStatusIndex++
                if (Test-Path $dest) {
                    Write-Warn "$display - replacing existing install" "$display - replacing existing install"
                }
                $copyTargets += [pscustomobject]@{
                    Source = $t.Source
                    Dest = $dest
                    DisplayName = $display
                    TargetId = $t.Name
                    TargetKind = "skill"
                    IsShared = $false
                }
                $installed++
            }

            $progressLabel = ""
            if ((Test-InstallReportEnabled) -and (Test-LiveCounterEnabled)) {
                $progressLabel = "  [2/5] Skill sync         "
            } elseif ((-not $script:InstallCompactOutput) -and (Test-LiveCounterEnabled)) {
                $progressLabel = T "[2/5] Skill sync" "[2/5] Skill sync"
            }
            $copyEventsDuringBatch = $false
            $progressTargetIds = @()
            $progressTargetKinds = @()
            $progressTargetStatuses = @()
            if ((Test-InstallReportEnabled) -and $script:InstallReportEventFile) {
                $copyEventsDuringBatch = $true
                for ($i = 0; $i -lt $copyTargets.Count; $i++) {
                    $progressTargetIds += $copyTargets[$i].TargetId
                    $progressTargetKinds += $copyTargets[$i].TargetKind
                    $progressTargetStatuses += $copyStatuses[$i]
                }
            }
            Invoke-StagedCopyReplaceMany `
                -Targets $copyTargets `
                -ProgressLabel $progressLabel `
                -ProgressStatuses $copyStatuses `
                -ProgressEventFile $(if ($copyEventsDuringBatch) { $script:InstallReportEventFile } else { "" }) `
                -ProgressPlatform $Platform `
                -ProgressTargetIds $progressTargetIds `
                -ProgressTargetKinds $progressTargetKinds `
                -ProgressTargetStatuses $progressTargetStatuses
            if (-not $copyEventsDuringBatch) {
                for ($i = 0; $i -lt $copyTargets.Count; $i++) {
                    Write-InstallReportTargetEvent `
                        -TargetPlatform $Platform `
                        -TargetId $copyTargets[$i].TargetId `
                        -TargetKind $copyTargets[$i].TargetKind `
                        -Status $copyStatuses[$i]
                }
            }
            Write-SkillSyncSummary `
                -SkillTargets $skillTargetTotal `
                -SupportTargets $supportTargetTotal `
                -InstalledCount $syncInstalled `
                -UpdatedCount $syncUpdated `
                -SkippedCount $syncCurrent `
                -ModeLabel (T "copy-only compatibility mode" "copy-only compatibility mode")
            foreach ($target in $copyTargets) {
                if ($target.IsShared) {
                    Write-Ok "_shared -> copied" "_shared -> copied"
                } else {
                    Write-Ok "$($target.DisplayName) -> copied (copy-only compatibility mode)" "$($target.DisplayName) -> copied (copy-only compatibility mode)"
                }
            }
        } else {
            $syncStatusIndex = 0
            $sharedSrc = Join-Path $ScriptDir "_shared"
            if (Test-Path $sharedSrc) {
                $sharedStatus = $syncStatuses[$syncStatusIndex]
                $syncStatusIndex++
                Install-Shared -SkillsRoot $SkillsDir -CopyOnly:$copyOnly
                Write-InstallReportTargetEvent -TargetPlatform $Platform -TargetId "_shared" -TargetKind "support" -Status $sharedStatus
            }

            foreach ($t in $allTargets) {
                $dest = Join-Path $SkillsDir $t.Name
                $display = if ($t.IsFamily) { "$($t.Family)/$($t.Name)" } else { $t.Name }
                $targetStatus = $syncStatuses[$syncStatusIndex]
                $syncStatusIndex++
                Install-OneTarget -DisplayName $display -Source $t.Source -Dest $dest `
                    -InstalledRef ([ref]$installed) -SkippedRef ([ref]$skipped) -CopyOnly:$copyOnly
                Write-InstallReportTargetEvent -TargetPlatform $Platform -TargetId $t.Name -TargetKind "skill" -Status $targetStatus
            }
        }

        if ($Platform -eq "codex") {
            Invoke-LoggedIfCompact { Set-CodexBootstrap }
        }

        Write-Ok ("Done: {0} installed, {1} skipped" -f $installed, $skipped) ("Done: {0} installed, {1} skipped" -f $installed, $skipped)
        Write-Info "Install path: $SkillsDir" "Install path: $SkillsDir"
        if ($copyOnly) {
            Write-Info "To update skills safely: cd $ScriptDir; .\install.cmd -UpdateSource, then rerun .\install.cmd" "To update skills safely: cd $ScriptDir; .\install.cmd -UpdateSource, then rerun .\install.cmd"
        } else {
            Write-Info "To update skills safely: cd $ScriptDir; .\install.cmd -UpdateSource" "To update skills safely: cd $ScriptDir; .\install.cmd -UpdateSource"
        }
        if (($installed -gt 0) -and (-not $copyOnly)) {
            Write-Info "Junction installs refresh linked skills after the checkout fast-forwards through the safe source updater." "Junction installs refresh linked skills after the checkout fast-forwards through the safe source updater."
        }

        Invoke-LoggedIfCompact { Invoke-InstallHooks -Action "install" -TargetPlatform $Platform }
        Invoke-LoggedIfCompact { Invoke-PostflightInstallVerification -TargetPlatform $Platform -SkillsRoot $SkillsDir -SkillNames $SkillNames -ExtraTargets $addonTargets -CopyOnly:$copyOnly }
        Invoke-LoggedIfCompact { Invoke-WriteOwnershipMarker -TargetPlatform $Platform -SkillsRoot $SkillsDir -SkillNames $SkillNames -ExtraTargets $addonTargets -CopyOnly:$copyOnly }
        Invoke-LoggedIfCompact { Invoke-SnapshotAfterInstall -TargetPlatform $Platform -SkillsDir $SkillsDir }
        Invoke-LoggedIfCompact { Write-AddonSidecarsAfterInstall -SkillsRoot $SkillsDir }
        Invoke-LoggedIfCompact { Write-InstallStateManifest -TargetPlatform $Platform -SkillsRoot $SkillsDir -SkillNames $SkillNames -ExtraTargets $addonTargets -CopyOnly:$copyOnly }

        Write-Info "[Ghost-ALICE] User: when local changes are detected during the agent tool update, they are backed up instead of overwritten. Next time you open Claude/Codex, please ask the line below." "[Ghost-ALICE] User: when local changes are detected during the agent tool update, they are backed up instead of overwritten. Next time you open Claude/Codex, please ask the line below."
        Write-InstallLogLine ""
        Write-InstallLogLine ("    " + (T "Please review backed-up changes." "Please review backed-up changes."))
        Write-InstallLogLine ""
        Write-Info "[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. Missing, empty, or unparsable manifests pass silently." "[Ghost-ALICE] Tech: undecided entries live in ~/.ghost-alice/pending-merges/<platform>/manifest.json. Missing, empty, or unparsable manifests pass silently."

        Write-InstallReportEvent -TargetPlatform $Platform -TotalTargets $totalTargetCount -Current $syncCurrent -Updated $syncUpdated -New $syncInstalled
        if (Test-InstallReportEnabled) {
            if (Test-LiveCounterEnabled) {
                if (-not $copyOnly) {
                    [Console]::Write("`r" + (Format-SkillSyncLine -CurrentCount $syncCurrent -UpdatedCount $syncUpdated -NewCount $syncInstalled))
                    [Console]::WriteLine()
                }
                Write-InstallReportTail -PlatformLabel $Platform -Visibility $visibility
            } else {
                Write-InstallReportFull -PlatformLabel $Platform -TotalTargets $totalTargetCount -CurrentCount $syncCurrent -UpdatedCount $syncUpdated -NewCount $syncInstalled -Visibility $visibility
            }
        }
    } finally {
        $script:InstallCompactOutput = $previousCompactOutput
    }
}
