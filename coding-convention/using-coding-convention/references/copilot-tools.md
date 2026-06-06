# Copilot CLI tool mapping

Skills are written against Claude Code tool names. When a skill references one of the names below, use the corresponding equivalent in Copilot CLI.

| Skill reference | Copilot CLI equivalent |
|-----------|--------------------|
| `Read` (read a file) | `view` |
| `Write` (create a file) | `create` |
| `Edit` (edit a file) | `edit` |
| `Bash` (run a command) | `bash` |
| `Grep` (search file contents) | `grep` |
| `Glob` (search by filename) | `glob` |
| `Skill` (invoke a skill) | `skill` |
| `WebFetch` | `web_fetch` |
| `Task` (dispatch a subagent) | `task` (see [Agent types](#agent-types)) |
| Multiple `Task` calls (parallel) | Multiple `task` calls |
| Task status and output | `read_agent`, `list_agents` |
| `TodoWrite` (task tracking) | `sql` against the built-in `todos` table |
| `WebSearch` | No equivalent. Use `web_fetch` with a search engine URL |
| `EnterPlanMode` / `ExitPlanMode` | No equivalent. Stay in the main session |

## Agent types

The `task` tool in Copilot CLI takes an `agent_type` parameter.

| Claude Code agent | Copilot CLI equivalent |
|----------------------|--------------------|
| `general-purpose` | `"general-purpose"` |
| `Explore` | `"explore"` |
| Named plugin agent (for example, `coding-convention:code-reviewer`) | Auto-discovered from installed plugins |

## Asynchronous shell sessions

Copilot CLI supports persistent asynchronous shell sessions. Claude Code has no direct equivalent.

| Tool | Purpose |
|------|------|
| `bash` + `async: true` | Long-running background execution |
| `write_bash` | Send input to a running session |
| `read_bash` | Read output from an asynchronous session |
| `stop_bash` | Terminate an asynchronous session |
| `list_bash` | List active shell sessions |

## Additional Copilot CLI tools

| Tool | Purpose |
|------|------|
| `store_memory` | Persist codebase facts for the next session |
| `report_intent` | Update the current intent in the UI status line |
| `sql` | Query the session SQLite DB (todos, metadata) |
| `fetch_copilot_cli_documentation` | Look up Copilot CLI documentation |
| GitHub MCP tools (`github-mcp-server-*`) | Native GitHub API access (issues, PRs, code search) |
