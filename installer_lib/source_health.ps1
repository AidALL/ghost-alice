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
                Write-Err "To preserve source-local changes while updating, run .\install.cmd --update-source." "To preserve source-local changes while updating, run .\install.cmd --update-source."
                Write-Err "If an old checkout is already blocked by local changes during git pull, use the bootstrap one-command update in docs/getting-started/troubleshooting.md." "If an old checkout is already blocked by local changes during git pull, use the bootstrap one-command update in docs/getting-started/troubleshooting.md."
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

function Update-SourceCheckout {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Err "git not found; cannot update source checkout" "git not found; cannot update source checkout"
        throw "git not found; cannot update source checkout"
    }

    & git -C $ScriptDir rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "source is not a git worktree; cannot update source checkout" "source is not a git worktree; cannot update source checkout"
        throw "source is not a git worktree; cannot update source checkout"
    }

    $branch = (& git -C $ScriptDir symbolic-ref --quiet --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $branch) { $branch = "DETACHED" }
    $upstream = (& git -C $ScriptDir rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $upstream) {
        Write-Err "source branch has no upstream; cannot update automatically: $branch" "source branch has no upstream; cannot update automatically: $branch"
        Write-Err "Inspect git remote -v and git branch --set-upstream-to first." "Inspect git remote -v and git branch --set-upstream-to first."
        throw "source branch has no upstream; cannot update automatically"
    }

    Write-Info "Source update: branch=$branch, upstream=$upstream" "Source update: branch=$branch, upstream=$upstream"

    $statusLines = @(& git -C $ScriptDir status --porcelain=v1 --untracked-files=all)
    $stashCreated = $false
    $stashRef = ""
    if ($statusLines.Count -gt 0) {
        $stashMessage = "ghost-alice source update backup {0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
        Write-Info "Saving source local changes in git stash." "Saving source local changes in git stash."
        Push-Location -LiteralPath $ScriptDir
        try {
            & git stash push -u -m $stashMessage -- . *> $null
        } finally {
            Pop-Location
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Could not stash source local changes; aborting update." "Could not stash source local changes; aborting update."
            throw "Could not stash source local changes; aborting update."
        }
        $stashCreated = $true
        $stashRef = (& git -C $ScriptDir stash list "--format=%gd" | Select-Object -First 1)
        if (-not $stashRef) { $stashRef = "stash@{0}" }
        Write-Warn "Source local changes saved in git stash: $stashRef" "Source local changes saved in git stash: $stashRef"
        Write-Warn "Review: git -C `"$ScriptDir`" stash show -p $stashRef" "Review: git -C `"$ScriptDir`" stash show -p $stashRef"
        Write-Warn "Reapply only if intentional: git -C `"$ScriptDir`" stash pop $stashRef" "Reapply only if intentional: git -C `"$ScriptDir`" stash pop $stashRef"
    }

    Push-Location -LiteralPath $ScriptDir
    try {
        & git pull --ff-only
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Source checkout fast-forward update failed." "Source checkout fast-forward update failed."
        if ($stashCreated) {
            Write-Warn "Local changes remain saved in $stashRef." "Local changes remain saved in $stashRef."
        }
        throw "Source checkout fast-forward update failed."
    }

    Write-Ok "Source checkout updated. Now rerun the installer: .\install.cmd" "Source checkout updated. Now rerun the installer: .\install.cmd"
    if ($stashCreated) {
        Write-Warn "Review saved source changes: git -C `"$ScriptDir`" stash show -p $stashRef" "Review saved source changes: git -C `"$ScriptDir`" stash show -p $stashRef"
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
