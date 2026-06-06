# Ghost-ALICE installer library: report
# Dot-sourced by install.ps1. Do not run directly.

function Resolve-EffectiveVisibility {
    param([string]$Flag)
    if ($Flag) { return $Flag }
    $pythonExe = Find-PythonExe
    if ($pythonExe) {
        $shared = Join-Path $script:GhostAliceRoot "_shared"
        $code = "import sys, os; sys.path.insert(0, sys.argv[1]); import runtime_config; print(runtime_config.load_config()['agent_visibility']['profile'])"
        $prof = & $pythonExe -c $code $shared 2>$null
        if ($LASTEXITCODE -eq 0 -and $prof) {
            $prof = "$prof".Trim()
            if ($prof -in @("strict", "dynamic", "minimal")) { return $prof }
        }
    }
    return "dynamic"
}

function Initialize-InstallLog {
    if ($script:InstallReportLogFile) {
        $env:GHOST_ALICE_INSTALL_LOG_FILE = $script:InstallReportLogFile
        return
    }

    $logRoot = Join-Path (Join-Path $HOME ".ghost-alice") "install"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
    $script:InstallReportLogFile = Join-Path $logRoot "$timestamp.log"
    $env:GHOST_ALICE_INSTALL_LOG_FILE = $script:InstallReportLogFile
}

function Write-InstallLogLine {
    param([string]$Line)
    Initialize-InstallLog
    Add-Content -LiteralPath $script:InstallReportLogFile -Encoding UTF8 -Value $Line
}

function Write-InstallMessage {
    param(
        [string]$Prefix,
        [string]$Msg,
        [string]$LegacyMsg = "",
        [ConsoleColor]$Color = [ConsoleColor]::White
    )

    $line = $Prefix + (T $Msg $LegacyMsg)
    if ($script:InstallCompactOutput) {
        Write-InstallLogLine $line
        return
    }
    Write-Host $line -ForegroundColor $Color
}

function Write-Info  { param([string]$Msg, [string]$LegacyMsg = "") Write-InstallMessage -Prefix "[INFO] " -Msg $Msg -LegacyMsg $LegacyMsg -Color Cyan }

function Write-Ok    { param([string]$Msg, [string]$LegacyMsg = "") Write-InstallMessage -Prefix "[OK] " -Msg $Msg -LegacyMsg $LegacyMsg -Color Green }

function Write-Warn  { param([string]$Msg, [string]$LegacyMsg = "") Write-InstallMessage -Prefix "[WARN] " -Msg $Msg -LegacyMsg $LegacyMsg -Color Yellow }

function Write-Err   { param([string]$Msg, [string]$LegacyMsg = "") Write-InstallMessage -Prefix "[ERROR] " -Msg $Msg -LegacyMsg $LegacyMsg -Color Red }

function Test-InstallReportEnabled {
    return ($script:InstallCompactOutput -and -not $script:InstallReportChild)
}

function Invoke-LoggedIfCompact {
    param([scriptblock]$Body)

    if (-not $script:InstallCompactOutput) {
        & $Body
        return
    }

    Initialize-InstallLog
    $output = @()
    try {
        $output = & $Body *>&1
    } catch {
        $output += $_
        foreach ($line in @($output)) {
            Write-InstallLogLine ([string]$line)
        }
        throw
    }
    foreach ($line in @($output)) {
        Write-InstallLogLine ([string]$line)
    }
}

function Join-InstallLabels {
    param([string[]]$Items)
    return (@($Items) -join ", ")
}

function Get-HookSuiteLabel {
    return "prompt, session-intent, web-search-first, tool-checkpoint, completion, session-start, io-trace"
}

function Format-SkillSyncLine {
    param(
        [int]$CurrentCount,
        [int]$UpdatedCount,
        [int]$NewCount
    )
    return ("  [2/5] Skill sync          [{0}] [Current], [{1}] [updated], [{2}] [newly added]" -f $CurrentCount, $UpdatedCount, $NewCount)
}

function Format-CommonSkillSyncLine {
    param([int]$CommonTargets)
    return ("  [2/5] Skill sync          [{0}] common targets" -f $CommonTargets)
}

function Format-ProgressBar {
    param(
        [int]$DoneCount,
        [int]$TotalCount,
        [int]$Width = 30
    )

    $filled = 0
    if ($TotalCount -gt 0) {
        $filled = [int][Math]::Floor(($DoneCount * $Width) / $TotalCount)
    }
    if ($filled -lt 0) { $filled = 0 }
    if ($filled -gt $Width) { $filled = $Width }
    return (("#" * $filled) + ("-" * ($Width - $filled)))
}

function Format-CommonTargetProgressLine {
    param(
        [int]$DoneCount,
        [int]$TotalCount,
        [string]$Suffix = "common targets synced"
    )

    # In-place (`r) refresh leaves a stale tail when the previous frame was longer
    # (e.g. "...synced" overwritten by "...For X [1/2]" renders "[1/2]nced"). The suffix length
    # varies ("For X [i/n]" vs "common targets synced on all platforms"), so right-pad to a fixed
    # width so a shorter line fully overwrites the previous one. ANSI clear-EOL is not used because
    # Windows PowerShell 5.1 does not guarantee VT support. Only this progress line is printed to
    # the live console (detailed logs go to the file), so the padding is safe.
    $line = ("        Common targets      [{0}] [{1}/{2}] {3}" -f (Format-ProgressBar -DoneCount $DoneCount -TotalCount $TotalCount), $DoneCount, $TotalCount, $Suffix)
    return $line.PadRight(110)
}

function Write-AutoAnimateCommonTargetProgress {
    param(
        [int]$FromCount,
        [int]$ToCount,
        [int]$TotalCount,
        [string]$Suffix = "common targets synced",
        [int]$StepDelayMs = 20
    )

    # PowerShell tween corresponding to shell report.sh's report_auto_animate_target_operation_progress_line.
    # Instead of jumping the milestone count in one shot (0->25->50), it redraws From+1..To one cell at a time
    # to create a smooth roll-up like macOS. Like the caller, it overwrites in place (`r) + fixed width
    # (Format-CommonTargetProgressLine PadRight). ANSI clear-EOL is not used because Windows
    # PowerShell 5.1/conhost does not guarantee VT by default. Start-Sleep resolution is
    # bound to the Windows timer (~15.6ms), so 20ms effectively becomes ~16-31ms frames, which is enough for animation.
    $from = $FromCount
    $to = $ToCount
    if ($from -lt 0) { $from = 0 }
    if ($to -lt $from) { $to = $from }
    if ($to -gt $TotalCount) { $to = $TotalCount }

    for ($completed = $from + 1; $completed -le $to; $completed++) {
        [Console]::Write("`r")
        [Console]::Write((Format-CommonTargetProgressLine -DoneCount $completed -TotalCount $TotalCount -Suffix $Suffix))
        if ($completed -lt $to) {
            Start-Sleep -Milliseconds $StepDelayMs
        }
    }
}

function Write-InstallReportStart {
    param(
        [string]$PlatformLabel,
        [int]$TotalTargets,
        [string]$Visibility = "dynamic"
    )

    Initialize-InstallLog
    Write-Host "Ghost-ALICE OS installation Process Report"
    Write-Host ""
    Write-Host "Target"
    Write-Host ("  Platform: {0}" -f $PlatformLabel)
    Write-Host ("  Skills: [{0}] targets" -f $TotalTargets)
    Write-Host "  Hooks: enabled"
    Write-Host ("  Visibility Level: [{0}]" -f $Visibility)
    Write-Host ""
    Write-Host "Progress"
    Write-Host "  [1/5] Preflight           ok"
    Write-Host -NoNewline (Format-SkillSyncLine -CurrentCount 0 -UpdatedCount 0 -NewCount 0)
}

function Write-InstallReportTail {
    param(
        [string]$PlatformLabel,
        [string]$Visibility = "dynamic"
    )

    Write-Host ("  [3/5] Hooks               {0} enabled" -f (Get-HookSuiteLabel))
    Write-Host ("  [4/5] Runtime config      {0} hooks=true, Visibility Level=[{1}]" -f $PlatformLabel, $Visibility)
    Write-Host "  [5/5] Verification        ok"
    Write-Host ""
    Write-Host "Attention"
    Write-Host "  - visibility can be changed later with /visibility between: dynamic | minimal | strict"
    Write-Host ""
    Write-Host "Details"
    Write-Host ("  log: {0}" -f $script:InstallReportLogFile)
    Write-Host "  rerun with --verbose to show per-skill actions"
}

function Write-InstallReportFull {
    param(
        [string]$PlatformLabel,
        [int]$TotalTargets,
        [int]$CurrentCount,
        [int]$UpdatedCount,
        [int]$NewCount,
        [string]$Visibility = "dynamic"
    )

    Initialize-InstallLog
    Write-Host "Ghost-ALICE OS installation Process Report"
    Write-Host ""
    Write-Host "Target"
    Write-Host ("  Platform: {0}" -f $PlatformLabel)
    Write-Host ("  Skills: [{0}] targets" -f $TotalTargets)
    Write-Host "  Hooks: enabled"
    Write-Host ("  Visibility Level: [{0}]" -f $Visibility)
    Write-Host ""
    Write-Host "Progress"
    Write-Host "  [1/5] Preflight           ok"
    Write-Host (Format-SkillSyncLine -CurrentCount $CurrentCount -UpdatedCount $UpdatedCount -NewCount $NewCount)
    Write-InstallReportTail -PlatformLabel $PlatformLabel -Visibility $Visibility
}

function Write-InstallReportAutoFull {
    param(
        [string]$PlatformLabel,
        [int]$CommonTargets,
        [int]$SyncedTargets,
        [string]$Visibility = "dynamic"
    )

    Initialize-InstallLog
    Write-Host "Ghost-ALICE OS installation Process Report"
    Write-Host ""
    Write-Host "Target"
    Write-Host ("  Platform: {0}" -f $PlatformLabel)
    Write-Host ("  Skills: [{0}] common targets" -f $CommonTargets)
    Write-Host "  Hooks: enabled"
    Write-Host ("  Visibility Level: [{0}]" -f $Visibility)
    Write-Host ""
    Write-Host "Progress"
    Write-Host "  [1/5] Preflight           ok"
    Write-Host (Format-CommonSkillSyncLine -CommonTargets $CommonTargets)
    Write-Host (Format-CommonTargetProgressLine -DoneCount $SyncedTargets -TotalCount $CommonTargets -Suffix "common targets synced on all platforms")
    Write-InstallReportTail -PlatformLabel $PlatformLabel -Visibility $Visibility
}

function Write-InstallReportAutoStart {
    param(
        [string]$PlatformLabel,
        [int]$CommonTargets,
        [string]$Visibility = "dynamic"
    )

    Initialize-InstallLog
    Write-Host "Ghost-ALICE OS installation Process Report"
    Write-Host ""
    Write-Host "Target"
    Write-Host ("  Platform: {0}" -f $PlatformLabel)
    Write-Host ("  Skills: [{0}] common targets" -f $CommonTargets)
    Write-Host "  Hooks: enabled"
    Write-Host ("  Visibility Level: [{0}]" -f $Visibility)
    Write-Host ""
    Write-Host "Progress"
    Write-Host "  [1/5] Preflight           ok"
    Write-Host (Format-CommonSkillSyncLine -CommonTargets $CommonTargets)
    [Console]::Write((Format-CommonTargetProgressLine -DoneCount 0 -TotalCount $CommonTargets))
}

function Write-InstallReportEvent {
    param(
        [string]$TargetPlatform,
        [int]$TotalTargets,
        [int]$Current,
        [int]$Updated,
        [int]$New
    )

    if (-not $script:InstallReportEventFile) {
        return
    }

    $eventDir = Split-Path -Parent $script:InstallReportEventFile
    if ($eventDir) {
        New-Item -ItemType Directory -Path $eventDir -Force | Out-Null
    }
    $reportEvent = [ordered]@{
        type = "platform-result"
        platform = $TargetPlatform
        total_targets = $TotalTargets
        current = $Current
        updated = $Updated
        new = $New
        verification = "ok"
        hooks = "enabled"
    }
    ($reportEvent | ConvertTo-Json -Compress) | Add-Content -LiteralPath $script:InstallReportEventFile -Encoding UTF8
}

function Write-InstallReportTargetEvent {
    param(
        [string]$TargetPlatform,
        [string]$TargetId,
        [ValidateSet("skill", "support")]
        [string]$TargetKind,
        [ValidateSet("current", "updated", "new")]
        [string]$Status
    )

    if (-not $script:InstallReportEventFile) {
        return
    }

    $eventDir = Split-Path -Parent $script:InstallReportEventFile
    if ($eventDir) {
        New-Item -ItemType Directory -Path $eventDir -Force | Out-Null
    }
    $reportEvent = [ordered]@{
        type = "target-result"
        platform = $TargetPlatform
        target_id = $TargetId
        target_kind = $TargetKind
        status = $Status
    }
    ($reportEvent | ConvertTo-Json -Compress) | Add-Content -LiteralPath $script:InstallReportEventFile -Encoding UTF8
}

function Read-AllCommonTargetProgress {
    param(
        [string]$EventFile,
        [int]$PlatformCount
    )

    if (-not (Test-Path $EventFile)) {
        return 0
    }

    $targets = @{}
    foreach ($line in (Get-Content -LiteralPath $EventFile -Encoding UTF8)) {
        if (-not $line -or -not $line.Trim()) { continue }
        try {
            $reportEvent = $line | ConvertFrom-Json
        } catch {
            continue
        }
        if ($reportEvent.type -ne "target-result") { continue }
        if (-not $reportEvent.platform -or -not $reportEvent.target_id -or -not $reportEvent.target_kind) { continue }
        $key = "{0}:{1}" -f $reportEvent.target_kind, $reportEvent.target_id
        if (-not $targets.ContainsKey($key)) {
            $targets[$key] = @{}
        }
        $targets[$key][[string]$reportEvent.platform] = $true
    }

    $complete = 0
    foreach ($platforms in $targets.Values) {
        if ($platforms.Count -ge $PlatformCount) {
            $complete++
        }
    }
    return $complete
}

function Read-WeightedCommonTargetProgress {
    param(
        [string]$EventFile,
        [int]$PlatformCount,
        [int]$TotalCount
    )

    if (-not (Test-Path $EventFile)) {
        return 0
    }

    $pairs = @{}
    foreach ($line in (Get-Content -LiteralPath $EventFile -Encoding UTF8)) {
        if (-not $line -or -not $line.Trim()) { continue }
        try {
            $reportEvent = $line | ConvertFrom-Json
        } catch {
            continue
        }
        if ($reportEvent.type -ne "target-result") { continue }
        if (-not $reportEvent.platform -or -not $reportEvent.target_id -or -not $reportEvent.target_kind) { continue }
        $key = "{0}:{1}:{2}" -f $reportEvent.target_kind, $reportEvent.target_id, $reportEvent.platform
        $pairs[$key] = $true
    }

    if ($PlatformCount -le 0) {
        return 0
    }
    return [Math]::Min($TotalCount, [int][Math]::Floor($pairs.Count / $PlatformCount))
}

function Test-LiveCounterEnabled {
    $progress = [string]$env:GHOST_ALICE_INSTALL_PROGRESS
    switch ($progress) {
        "0" { return $false }
        "false" { return $false }
        "False" { return $false }
        "FALSE" { return $false }
        "off" { return $false }
        "OFF" { return $false }
        "no" { return $false }
        "NO" { return $false }
    }

    try {
        if ([Console]::IsOutputRedirected) {
            return $false
        }
    } catch {
        return $false
    }
    return $true
}

function Get-CountLabel {
    param(
        [int]$Count,
        [string]$Singular,
        [string]$Plural
    )
    if ($Count -eq 1) {
        return ("{0} {1}" -f $Count, $Singular)
    }
    return ("{0} {1}" -f $Count, $Plural)
}

function Write-SkillSyncSummary {
    param(
        [int]$SkillTargets,
        [int]$SupportTargets,
        [int]$InstalledCount,
        [int]$UpdatedCount,
        [int]$SkippedCount,
        [string]$ModeLabel = ""
    )

    $skillLabel = Get-CountLabel -Count $SkillTargets -Singular (T "skill target" "skill target") -Plural (T "skill targets" "skill targets")
    $supportLabel = Get-CountLabel -Count $SupportTargets -Singular (T "support target" "support target") -Plural (T "support targets" "support targets")
    $message = "[2/5] Skill sync: {0}, {1}; {2} installed, {3} updated, {4} skipped" -f $skillLabel, $supportLabel, $InstalledCount, $UpdatedCount, $SkippedCount
    if ($ModeLabel) {
        $message = "$message ($ModeLabel)"
    }
    Write-Info $message $message
}
