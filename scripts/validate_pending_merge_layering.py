#!/usr/bin/env python3
"""Static validation for the pending-merge/session-start layered gate.

Statically checks whether the pending-merge self-check remains present in hooks,
bootstrap, the common contract, merge-companion and install guidance. Exits 1 when any check fails. CI
uses this as a regression gate.

Usage:
  python3 scripts/validate_pending_merge_layering.py        # full pending-merge layer check
  python3 scripts/validate_pending_merge_layering.py --json # JSON output for CI
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _stdout_can_encode(text: str) -> bool:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _safe_for_stdout(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    if not _stdout_can_encode(text):
        text = text.translate({8212: ord("-")})
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _print_safe(text: str = "") -> None:
    print(_safe_for_stdout(text))


def _status_symbol(ok: bool) -> str:
    if _stdout_can_encode("✓✗"):
        return "✓" if ok else "✗"
    return "OK" if ok else "FAIL"


@dataclass
class LayerCheck:
    layer: str
    description: str
    file: Path
    pattern: str
    multiline: bool = False
    must_exist: bool = True
    owner: Path | None = None
    owner_section: str = ""
    owner_pattern: str = ""


CHECKS: list[LayerCheck] = [
    LayerCheck(
        layer="pending-merge-session-start",
        description="install_hooks.py defines SessionStart hook constants",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"SESSION_START_MARKER\s*=",
    ),
    LayerCheck(
        layer="pending-merge-session-start",
        description="install_hooks.py maps on_session_start intent",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"['\"]on_session_start['\"]",
    ),
    LayerCheck(
        layer="pending-merge-user-prompt",
        description="install_hooks.py creates the prompt-submit pending-merge precheck command",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"HOOK_COMMAND\s*=\s*_hook_reminder_command\(",
    ),
    LayerCheck(
        layer="pending-merge-user-prompt",
        description="install_hooks.py HOOK_COMMAND body contains the word 'merge-companion'",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"merge-companion",
    ),
    LayerCheck(
        layer="pending-merge-prose-rule",
        description="common governance names merge-companion only for a requested review or an affected-skill change",
        file=REPO / "platforms" / "common-governance.md",
        pattern=r"`merge-companion`: an undecided pending merge, when the user asks to review it or the selected work changes an affected skill",
    ),
    LayerCheck(
        layer="pending-merge-skill-activation",
        description="the common Owners entry sends pending merges to merge-companion, which owns the re-check before changing an installed skill and the affected-change trigger",
        file=REPO / "platforms" / "common-governance.md",
        pattern=r"`merge-companion`: an undecided pending merge",
        owner=REPO / "merge-companion" / "SKILL.md",
        owner_section="Entry and activation",
        owner_pattern=r"re-check the current platform's manifest[\s\S]*does not answer that check[\s\S]*bounded read-only lookup unrelated to those changes[\s\S]*leaves the backups undecided",
    ),
    LayerCheck(
        layer="pending-merge-bootstrap-self-check",
        description="Codex template refers to the one common governance source",
        file=REPO / "platforms" / "codex" / "AGENTS.md",
        pattern=r"\{\{COMMON_GOVERNANCE\}\}",
        owner=REPO / "platforms" / "common-governance.md",
        owner_section="Owners",
        owner_pattern=r"`merge-companion`: an undecided pending merge",
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.sh tail contains localized User/Tech backup-review guidance",
        file=REPO / "install.sh",
        pattern=r"User:.*Please\s+review\s+backed-up\s+changes",
        multiline=True,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.ps1 tail contains localized User/Tech backup-review guidance",
        file=REPO / "install.ps1",
        pattern=r"User:.*Please\s+review\s+backed-up\s+changes",
        multiline=True,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.sh tail does not contain old Korean control copy",
        file=REPO / "install.sh",
        pattern=r"review\s+whether\s+unprocessed\s+pending\s+merges\s+remain\s+after\s+skill\s+updates",
        must_exist=False,
    ),
    LayerCheck(
        layer="pending-merge-install-tail",
        description="install.ps1 tail does not contain old Korean control copy",
        file=REPO / "install.ps1",
        pattern=r"review\s+whether\s+unprocessed\s+pending\s+merges\s+remain\s+after\s+skill\s+updates",
        must_exist=False,
    ),
    LayerCheck(
        layer="pending-merge-readme",
        description="diff_collector.py defines _write_readme_first",
        file=REPO / "merge-companion" / "scripts" / "diff_collector.py",
        pattern=r"def\s+_write_readme_first\s*\(",
    ),
    LayerCheck(
        layer="pending-merge-session-start-failsafe",
        description="install_hooks.py creates the SessionStart pending-merge precheck command",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"SESSION_START_COMMAND\s*=\s*_session_start_command\(",
    ),
    LayerCheck(
        layer="EXTERNAL-EVIDENCE",
        description="input hooks do not generate unconditional external-search guidance",
        file=REPO / "_shared" / "install_hooks.py",
        pattern=r"WEB_SEARCH_FIRST_INTERNAL|def _web_search_first_command|def _platform_web_search_entry",
        must_exist=False,
    ),
    LayerCheck(
        layer="EXTERNAL-EVIDENCE",
        description="verification procedure delegates external evidence selection to its owning reference",
        file=REPO / "coding-convention" / "verification-before-completion" / "SKILL.md",
        pattern=r"external-tool-evidence\.md",
        owner=REPO / "coding-convention/verification-before-completion/references/external-tool-evidence.md",
        owner_section="Select evidence for the claim",
        owner_pattern=r"unresolved uncertainty[\s\S]*Official documentation establishes intended behavior[\s\S]*contrary evidence",
    ),
    LayerCheck(
        layer="EXTERNAL-EVIDENCE",
        description="adversarial-verification delegates external-tool evidence to its installed owner",
        file=REPO / "adversarial-verification" / "SKILL.md",
        pattern=r"verification-before-completion[^\n]*`references/external-tool-evidence\.md`",
        owner=REPO / "coding-convention/verification-before-completion/references/external-tool-evidence.md",
        owner_section="Original sources and locators",
        owner_pattern=r"Source-locator contract:[\s\S]*accessible_url[\s\S]*file_path[\s\S]*source-locator",
    ),
    LayerCheck(
        layer="EXTERNAL-EVIDENCE",
        description="verification-before-completion links its external-tool evidence owner",
        file=REPO / "coding-convention" / "verification-before-completion" / "SKILL.md",
        pattern=r"\]\(references/external-tool-evidence\.md\)",
        owner=REPO / "coding-convention/verification-before-completion/references/external-tool-evidence.md",
        owner_section="Original sources and locators",
        owner_pattern=r"Source-locator contract:[\s\S]*accessible_url[\s\S]*file_path[\s\S]*source-locator",
    ),
    LayerCheck(
        layer="EVAL",
        description="evaluator artifact contract document exists and mentions verifier-result.json",
        file=REPO / "docs" / "policies" / "evaluator-artifact-contract.md",
        pattern=r"scenario\.json[\s\S]+trace\.json[\s\S]+report\.json[\s\S]+candidate-playbook\.md[\s\S]+verifier-result\.json",
        multiline=True,
    ),
    LayerCheck(
        layer="EVAL",
        description="adversarial-verification references the evaluator artifact contract",
        file=REPO / "adversarial-verification" / "SKILL.md",
        pattern=r"docs/policies/evaluator-artifact-contract\.md",
        owner=REPO / "docs/policies/evaluator-artifact-contract.md",
        owner_section="Promotion Rules",
        owner_pattern=r"read-only evaluator pass must not mutate installed assets[\s\S]*accepted verifier-result[\s\S]*accepted: false[\s\S]*stop installed asset changes",
        multiline=True,
    ),
    LayerCheck(
        layer="EVAL",
        description="verification-before-completion references the evaluator artifact contract",
        file=REPO / "coding-convention" / "verification-before-completion" / "SKILL.md",
        pattern=r"\]\(references/evaluator-artifacts\.md\)",
        owner=REPO / "coding-convention/verification-before-completion/references/evaluator-artifacts.md",
        owner_section="Evaluator Artifact Contract",
        owner_pattern=r"docs/policies/evaluator-artifact-contract\.md[\s\S]*accepted `verifier-result\.json`[\s\S]*Missing required evidence",
        multiline=True,
    ),
]


def check_layer(c: LayerCheck) -> tuple[bool, str]:
    if not c.file.exists():
        return False, f"{c.layer}: file missing. {c.file}"
    text = c.file.read_text(encoding="utf-8")
    if c.file.name in ("install.sh", "install.ps1"):
        # Scan installer entrypoints and installer_lib modules together for pending-merge layers.
        ext = ".sh" if c.file.name == "install.sh" else ".ps1"
        lib_dir = c.file.parent / "installer_lib"
        if lib_dir.is_dir():
            for lib in sorted(lib_dir.glob(f"*{ext}")):
                text += "\n" + lib.read_text(encoding="utf-8")
    flags = re.MULTILINE | re.DOTALL if c.multiline else re.MULTILINE
    found = re.search(c.pattern, text, flags) is not None
    if c.must_exist and not found:
        return False, f"{c.layer}: pattern not found. {c.description} ({c.file.relative_to(REPO)})"
    if not c.must_exist and found:
        return False, f"{c.layer}: forbidden pattern found. {c.description} ({c.file.relative_to(REPO)})"
    if c.owner is not None:
        if not c.owner.is_file():
            return False, f"{c.layer}: contract owner missing. {c.owner}"
        owner_text = c.owner.read_text(encoding="utf-8")
        heading = re.search(r"^(#{1,6}) " + re.escape(c.owner_section) + r"$", owner_text, re.MULTILINE)
        if heading is None:
            return False, f"{c.layer}: contract owner section missing. {c.owner_section}"
        body = owner_text[heading.end():]
        following = re.search(r"^#{1," + str(len(heading.group(1))) + r"} ", body, re.MULTILINE)
        if following:
            body = body[:following.start()]
        if not re.search(c.owner_pattern, body, re.MULTILINE):
            return False, f"{c.layer}: contract owner requirements missing. {c.owner_section}"
    return True, f"{c.layer}: OK. {c.description}"


def _function_source(text: str, function_name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(text, node) or ""
    return ""


def check_session_start_failsafe() -> tuple[bool, str]:
    """Check whether the SessionStart helper keeps the failsafe that swallows hook failure."""
    f = REPO / "_shared" / "install_hooks.py"
    if not f.exists():
        return False, "pending-merge-session-start-failsafe: install_hooks.py missing"
    text = f.read_text(encoding="utf-8")
    body = _function_source(text, "_session_start_command")
    if not body:
        return False, "pending-merge-session-start-failsafe: _session_start_command definition not found"
    if "|| true" not in body:
        return False, "pending-merge-session-start-failsafe: SessionStart helper does not include the '|| true' failsafe"
    if "SESSION_START_MARKER" not in body:
        return False, "pending-merge-session-start-failsafe: SessionStart helper does not attach SESSION_START_MARKER to the command"
    return True, "pending-merge-session-start-failsafe: SessionStart helper failsafe confirmed"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="Print JSON output")
    args = p.parse_args()

    results = []
    failures = []

    for c in CHECKS:
        ok, msg = check_layer(c)
        results.append({"layer": c.layer, "ok": ok, "msg": msg})
        if not ok:
            failures.append(msg)

    ok, msg = check_session_start_failsafe()
    results.append({"layer": "pending-merge-session-start-failsafe-detail", "ok": ok, "msg": msg})
    if not ok:
        failures.append(msg)

    if args.json:
        print(json.dumps({"failures": failures, "results": results}, indent=2, ensure_ascii=True))
    else:
        for r in results:
            sym = _status_symbol(r["ok"])
            _print_safe(f"{sym} {r['msg']}")
        if failures:
            _print_safe(f"\nFAIL: {len(failures)} layer validation failure(s)")
        else:
            _print_safe("\nOK: pending-merge/session-start layered gate checks passed")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
