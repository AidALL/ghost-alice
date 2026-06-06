# Codex Tool Mapping

Skills are written against Claude Code tool names. When a skill references one of the names below, use the corresponding Codex equivalent.

| Skill reference | Codex equivalent |
|-----------|--------------|
| `Task` (subagent dispatch) | `spawn_agent` (see [Named Agent Dispatch](#named-agent-dispatch)) |
| Multiple `Task` calls (parallel) | Multiple `spawn_agent` calls |
| Task result return | `wait` |
| Task auto-close | release the slot with `close_agent` |
| `TodoWrite` (task tracking) | `update_plan` |
| `Skill` (skill invocation) | skills load natively, just follow the instructions |
| `Read`, `Write`, `Edit` (files) | use the native file tools |
| `Bash` (command execution) | use the native shell tool |
## Contents

- [Install/Bootstrap Locations](#installbootstrap-locations)
- [Subagent Dispatch Requires Multi-Agent Support](#subagent-dispatch-requires-multi-agent-support)
- [Named Agent Dispatch](#named-agent-dispatch)
  - [Message Framing](#message-framing)
  - [When This Workaround Can Be Removed](#when-this-workaround-can-be-removed)
- [Environment Detection](#environment-detection)
- [Closing Out in the Codex App](#closing-out-in-the-codex-app)


## Install/Bootstrap Locations

- Codex user skills: `~/.agents/skills/`
- Codex global instructions: `~/.codex/AGENTS.md`
- The Ghost-ALICE installer installs user skills to `~/.agents/skills/` on Codex and writes the global bootstrap to `~/.codex/AGENTS.md`.
- Currently Ghost-ALICE uses a copy install on Codex instead of a link/junction. Therefore, after a `git pull` of the repository, you must run the install script again to refresh the installed copy.

## Subagent Dispatch Requires Multi-Agent Support

Add the following to the Codex config (`~/.codex/config.toml`).

```toml
[features]
multi_agent = true
```

This enables `spawn_agent`, `wait`, and `close_agent`, so you can use skills such as `dispatching-parallel-agents` or `subagent-driven-development`.

## Named Agent Dispatch

Claude Code skills reference named agent types such as `coding-convention:code-reviewer`. Codex has no named-agent registry. `spawn_agent` creates a generic agent from a built-in role (`default`, `explorer`, `worker`).

When a skill instructs a named-agent dispatch.

1. Find the agent prompt file (for example, `agents/code-reviewer.md` or the skill-local prompt template `code-quality-reviewer-prompt.md`)
2. Read the prompt content
3. Fill in the template placeholders (`{BASE_SHA}`, `{WHAT_WAS_IMPLEMENTED}`, and so on)
4. Spawn a `worker` agent with the filled content as `message`

| Skill instruction | Codex equivalent |
|-----------|--------------|
| `Task tool (coding-convention:code-reviewer)` | `spawn_agent(agent_type="worker", message=...)` with the content of `code-reviewer.md` |
| `Task tool (general-purpose)` inline prompt | `spawn_agent(message=...)` with the same prompt |

### Message Framing

The `message` parameter is user-level input, not a system prompt. To maximize instruction adherence, structure it as follows.

```
Your task is to perform the following. Follow the instructions below exactly.

<agent-instructions>
[prompt content filled in from the agent .md file]
</agent-instructions>

Execute this now. Output ONLY the structured response following the format
specified in the instructions above.
```

- Use task-delegation framing ("Your task is...") instead of persona framing ("You are...")
- Wrap the instructions in XML tags. The model treats a tagged block as authoritative.
- End with an explicit execution instruction to prevent the model from summarizing the instructions

### When This Workaround Can Be Removed

This approach compensates for the fact that the Codex plugin system does not yet support the `agents` field in `plugin.json`. Once `RawPluginManifest` gains an `agents` field, a plugin can place `agents/` as a symbolic link (the same way as the existing `skills/` link) and skills can dispatch named agent types directly.

## Environment Detection

A skill that creates a worktree or finishes a branch must detect the environment with read-only git commands before proceeding.

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
BRANCH=$(git branch --show-current)
```

- `GIT_DIR != GIT_COMMON` means you are already inside a linked worktree (skip creation)
- An empty `BRANCH` means a detached HEAD (no branch, push, or PR in the sandbox)

For how each skill uses this, see `using-git-worktrees` Step 0 and `finishing-a-development-branch` Step 1.

## Closing Out in the Codex App

When the sandbox blocks branch and push operations (a detached HEAD in an externally managed worktree), the agent commits all work and tells the user to use the app's native controls.

- "Create branch" means set a branch name, then commit, push, and open the PR through the app UI
- "Hand off to local" means transfer the work to the user's local checkout

The agent can still run tests, stage files, and output a branch name, commit message, and PR description for the user to copy.
