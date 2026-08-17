#!/usr/bin/env python3
"""Purpose-hidden behavior evaluation using isolated direct subprocesses."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from _shared.fresh_agent_session import FreshAgentSessionError, run_fresh_agent_session
    from _shared.project_runtime import ProjectRuntime, project_runtime
except ModuleNotFoundError:  # Direct execution from the installed _shared directory.
    from fresh_agent_session import FreshAgentSessionError, run_fresh_agent_session
    from project_runtime import ProjectRuntime, project_runtime

CODE_OWNER_ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"claude", "codex"}
MANIFEST_KEYS = {
    "schema_version", "platform", "installed_at", "source_root", "source_branch",
    "source_head", "source_dirty_state", "remote_freshness_state", "targets",
    "system_env_changes",
}
RECORD_KEYS = {
    "case_id", "case_hash", "suite_digest", "source", "version", "installed_provenance", "mode",
    "verdict", "dimensions", "reason", "process",
}
REASONS = {
    "not-evaluated", "subject-timeout", "subject-process-failure", "subject-empty-response",
    "evaluator-timeout", "evaluator-process-failure", "invalid-evaluator-output",
    "evaluator-confirmed-pass", "evaluator-confirmed-fail",
}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _object(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    if set(value) != keys:
        raise ValueError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _identifier(value: Any, label: str) -> str:
    value = _text(value, label)
    forbidden = ("credential", "password", "secret", "token", "api-key")
    if not SAFE_ID.fullmatch(value) or any(word in value for word in forbidden):
        raise ValueError(f"{label} must be a constrained public identifier")
    return value


def _timestamp(value: Any, label: str) -> str:
    value = _text(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label)
    if not SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def validate_case_bundle(value: Any) -> dict[str, Any]:
    keys = {"id", "source", "version", "prompt", "evaluator_private"}
    raw = _object(value, keys, "case bundle")
    case_id, source = _identifier(raw["id"], "case id"), _identifier(raw["source"], "case source")
    version = _text(raw["version"], "case version")
    try:
        if date.fromisoformat(version).isoformat() != version:
            raise ValueError
    except ValueError as exc:
        raise ValueError("case version must be an ISO date") from exc
    private_keys = {
        "purpose", "rubric", "expected_answer", "pass_criteria", "prior_output",
        "experiment_label",
    }
    private = _object(raw["evaluator_private"], private_keys, "evaluator private state")
    normalized_private = {
        key: _text(private[key], f"evaluator_private.{key}")
        for key in private_keys - {"pass_criteria"}
    }
    if not isinstance(private["pass_criteria"], list) or not private["pass_criteria"]:
        raise TypeError("pass_criteria must be a non-empty list")
    criteria = []
    for item in private["pass_criteria"]:
        item = _object(item, {"id", "criterion"}, "pass criterion")
        criteria.append({
            "id": _identifier(item["id"], "dimension id"),
            "criterion": _text(item["criterion"], "private criterion text"),
        })
    if len({item["id"] for item in criteria}) != len(criteria):
        raise ValueError("dimension ids must be unique")
    normalized_private["pass_criteria"] = criteria
    return {
        "id": case_id, "source": source, "version": version,
        "prompt": _text(raw["prompt"], "case prompt"),
        "evaluator_private": normalized_private,
    }


def load_sealed_case(path: Path | str) -> tuple[dict[str, Any], str]:
    candidate = Path(path)
    try:
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sealed case file is unreadable") from exc
    return validate_case_bundle(value), hashlib.sha256(raw).hexdigest()


def _canonical_manifest(platform: str) -> Path:
    if platform not in PLATFORMS:
        raise ValueError("platform must be claude or codex")
    return Path.home() / ".ghost-alice" / "install-state" / f"{platform}.json"


def load_installed_provenance(platform: str, manifest_path: Path | str | None = None) -> dict[str, str]:
    expected = _canonical_manifest(platform)
    candidate = Path(manifest_path).expanduser() if manifest_path else expected
    if candidate.is_symlink() or candidate.resolve() != expected.resolve() or not candidate.is_file():
        raise ValueError("manifest must be the canonical installer-owned state file")
    try:
        manifest = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("canonical install manifest is unreadable") from exc
    manifest = _object(manifest, MANIFEST_KEYS, "install manifest")
    if isinstance(manifest["schema_version"], bool) or manifest["schema_version"] != 1:
        raise ValueError("manifest schema_version must be 1")
    if manifest["platform"] != platform:
        raise ValueError("manifest platform mismatch")
    installed_at = _timestamp(manifest["installed_at"], "manifest installed_at")
    _text(manifest["source_root"], "manifest source_root")
    _text(manifest["source_branch"], "manifest source_branch")
    head = _text(manifest["source_head"], "manifest source_head")
    if head != "unknown" and not GIT_SHA.fullmatch(head):
        raise ValueError("manifest source_head must be a git digest or unknown")
    dirty = manifest["source_dirty_state"]
    if dirty not in {"clean", "dirty", "unknown"}:
        raise ValueError("manifest dirty state is invalid")
    if manifest["remote_freshness_state"] != "unverified":
        raise ValueError("manifest remote freshness state is invalid")
    if not isinstance(manifest["targets"], list) or not isinstance(manifest["system_env_changes"], list):
        raise TypeError("manifest targets and system_env_changes must be arrays")
    shared = next((target for target in manifest["targets"]
                   if isinstance(target, Mapping) and target.get("target_name") == "_shared"), None)
    required = {
        "target_name", "source_path", "dest_path", "install_mode", "target_tree_hash",
        "managed_markers", "installed_at",
    }
    if shared is None or not required <= set(shared):
        raise ValueError("manifest lacks _shared target provenance")
    if shared["install_mode"] not in {"copy", "copy-fallback", "symlink", "junction"}:
        raise ValueError("_shared target is not installed")
    if not isinstance(shared["managed_markers"], list) or "_shared" not in shared["managed_markers"]:
        raise ValueError("_shared target marker is missing")
    _text(shared["source_path"], "target source_path")
    _text(shared["dest_path"], "target dest_path")
    _digest(shared["target_tree_hash"], "target tree hash")
    _timestamp(shared["installed_at"], "target installed_at")
    return {
        "platform": platform, "installed_at": installed_at,
        "source_head": head, "source_dirty_state": dirty,
    }


def _command(value: Sequence[str], label: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{label} must be a sequence")
    command = [_text(item, f"{label} item") for item in value]
    if not command:
        raise ValueError(f"{label} must not be empty")
    return command


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _invoke(runtime: ProjectRuntime,
            runner: Callable[..., subprocess.CompletedProcess[str]], command: Sequence[str],
            input_text: str, cwd: Path | str | None, timeout: float,
            env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    kwargs = dict(input=input_text, capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=timeout, cwd=cwd, shell=False)
    return runtime.run(
        command,
        runner=runner,
        env=env,
        **kwargs,
    )


def _preserve_evaluator_failure(
    runtime: ProjectRuntime,
    stage: str,
    capture: subprocess.CompletedProcess[str] | BaseException | None,
) -> None:
    def captured_text(name: str) -> str:
        value = getattr(capture, name, None)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value if isinstance(value, str) else ""

    stdout, stderr = captured_text("stdout"), captured_text("stderr")
    (runtime.work_dir / "evaluator-stdout.txt").write_text(stdout, encoding="utf-8")
    (runtime.work_dir / "evaluator-stderr.txt").write_text(stderr, encoding="utf-8")
    (runtime.work_dir / "failure-stage.txt").write_text(f"{stage}\n", encoding="utf-8")
    runtime.preserve(stage)


def _state(exit_code: int | None = None, timed_out: bool = False) -> dict[str, Any]:
    return {"exit_code": exit_code, "timed_out": timed_out}


def _record(
    case: Mapping[str, Any],
    installed: Mapping[str, str],
    suite_digest: str,
) -> dict[str, Any]:
    canonical = json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "case_id": case["id"], "case_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "suite_digest": suite_digest,
        "source": case["source"], "version": case["version"],
        "installed_provenance": dict(installed), "mode": "blind-behavior",
        "verdict": "fail", "dimensions": {}, "reason": "not-evaluated",
        "process": {"subject": _state(), "evaluator": _state()},
    }


def _evaluator_result(value: Any, ids: Sequence[str]) -> dict[str, Any] | None:
    try:
        result = _object(value, {"verdict", "dimensions", "reason"}, "evaluator result")
        _text(result["reason"], "evaluator reason")
        dimensions = result["dimensions"]
        if result["verdict"] not in {"pass", "fail"}:
            raise ValueError
        if not isinstance(dimensions, Mapping) or set(dimensions) != set(ids):
            raise ValueError
        if any(status not in {"pass", "fail"} for status in dimensions.values()):
            raise ValueError
        expected = "pass" if all(status == "pass" for status in dimensions.values()) else "fail"
        if result["verdict"] != expected:
            raise ValueError
        return {"verdict": result["verdict"], "dimensions": dict(dimensions)}
    except (TypeError, ValueError):
        return None


def _outcome(record: dict[str, Any], feedback: dict[str, str] | None = None) -> dict[str, Any]:
    return {"record": record, "conduct_feedback": feedback}


def run_blind_behavior_case(
    *, case_path: Path | str, platform: str, subject_command: Sequence[str],
    evaluator_command: Sequence[str], install_manifest_path: Path | str | None = None,
    controller_cwd: Path | str | None = None, timeout_seconds: float = 120,
    repo_root: Path | str | None = None,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    case, suite_digest = load_sealed_case(case_path)
    installed = load_installed_provenance(platform, install_manifest_path)
    subject, evaluator = _command(subject_command, "subject command"), _command(evaluator_command, "evaluator command")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    private = list(_strings(case))
    if any(value in "\0".join(subject) for value in private):
        raise ValueError("subject argv contains case or evaluator-private state")
    record = _record(case, installed, suite_digest)
    runtime_root = Path(repo_root).resolve() if repo_root else CODE_OWNER_ROOT
    with project_runtime(runtime_root, "blind-behavior") as runtime:
        try:
            response = run_fresh_agent_session(
                platform=platform,
                prompt=case["prompt"],
                cli_command=subject,
                repo_root=runtime_root,
                timeout_seconds=timeout_seconds,
                run_process=run_process,
            )
        except subprocess.TimeoutExpired:
            record["reason"], record["process"]["subject"] = "subject-timeout", _state(timed_out=True)
            return _outcome(record)
        except (FreshAgentSessionError, OSError, subprocess.SubprocessError):
            record["reason"] = "subject-process-failure"
            return _outcome(record)
        record["process"]["subject"] = _state(0)
        (runtime.work_dir / "subject-response.txt").write_text(response, encoding="utf-8")
        packet = json.dumps({
            "prompt": case["prompt"], "evaluator_private": case["evaluator_private"],
            "response": response,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            process = _invoke(runtime, run_process, evaluator, packet, controller_cwd,
                              timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            record["reason"], record["process"]["evaluator"] = "evaluator-timeout", _state(timed_out=True)
            _preserve_evaluator_failure(runtime, "evaluator-timeout", exc)
            return _outcome(record)
        except (OSError, subprocess.SubprocessError) as exc:
            _preserve_evaluator_failure(runtime, "evaluator-process-failure", exc)
            record["reason"] = "evaluator-process-failure"
            return _outcome(record)
        record["process"]["evaluator"] = _state(process.returncode)
        if process.returncode != 0:
            _preserve_evaluator_failure(runtime, "evaluator-process-failure", process)
            record["reason"] = "evaluator-process-failure"
            return _outcome(record)
        try:
            raw = json.loads(process.stdout)
        except (TypeError, json.JSONDecodeError):
            raw = None
        ids = [item["id"] for item in case["evaluator_private"]["pass_criteria"]]
        result = _evaluator_result(raw, ids)
        if result is None:
            _preserve_evaluator_failure(runtime, "invalid-evaluator-output", process)
            record["reason"] = "invalid-evaluator-output"
            return _outcome(record)
        record.update(verdict=result["verdict"], dimensions=result["dimensions"],
                      reason=f"evaluator-confirmed-{result['verdict']}")
        if result["verdict"] == "pass":
            return _outcome(record)
        _preserve_evaluator_failure(runtime, "evaluator-confirmed-fail", process)
        failed = sorted(key for key, status in result["dimensions"].items() if status == "fail")
        return _outcome(record, {
            "id": f"blind-behavior:{record['case_hash'][:16]}",
            "summary": f"Blind behavior case {case['id']} failed.",
            "failure_pattern": f"Failed evaluator dimensions: {', '.join(failed)}",
            "corrective_rule": "Satisfy every blind behavior dimension before claiming success.",
            "source": "blind-behavior", "status": "open",
        })


def _safe_record(value: Any) -> dict[str, Any]:
    record = dict(_object(value, RECORD_KEYS, "blind behavior record"))
    _identifier(record["case_id"], "record case_id")
    _identifier(record["source"], "record source")
    try:
        if date.fromisoformat(record["version"]).isoformat() != record["version"]:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("record version must be an ISO date") from exc
    _digest(record["case_hash"], "record case_hash")
    _digest(record["suite_digest"], "record suite_digest")
    if record["mode"] != "blind-behavior" or record["verdict"] not in {"pass", "fail"}:
        raise ValueError("record mode or verdict is invalid")
    if record["reason"] not in REASONS:
        raise ValueError("record reason is not sanitized")
    provenance = _object(record["installed_provenance"], {
        "platform", "installed_at", "source_head", "source_dirty_state",
    }, "record provenance")
    if provenance["platform"] not in PLATFORMS:
        raise ValueError("record platform is invalid")
    _timestamp(provenance["installed_at"], "record installed_at")
    if provenance["source_head"] != "unknown" and not GIT_SHA.fullmatch(provenance["source_head"]):
        raise ValueError("record source_head is invalid")
    if provenance["source_dirty_state"] not in {"clean", "dirty", "unknown"}:
        raise ValueError("record dirty state is invalid")
    dimensions = record["dimensions"]
    if not isinstance(dimensions, Mapping) or any(
        status not in {"pass", "fail"} for status in dimensions.values()
    ):
        raise ValueError("record dimensions must use opaque ids")
    for dimension_id in dimensions:
        _identifier(dimension_id, "record dimension id")
    process = _object(record["process"], {"subject", "evaluator"}, "record process")
    for name, state in process.items():
        state = _object(state, {"exit_code", "timed_out"}, f"record {name}")
        if state["exit_code"] is not None and (
            isinstance(state["exit_code"], bool) or not isinstance(state["exit_code"], int)
        ):
            raise TypeError("record exit_code must be an integer or null")
        if not isinstance(state["timed_out"], bool):
            raise TypeError("record timed_out must be boolean")
    try:
        return json.loads(json.dumps(record, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise TypeError("record must be JSON serializable") from exc


def write_record_atomic(path: Path | str, record: Any) -> None:
    target, checked = Path(path), _safe_record(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(checked, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _command_argument(value: str) -> list[str]:
    try:
        return _command(json.loads(value), "command")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("command must be a JSON string array") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one canonical blind behavior case.")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--install-manifest", type=Path)
    parser.add_argument("--subject-command", required=True, type=_command_argument)
    parser.add_argument("--evaluator-command", required=True, type=_command_argument)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--record", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        outcome = run_blind_behavior_case(
            case_path=args.case, platform=args.platform, install_manifest_path=args.install_manifest,
            subject_command=args.subject_command, evaluator_command=args.evaluator_command,
            controller_cwd=args.case.parent, timeout_seconds=args.timeout_seconds,
        )
        if args.record:
            write_record_atomic(args.record, outcome["record"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        print("blind behavior controller rejected invalid input or state", file=sys.stderr)
        return 2
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome["record"]["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
