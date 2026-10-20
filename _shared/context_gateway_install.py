"""Compose gateway inventory, planning and transaction effects behind the public CLI.

`install` registers or refreshes the gateway service: it reads the runtime target from the install
record, writes the launch agent, `state.json` and the Codex provider blocks, starts the service, confirms
readiness and writes the record's `service` section. It copies no module: the service runs from the
runtime the installer publishes. `detach` unregisters the service the record names. Registration
requires Python 3.14+ with compression.zstd; detachment runs on Python 3.11+. Both take the installer's
lock when no installer holds it.
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
import installed_payload
# The tests read the provider rendering, the markers and the native modules through this module.
from context_gateway_model import (GatewayBlocked, GatewayFailure, GatewayServiceError, LABEL, Request, TABLE_BEGIN,  # noqa: F401
    TABLE_END, TOP_BEGIN, TOP_END)
from context_gateway_plan import render_provider, remove_provider  # noqa: F401
from context_gateway_transaction import execute
from hook_definitions import PROVIDER  # noqa: F401

_write = platform.write
_service_state = platform.service_state
_start_service = platform.start
_ready = platform.ready


def _stop_service(domain):
    return platform.stop(domain, state=_service_state)


def _record_service(section):
    platform.record_service(Path.home(), section)


_record = _record_service


def _effects():
    return platform.Platform(write=_write, state=_service_state, stop=_stop_service, start=_start_service, ready=_ready,
                             record=lambda section: _record(section))


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


def _read_record(home):
    try:
        return installed_payload.read(Path(home) / '.ghost-alice') or {}
    except installed_payload.RecordError as exc:
        raise GatewayBlocked('The install record cannot be read: ' + str(exc)) from exc


def _runtime_root(record):
    """The folder of the record's runtime `_shared` target: where the service's code is."""
    for key, entry in record.get('targets', {}).items():
        if isinstance(entry, dict) and entry.get('kind') == 'runtime' and entry.get('name') == '_shared':
            return Path(key).parent
    raise GatewayBlocked('Install Core first: the install record has no runtime target for the gateway to run from')


def install(codex_dir, ledger_root, state_dir, launch_agent, claude_dir=None, *, strict_log_root=None, confirm=True):
    _require_gateway_runtime()
    supplied = [codex_dir, ledger_root, state_dir, launch_agent]
    supplied.extend(p for p in (claude_dir, strict_log_root) if p is not None)
    if sys.platform != 'darwin' or any(not Path(p).is_absolute() for p in supplied):
        raise GatewayBlocked('macOS and explicit absolute installation coordinates are required')
    home = Path.home()
    io = _effects()
    with platform.operation_lock(home):
        record = _read_record(home)
        service = record.get('service') if isinstance(record.get('service'), dict) else None
        request = Request('install', home, sys.platform, _runtime_root(record), Path(codex_dir), Path(ledger_root),
            Path(state_dir), Path(launch_agent), Path(claude_dir) if claude_dir is not None else None,
            Path(strict_log_root) if strict_log_root is not None else None)
        observed = inventory.installation(request, io)
        prior = planning.validate_install(request, observed, service)
        if prior is None:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            scope = uuid.uuid4().hex
        else:
            port, scope = prior['port'], prior['scope']
        now = datetime.now(timezone.utc)
        plan = planning.install_plan(request, observed, prior, service, port=port, scope=scope,
            interpreter=sys.executable, stamp=now.strftime('%Y%m%dT%H%M%S%fZ'), now=now.isoformat(),
            domain=platform.service_domain(request.platform))
        return execute(plan, io, confirm=confirm)


def detach(*, confirm=False, home=None, io=None, host_platform=None):
    """Unregister the service the install record names; `missing` when it names none."""
    home = Path.home() if home is None else Path(home)
    io = _effects() if io is None else io
    host_platform = sys.platform if host_platform is None else host_platform
    with platform.operation_lock(home):
        record = _read_record(home)
        service = record.get('service')
        if not isinstance(service, dict):
            return dict(kind='context-gateway', action='missing', reason='gateway-not-registered')
        request = Request('detach', home, host_platform)
        observed = inventory.registration(service, io)
        plan = planning.detach_plan(request, observed, service, domain=platform.service_domain(request.platform))
        return execute(plan, io, confirm=confirm)


def _print_failure(exc):
    print(json.dumps(dict(status=exc.outcome, error_code=exc.code, phase=exc.phase, detail=str(exc))), file=sys.stderr)


def _print_blocked(exc):
    print(json.dumps(dict(status='blocked', error_code='GATEWAY_PRECONDITION', phase='preflight', detail=str(exc))),
          file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['detach']:
        parser = argparse.ArgumentParser(description='Unregister the gateway service the install record names')
        parser.add_argument('--user-home', type=Path)
        parser.add_argument('--confirm', action='store_true')
        args = parser.parse_args(argv[1:])
        kwargs = dict(confirm=args.confirm)
        if args.user_home is not None:
            kwargs['home'] = args.user_home
        try:
            result = detach(**kwargs)
        except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
            _print_failure(exc)
            return exc.exit_code
        except (ValueError, OSError, RuntimeError) as exc:
            _print_blocked(exc)
            return 2
        print(json.dumps(result))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-dir', required=True, type=Path)
    parser.add_argument('--claude-dir', type=Path)
    parser.add_argument('--strict-log-root', type=Path)
    parser.add_argument('--ledger-root', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--launch-agent', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = install(args.codex_dir, args.ledger_root, args.state_dir, args.launch_agent,
            claude_dir=args.claude_dir, strict_log_root=args.strict_log_root, confirm=not args.dry_run)
    except (GatewayBlocked, GatewayServiceError, GatewayFailure) as exc:
        _print_failure(exc)
        return exc.exit_code
    except (ValueError, OSError, RuntimeError) as exc:
        _print_blocked(exc)
        return 2
    if result['status'] == 'dry-run':
        print(json.dumps(result))
    else:
        print(json.dumps(dict(status=result['status'], service=result['service'], listener_ready=result['listener_ready'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
