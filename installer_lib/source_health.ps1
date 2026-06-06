# Ghost-ALICE installer library: source_health
# Dot-sourced by install.ps1. Do not run directly.

function Test-SourceHealth {
    if ($SkipSourceHealth) {
        Write-Warn "Skipping source health gate (-SkipSourceHealth)" "Skipping source health gate (-SkipSourceHealth)"
        return
    }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Warn "git not found; source health is unverified (continuing)" "git not found; source health is unverified (continuing)"
        return
    }

    & git -C $ScriptDir rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "source is not a git worktree; source health is unverified (continuing)" "source is not a git worktree; source health is unverified (continuing)"
        return
    }

    $branch = (& git -C $ScriptDir symbolic-ref --quiet --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $branch) { $branch = "DETACHED" }
    $head = (& git -C $ScriptDir rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $head) { $head = "unknown" }
    $upstream = (& git -C $ScriptDir rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $upstream) { $upstream = "none" }

    $trackedDirty = $false
    $untracked = $false
    $statusLines = & git -C $ScriptDir status --porcelain=v1 --untracked-files=all
    foreach ($line in @($statusLines)) {
        if (-not $line) { continue }
        if ($line.StartsWith("?? ")) {
            $untracked = $true
        } elseif ($line.StartsWith("!! ")) {
            continue
        } else {
            $trackedDirty = $true
        }
    }

    Write-Info "Source health: branch=$branch, head=$head, upstream=$upstream" "Source health: branch=$branch, head=$head, upstream=$upstream"

    if ($upstream -ne "none") {
        $countsOutput = & git -C $ScriptDir rev-list --left-right --count 'HEAD...@{u}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $countsOutput) {
            $parts = (($countsOutput -join " ").Trim() -split "\s+")
            $ahead = 0
            $behind = 0
            if ($parts.Count -ge 2) {
                [int]::TryParse($parts[0], [ref]$ahead) | Out-Null
                [int]::TryParse($parts[1], [ref]$behind) | Out-Null
            }

            if ($ahead -gt 0 -and $behind -gt 0) {
                Write-Warn "Source branch has diverged from its upstream; continuing because local commits may be intentional." "Source branch has diverged from its upstream; continuing because local commits may be intentional."
                Write-Warn "Before release, run git fetch origin, inspect git status, and reconcile this checkout history." "Before release, run git fetch origin, inspect git status, and reconcile this checkout history."
            } elseif ($behind -gt 0) {
                Write-Err "Source branch is behind its upstream; refusing to install from stale source." "Source branch is behind its upstream; refusing to install from stale source."
                Write-Err "Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout before installing. If this local source is intentional, rerun with -SkipSourceHealth." "Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout before installing. If this local source is intentional, rerun with -SkipSourceHealth."
                throw "Source branch behind upstream"
            } elseif ($ahead -gt 0) {
                Write-Warn "Source branch is ahead of its upstream; continuing because local commits may be intentional." "Source branch is ahead of its upstream; continuing because local commits may be intentional."
            }
        } else {
            Write-Warn "Source upstream comparison failed; continuing with local source health checks only." "Source upstream comparison failed; continuing with local source health checks only."
        }
    }

    if ($untracked) {
        Write-Warn "Source tree has untracked files; continuing because tracked files are clean." "Source tree has untracked files; continuing because tracked files are clean."
    }

    if ($trackedDirty) {
        Write-Warn "Source tree has tracked local changes; continuing. Installed target user edits remain protected by pending-merges backup and manifest checks." "Source tree has tracked local changes; continuing. Installed target user edits remain protected by pending-merges backup and manifest checks."
        return
    }
}

function Initialize-GitHooks {
    $hookDir = "hooks"
    $currentPath = & git -C $ScriptDir config --local core.hooksPath 2>$null
    $hadCurrentPath = ($LASTEXITCODE -eq 0)
    if (-not $hadCurrentPath) { $currentPath = "" }
    if ($currentPath -ne $hookDir) {
        & git -C $ScriptDir config --local core.hooksPath $hookDir 2>$null
        if ($LASTEXITCODE -eq 0) {
            $script:SourceRepoHookChange = [ordered]@{
                kind = "source_repo_hook_path"
                repo_root = ConvertTo-InstallStatePath $ScriptDir
                before_present = [bool]$hadCurrentPath
                before = if ($hadCurrentPath) { [string]$currentPath } else { $null }
                after = $hookDir
                applied_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-Info "Git hook path -> $hookDir (post-merge auto-refresh enabled)" "Git hook path -> $hookDir (post-merge auto-refresh enabled)"
        } else {
            Write-Warn "Failed to set core.hooksPath" "Failed to set core.hooksPath"
        }
    }
    # Windows filesystem does not honor chmod; Git tracks the executable bit
    # via the index. hooks/post-merge must be committed once with the exec
    # bit set, after which Git restores it on every checkout.
}
