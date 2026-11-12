# Installer Architecture

[🇺🇸 English](./installer-architecture.md) | [🇰🇷 한국어](../ko/reference/installer-architecture.md)

This document is the contributor bridge between the quick installation docs and the installer compatibility contracts. It gives contributors the architecture map they need before tracing a change through the installer, hook payloads, install state, and tests.

For the installation steps, read the [installation guide](../getting-started/installation.md). For compatibility requirements and test ownership, read [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md).
## Contents

- [Reading Path](#reading-path)
- [Architecture Summary](#architecture-summary)
- [Install Flow](#install-flow)
- [Install Record](#install-record)
- [Gateway Lifecycle](#gateway-lifecycle)
- [Platform Surfaces](#platform-surfaces)
- [State And Safety Model](#state-and-safety-model)
- [Contributor Entry Points](#contributor-entry-points)
- [What Not To Infer](#what-not-to-infer)
- [Full Understanding](#full-understanding)


## Reading Path

1. Use this page to understand the moving parts and the call direction.
2. Use [repository structure](./repository-structure.md) to find the files.
3. Use [installation](../getting-started/installation.md) and [uninstall](../getting-started/uninstall.md) to understand user-facing behavior.
4. Use [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md), [tool output semantics](../policies/tool-output-semantics.md), and [live smoke regression](../policies/live-smoke-regression.md) to understand the contract and verification burden.

## Architecture Summary

The installer has four layers.

| Layer | Main files | Responsibility |
| --- | --- | --- |
| Shell entrypoints | `install.sh`, `install.ps1`, `install.cmd` | `install.sh` sets a UTF-8 locale, finds or installs Python 3.11+ and runs `_shared/installer/cli.py` with the arguments it received unchanged. `install.ps1` stops with the PowerShell 7.6 requirement on an older PowerShell, sets UTF-8 console input and output, finds or installs Python 3.11+, turns the parameter names of the earlier PowerShell installer into the matching long flags and runs the same cli with every argument in order. It reports `.\install.cmd` in `GHOST_ALICE_INSTALL_COMMAND`, so the installer's help and hints name the Windows command; without that report they name `bash install.sh`. `install.cmd` runs `install.ps1` with `pwsh.exe`, or with `powershell.exe` when `pwsh.exe` is absent |
| Installer package | `_shared/installer/` (`cli`, `auto`, `selection`, `addons`, `preservation`, `transaction`, `hosts`, `diagnostics`, `lifecycle`, `source`, `prerequisites`, `lock`, `report`, `locations`, `config`, `runner`) | Parse options, pick the command from the command table, resolve platforms and targets, keep the install step order, hold the install lock and print the report; every file change goes through the entry points below |
| Installer entry points | `_shared/install_hooks.py`, `_shared/install_transaction.py`, `_shared/install_state_writer.py`, `_shared/addon_installer.py`, `_shared/uninstall_cleanup.py`, `_shared/install_doctor.py` and the other `_shared/*.py` tools the package runs as child processes | Install skills and hooks, write the install record in the same step as each placement, record install state, protect local edits, remove managed targets and diagnose an installation |
| Runtime surfaces | `~/.claude/`, `~/.codex/`, `~/.agents/skills/`, `~/.ghost-alice/` | Hold installed skills, platform hook config, Claude and Codex global bootstrap instructions, the install record, publish rollbacks, install-state manifests, hook feature rollback metadata, pending merges, and uninstall reports |

The installer updates assets and hooks while protecting external changes and recording its own changes. External changes are changes the installer finds in installed targets after install that its records do not hold; the installer does not record who made them. Status, doctor, update and uninstall commands consume those records to identify the actual installed state and changes they can safely restore.

The hook runtime contains both `_shared` modules and `session-intent-analyzer/scripts/session_intent_ledger.py` under the same runtime root. Native hooks resolve the ledger API and its sibling helpers from that deployment. The source checkout identifies installation inputs; editing that checkout does not select execution libraries for an installed hook. The ledger database path and native session identity remain separate runtime inputs.

The following shared modules belong to neither the installer nor the hook runtime, so the installer, the hook runtime and the skills may all import them. `_shared/hook_definitions.py` owns the names that the installer and the hook runtime read together: hook ids and host event names, and the `ghost_alice_context` provider name. `_shared/install_lock.py` owns the install lock file and the one judgment of whether this process or the process that started it holds the lock. `_shared/content_digest.py` owns the one fingerprint of installed content and the one comparison of an install record entry with the disk. `_shared/installed_payload.py` owns the install record. `_shared/install_destination.py` owns the validated install locations and the check that limits where Core code may change files. `_shared/atomic_write.py` owns file replacement: a read of the file during the replacement returns either the whole old contents or the whole new contents. `_shared/file_lock.py` owns the exclusive lock on a file and the time limit for waiting on it. `installed_payload` imports only `content_digest` and `install_lock`, `atomic_write` imports only `install_destination`, `install_lock` imports `file_lock`, and `_shared/pending_manifest.py` imports `atomic_write`, `file_lock` and `install_destination` to own pending paths, parsed observations and locked updates. The other shared modules import only the standard library. A runtime module imports no installer module, and an installer module imports no runtime module. `_shared.test_hook_definitions` checks these rules, and that no `installer` package module shadows a standard-library module name.

## Install Flow

| Sender → receiver | Input passed | Result used by the next step |
| --- | --- | --- |
| Command line → shell entrypoint | The command line that whoever runs install.sh (the human user, the response model or a script such as the post-merge hook) passed. | A usable Python 3.11+ runtime and the arguments, unchanged. |
| Entrypoint → `installer.cli` | The arguments and the source checkout the shell lives in. | Parsed options, the selected command and, for an install, the platform, its skills directory and the effective visibility. |
| `installer.cli` → `installer.selection` and `installer.addons` | Requested skill names and addon sources. | Exact installation targets (core skills, `_shared`, addon targets) and the target count. |
| Orchestration → `install_transaction.py publish` | One publish request: the host, the source and installer commits, and each target's name, kind, provider, source path, install location and mode. | Installed skills/helpers, their install record entries written in the same step, and preserved local edits. |
| Deployment → hook/bootstrap synchronization | Managed payloads, platform paths and registration commands. | Updated managed rule blocks and hook registrations. |
| Installer → install-state writer → status output | Actual changed targets, hashes and rollback information. | Per-platform manifest and an install report for status, doctor and uninstall. |

`install.cmd` is only a Windows wrapper around `install.ps1`. `install.ps1` and `install.sh` are both thin shells around the same `_shared/installer/cli.py`, so Windows and Unix-like systems run one installer implementation. On Unix-like environments, `install.sh` owns the shell entry path and re-enters Bash when needed. Every command, `--help` and `--list` included, runs in Python: the shell keeps no copy of the installer's text, options or commands.

`installer.cli` owns one command table, `COMMAND_LINE`. Each command row names the tokens that select it, its handler, the install-lock name it holds, whether it needs a platform, and its `--help` row. Option parsing, the auto-mode decision, dispatch, the lock and `--help` all read that table, so a command cannot reach one of them without the others. A token that starts with `-` and names no command stops as `Unknown option`; a command never runs once per detected platform, only an install without a platform does. The installer names the checkout it runs from once, as `installer.runner.REPO_ROOT`.

### Install step order

The install command runs in this order: `installer.cli.dispatch` runs step 1, `installer.cli.prepare_install` runs step 2, `installer.cli.run_command` takes the lock, and `installer.cli.run_install` runs the rest. `_shared/test_installer_cli.py` fixes the order.

1. The install source check, before anything changes. `installer.source.match_install_source` compares the install record's `source.root` with the checkout the installer runs from, both with their links followed; it is the one judgment of whether a checkout is the install source. `installer.source.check_install_source` acts on it: no record, a record without a source, or the recorded checkout goes on. Another checkout, such as a second clone or a git worktree, goes on with `--adopt-source`, and without a question when the recorded checkout no longer exists, such as after a fresh clone at another path. Otherwise the installer names both folders; at a terminal it asks its terminal question whether this checkout replaces the install source (no by default), elsewhere it stops with exit 2 and the command to run. In auto mode the parent installer checks once, carries out the install source replacement and passes it to each child installer as `--adopt-source`. That replacement is triggered by the parent installer's own `--adopt-source`, a yes typed at the installer's terminal question (the installer does not check who typed it), or the installer's replacement without a question when the recorded checkout no longer exists. An unreadable record skips the check with a warning. The command's first publish records the new source. Addon sources are prepared after this step. `--repair` runs the same step first but never replaces the install source: from another checkout it stops with exit 2 and names the install that moves the installation. Under the lock (step 3) the command compares again, so a source that another installer changed while this command waited is judged as it is then.
2. Prerequisites (`git`, `node`) and the git hook path: the repository's shared `core.hooksPath` becomes the absolute path of the install source's `hooks` folder ([Post-merge hook](#post-merge-hook)).
3. The install lock (`~/.ghost-alice/install.lock`), held until the command ends. Right after taking it, the installer restores every publish whose journal says neither `done` nor `restored`.
4. The migrations: the install record, only when `~/.ghost-alice/installed-payload.json` does not exist and an earlier installer's install-state does, and the removal of the two folders where an earlier installer kept the copies it replaced, `~/.ghost-alice/install-rollbacks` and the Claude skills folder's `.ghost-alice-install-rollbacks`, where a folder that cannot be removed is reported as a warning and recorded in `~/.ghost-alice/state-retention-failures.jsonl`; then source health; then target collection (core skills, `_shared`, addon targets).
5. Deprecated installed skills leave the discovery path; then the encoding guard; then the preflight that quarantines or records local edits.
6. Pending addon uninstalls resume; an addon collision stops the install.
7. Placement: `_shared` and every target as links (Claude Code) or as one copy batch (Codex). Each placement is one `install_transaction.publish` call in the installer process, with the participants the install record names, that writes the targets' install record entries in the same step. A publish stages each target in `.ghost-alice-install-staging` beside it and removes that folder after the publish or the restore when it is empty. A folder that holds another entry stays; a removal that fails for another reason is recorded in `~/.ghost-alice/state-retention-failures.jsonl`.
8. Runtime publish: `_shared` and the companion script `session-intent-analyzer/scripts/session_intent_ledger.py` under `~/.ghost-alice/runtime/current`, in one publish. When the install record has a `service` section, the gateway participant stops the registered service before the files change and starts it after. The Codex copy `~/.agents/skills/_shared` and the Claude links are not the service's code, so their placement calls no service effect. Then one read-only `Context gateway:` report line; then hook installation, the managed rule block and the command wrappers.
9. Verification, the merge-companion snapshot, addon sidecars, the install-state manifest and the install event. For copied skill and support directories, the publisher writes the ownership marker in staging before placement. The records in this final step describe the completed installation. After publish, a hook or bootstrap failure does not undo that completed publish or advance the full-install snapshot.

The command table names the lock of each command: install, uninstall, repair, `--cleanup-pending` and the `gateway` passthrough run under it, and `installer.cli.run_command` takes it before the handler and releases it after. A child tool reuses the lock of the installer process that called it: the lock records that process id, and `install_lock.held_by_process_tree` answers whether the lock belongs to the calling process or its parent. The gateway registration command and the install record writer both ask that function, and an unrelated holder blocks them; the registration command run on its own takes the lock itself. The lock's owner text is only a label and grants nothing.

`install_lock.acquire_lock` returns a generation token; the installer and gateway release only that generation. The persistent sibling `install.lock.guard` serializes acquisition, stale takeover and release, so an earlier holder cannot unlink a newer generation. Direct Python callers that omit the release token can release only the generation this process acquired. The standalone `acquire` command prints the token and `release --generation <token>` consumes it. Mandatory coordination uses POSIX `flock` or Windows `msvcrt` and reports failure when locking is unavailable. The source hook-path writer uses its own persistent `source-repo-hook-path.json.guard` before taking the installation lock; it serializes the shared Git value and the original rollback record.

### Post-merge hook

The git hook step (step 2) writes the repository's `core.hooksPath` with `git config --local`, the configuration every worktree of the repository shares, as the absolute path of the install source's `hooks` folder. A successful merge or pull in any worktree therefore runs the install source's `hooks/post-merge`, never another checkout's copy of that file; a worktree's own setting (`git config --worktree`) still comes first. `installer.source.setup_git_hooks` returns the value it wrote and the value from before Ghost-ALICE wrote one. Each host's install-state records both (`system_env_changes`, kind `source_repo_hook_path`), and the install record keeps the written value in `source.hooks_path`. The relative `hooks` an earlier installer wrote counts as no earlier value, unless that installer's install-state, for a checkout of the same repository, recorded the value it replaced. A reinstall that finds its own value in place keeps the earlier value the installation recorded. The step also writes the two values to `~/.ghost-alice/install-state/source-repo-hook-path.json` in the same step as the git write, so an install that stops before its install-state is written leaves the earlier value for the next install. A value in place that no record names leaves the earlier value unknown, so the install-state records no entry for it.

The hook clears the variables git exports for the merged worktree, such as `GIT_DIR`, and runs the install source's `bash install.sh --post-merge --merged-checkout <merged worktree>`. `installer.cli.run_post_merge` asks `installer.source.match_install_source`, the judgment of the install source check, about the merged checkout. When the merged checkout is the checkout the install record names as the install source, the step goes on with the auto-mode install for every detected host. The step takes no lock: each host's install takes it, and while another install holds it the install refuses and the hook reports the exit code in one line. For any other checkout, without a record, or with a record that cannot be read, the step prints one line and installs nothing; a recorded checkout that no longer exists is replaced only by an install that runs outside the post-merge step, whoever runs install.sh (the human user, the response model or a script), never by a merge. The hook always exits 0, so a merge never fails because of it.

The full uninstall of the last host restores the hook path. When the repository's value is still the value an install-state records as written, `_shared/uninstall_cleanup.py` writes back the recorded earlier value, or unsets the path when there was none. While another host's install-state remains, the hook path and `source-repo-hook-path.json` stay for that host; the last host's removal deletes that record. A value that no install-state records as written, and a value that changed after the install, stay as they are.

The installer collects target identities once and passes the same `Collected` value through preflight, placement, verification and inventory writes. The publisher writes copied targets' ownership markers as part of staging. A partial reinstall replaces selected rows and preserves unselected Core baselines. `install_state_writer` also owns partial-failure records and successful-removal pruning; callers do not reconstruct those inventories.

## Install Record

`~/.ghost-alice/installed-payload.json` records what the installer installed where. Both hosts use one file with one entry per install location path; a location that both hosts use, such as the runtime `~/.ghost-alice/runtime/current/_shared`, has one entry whose `used_by` names both.

| Section | Content |
| --- | --- |
| `schema_version` | `1` |
| `installer` | `commit` and `schema_version` of the installer that last wrote the record |
| `source` | `root`, `commit`, `dirty` and `hooks_path` of the source checkout |
| `last_transaction` | `id`, `updated_at` and `rollback_dir` of the last publish |
| `targets` | Per install location: `name`, `kind` (`skill`, `support`, `runtime`, `command`, `resource`), `mode` (`link`, `copy`), `provider` (`core` or `addon:<id>`), `source`, `source_commit`, `used_by`, `link_target` for a link or `files` for a copy, `installed_at` and `baseline` |
| `service` | The registered context gateway, absent when none is registered: `label`, `runtime_root` (the folder that holds the runtime `_shared` the service runs from), `program`, the three service files `launch_agent`, `state_path` and `config_path` with `launch_agent_digest`, `state_digest` and `config_block_digest` (the fingerprint of the provider blocks in Codex `config.toml`; `null` until a registration recorded it), `config_backup`, `registered_at`, `restarted_at` and `listener_ready` |

A copy's `files` maps each relative path to `sha256:<hex>` of its bytes, and a copied single file is its own one path `.`; a link records only the path it points to. `content_digest` computes both and leaves out caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.git`), `.pyc` and `.DS_Store` files, the ownership marker and linked files and folders. `baseline` says where the fingerprints come from: `recorded` by a publish, `migrated` from an earlier installer's records, or `unknown` when those records had none.

| Writer | When | What it writes |
| --- | --- | --- |
| `install_transaction.publish` | Every placement, and once per install for the links that already point at their source | The published targets, `source`, `installer` and `last_transaction`, in the same step as the renames; a link that already points at its source is recorded and not replaced |
| `installed_payload.migrate` | The first install, repair or uninstall when the record does not exist and an earlier installer's install-state does | Every host's install-state target: a link with the path it points to now, a copy with its ownership marker's fingerprints, else the merge-companion snapshot's, else none (`unknown`); `provider` from the addon sidecars. Current files are never adopted as a baseline, and the earlier records stay unchanged |
| `installed_payload.migrate_service` | The first install, repair or uninstall when the record has no `service` section and `~/.ghost-alice/context-gateway-binding.json` names an installed gateway receipt | The `service` section from the receipt (`config_block_digest` stays `null` until a registration records it) and a `migrated` runtime target for each companion script the receipt fingerprinted. The receipt and the binding stay unchanged and are not read again |
| `context_gateway_install.py` | The `gateway` registration and detach commands, in the commit phase of their phase machine | A registration writes the `service` section after the service is ready; a detach deletes it; a failed operation puts the section back as it was |
| `installer.gateway_participant` | After a publish that touched the service's code | `restarted_at` and `listener_ready` of the `service` section once the service is back |

`installed_payload.update` is the one writer of record sections and `installed_payload.restore` puts back a copy a publish saved. Both write through a temporary file and a rename, and refuse unless the install lock belongs to the calling process or its parent. A record this installer cannot read, such as another schema version, stays unchanged while the files still publish. `--status` and `--doctor` print one summary line of the platform's targets in the record and one line per target that differs from it. Targets without a baseline are counted and not compared, and a match is reported only for the targets that were compared; the record does not change their result.

```mermaid
flowchart TB
    REQ["installer.transaction builds one publish request and calls publish with the participants the record names"]
    JR["publish writes rollbacks/txid/journal.json (prepare) and a copy of the record"]
    ST["stage: a copy or link next to each install location; copied skill/support directories receive ownership markers; the journal records each original and staged result"]
    BEF["participants' before_publish: the gateway participant stops an affected service and confirms its absence"]
    PUB["publish: originals move into rollbacks/txid/, staged results are renamed into place, the record is updated"]
    AFT["participants' after_publish: the gateway participant starts the service, confirms readiness, records the restart"]
    DONE["journal says done; the newest 20 finished folders stay"]
    RS["restore: participants' before_restore, originals and the saved record return, participants' after_restore; journal says restored; the cause's exit code stays"]
    NEXT["next locked command: recover --txid checks targets and kept originals before restoring a prepare journal"]
    REQ --> JR --> ST --> BEF --> PUB --> AFT --> DONE
    ST -->|"failure"| RS
    BEF -->|"failure: nothing changed yet"| RS
    PUB -->|"failure"| RS
    AFT -->|"failure"| RS
    JR -.->|"the process stopped"| NEXT
```

A publish keeps what it replaced only in `~/.ghost-alice/rollbacks/<txid>/`: the originals, the record copy and `journal.json`. The journal names the targets the publish replaces and records each one's original and staged result: files with their digests, links with their targets and every folder, empty ones too, leaving out only caches and generated files. It says `prepare` before anything changes, `done` after the files, the record and the participants' `after_publish` finished, and `restored` after a restore. A participant's error keeps its exit code (2, 3, 4 or 5) through the restore, and a restore step that fails with its own code reports that code (5 for an unconfirmed service recovery). A publish that replaces nothing (links already pointing at their source) calls no participant. `install_transaction` knows only the participant interface (`installer/participant.py`: `before_publish`, `after_publish`, `before_restore`, `after_restore` with the record root, the host, the transaction id and the replaced install locations); `installer/gateway_participant.py` is the one installer module that imports the gateway modules. When a process stops before `done`, the next locked command runs `install_transaction.py recover --txid <id>`: it checks every target first, so an install location must still hold what the publish left there or its original, and a moved original must still match its recorded original state. A mismatch leaves every install location, kept copy, install record and journal unchanged. When a publish cannot be restored, the installer stops the command with exit 1 and names its folder for the human user to review, because placing files over a half-finished publish would hide which files are the originals. This restore puts back files and the record only; it does not stop or restart the context gateway service.

When a copy publish completed after the merge-companion snapshot but a later installation step failed, the next collection compares files with that publish's recorded baseline. The collector accepts a newer copy record only for the selected host, target and destination, with valid relative file paths and hashes. It preserves differences from that baseline as external changes; it does not certify current disk contents as a new baseline or mark the whole installation complete.

## Gateway Lifecycle

The public Python APIs `context_gateway_install.install`, `context_gateway_install.detach`, `addon_uninstall.uninstall_addon` and `addon_uninstall.resume_pending` accept `apply_changes=` and the historical `confirm=` keyword. Equal values supplied under both names select one execution mode; conflicting values raise `ValueError` before filesystem or service work. Omission keeps the existing default: registration and addon removal apply, while gateway detachment previews. Internal helpers use only `apply_changes`. Gateway detachment accepts `--apply` and the historical `--confirm` alias; registration retains its `--dry-run` option. These switches select execution and record no human approval.

New Core hook configuration uses the semantic key `on_prompt_submit`. The shared resolver also reads historical `on_user_prompt`; both resolve to the host event `UserPromptSubmit`, so the existing event registration rules still prevent duplicate hooks. Addon policy rejects both keys. New installer guidance labels its recipient `For the human user:`; historical `User:` lines remain installer output, not a human utterance.

Gateway registration, refresh and detachment use six `_shared/context_gateway_*` modules. `model` defines request, observation, plan, result and effects types; it does not invoke an AI model. `inventory` reads the service files through an injected platform adapter. `plan` compares the service files with the install record's `service` section and produces an ordered plan without I/O. `transaction` applies the plan, writes the record's `service` section in its commit phase and owns reverse recovery. `platform` implements individual filesystem, record and native-service operations. `install` composes them behind the `gateway` command: `bash install.sh gateway --codex-dir <dir> --ledger-root <dir> --state-dir <dir> --launch-agent <plist>` registers or refreshes, `bash install.sh gateway detach --apply` unregisters, and `--dry-run` prints the validated plan without applying it.

The registration reads the runtime target the latest publish wrote from the install record and writes only the three service files: the launch agent, `state.json` and the provider blocks in Codex `config.toml`. It copies no module: the service runs the runtime `_shared` and the companion script the installer publishes, and the record's `targets` say which bytes those are, so Core must be installed first. The state directory must lie outside that runtime, links followed. Without a `service` section the command registers a new listener and refuses an existing launch agent, state directory entry or provider block the record does not name. With one, it refreshes with the same port and scope when the three files are as the section records them, and stops before any change when one of them changed outside the installer (exit 2): the human user reviews the file before the command runs again.

```mermaid
flowchart LR
    REC[("install record: targets and service")]
    PUB["publish (installer.transaction)"]
    REG["gateway registration and detach (context_gateway_install)"]
    PART["gateway participant (installer.gateway_participant)"]
    JUDGE{"does a location equal, lie under or above service.runtime_root, or name a service file; or is it a Codex full removal?"}
    REM["uninstall (lifecycle, uninstall_cleanup)"]
    SVC["launchd service"]
    PUB -->|"targets, source, last_transaction"| REC
    REG -->|"service written or deleted"| REC
    REC -->|"service.runtime_root and service files"| JUDGE
    PART -->|"every publish"| JUDGE
    REM -->|"the removal roots"| JUDGE
    JUDGE -->|"no"| NONE["publish or remove; the service is not touched"]
    JUDGE -->|"yes, a publish"| STOP["stop, publish, start, confirm readiness; restarted_at recorded"]
    JUDGE -->|"yes, a removal"| DET["detach: stop, remove the provider blocks and the launch agent, delete service; then remove"]
    STOP --> SVC
    DET --> SVC
```

`installer/gateway_participant.py`, the one installer module that imports the gateway modules, judges every publish and removal from the record: a location equal to, under or above `service.runtime_root`, or one of the three service files, touches the service; `~/.codex`, `~/.claude` and the ledger are data the service reads, not its code, and a link is compared by its own path, never by where it points. An affected publish stops the service before the files change, starts it after, confirms readiness and records `restarted_at`; a failed readiness restores the files and starts the prior service (exit 4). An affected removal, and every Codex full uninstall, detaches first: stop, remove the provider blocks and the launch agent, delete the `service` section; the state directory stays. If a service file changed outside the installer, the installer stops the detach for the human user to review. Without a `service` section the removal asks launchd: if a loaded service has the gateway's label and its program is inside a removal root, the installer holds the removal for the human user to review (exit 3); an absent service or one that runs from elsewhere lets the removal go on. The install, after its runtime publish, and the repair ask launchd through the same function, `installer.gateway_participant.loaded_service`. When a loaded gateway service runs from the runtime root and the record has no `service` section, the publish goes on and the installer leaves the service as it is: it has no record of the service's files. The `Context gateway:` line then warns and names the registration command, which records the service and restarts it on the new code. An observation that is not clear counts as none and leaves one line in the install log. A managed Git update of the checkout (`--update-source`) does not touch the service: the checkout is not its code.

A changed transport configuration can require a new host launch: a registered provider or a successful installation does not establish adoption by a native process that is already running, and that claim needs the session receipt of that process and observations of its actual requests. The service records request shapes and identities, selected source references, raw server usage and response hashes, and retains no extra conversation or credential bodies. To judge its effect, match each selected original, its reference and its absence across several later requests, verify that the originals that later decisions need remain retrievable, count each server response once and leave missing usage unknown; a cache hit or one smaller request does not prove exclusion. [Architecture](../../architecture.md#context-and-original-evidence) owns how the gateway handles headers, redirects, unknown provider paths and the requests it forwards unchanged.

Gateway errors keep their phase and stable identifier: exit 2 for a failed precondition, 3 for unconfirmed service state before any file changed, 4 for an operation that failed but restored the prior installation, and 5 for uncertain recovery after changes began. The installer reports the structured cause and keeps that exit code. Registration requires Python 3.14 with a working zstd codec; detachment runs on Python 3.11. The [installer lifecycle specification](../coding-convention/specs/2026-10-15-gateway-installer-design.md) records the design the phase machine comes from.

## Platform Surfaces

| Platform | Installed surfaces |
| --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/`, `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`, Claude command wrappers |
| Codex | `~/.agents/skills/`, `~/.codex/AGENTS.md`, `~/.codex/ghost-alice-governance.md`, `~/.codex/hooks.json`, `~/.codex/config.toml` |
| Shared state | `~/.ghost-alice/installed-payload.json`, `~/.ghost-alice/rollbacks/`, `~/.ghost-alice/install-state/`, `~/.ghost-alice/pending-merges/`, `~/.ghost-alice/uninstall-reports/`, `~/.ghost-alice/install/` |

Claude Code can expose native skill invocation and hook permissions, but those surfaces do not replace a global instruction port for sessions outside the repository. The installer therefore merges the managed block from `platforms/claude/CLAUDE.md` into the Claude config directory. Codex does not expose the same skill surface, so its separate bootstrap and hook config make required gates auditable through `SKILL.md` read records and hook payloads. Both global rule files preserve text outside their managed blocks; a destination without a Ghost-ALICE marker receives a `.ghost-alice-proposed` file instead of an overwrite.

Managed hook commands and the Codex hook trust entries that the installer writes for them on every install run must agree with the install record. A running host can retain its previously loaded registration. Verify adoption through an actual native callback after the appropriate reload; refer to [hook trust and reload](../getting-started/troubleshooting.md#native-hook-trust-and-reload). The [session gate notes](../policies/session-gate-matrix.md#notes) list installed bytes and native execution as separate claims.

The bootstrap writer reuses an existing regular proposal file with identical bytes, without rewriting it. It checks the preferred filename and its separately named siblings. If none matches, it creates the preferred file or, when that path already exists, a separately named proposal beside it; it preserves existing files and links. It reports the selected path and continues the install. The hook settings editor changes the managed TOML assignment while retaining unrelated multiline values, quoted table names and comments; dotted and inline table forms remain editable. A cleanup edit that cannot preserve valid TOML is reported for review.

The Codex hook editor uses the bundled Tomli 2.4.0 parser through `installer.codex_toml`. It accepts TOML 1.1 and an initial UTF-8 BOM without downloading a parser during installation. Invalid TOML still stops hook configuration before the editor saves it. For malformed JSON hook settings, the existing backup-and-continue path reports the settings path, backup path and continuation with empty settings in the installer's normal output.

## State And Safety Model

The installer records what it changes instead of guessing during cleanup.

| State path | Why it exists |
| --- | --- |
| `~/.ghost-alice/installed-payload.json` | The install record: every install location of both hosts, its source and the fingerprints of copied files, written in the same step as each placement |
| `~/.ghost-alice/rollbacks/<txid>/` | What one publish replaced, the record as it was before it, and its journal; the next locked command restores a publish that stopped before its journal said done. [Runtime state retention](repository-structure.md#runtime-state-retention) sets how many finished publishes stay |
| `~/.ghost-alice/install-state/<platform>.json` | Records installer-owned targets and environment changes for status and uninstall |
| `~/.ghost-alice/pending-merges/<platform>/` | The installer backs up external changes to Ghost-ALICE copy-mode skill files before replacing them. It checks roots with the install marker and every root the current install replaces, even when that root's marker has disappeared; the response model follows merge-companion to ask the human user whether to merge, discard or defer each backup. Folders that Ghost-ALICE does not install, such as the Claude app's `synced`, are never recorded |
| `~/.ghost-alice/install/` | Keeps the install reports and event traces for audit and recovery; [runtime state retention](repository-structure.md#runtime-state-retention) sets how many runs stay |

This is why update and uninstall are part of the installer architecture, not separate chores. A change that affects copied assets, hooks, or runtime config usually also affects status, doctor, pending merge behavior, and uninstall rollback.

## Contributor Entry Points

| Change type | Start here | Then verify with |
| --- | --- | --- |
| Python runtime detection, the PowerShell 7.6 check, PowerShell parameter names or locale setup | `install.sh`, `install.ps1`, `install.cmd` | `scripts/tests/test_thin_shells.py`, `scripts/tests/test_install_runtime_detection.py`, compatibility matrix groups |
| Installer option, command table, dispatch or install order | `_shared/installer/cli.py`, `_shared/installer/auto.py` | `_shared/test_installer_cli.py`, `_shared/test_installer_auto.py`, `_shared/test_installer_checkout.py`, the `_shared/test_*_shell.py` end-to-end tests |
| Installer wording or the install report | `_shared/installer/report.py` | `_shared/test_installer_report.py`, `scripts/tests/test_installer_default_auto.py` |
| Hook payload or hook status wording | `_shared/install_hooks.py` | `_shared.test_install_hooks`, `scripts/check_skill_gate_contract.py` |
| Hook id or host event name | `_shared/hook_definitions.py` | `_shared.test_hook_definitions` |
| Install record, fingerprints, publish journal or restore | `_shared/installed_payload.py`, `_shared/content_digest.py`, `_shared/install_transaction.py`, `_shared/installer/transaction.py`, `_shared/installer/lock.py` | `_shared/test_installed_payload.py`, `_shared/test_content_digest.py`, `_shared/test_install_transaction_publish.py`, `_shared/test_install_record_shell.py` |
| Install-state or uninstall behavior | `_shared/install_state_writer.py`, `_shared/uninstall_cleanup.py`, `_shared/installer/lifecycle.py`, `_shared/installer/transaction.py` | `_shared/test_installer_lifecycle.py`, `_shared/test_installer_transaction.py`, install-state schema tests and uninstall tests |
| Public docs or command surface | `README.md`, `docs/`, `index.html` | `scripts/validate_public_surfaces.py` and public-surface contract tests |
| Platform compatibility rule | `docs/policies/installer-platform-compatibility-matrix.md` | `scripts/run_installer_compat_tests.py --list` and the focused group named by the policy |

When changing one part, look for the paired contract and find its regression checks in the test owner that the compatibility matrix names.

## What Not To Infer

- Hook files existing on disk does not prove hook payloads are current.
- A copied skill directory does not prove the platform can invoke or audit that skill.
- A passing install smoke does not prove status, doctor, update, pending merge, and uninstall paths are aligned.
- A user-facing install command does not explain the architecture; it is only the entrypoint.

## Full Understanding

Full installer understanding requires tracing one change through the entrypoint, orchestration layer, runtime surface, state file, and test owner. This page is the first map for that trace. It is not a substitute for reading the relevant implementation and compatibility contract.
