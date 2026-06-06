#!/usr/bin/env bash
# Ghost-ALICE installer library: source_health
# Sourced by install.sh. Do not execute directly.

check_source_health() {
  if [ "${SKIP_SOURCE_HEALTH:-0}" = "1" ]; then
    warn "$(t 'Skipping source health gate (--skip-source-health)' 'Skipping source health gate (--skip-source-health)')"
    return 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    warn "$(t 'git not found; source health is unverified (continuing)' 'git not found; source health is unverified (continuing)')"
    return 0
  fi

  if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "$(t 'source is not a git worktree; source health is unverified (continuing)' 'source is not a git worktree; source health is unverified (continuing)')"
    return 0
  fi

  local branch head upstream status_line upstream_counts ahead_count behind_count
  local tracked_dirty=0
  local untracked=0

  branch="$(git -C "$SCRIPT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || printf '%s' 'DETACHED')"
  head="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || printf '%s' 'unknown')"
  upstream="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [ -z "$upstream" ]; then
    upstream="none"
  fi

  while IFS= read -r status_line; do
    [ -z "$status_line" ] && continue
    case "$status_line" in
      "?? "*) untracked=1 ;;
      "!! "*) ;;
      *) tracked_dirty=1 ;;
    esac
  done < <(git -C "$SCRIPT_DIR" status --porcelain=v1 --untracked-files=all)

  info "$(t "Source health: branch=${branch}, head=${head}, upstream=${upstream}" "Source health: branch=${branch}, head=${head}, upstream=${upstream}")"

  if [ "$upstream" != "none" ]; then
    upstream_counts="$(git -C "$SCRIPT_DIR" rev-list --left-right --count 'HEAD...@{u}' 2>/dev/null || true)"
    if [ -n "$upstream_counts" ]; then
      read -r ahead_count behind_count <<EOF
$upstream_counts
EOF
      ahead_count="${ahead_count:-0}"
      behind_count="${behind_count:-0}"

      if [ "$ahead_count" -gt 0 ] 2>/dev/null && [ "$behind_count" -gt 0 ] 2>/dev/null; then
        warn "$(t 'Source branch has diverged from its upstream; continuing because local commits may be intentional.' 'Source branch has diverged from its upstream; continuing because local commits may be intentional.')"
        warn "$(t 'Before release, run git fetch origin, inspect git status, and reconcile this checkout history.' 'Before release, run git fetch origin, inspect git status, and reconcile this checkout history.')"
      elif [ "$behind_count" -gt 0 ] 2>/dev/null; then
        error "$(t 'Source branch is behind its upstream; refusing to install from stale source.' 'Source branch is behind its upstream; refusing to install from stale source.')"
        error "$(t 'Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout before installing. If this local source is intentional, rerun with --skip-source-health.' 'Run git fetch origin, inspect git status, then fast-forward or otherwise reconcile this checkout before installing. If this local source is intentional, rerun with --skip-source-health.')"
        error "$(t 'To preserve source-local changes while updating, run bash install.sh --update-source.' 'To preserve source-local changes while updating, run bash install.sh --update-source.')"
        error "$(t 'If an old checkout is already blocked by local changes during git pull, use the bootstrap one-command update in docs/getting-started/troubleshooting.md.' 'If an old checkout is already blocked by local changes during git pull, use the bootstrap one-command update in docs/getting-started/troubleshooting.md.')"
        return 1
      elif [ "$ahead_count" -gt 0 ] 2>/dev/null; then
        warn "$(t 'Source branch is ahead of its upstream; continuing because local commits may be intentional.' 'Source branch is ahead of its upstream; continuing because local commits may be intentional.')"
      fi
    else
      warn "$(t 'Source upstream comparison failed; continuing with local source health checks only.' 'Source upstream comparison failed; continuing with local source health checks only.')"
    fi
  fi

  if [ "$untracked" = "1" ]; then
    warn "$(t 'Source tree has untracked files; continuing because tracked files are clean.' 'Source tree has untracked files; continuing because tracked files are clean.')"
  fi

  if [ "$tracked_dirty" = "1" ]; then
    warn "$(t 'Source tree has tracked local changes; continuing. Installed target user edits remain protected by pending-merges backup and manifest checks.' 'Source tree has tracked local changes; continuing. Installed target user edits remain protected by pending-merges backup and manifest checks.')"
    return 0
  fi

  return 0
}

update_source_checkout() {
  if ! command -v git >/dev/null 2>&1; then
    error "$(t 'git not found; cannot update source checkout' 'git not found; cannot update source checkout')"
    return 1
  fi

  if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    error "$(t 'source is not a git worktree; cannot update source checkout' 'source is not a git worktree; cannot update source checkout')"
    return 1
  fi

  local branch upstream status_output stash_created stash_message stash_ref
  branch="$(git -C "$SCRIPT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || printf '%s' 'DETACHED')"
  upstream="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [ -z "$upstream" ]; then
    error "$(t "source branch has no upstream; cannot update automatically: ${branch}" "source branch has no upstream; cannot update automatically: ${branch}")"
    error "$(t 'Inspect git remote -v and git branch --set-upstream-to first.' 'Inspect git remote -v and git branch --set-upstream-to first.')"
    return 1
  fi

  info "$(t "Source update: branch=${branch}, upstream=${upstream}" "Source update: branch=${branch}, upstream=${upstream}")"

  status_output="$(git -C "$SCRIPT_DIR" status --porcelain=v1 --untracked-files=all)"
  stash_created=0
  stash_ref=""
  if [ -n "$status_output" ]; then
    stash_message="ghost-alice source update backup $(date +%Y%m%d-%H%M%S)"
    info "$(t 'Saving source local changes in git stash.' 'Saving source local changes in git stash.')"
    if ! git -C "$SCRIPT_DIR" stash push -u -m "$stash_message" -- . >/dev/null; then
      error "$(t 'Could not stash source local changes; aborting update.' 'Could not stash source local changes; aborting update.')"
      return 1
    fi
    stash_created=1
    stash_ref="$(git -C "$SCRIPT_DIR" stash list --format='%gd' | sed -n '1p')"
    [ -z "$stash_ref" ] && stash_ref="stash@{0}"
    warn "$(t "Source local changes saved in git stash: ${stash_ref}" "Source local changes saved in git stash: ${stash_ref}")"
    warn "$(t "Review: git -C \"${SCRIPT_DIR}\" stash show -p ${stash_ref}" "Review: git -C \"${SCRIPT_DIR}\" stash show -p ${stash_ref}")"
    warn "$(t "Reapply only if intentional: git -C \"${SCRIPT_DIR}\" stash pop ${stash_ref}" "Reapply only if intentional: git -C \"${SCRIPT_DIR}\" stash pop ${stash_ref}")"
  fi

  if ! git -C "$SCRIPT_DIR" pull --ff-only; then
    error "$(t 'Source checkout fast-forward update failed.' 'Source checkout fast-forward update failed.')"
    if [ "$stash_created" = "1" ]; then
      warn "$(t "Local changes remain saved in ${stash_ref}." "Local changes remain saved in ${stash_ref}.")"
    fi
    return 1
  fi

  ok "$(t 'Source checkout updated. Now rerun the installer: bash install.sh' 'Source checkout updated. Now rerun the installer: bash install.sh')"
  if [ "$stash_created" = "1" ]; then
    warn "$(t "Review saved source changes: git -C \"${SCRIPT_DIR}\" stash show -p ${stash_ref}" "Review saved source changes: git -C \"${SCRIPT_DIR}\" stash show -p ${stash_ref}")"
  fi
}

setup_git_hooks() {
  local hook_dir="hooks"
  local current_path
  local current_rc=0
  current_path="$(git -C "$SCRIPT_DIR" config --local core.hooksPath 2>/dev/null)" || current_rc=$?
  if [ "$current_path" != "$hook_dir" ]; then
    if git -C "$SCRIPT_DIR" config --local core.hooksPath "$hook_dir" 2>/dev/null; then
      SOURCE_REPO_HOOK_CHANGED=1
      SOURCE_REPO_HOOK_AFTER="$hook_dir"
      if [ "$current_rc" -eq 0 ]; then
        SOURCE_REPO_HOOK_BEFORE_PRESENT=1
        SOURCE_REPO_HOOK_BEFORE="$current_path"
      else
        SOURCE_REPO_HOOK_BEFORE_PRESENT=0
        SOURCE_REPO_HOOK_BEFORE=""
      fi
      info "Git hook path → $hook_dir (post-merge auto-refresh enabled)"
    else
      warn "$(t 'Failed to set Git hook path. Post-merge hook may not fire' 'Failed to set Git hook path. Post-merge hook may not fire')"
      return 0
    fi
  fi
  if [ -f "$SCRIPT_DIR/$hook_dir/post-merge" ] && [ ! -x "$SCRIPT_DIR/$hook_dir/post-merge" ]; then
    chmod +x "$SCRIPT_DIR/$hook_dir/post-merge" 2>/dev/null || true
  fi
}
