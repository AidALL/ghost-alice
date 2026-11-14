"""What the installer registers in the host: hooks, managed rule blocks and command wrappers."""

from __future__ import annotations

from pathlib import Path
import json
from typing import TYPE_CHECKING

import hook_definitions
from installer import locations, report, runner

if TYPE_CHECKING:
    from installer.cli import Context

CODEX_BOOTSTRAP_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"
CLAUDE_BOOTSTRAP_MARKER = "# Ghost-ALICE Claude Bootstrap"
CLAUDE_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: claude-bootstrap -->"
CLAUDE_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: claude-bootstrap -->"
HOOK_ACTION_LABEL = " + ".join(hook_definitions.CORE_HOOK_IDS)
GLOBAL_RULE_BLOCKS = "_shared/global_rule_blocks.py"


def codex_bootstrap_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "codex" / "AGENTS.md"


def codex_loader_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "codex" / "bootstrap.md"


def claude_bootstrap_source() -> Path:
    return runner.REPO_ROOT / "platforms" / "claude" / "CLAUDE.md"


def install_hooks(ctx: "Context", action: str, platform: str, *, backup_sources=None):
    """`install_hooks.py --core-source-root --platform --hook-shared-dir --skills-dir
    [--visibility] [--uninstall|--status] [--addon-source ...]`; install
    defaults visibility to `dynamic`; status and doctor pass no visibility."""
    report.emit("")
    report.info(f"Agent hook {action}... ({HOOK_ACTION_LABEL})")
    from install_hooks import HookStatus, STATUS_LABELS
    def failed(message):
        report.error(message)
        return HookStatus(platform, 'HOOK_ACTION_ERROR', STATUS_LABELS['error'], 'error',
                          {'action': action, 'items': [], 'errors': [message]})
    hook_py = runner.REPO_ROOT / "_shared" / "install_hooks.py"
    if not hook_py.is_file():
        return failed(f"install_hooks.py not found: {hook_py}")
    if action == "uninstall":
        from install_hooks import uninstall_hook
        return uninstall_hook(platform, backup_sources=backup_sources)
    args = ["--core-source-root", str(runner.REPO_ROOT), "--platform", platform,
            "--hook-shared-dir", str(locations.runtime_shared_dir()), "--skills-dir", str(ctx.skills_dir)]
    if action == "install":
        args.extend(["--visibility", ctx.options.visibility or "dynamic"])
    elif action == "status":
        args.append("--status")
    for addon_source in ctx.addon_sources:
        args.extend(["--addon-source", str(addon_source)])
    result = runner.run_tool('_shared/install_hooks.py', *args, '--format', 'json', capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip('\n'))
    try:
        payload = json.loads(result.stdout or '')
        row = payload['results'][platform]
        status = HookStatus(**row)
        allowed = {'install': {'installed', 'already', 'skipped', 'error'},
                   'status': {'installed', 'missing', 'skipped', 'error'}}[action]
        if (status.platform != platform or status.legacy_status not in allowed
                or not isinstance(status.details, dict)
                or (action != 'status' and status.details.get('action') != action)
                or not isinstance(status.status_token, str) or not status.status_token
                or not isinstance(status.status_label, str)
                or not isinstance(status.details.get('items', []), list)
                or not isinstance(status.details.get('errors', []), list)
                or type(payload['returncode']) is not int
                or payload['returncode'] != result.returncode
                or (result.returncode == 0) != (status.legacy_status != 'error')):
            raise ValueError('inconsistent hook result')
    except (ValueError, KeyError, TypeError) as error:
        return failed(f'Hook {action} returned an invalid result: {error}')
    return status


def _merge_block(args: list[str], failure: str, proposed_prefix: str, updated: str) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.error(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("proposed:"):
        report.warn(f"{proposed_prefix}: {output[len('proposed:'):]}")
    else:
        report.ok(updated)
    return True


def ensure_claude_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py claude-merge --source --dest --proposed`; `proposed:` → warning."""
    claude_home = locations.claude_home()
    rules = claude_home / "CLAUDE.md"
    claude_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["claude-merge", "--source", str(claude_bootstrap_source()),
                         "--dest", str(rules), "--proposed", f"{rules}.ghost-alice-proposed"],
                        "Claude CLAUDE.md block merge failed; aborting install",
                        "Claude CLAUDE.md has no Ghost-ALICE block; proposed file available",
                        "Claude CLAUDE.md bootstrap block updated")


def ensure_codex_bootstrap(ctx: "Context") -> bool:
    """`global_rule_blocks.py codex-merge --source --loader --dest --proposed`."""
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    codex_home.mkdir(parents=True, exist_ok=True)
    return _merge_block(["codex-merge", "--source", str(codex_bootstrap_source()),
                         "--loader", str(codex_loader_source()),
                         "--dest", str(agents), "--proposed", f"{agents}.ghost-alice-proposed"],
                        "Codex AGENTS.md block merge failed; aborting install",
                        "Codex AGENTS.md has no Ghost-ALICE block; proposed file available",
                        "Codex AGENTS.md bootstrap block updated")


def _remove_block(args: list[str], failure: str, removed: str, updated: str, require_change: bool) -> bool:
    result = runner.run_tool(GLOBAL_RULE_BLOCKS, *args, capture=True)
    if result.stderr:
        report.emit(result.stderr.rstrip("\n"))
    if not result.ok:
        report.warn(failure)
        return False
    output = (result.stdout or "").strip()
    if output.startswith("removed:"):
        report.ok(removed)
        return True
    if output.startswith("updated:"):
        report.ok(updated)
        return True
    return not require_change


def remove_claude_bootstrap(ctx: "Context", require_change: bool = False) -> bool:
    """`global_rule_blocks.py claude-remove --dest`; result prefixes decide the message."""
    rules = locations.claude_home() / "CLAUDE.md"
    if not rules.is_file():
        return not require_change
    return _remove_block(["claude-remove", "--dest", str(rules)], "Claude CLAUDE.md block removal failed",
                         "Claude CLAUDE.md bootstrap removed",
                         "Claude CLAUDE.md Ghost-ALICE block removed; content outside the block preserved", require_change)


def remove_claude_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(skills_root, ctx.platform):
        return False
    return remove_claude_bootstrap(ctx, True)


def remove_codex_bootstrap_if_unused(ctx: "Context", skills_root: Path) -> bool:
    from installer import selection

    if selection.has_managed_installs(skills_root, ctx.platform):
        return False
    codex_home = locations.codex_home()
    agents = codex_home / "AGENTS.md"
    if not agents.is_file() and not (codex_home / "ghost-alice-governance.md").is_file():
        return False
    return _remove_block(["codex-remove", "--dest", str(agents)], "Codex AGENTS.md block removal failed",
                         "Codex AGENTS.md bootstrap removed",
                         "Codex AGENTS.md Ghost-ALICE block removed; content outside the block preserved", True)


def sync_commands(ctx: "Context", mode: str = "sync", target: str = "all") -> int:
    """`commands_sync.py <catalog> <checkout .claude/commands> <mode> <target>`; a missing catalog is a warning."""
    catalog = runner.REPO_ROOT / "skill-catalog" / "skills.json"
    if not catalog.is_file():
        report.warn(f"skills.json not found: {catalog}")
        return 1
    claude_dir = runner.REPO_ROOT / ".claude" / "commands"
    return runner.run_tool("_shared/commands_sync.py", str(catalog), str(claude_dir), mode, target).returncode
