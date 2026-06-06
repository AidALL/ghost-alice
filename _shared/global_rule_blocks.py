#!/usr/bin/env python3
"""Managed block merge helpers for global AI rule files."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


CODEX_BOOTSTRAP_MARKER = "# Ghost-ALICE Codex Bootstrap"
CODEX_MANAGED_BLOCK_BEGIN = "<!-- Ghost-ALICE managed block begin: codex-bootstrap -->"
CODEX_MANAGED_BLOCK_END = "<!-- Ghost-ALICE managed block end: codex-bootstrap -->"

CODEX_HOOKLESS_FALLBACK_BLOCK = """## Codex Hook Enforcement And Hookless Fallback

When Codex hooks are trusted and a `PreToolUse` tool-checkpoint payload is observed, treat tool-checkpoint as a hook-enforced retry point. After a hook denial, the agent emits a `[tool-checkpoint]` and retries the same tool call. Hook timing enforcement does not weaken the semantic gate. Required decision fields are `intent`, `why`, `procedure`, `contract-ref`, `contract-check`, `localized-human-note`, `rejected-alternatives`, `unverified-premises`, and `failure-mode-if-wrong`. Add `recovery-action` only when a mismatch, scope reopen, external side effect, or hard-to-recover action needs a concrete next step.

If hooks are disabled, review/trust has not completed, or no hook payload has been observed in the current session, the session is in hookless/manual fallback mode. Only in that case, the bootstrap directly enforces the procedure below.

- Immediately after each user turn starts, check the pending-merge precheck first, apply the session-intent-analyzer intake next, then apply rule 0 (task-router), the Session Gate Contract, and tool-checkpoint.
- Do not store the raw user input. The session-intent-analyzer ledger records only digest-only intake status. The agent adds a semantic delta only when the user's intent, constraints, decisions, or completion criteria materially change.
- session-intent-analyzer intent-state.json is update-plus-accumulate state. Scalar intent is corrected to the latest delta when a semantic delta exists, while list-like constraints/non-goals/open questions/criteria/decisions accumulate by dedupe or id-based merge.
- The pending-merge precheck completes in the pre-routing/session-start layer before the user-input governance graph begins.
- After this precheck is clean, or after surfacing ends through an explicit user defer/skip, the runtime hook graph is: user input -> session-intent-analyzer digest/ledger/current-session pointer write and allow -> skill-evolution report-only consumption and jailbreak-detector model_security_decision write -> current-lineage block only carried to downstream-gates.json -> tool-stage tool-checkpoint.
- jailbreak-detector deterministic hard-block rules are a narrow regression guard for explicit, high-confidence attack signals. Gradual multi-turn jailbreak resistance depends on session-intent summary quality and cumulative constraint comparison quality.
- tool-checkpoint looks only at the current-lineage block gate and the silent allow invariant. If `opened=false` or `decision=block`, it denies; an absent gate or any other state is silent allow. It does not use tool-call identity, payload content, or audit/log/correlation metadata as decision input; audit/log/correlation metadata stays outside the decision body.
- If task-router outputs `boundary-contract: required`, apply `boundary-contract` before any other tool call.
- Leave a `[gate-state]` block in the first commentary.
- Leave a `[tool-checkpoint]` block immediately before every new tool action.
- A routine inspection batch explicitly declared by the previous full gate may be referenced briefly as `[tool-checkpoint:batch]`; output polling for an already-started process may be referenced briefly as `[tool-checkpoint:continuation]`.
- `[tool-checkpoint:continuation]` refers only to output polling for the same process/session/tool-call id. Switching to a new command, input, timeout, interruption, or ref returns to a full `[tool-checkpoint]`.
- Simple polling of the same ref does not require repeated output; this is a shorthand that avoids repeating the full gate. Surface it the first time or when state changes.
- Do not infer whether an action is safe from tool-call identity or payload content. The decision depends only on the current-lineage block gate and the silent allow invariant.
- Gate schemas such as `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, and `[io-trace]` follow English canonical narrative + English control surface.
- Immediately before a completion claim, leave a `[completion-check]` block that connects acceptance criteria to fresh evidence.
- Leave an `[io-trace]` block at the end of the response.
- If any required step was missed, repair it immediately and then continue.

This mode is not a substitute for hook guarantees; it is the fallback when hook evidence is absent. When hook payload is observed, use that evidence first. When it is absent, apply the prose gates.
"""


class GlobalRuleBlockError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApplyResult:
    status: str
    path: Path


@dataclass(frozen=True)
class RuleBlockSpec:
    marker: str
    begin: str
    end: str
    legacy_markers: tuple[str, ...] = ()
    legacy_blocks: tuple[tuple[str, str], ...] = ()


CODEX_SPEC = RuleBlockSpec(
    marker=CODEX_BOOTSTRAP_MARKER,
    begin=CODEX_MANAGED_BLOCK_BEGIN,
    end=CODEX_MANAGED_BLOCK_END,
    legacy_markers=("# AidALL Codex Bootstrap",),
    legacy_blocks=(
        (
            "<!-- AidALL managed block begin: codex-bootstrap -->",
            "<!-- AidALL managed block end: codex-bootstrap -->",
        ),
    ),
)
def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise GlobalRuleBlockError(f"file not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise GlobalRuleBlockError(f"file is not valid UTF-8: {path}") from exc


def _write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _known_markers(spec: RuleBlockSpec) -> tuple[str, ...]:
    return (spec.marker, *spec.legacy_markers)


def _known_blocks(spec: RuleBlockSpec) -> tuple[tuple[str, str], ...]:
    return ((spec.begin, spec.end), *spec.legacy_blocks)


def _find_managed_block(text: str, spec: RuleBlockSpec) -> tuple[int, int] | None:
    for begin_token, end_token in _known_blocks(spec):
        begin = text.find(begin_token)
        end = text.find(end_token)
        if begin != -1 and end != -1 and begin < end:
            return begin, end + len(end_token)
    return None


def _normalize_leading_marker(text: str, spec: RuleBlockSpec) -> str:
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    first_line = lines[0]
    if first_line.rstrip("\n").strip() not in _known_markers(spec):
        return text
    newline = "\n" if first_line.endswith("\n") else ""
    return spec.marker + newline + "".join(lines[1:])


def _strip_marker_line(source_text: str, spec: RuleBlockSpec) -> str:
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if lines and lines[0].strip() in _known_markers(spec):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _render_managed_block(source_text: str, spec: RuleBlockSpec, *, extra_block: str = "") -> str:
    body = _strip_marker_line(source_text, spec)
    if extra_block:
        body = body.rstrip() + "\n\n" + extra_block.strip()
    return f"{spec.begin}\n{body.rstrip()}\n{spec.end}"


def _render_bootstrap(source_text: str, spec: RuleBlockSpec, *, extra_block: str = "") -> str:
    return f"{spec.marker}\n{_render_managed_block(source_text, spec, extra_block=extra_block)}\n"


def _merge_bootstrap_text(
    existing_text: str | None,
    source_text: str,
    spec: RuleBlockSpec,
    *,
    extra_block: str = "",
) -> tuple[str, str]:
    rendered = _render_bootstrap(source_text, spec, extra_block=extra_block)
    if not existing_text:
        return "updated", rendered

    existing = existing_text.replace("\r\n", "\n").replace("\r", "\n")
    managed_block = _find_managed_block(existing, spec)
    if managed_block is not None:
        begin, end = managed_block
        replacement = _render_managed_block(source_text, spec, extra_block=extra_block)
        prefix = _normalize_leading_marker(existing[:begin], spec)
        merged = prefix + replacement + existing[end:]
        if not merged.endswith("\n"):
            merged += "\n"
        return "updated", merged

    if existing.startswith(_known_markers(spec)):
        return "updated", rendered

    return "proposed", rendered


def _apply_bootstrap(
    source_path: Path | str,
    dest_path: Path | str,
    spec: RuleBlockSpec,
    *,
    proposed_path: Path | str | None = None,
    extra_block: str = "",
) -> ApplyResult:
    source = Path(source_path)
    dest = Path(dest_path)
    proposed = Path(proposed_path) if proposed_path is not None else dest.with_name(dest.name + ".ghost-alice-proposed")
    source_text = _read_text(source)

    existing_text = None
    if dest.exists():
        try:
            existing_text = dest.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            status, body = "proposed", _render_bootstrap(source_text, spec, extra_block=extra_block)
            _write_text(proposed, body)
            return ApplyResult(status, proposed)

    status, body = _merge_bootstrap_text(existing_text, source_text, spec, extra_block=extra_block)
    if status == "proposed":
        _write_text(proposed, body)
        return ApplyResult(status, proposed)

    _write_text(dest, body)
    return ApplyResult(status, dest)


def _remove_marker_line(text: str, spec: RuleBlockSpec) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() in _known_markers(spec):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _remove_bootstrap(dest_path: Path | str, spec: RuleBlockSpec) -> ApplyResult:
    dest = Path(dest_path)
    if not dest.exists():
        return ApplyResult("unchanged", dest)

    existing = _read_text(dest).replace("\r\n", "\n").replace("\r", "\n")
    managed_block = _find_managed_block(existing, spec)
    if managed_block is not None:
        begin, end = managed_block
        remaining = _remove_marker_line(existing[:begin] + existing[end:], spec).strip()
        if not remaining:
            dest.unlink()
            return ApplyResult("removed", dest)
        _write_text(dest, remaining + "\n")
        return ApplyResult("updated", dest)

    if existing.startswith(_known_markers(spec)):
        dest.unlink()
        return ApplyResult("removed", dest)

    return ApplyResult("unchanged", dest)


def _codex_extra_block(*, hookless_fallback: bool) -> str:
    return CODEX_HOOKLESS_FALLBACK_BLOCK if hookless_fallback else ""


def render_codex_managed_block(source_text: str, *, hookless_fallback: bool = False) -> str:
    return _render_managed_block(
        source_text,
        CODEX_SPEC,
        extra_block=_codex_extra_block(hookless_fallback=hookless_fallback),
    )


def render_codex_bootstrap(source_text: str, *, hookless_fallback: bool = False) -> str:
    return _render_bootstrap(
        source_text,
        CODEX_SPEC,
        extra_block=_codex_extra_block(hookless_fallback=hookless_fallback),
    )


def merge_codex_bootstrap_text(existing_text: str | None, source_text: str, *, hookless_fallback: bool = False) -> tuple[str, str]:
    return _merge_bootstrap_text(
        existing_text,
        source_text,
        CODEX_SPEC,
        extra_block=_codex_extra_block(hookless_fallback=hookless_fallback),
    )


def apply_codex_bootstrap(
    source_path: Path | str,
    dest_path: Path | str,
    *,
    proposed_path: Path | str | None = None,
    hookless_fallback: bool = False,
) -> ApplyResult:
    return _apply_bootstrap(
        source_path,
        dest_path,
        CODEX_SPEC,
        proposed_path=proposed_path,
        extra_block=_codex_extra_block(hookless_fallback=hookless_fallback),
    )


def remove_codex_bootstrap(dest_path: Path | str) -> ApplyResult:
    return _remove_bootstrap(dest_path, CODEX_SPEC)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge Ghost-ALICE-managed global rule file blocks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    codex = subparsers.add_parser("codex-merge")
    codex.add_argument("--source", required=True, type=Path)
    codex.add_argument("--dest", required=True, type=Path)
    codex.add_argument("--proposed", type=Path, default=None)
    codex.add_argument("--hookless-fallback", action="store_true")

    codex_remove = subparsers.add_parser("codex-remove")
    codex_remove.add_argument("--dest", required=True, type=Path)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "codex-merge":
            result = apply_codex_bootstrap(
                args.source,
                args.dest,
                proposed_path=args.proposed,
                hookless_fallback=args.hookless_fallback,
            )
            print(f"{result.status}:{result.path}")
        elif args.command == "codex-remove":
            result = remove_codex_bootstrap(args.dest)
            print(f"{result.status}:{result.path}")
        else:
            raise AssertionError(args.command)
    except GlobalRuleBlockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
