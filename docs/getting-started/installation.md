# Installation And Update Guide

[🇺🇸 English](./installation.md) | [🇰🇷 한국어](../ko/getting-started/installation.md)

This guide is the command reference for installing, updating, checking, and repairing Ghost-ALICE OS. Public guidance uses OS-native entrypoints with the same long flag surface.

- macOS, Linux, WSL, or Git Bash: `bash install.sh ...`
- Windows Command Prompt or PowerShell: `.\install.cmd ...`

On Windows, `install.cmd` keeps the native wrapper path, Python 3.11+ installer contract, UTF-8 console setup, and `-NoProfile -ExecutionPolicy Bypass`. This handles PowerShell execution policy blocks and does not change the user or machine execution policy.

The Windows installer needs PowerShell 7.6 or newer. Windows PowerShell 5.1 is not supported: under an older PowerShell the installer prints the requirement and stops. Install PowerShell with `winget install --id Microsoft.PowerShell --exact`, then rerun `.\install.cmd`.

## Contents

- [Quick Install](#quick-install)
- [Close Programs Before Installation](#close-programs-before-installation)
- [Install Addons](#install-addons)
- [Install One Platform](#install-one-platform)
- [Check Status](#check-status)
- [Update](#update)
- [Move Existing Work Records](#move-existing-work-records)
- [Common Commands](#common-commands)
- [Runtime And Platform Reference](#runtime-and-platform-reference)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)

## Quick Install

macOS, Linux, WSL, or Git Bash:

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows Command Prompt:

```cmd
git clone https://github.com/AidALL/ghost-alice.git %USERPROFILE%\ghost-alice
cd %USERPROFILE%\ghost-alice
.\install.cmd
```

`bash install.sh` and `.\install.cmd` detect the agent platform. The options below select a specific platform or manage each installation separately.

After a successful installation, use `~/.ghost-alice/bin/ghost-alice` (Windows: `%USERPROFILE%\.ghost-alice\bin\ghost-alice.cmd`) for status, update and removal. The Core skills this installer places, runtime and management commands execute without the development clone. The installer attempts automatic acquisition of missing Python 3.11+, Git and Node.js and explains package-manager automatic-consent flags before acquiring the first program.

When upgrading an older installation, update every existing Core skill on each installed host before deleting the clone. Selecting only some skills leaves the others unchanged; older Claude skill links can still point into the clone, and older skill instructions can still refer to its policy files. Supply the existing skill names to the new installer, or use the fixed `update` command below, which retains those installed selections. If existing work records remain inside that folder, complete [moving existing work records](#move-existing-work-records) before moving or deleting the folder.

On the first upgrade from a v2-only manager, the candidate can install from the same source while work records remain there, provided the temporary v2 record preserves all previously recorded host relationships. When every requested host succeeds, the installer activates the new manager and preserves which hosts use each observed source. If the outer installer still reads v2 after success, the new manager finishes publishing those saved relationships when it starts the record-move stage. If interrupted, the next new-manager run can finish that handoff. If the old manager cannot read the required relationship, the candidate stops before changing Git hooks or host targets. Update from the previous source, or run the current installed management command from a terminal, accept the offered record move and retry the source change.

## Close Programs Before Installation

Run installation and update commands from a terminal and save your work in the related hosts first. Before changing installed files, the installer checks the selected platforms, every host with an installed Core selection, and hosts involved in unfinished installations. If related host programs or record-writing CLIs are running, it displays their observed program names and process IDs (PIDs) and asks whether to request their normal exit. Answer after the question appears; input entered before each question is discarded. The installer continues only after its process observation confirms their absence.

On macOS the list shows the desktop app once for its bundled helpers. The Codex app can appear as `ChatGPT`. If you agree, the installer asks that running app to quit. A separately running CLI is listed with its own PID.

| Answer | Installer action |
| --- | --- |
| `y` or `yes` | Request normal exit for the displayed PIDs that are still observed, then check whether the programs have exited. |
| Enter, `n`, `no` or end of input (EOF) | Cancel installation with exit code 1 before changing installed files. |
| Other input | Ask again. |

A normal-exit request does not establish that a program exited. If a program remains running or a request fails, close it yourself, then press Enter to recheck or `n` to cancel. Enter rechecks the waiting programs without repeating their exit request; a newly observed PID requires a new displayed question and affirmative answer. On Windows, close windowless CLIs and programs without a supported normal-exit method yourself. If closing a related program could terminate the installer or its terminal, the installer stops and asks you to rerun from an independent terminal. If process observation fails, resolve the reported error before rechecking; an unknown state does not establish absence. The installer does not force termination. Noninteractive runs and automatic post-merge installations neither ask nor request exit: they proceed only when related programs are confirmed absent, and otherwise stop with exit code 1. The post-merge hook reports that installation result without failing the Git merge.

The installer running the command owns the exit question. An older installed command that lacks this question cannot offer it during the first update; the new candidate checks for absence without asking and stops if related programs are running or their state is unknown. To use the question on that first update, run `bash install.sh` or `.\install.cmd` from a folder containing the new installer. Candidate and per-host installation processes recheck absence before their changes without asking again or requesting exit. These checks protect installed-file changes; Python preparation and update downloads may already have occurred. After installation succeeds, [moving existing work records](#move-existing-work-records) has its own question and checks.

## Install Addons

An addon is installed from its own repository or a local path with `--addon-source PATH|URL`; add `--platform` to install it to one platform.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

```cmd
.\install.cmd --addon-source C:\path\to\addon-repo
```

For git URL addon sources, `--addon-tag` selects the branch or tag to clone into the local addon source cache.

## Install One Platform

The following commands install Core only. Add `--addon-source PATH|URL` to include an addon for the selected platform.

| Target | macOS / Linux / WSL | Windows Command Prompt / PowerShell |
| --- | --- | --- |
| Claude Code | `bash install.sh --platform claude` | `.\install.cmd --platform claude` |
| Codex | `bash install.sh --platform codex` | `.\install.cmd --platform codex` |

To choose interactively:

```bash
bash install.sh --prompt-platform
```

```cmd
.\install.cmd --prompt-platform
```

## Check Status

Doctor diagnoses the installation without changing it. It returns exit code 1 when `overall` is `error`, when an unfinished add-on removal left a `.removing` marker, or when the hook status check cannot complete. Use it before changing a suspicious install. The `hook-rejections` line lists the hook commands the Ghost-ALICE hook runner refused to run in the last 14 days, one line per session with the count and the last refusal's time, hook and reason, and it does not change `overall`.

```bash
bash install.sh --doctor
bash install.sh --status
```

```cmd
.\install.cmd --doctor
.\install.cmd --status
```

The healthy target is `overall: ok`.

Doctor also prints informational Core lines: `native-intake-waits`, `native-intake-failures`, `hook-failures` and `intake-hook-degraded`. These lists do not change `overall` or the exit code. They report recorded waits, failures or degradation markers; a degradation marker alone does not establish whether an input was stored. See [intake warning and doctor lists](troubleshooting.md#intake-warning-and-doctor-lists). Doctor and status also report old record paths whose cleanup remains pending in existing move journals. These diagnostics do not move records, retry deletion or decide that the current files may be deleted.

## Update

The fixed command downloads the selected version and passes your terminal to its installer. That installer asks the installation questions and updates the Core skills already selected for each host. It works after the development clone moves or disappears, adds no newly detected hosts or skills, and leaves existing addons unchanged.

```bash
~/.ghost-alice/bin/ghost-alice update
~/.ghost-alice/bin/ghost-alice --doctor
```

```cmd
%USERPROFILE%\.ghost-alice\bin\ghost-alice.cmd update
%USERPROFILE%\.ghost-alice\bin\ghost-alice.cmd --doctor
```

If acquisition coordinates are missing, supply `--remote URL --ref NAME` when running update. To update the developer checkout itself, use the existing `bash install.sh --update-source` or `.\install.cmd --update-source`; that separate path stashes local changes before a fast-forward and follows [source update recovery](./troubleshooting.md#git-update-recovery). Fixed update leaves the checkout unchanged. See [installer architecture](../reference/installer-architecture.md#fixed-commands-policies-and-ledger-data) for saved-code reinstallation and ledger locations.

If a recorded Core skill has an invalid host list (`used_by`), the installer stops and identifies the installation record and skill path.

### Move Existing Work Records

After a successful installation or update, the installer checks whether work records still in use remain inside a folder used to install Ghost-ALICE. It shows that folder, the current record location and the new location at `~/.ghost-alice/session-intent`, then asks whether to move the records. The default is Yes; no separate command or migration flag is needed. Enter pressed before this question does not request a move. When several hosts share one location, the installer lists that location once.

| Answer | Installer action |
| --- | --- |
| Enter, `y` or `yes` | Request the move, then check that the affected record writers have stopped. |
| `n` or `no` | Keep the current location. |
| End of input (EOF) | Keep the current location. |
| Other input | Ask again. |

The record-movement step rechecks the hosts and other CLI work that use the displayed records, even after the [pre-installation exit check](#close-programs-before-installation). Keep those programs closed while moving records. If the installer finds a writer running, press Enter after stopping it to recheck, or `n` to keep the current location. If observation fails, inspect the reported setting or process-query error before rechecking; an unknown state does not establish that a program is running. Noninteractive runs and automatic post-merge installations do not ask for new moves, but automatically finish cleanup of already accepted moves after the required checks. Run `~/.ghost-alice/bin/ghost-alice` (Windows: `%USERPROFILE%\.ghost-alice\bin\ghost-alice.cmd`) from a terminal later to request a new move through the same follow-up after installation succeeds. Doctor reports the current locations without moving them. The exit check and follow-up use Korean or English according to the terminal language; other installation progress messages can remain in English.

The installer reports installation and record movement separately. A deferred or declined move leaves the current location in use. A move error returns a nonzero exit even when the installer restores the previous location; follow the reported recovery condition before retrying. After committing the records and settings at the new location and completing service resumption, the installer verifies and removes the old record folder. Record movement is complete only when that folder is removed; restoring the previous location never deletes it. If cleanup fails, the installer preserves the new location and new writes, reports the remaining paths and cause, and retains the cleanup journal for a later installation to retry unchanged remaining files after the reported cause is resolved. Repeating the command while that cause remains can fail again. An added or changed old file, or a host or gateway still using the old location, prevents deletion. The same follow-up handles an old folder retained by an earlier installer after verifying that its records are preserved at the new location. Other retained installation artifacts are reported separately. A successful code installation alone does not establish that records moved. The [architecture reference](../reference/installer-architecture.md#fixed-commands-policies-and-ledger-data) describes the commit and recovery conditions.

When installing an older version that does not support this follow-up, the installer completes the requested code installation without sending a record-move request to that version. On the first update from a version whose parent installer has no follow-up support, the old parent installs the new code but cannot ask this question. The next normal fixed-command installation or update uses the new parent and can ask. Running `bash install.sh` or `.\install.cmd` directly from a folder containing the new code can ask on that first installation. Before records and settings are committed at the new location, moving the old source folder does not change the configured record path: a later request can recreate the old path and split old and new records. The installer does not search moved folders or merge those histories automatically. If it cannot confirm which source folder an installed record location belongs to, it reports that relationship as unknown and does not offer an inferred move. Declining the move, including with `n`, `no` or end of input, keeps the records in place and permits another update from the same source. The installer retains the three most recent distinct successful versions under its usual retention rules. Reinstalling a retained version requires its reader to understand the current installation record. After activation, keeping records under an old checkout can require v3 even if later updates use that same checkout. A v2-only package can therefore be unavailable for direct reinstall. You can keep using the current manager and decide whether to move the records on a later interactive update.

### Install Source

The install record `~/.ghost-alice/installed-payload-v2.json` or `~/.ghost-alice/installed-payload-v3.json` names the checkout the installation came from, the install source. Install and `--repair` compare that developer origin with the requested input origin before changing installation targets. Execution uses a fixed package; the package records its input origin separately. When the recorded checkout no longer exists, for example after a fresh clone at another path, the installer records the new checkout as the install source without asking. When both checkouts exist, such as a second clone or a git worktree, the installer shows both folders. At a terminal it asks whether this checkout replaces the install source and stops unless the answer typed at the installer's terminal question is yes (the installer does not check who typed it); elsewhere it stops with exit code 2. When the human user decides to move the installation to another checkout, run the installer there with `--adopt-source`; its first placement records that checkout as the install source. `--repair` never moves the installation: from another checkout it stops with exit code 2 and names that install command.

If the saved manager is missing files, run the installer from a checkout containing that version’s files. When the saved file list is available, installation and `--repair` can restore missing files whose contents match that list. A different version or an edited surviving file stops this recovery. If the entire saved version directory is gone, reinstall the matching version from the checkout with the original host, skills and addon sources.

```bash
cd /path/to/new-checkout
bash install.sh --adopt-source
```

```cmd
cd C:\path\to\new-checkout
.\install.cmd --adopt-source
```

The installer sets the repository's git hook path (`core.hooksPath`, which every worktree of the repository shares) to the fixed package's `hooks` folder. After a merge or pull in the install source, its post-merge hook runs the installer in the merged checkout for every detected host. It uses the local files, including commits you have not pushed. A merge or pull in another worktree of the same repository runs the same hook from the install source, never that worktree's own hook file; the hook prints one line and installs nothing. A worktree's own hook setting (`git config --worktree core.hooksPath`) still comes first. Only an install you run replaces the install source; a merge never does, and a merge never fails because of the hook. Removal of the last consumer of the management commands puts back the hook path from before the install, unless the path changed since.

## Common Commands

| Purpose | macOS / Linux / WSL | Windows Command Prompt / PowerShell |
| --- | --- | --- |
| List skills | `bash install.sh --list` | `.\install.cmd --list` |
| Show install state | `bash install.sh --status` | `.\install.cmd --status` |
| Run protected diagnostic | `bash install.sh --doctor` | `.\install.cmd --doctor` |
| Safe source update | `bash install.sh --update-source` | `.\install.cmd --update-source` |
| Install an addon | `bash install.sh --addon-source /path/to/addon-repo` | `.\install.cmd --addon-source C:\path\to\addon-repo` |
| Selective core install | `bash install.sh task-router verification-before-completion` | `.\install.cmd task-router verification-before-completion` |
| Full uninstall | `bash install.sh --uninstall` | `.\install.cmd --uninstall` |
| Selective uninstall | `bash install.sh --platform codex --uninstall task-router` | `.\install.cmd --platform codex --uninstall task-router` |
| Clean false pending entries | `bash install.sh --platform claude --cleanup-pending` | `.\install.cmd --platform claude --cleanup-pending` |

## Runtime And Platform Reference

### Agent Visibility Profile

The default profile is `dynamic`. The profile controls how much governance surface is shown to the user; it does not reduce hook installation or execution, the hook log (`~/.ghost-alice/session-logs/<host>/<session>/strict-hook-output.jsonl`), audit records or Work-Impact Projection.

The [Ghost-ALICE intake warning](./troubleshooting.md#intake-warning-and-doctor-lists) appears under every profile.

| Profile | macOS / Linux / WSL | Windows Command Prompt / PowerShell |
| --- | --- | --- |
| strict | `bash install.sh --visibility strict` | `.\install.cmd --visibility strict` |
| dynamic | `bash install.sh --visibility dynamic` | `.\install.cmd --visibility dynamic` |
| minimal | `bash install.sh --visibility minimal` | `.\install.cmd --visibility minimal` |

`--agent-visibility` remains an accepted compatibility alias. Prefer `--visibility` in new docs and commands.

### Slash Commands By Platform

Ghost-ALICE installs `/visibility strict|dynamic|minimal` as a Claude Code workspace command. Every platform, including Codex, can inspect and change the same profile value through `_shared/agent_visibility_cli.py`. Codex has no `/visibility` command.

### Python Contract

The installer requires Python 3.11 or newer for every command, `--help` and `--list` included: `install.sh` only finds or prepares Python and hands the arguments to `_shared/installer/cli.py`. If Python 3.11+ is missing, it attempts automatic preparation where possible.

The optional macOS native context gateway requires Python 3.14 or newer with `compression.zstd`. Its registration command, `bash install.sh gateway --codex-dir <dir> --ledger-root <dir> --state-dir <dir> --launch-agent <plist>`, checks that capability first, then writes the launch agent, `state.json` and the Codex provider blocks and records the service in the install record. It copies no module: the service runs the runtime `_shared` the installer published, so install Core first. This component requirement does not change the Core installer and hook minimum of Python 3.11. If Codex `config.toml`, the launch agent or a state file is a symlink, registration and detachment stop before changing the service and preserve the link and its target. A refresh keeps the listener's port and scope while the three service files are as the install record says; a file changed outside the installer stops the command before any change, and an existing launch agent, state directory entry or provider block the record does not name is preserved. If a first registration fails and the candidate service is confirmed stopped, rollback removes that attempt's new state artifacts and record entry so a new attempt can proceed. An uncertain stop or concurrent artifact change preserves recovery material for review. After the registration, `bash install.sh` alone updates the installation; `bash install.sh gateway detach --apply` unregisters the service and keeps its state directory. When launchd runs a gateway service that the install record does not name, install and `--repair` leave that service as it is and print one warning that names the registration command; the registration records the service and restarts it on the new code.

- macOS: if Homebrew is available, `brew install python3`
- Linux / WSL: available package managers such as `apt-get`, `dnf`, `yum`, or `pacman`
- Windows: `winget`, `choco`, then `scoop`

If Python 3.11+ is still unavailable, installation stops and prints manual recovery guidance.

### PowerShell Contract

`.\install.cmd` runs the Windows installer with PowerShell 7 (`pwsh.exe`), and with Windows PowerShell (`powershell.exe`) when `pwsh.exe` is absent. The Windows installer needs PowerShell 7.6 or newer, the 7.6 LTS line. Under an older PowerShell, Windows PowerShell 5.1 included, it prints `PowerShell 7.6 or newer is required` with the install command `winget install --id Microsoft.PowerShell --exact` and stops with exit code 1; the installer never installs PowerShell itself. After that check, it sets UTF-8 console input and output, prepares Python 3.11+ and passes every argument in order to `_shared/installer/cli.py`, the same installer that `install.sh` runs, so Windows accepts the same long flags.

On macOS and other POSIX hosts, installed hooks try `GHOST_ALICE_PYTHON` first, then Python from the runtime `PATH` and common installation locations, and finally the interpreter used by the installer. Each candidate must pass the Python 3.11+ check. This final fallback lets a desktop app run hooks even when its `PATH` omits the installer's Python directory. It does not change the host's global Python or `PATH`; rerun the installer if that interpreter moves and no other candidate is available.

### Node.js Contract

Claude Code and Codex hook-enabled installs require a resolvable Node.js runtime because the `tool-checkpoint` PreToolUse gate runs `ghost-alice-hook.mjs`. The installer checks configured runtimes, the platform registration and `PATH`, including the configured Codex runtime fallback. It blocks hook installation only when the target platform requires hooks and no usable Node runtime resolves.

### Platform Update Behavior

| Host | Core placement | Skill path |
| --- | --- | --- |
| Claude Code | Copy | `~/.claude/skills/` |
| Codex | Copy | `~/.agents/skills/` |

Every supported OS receives Core and `_shared` as copies. Editing development files does not alter installed Core; update or an explicit install publishes new code. Addon link/copy policy remains separate. An existing Codex `untrusted` setting stays unless the installer obtains confirmation for that exact project.


### Core and addon transitions

Core installation and addon removal are separate operations. A fresh Core installation supplies the instructions, input/source bindings, work and review records, and Core hooks. Updating Core without an addon option preserves an already installed addon; `--addon-skip` skips addon installation and does not request its removal.

| Operation | Example for Codex | Managed scope |
| --- | --- | --- |
| Fresh Core install or Core update | `bash install.sh --platform codex` | Core; preserve existing addon files and registrations on update |
| Add or update an addon | `bash install.sh --platform codex --addon-source /path/to/addon-repo` | Core plus the addon's manifest-selected skills and observational hooks |
| Remove an addon while retaining Core | `bash install.sh --platform codex --uninstall --addon <addon-id>` | Detach recorded addon hooks before removing verified addon targets |
| Remove one platform | `bash install.sh --platform codex --uninstall` | That platform's addons and Core; retain shared assets needed by other installed platforms |

Removing an addon retains Core records. Per-addon removal keeps modified files and its sidecar/removal marker for review or retry, while detaching its managed executable registrations. This is a configuration/file operation; ongoing-host adoption and cancellation of already delivered work are separate observations.

Full/platform removal archives pending merge originals and last-platform installation recovery copies under unique `~/.ghost-alice/uninstall-backup/<platform>/support-state/` directories. It keeps previous archives, source checkouts, conversation/session evidence, records at the current ledger location, audit traces and secrets. See [uninstall cleanup](./uninstall.md) for ownership and shared-file rules.

For Claude Code, the installer records which manifest-selected addon `Skill(...)` allow rules it actually added in `~/.ghost-alice/addon-permissions/claude-v2.json`. The uninstaller clears one owned occurrence and preserves preexisting rules and later duplicate rules that this installer did not add. The uninstaller preserves a legacy rule without ownership evidence for the human user to review. For Codex, the uninstaller clears unchanged owned hook trust hashes and moves surviving Core hook trust entries and the entries of hooks Ghost-ALICE did not install with their hook positions, preserving their metadata; the installer does not record who wrote the latter entries. A trust collision or configuration write failure prevents successful file removal; see [uninstall cleanup](./uninstall.md).

### Installed Surfaces

A full install deploys:

- core skills from `skill-catalog/skills.json`
- coding-convention workflow skills
- optional addon skills through `--addon-source`
- `_shared/` utilities
- platform hook settings
- Node-backed hook dispatcher assets under `~/.ghost-alice/hooks/`
- Claude Code permission allowlist
- Claude `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md` global bootstrap
- Codex `~/.codex/AGENTS.md` loader and managed `~/.codex/ghost-alice-governance.md` common contract
- the install record `~/.ghost-alice/installed-payload-v2.json` or `~/.ghost-alice/installed-payload-v3.json`: one file for both hosts that names every install location, its source and the fingerprints of copied files, written in the same step as each placement
- `~/.ghost-alice/rollbacks/<txid>/`: the files one placement replaced, the install record as it was before, and the placement's journal; the next locked installer command restores a placement that stopped halfway, and [runtime state retention](../reference/repository-structure.md#runtime-state-retention) sets how many finished folders stay
- support state for install-state, pending merges, and uninstall reports

An installation that registered the optional context gateway: the install record's `service` section names the service's code root, `~/.ghost-alice/runtime/current`, and its three service files. The runtime placement (`_shared` and the companion script `session-intent-analyzer/scripts/session_intent_ledger.py`) stops the service before its files change and starts it after. `bash install.sh` alone is the update; no gateway refresh precedes it. The install prints one `Context gateway:` line with the service's runtime root and whether the service is running. An installation an earlier installer registered gets its `service` section once from the gateway's installation receipt, at the first install, repair or uninstall.

The installer owns only the marked Ghost-ALICE block in each global rule file. It refreshes that block while preserving text outside the bootstrap. If the destination is a file without a Ghost-ALICE marker, installation leaves it unchanged, writes `<rule-file>.ghost-alice-proposed`, and status/doctor does not report the proposal as an installed global rule.

### Common Governance and Project Instructions

Ghost-ALICE common governance applies across projects and task domains. On a normal Codex route, the response model reads the common contract at the path rendered into the managed `~/.codex/AGENTS.md` loader. The installer writes that contract to the sibling `ghost-alice-governance.md`. Claude Code receives the common contract directly in the managed portion of `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`. Project instruction files add project-specific constraints; they do not supply a second copy of the common procedures. The common contract's [Operating Model](../../platforms/common-governance.md#operating-model) defines the precedence of instructions that the human user enters in the conversation and the constraints stated in instruction files.

```mermaid
flowchart LR
    S["Platform common-contract source"] --> I["Installer replaces owned block"]
    I --> C["Codex loader + common-contract file"]
    I --> H["Claude global common-contract block"]
    C --> M["Model applies common procedures"]
    H --> M
    P["Project-specific constraints"] --> M
    U["Objective and instructions the human user enters in the conversation"] --> M
```

If Codex cannot read the named common file, the model tells the user once that the Ghost-ALICE contract is missing and continues the user's work without writing Ghost-ALICE ledger records. On Codex, when no tool is needed, the model answers directly without reading the common file; when it is unclear whether a tool is needed, it treats the request as work and reads the common file first. Status and doctor check the loader and its declared companion together. The installer preserves text outside the bootstrap and rule files without a Ghost-ALICE marker; it refuses to replace a Codex common contract through a symlink. A proposal is not an installed contract. The installer does not change unrelated projects' instruction budgets or rewrite their local rules.


### merge-companion

When an update detects external changes in installed files, the installer isolates those candidates in a pending merge queue. External changes are changes the installer finds in installed targets after install that its records do not hold; the installer does not record who made them.

- manifest: `~/.ghost-alice/pending-merges/<platform>/manifest.json`
- backup: `~/.ghost-alice/pending-merges/<platform>/`
- install state: `~/.ghost-alice/install-state/<platform>.json`

On the next Claude Code/Codex session, the hook reports pending entries. When the human user asks to review them, or when work changes an affected skill, the response model follows `merge-companion` to ask the human user whether to merge, discard or defer each entry.

## Uninstall

Uninstall uses the installer-owned install-state manifest and removes only the Ghost-ALICE managed block from the platform global rule file. Text outside the bootstrap remains in place.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

See [uninstall cleanup](./uninstall.md) for the full cleanup contract.

## Troubleshooting

When an update is blocked during `git pull`, a merge conflict, or an installer rerun, start with [troubleshooting](./troubleshooting.md).
