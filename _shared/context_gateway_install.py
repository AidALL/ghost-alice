"""Compose gateway inventory, planning and transaction effects behind the public CLI.

Installation requires Python 3.14+ with compression.zstd; removal uses Python
3.11+. Existing Core runtime and macOS service coordinates remain explicit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import context_gateway_inventory as inventory
import context_gateway_plan as planning
import context_gateway_platform as platform
from context_gateway_model import (COMPANIONS, GatewayBlocked, GatewayFailure, GatewayServiceError,
    LABEL, MODULES, Request, TABLE_BEGIN, TABLE_END, TOP_BEGIN, TOP_END, digest as _digest)
from context_gateway_plan import render_provider, remove_provider
from context_gateway_transaction import execute
from hook_definitions import PROVIDER

_write = platform.write
_service_state = platform.service_state
_start_service = platform.start
_ready = platform.ready


def _stop_service(domain):
    return platform.stop(domain, state=_service_state)


def _effects():
    return platform.Platform(write=_write, state=_service_state, stop=_stop_service, start=_start_service, ready=_ready)


def _require_gateway_runtime():
    if sys.version_info < (3, 14):
        raise RuntimeError('The optional context gateway requires Python 3.14+ with compression.zstd')
    try:
        from compression import zstd
        probe = b'Ghost-ALICE gateway request codec'
        if zstd.decompress(zstd.compress(probe)) != probe:
            raise ValueError('codec round-trip failed')
    except (ImportError, AttributeError, ValueError) as exc:
        raise RuntimeError('The optional context gateway requires a working Python 3.14 compression.zstd codec') from exc
def install(source_root, shared_dirs, codex_dir, ledger_root, state_dir, launch_agent, claude_dir=None, *,
            strict_log_root=None, dependency_bases=None, confirm=True):
    _require_gateway_runtime()
    supplied = [source_root, codex_dir, ledger_root, state_dir, launch_agent, *shared_dirs]
    supplied.extend(p for p in (claude_dir, strict_log_root) if p is not None)
    if sys.platform != 'darwin' or not shared_dirs or any(not Path(p).is_absolute() for p in supplied):
        raise GatewayBlocked('macOS and explicit absolute installation coordinates are required')
    request = Request('install', Path.home(), sys.platform, Path(source_root), tuple(map(Path, shared_dirs)),
        Path(codex_dir), Path(ledger_root), Path(state_dir), Path(launch_agent),
        Path(claude_dir) if claude_dir is not None else None,
        Path(strict_log_root) if strict_log_root is not None else None,
        dependency_bases={} if dependency_bases is None else dependency_bases)
    io = _effects()
    with platform.operation_lock(request.home):
        observed = inventory.installation(request, io)
        validated = planning.validate_install(request, observed)
        prior = validated[1]
        if prior is None:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            scope = uuid.uuid4().hex
        else:
            port, scope = prior['port'], prior['scope']
        now = datetime.now(timezone.utc)
        plan = planning.install_plan(request, observed, validated, port=port, scope=scope,
            interpreter=sys.executable, stamp=now.strftime('%Y%m%dT%H%M%S%fZ'), observed_at=now.isoformat(),
            domain=platform.service_domain(request.platform))
        result = execute(plan, io, confirm=confirm)
        return result


def detach_for_uninstall(*, user_home, remove_codex, removing_paths, confirm=False, codex_dir=None):
    request = Request('detach', Path(user_home), sys.platform,
        codex=Path(codex_dir) if codex_dir is not None else Path(user_home) / '.codex',
        removing=tuple(map(Path, removing_paths)), remove_codex=remove_codex)
    if any(not p.is_absolute() for p in (request.home, request.codex, *request.removing)):
        raise GatewayBlocked('Explicit absolute removal coordinates are required')
    io = _effects()
    with platform.operation_lock(request.home):
        observed = inventory.removal(request, io)
        decision = planning.removal_plan(request, observed, domain=platform.service_domain(request.platform))
        return dict(decision.result) if decision.plan is None else execute(decision.plan, io, confirm=confirm)


def coordinate_payload_replacement(participant):
    """General installer file effects participate in the gateway phase machine."""
    request = Request('replace', Path.home(), sys.platform, codex=Path.home() / '.codex',
        removing=tuple(participant.destinations))
    io = _effects()
    with platform.operation_lock(request.home):
        observed, candidates = inventory.replacement(request, io, participant.mappings)
        decision = planning.replacement_plan(request, observed, candidates, domain=platform.service_domain(request.platform),
            updated_at=datetime.now(timezone.utc).isoformat())
        if decision.plan is None:
            participant.run_unmanaged()
        else:
            execute(decision.plan, io, participant=participant)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['detach']:
        parser = argparse.ArgumentParser(description='Detach gateway dependencies before selected removal')
        parser.add_argument('--root', action='append', required=True, type=Path)
        parser.add_argument('--user-home', type=Path, default=Path.home())
        parser.add_argument('--codex-dir', type=Path)
        parser.add_argument('--confirm', action='store_true')
        args = parser.parse_args(argv[1:])
        try:
            result = detach_for_uninstall(user_home=args.user_home, remove_codex=False,
                removing_paths=args.root, confirm=args.confirm, codex_dir=args.codex_dir)
        except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
            print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)
            return exc.exit_code
        except (ValueError, OSError, RuntimeError) as exc:
            print(json.dumps(dict(status='blocked', error_code='GATEWAY_PRECONDITION', phase='preflight', detail=str(exc))), file=sys.stderr)
            return 2
        print(json.dumps(result))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--shared-dir', required=True, action='append', type=Path)
    parser.add_argument('--codex-dir', required=True, type=Path)
    parser.add_argument('--claude-dir', type=Path)
    parser.add_argument('--strict-log-root', type=Path)
    parser.add_argument('--ledger-root', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--launch-agent', required=True, type=Path)
    parser.add_argument('--dependency-bases-json', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = install(args.source_root, args.shared_dir, args.codex_dir, args.ledger_root,
            args.state_dir, args.launch_agent, claude_dir=args.claude_dir, strict_log_root=args.strict_log_root,
            dependency_bases=json.loads(args.dependency_bases_json.read_text()) if args.dependency_bases_json else None,
            confirm=not args.dry_run)
    except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
        print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)
        return exc.exit_code
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status='blocked', error_code='GATEWAY_PRECONDITION', phase='preflight', detail=str(exc))), file=sys.stderr)
        return 2
    if result['status'] == 'dry-run':
        print(json.dumps(result))
    else:
        print(json.dumps(dict(status=result['status'], service=result['service'],
            listener_ready=result['listener_ready'], installed_files=len(result['files']),
            existing_native_process_adoption=result['existing_native_process_adoption'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
