"""Pure installer ownership policy and ordered plans; no filesystem I/O."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import re
import tomllib

from context_gateway_model import (COMPANIONS, Decision, GatewayBlocked, LABEL, MODULES, Mutation,
    Plan, Preparation, PROVIDER, RETIRED_MODULES, TABLE_BEGIN, TABLE_END, TOP_BEGIN, TOP_END, digest, encoded)


def regular(item, *, codex=False):
    if item.kind == 'symlink':
        name = 'Codex config.toml' if codex else 'gateway artifact'
        raise GatewayBlocked('Symlinked ' + name + ' must be preserved: ' + str(item.path))
    if item.kind not in ('file', 'missing'):
        raise GatewayBlocked('A non-file gateway artifact must be preserved: ' + str(item.path))


def document(item, *, plist=False):
    regular(item)
    try:
        value = plistlib.loads(item.raw) if plist else json.loads(item.raw)
    except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
        raise GatewayBlocked('A required gateway document is missing or malformed: ' + str(item.path)) from exc
    if not isinstance(value, dict):
        raise GatewayBlocked('A required gateway document is not an object: ' + str(item.path))
    return value


def affected(path, roots, inventory, *, overlap=False):
    path = Path(path)
    for root in roots:
        root = Path(root)
        for candidate, parent in ((path, root), (inventory.resolved[path], inventory.resolved[root])):
            if candidate == parent or parent in candidate.parents or (overlap and candidate in parent.parents):
                return True
    return False


def _absolute(value):
    return isinstance(value, str) and Path(value).is_absolute()


def authenticated(inventory, registration):
    binding = document(inventory.file(registration))
    if (set(binding) != {'schema_version', 'installation_path', 'installation_digest'}
            or binding['schema_version'] != 'context-gateway-binding.v1'
            or not _absolute(binding['installation_path'])):
        raise GatewayBlocked('Gateway discovery binding is invalid')
    path = Path(binding['installation_path'])
    receipt_artifact = inventory.file(path)
    regular(receipt_artifact)
    if receipt_artifact.raw is None or digest(receipt_artifact.raw) != binding['installation_digest']:
        raise GatewayBlocked('Gateway installation receipt changed; retain Core for review')
    receipt = document(receipt_artifact)
    required = ('runtime_shared_dir', 'ledger_root', 'launch_agent', 'config_path', 'state_path')
    if (receipt.get('schema_version') != 'context-gateway-installation.v1'
            or receipt.get('status') != 'installed' or receipt.get('service') != LABEL
            or receipt.get('provider') != PROVIDER or any(not _absolute(receipt.get(k)) for k in required)
            or Path(receipt['state_path']).parent != path.parent):
        raise GatewayBlocked('Gateway installation receipt is unavailable or incompatible')
    return path, receipt


def valid_state(value):
    if (value.get('schema_version') != 'context-gateway.v1' or type(value.get('port')) is not int
            or not 0 < value['port'] < 65536 or not isinstance(value.get('scope'), str) or not value['scope']
            or any(not _absolute(value.get(k)) for k in ('ledger_root', 'codex_dir', 'usage_log'))
            or any(k in value and not _absolute(value[k]) for k in ('claude_dir', 'strict_log_root'))):
        raise GatewayBlocked('The prior owned listener/state binding is unavailable; preserve it for review')
    return value


def component_baselines(receipt):
    files = receipt.get('files')
    if (not isinstance(files, dict) or any(not _absolute(k) or not isinstance(v, str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', v) for k, v in files.items())):
        raise GatewayBlocked('Authenticated component baselines are unavailable; preserve the installation for review')
    return files


def validate_install(request, inventory):
    state_dir, config = request.state_dir, request.codex / 'config.toml'
    regular(inventory.file(config), codex=True)
    for path in (request.agent, request.registration, state_dir / 'installation.json', state_dir / 'state.json',
                 state_dir / 'usage.jsonl', state_dir / 'service.log', state_dir / 'installed-files.json'):
        regular(inventory.file(path))
    if request.ledger / 'ghost-state.sqlite3' not in inventory.prerequisites:
        raise GatewayBlocked('The selected existing Core ledger is required')
    if affected(state_dir, (*request.shared, request.registration.parent / 'runtime'), inventory):
        raise GatewayBlocked('Gateway state/logs/recovery material must be outside Core payload removal roots')
    receipt, prior = None, None
    if inventory.file(request.registration).exists:
        binding = document(inventory.file(request.registration))
        if binding.get('installation_path') != str(state_dir / 'installation.json'):
            raise GatewayBlocked('Detach or review the existing registered gateway before changing its installation coordinates')
        _, receipt = authenticated(inventory, request.registration)
        agent = inventory.file(request.agent)
        if receipt.get('launch_agent') != str(request.agent) or agent.raw is None or receipt.get('launch_agent_digest') != digest(agent.raw):
            raise GatewayBlocked('Registered gateway launch agent lacks its unchanged prior installation binding; preserve it for review')
    if inventory.file(request.agent).exists and receipt is None:
        raise GatewayBlocked('An unowned launch agent must be preserved; retained state alone does not establish ownership')
    if receipt is None and inventory.state_entries:
        raise GatewayBlocked('Existing gateway state lacks an authenticated prior installation binding; preserve it for review')
    original = inventory.file(config).raw or b''
    state_artifact = inventory.file(state_dir / 'state.json')
    markers = any(x in original.decode() for x in (TOP_BEGIN, TOP_END, TABLE_BEGIN, TABLE_END))
    if receipt is not None or state_artifact.exists or markers:
        if (receipt is None or receipt.get('config_path') != str(config)
                or receipt.get('state_path') != str(state_artifact.path) or state_artifact.raw is None
                or receipt.get('state_digest') != digest(state_artifact.raw)):
            raise GatewayBlocked('Gateway state/provider lacks its unchanged prior installation/state binding; preserve it for review')
        prior = valid_state(document(state_artifact))
        if prior['ledger_root'] != receipt['ledger_root'] or Path(prior['codex_dir']) / 'config.toml' != config:
            raise GatewayBlocked('Gateway state and prior installation coordinates do not agree')
        if markers:
            _owned_provider_removal(original.decode(), prior)
    previous = component_baselines(receipt) if receipt is not None else {}
    permitted = {str(s / 'native_transcript.py') for s in request.shared} | {
        str(s.parent / relative) for s in request.shared for relative in COMPANIONS}
    bases = request.dependency_bases
    if (not isinstance(bases, dict) or set(bases) - permitted or any(not isinstance(v, str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in bases.values())):
        raise GatewayBlocked('Only exact retained baselines for newly owned Core dependencies are permitted')
    payload = {}
    for shared in request.shared:
        if shared / 'completion_assessment.py' not in inventory.prerequisites:
            raise GatewayBlocked('The affected target must be an installed Core shared directory')
        candidates = [(shared / n, request.source / '_shared' / n) for n in MODULES] + [
            (shared.parent / r, request.source / r) for r in COMPANIONS]
        for target, source in candidates:
            installed, supplied = inventory.file(target), inventory.file(source)
            regular(installed)
            if supplied.kind == 'missing':
                raise FileNotFoundError('A required source dependency is missing: ' + str(source))
            if supplied.kind != 'file':
                raise GatewayBlocked('A required source dependency is missing: ' + str(source))
            if installed.exists and installed.raw != supplied.raw:
                baseline = previous.get(str(target), bases.get(str(target)))
                if baseline is None or digest(installed.raw) != baseline:
                    raise GatewayBlocked('A locally changed component must be preserved: ' + str(target))
            if target in payload and payload[target][1] != supplied.raw:
                raise GatewayBlocked('Conflicting source payloads target the same installed file: ' + str(target))
            payload[target] = (installed, supplied.raw)
    # Rendering also rejects unowned provider selection when no managed block exists.
    render_provider(original.decode(), prior['port'] if prior else 1, prior['scope'] if prior else 'preflight')
    return receipt, prior, tuple(payload.values())


def install_plan(request, inventory, validated, *, port, scope, interpreter, stamp, observed_at, domain):
    receipt, prior, payload = validated
    config, state_dir = request.codex / 'config.toml', request.state_dir
    state_path, receipt_path = state_dir / 'state.json', state_dir / 'installation.json'
    if prior:
        port, scope = prior['port'], prior['scope']
    state = dict(schema_version='context-gateway.v1', port=port, scope=scope,
        ledger_root=str(request.ledger), codex_dir=str(request.codex), usage_log=str(state_dir / 'usage.jsonl'))
    if request.claude is not None:
        state['claude_dir'] = str(request.claude)
    if request.strict_log is not None:
        state['strict_log_root'] = str(request.strict_log)
    service_log = state_dir / 'service.log'
    plist = dict(Label=LABEL, ProgramArguments=[interpreter, '-B', str(request.shared[-1] / 'context_gateway.py'),
        '--config', str(state_path)], RunAtLoad=True, StandardOutPath=str(service_log),
        StandardErrorPath=str(service_log), WorkingDirectory=str(state_dir))
    state_raw, agent_raw = encoded(state), plistlib.dumps(plist)
    rendered = render_provider((inventory.file(config).raw or b'').decode(), port, scope).encode()
    backup = state_dir / 'backups' / (stamp + '-config.toml')
    archive = state_dir / 'backups' / (stamp + '-artifacts')
    staged = tuple(Mutation(a, raw, 0o644) for a, raw in payload) + (
        Mutation(inventory.file(state_path), state_raw), Mutation(inventory.file(request.agent), agent_raw))
    result = dict(schema_version='context-gateway-installation.v1', status='installed', observed_at=observed_at,
        source_root=str(request.source), files={str(a.path): digest(raw) for a, raw in payload},
        provider=PROVIDER, service=LABEL, launch_agent=str(request.agent), config_path=str(config),
        runtime_shared_dir=str(request.shared[-1]), shared_dirs=[str(p) for p in request.shared],
        ledger_root=str(request.ledger), state_path=str(state_path), state_digest=digest(state_raw),
        launch_agent_digest=digest(agent_raw), config_backup=str(backup), installed_config_digest=digest(rendered),
        listener_ready=True, existing_native_process_adoption='unverified')
    result_raw = encoded(result)
    binding = encoded(dict(schema_version='context-gateway-binding.v1', installation_path=str(receipt_path),
                           installation_digest=digest(result_raw)))
    committed = (Mutation(inventory.file(config), rendered, inventory.file(config).mode or 0o600),
                 Mutation(inventory.file(receipt_path), result_raw), Mutation(inventory.file(request.registration), binding))
    legacy = inventory.file(state_dir / 'installed-files.json')
    retired = (Mutation(legacy, None),) if legacy.exists else ()
    guards = tuple(dict((m.before.path, m.before) for m in (*staged, *committed, *retired)).values())
    preparation = [Preparation(backup, inventory.file(config).raw or b'')]
    manifest = []
    for item in guards:
        name = hashlib.sha256(str(item.path).encode()).hexdigest()
        if item.raw is not None:
            preparation.append(Preparation(archive / name, item.raw))
        manifest.append(dict(path=str(item.path), backup=name if item.raw is not None else None, mode=item.mode))
    preparation.append(Preparation(archive / 'manifest.json', encoded(manifest)))
    for path in (state_dir / 'usage.jsonl', service_log):
        if not inventory.file(path).exists:
            preparation.append(Preparation(path, b'', True))
    return Plan('install', domain, state_dir, request.agent, guards, staged, committed, retired,
                tuple(preparation), prior, state, result, archive)


def _dependencies(inventory, agent, definition, state_path, state):
    argv = definition.get('ProgramArguments')
    if (definition.get('Label') != LABEL or not isinstance(argv, list) or len(argv) != 5
            or any(not isinstance(a, str) for a in argv) or argv[1] != '-B'
            or argv[3] != '--config' or argv[4] != str(state_path)
            or not _absolute(argv[0]) or not _absolute(argv[2]) or Path(argv[2]).name != 'context_gateway.py'):
        raise GatewayBlocked('Gateway service arguments do not establish its dependencies')
    valid_state(state)
    runtime = Path(argv[2]).parent
    dependencies = [agent, Path(argv[0]), runtime, state_path]
    dependencies.extend(runtime.parent / r for r in COMPANIONS)
    dependencies.extend(Path(state[k]) for k in ('ledger_root', 'codex_dir', 'usage_log', 'claude_dir', 'strict_log_root') if k in state)
    for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath'):
        if key in definition:
            if not _absolute(definition[key]):
                raise GatewayBlocked('Gateway service paths do not establish its dependencies')
            dependencies.append(Path(definition[key]))
    return tuple(dependencies)


def removal_plan(request, inventory, *, domain):
    missing = dict(kind='context-gateway', action='missing', reason='gateway-not-selected')
    registration = inventory.file(request.registration)
    if not registration.exists:
        if inventory.service is None:
            raise GatewayBlocked('Gateway discovery binding is missing; retain Core and review the existing provider/service installation')
        observed = inventory.service
        if observed.presence == 'absent':
            return Decision(result=missing)
        if observed.presence != 'present' or observed.agent is None:
            raise GatewayBlocked('Gateway native discovery is inconclusive; retain Core for review',
                code='GATEWAY_SERVICE_UNCONFIRMED', exit_code=3)
        definition = document(inventory.file(observed.agent), plist=True)
        argv = definition.get('ProgramArguments')
        if not isinstance(argv, list) or len(argv) != 5 or not _absolute(argv[4]):
            raise GatewayBlocked('Unregistered service arguments are unavailable; retain Core')
        state_path = Path(argv[4])
        state = document(inventory.file(state_path))
        allowed_state = {'schema_version', 'port', 'scope', 'ledger_root', 'codex_dir', 'usage_log', 'claude_dir', 'strict_log_root'}
        allowed_agent = {'Label', 'ProgramArguments', 'RunAtLoad', 'StandardOutPath', 'StandardErrorPath', 'WorkingDirectory'}
        if set(state) - allowed_state or set(definition) - allowed_agent:
            raise GatewayBlocked('Unregistered service has unknown dependency settings; retain Core')
        dependencies = _dependencies(inventory, observed.agent, definition, state_path, state)
        if any(affected(p, request.removing, inventory, overlap=True) for p in dependencies):
            raise GatewayBlocked('An active gateway uses the selected Core payload but lacks its discovery binding; retain Core for review')
        return Decision(result=dict(kind='context-gateway', action='unchanged', reason='independent-unregistered-gateway-runtime'))
    receipt_path, receipt = authenticated(inventory, request.registration)
    if affected(receipt_path.parent, request.removing, inventory) or affected(request.registration, request.removing, inventory):
        raise GatewayBlocked('Gateway state/logs/recovery material is inside the selected Core removal root; retain Core for review')
    config, agent, state_path = (Path(receipt[k]) for k in ('config_path', 'launch_agent', 'state_path'))
    regular(inventory.file(config), codex=True)
    state_item, agent_item = inventory.file(state_path), inventory.file(agent)
    regular(state_item); regular(agent_item)
    if (state_item.raw is None or agent_item.raw is None or digest(state_item.raw) != receipt.get('state_digest')
            or digest(agent_item.raw) != receipt.get('launch_agent_digest')):
        raise GatewayBlocked('Gateway service or state changed; preserve owned coordinates for review')
    state, definition = document(state_item), document(agent_item, plist=True)
    dependencies = _dependencies(inventory, agent, definition, state_path, state)
    if (definition['ProgramArguments'][2] != str(Path(receipt['runtime_shared_dir']) / 'context_gateway.py')
            or state['ledger_root'] != receipt['ledger_root'] or Path(state['codex_dir']) / 'config.toml' != config):
        raise GatewayBlocked('Gateway service, configuration and Core dependency binding do not agree')
    if not request.remove_codex and not any(affected(p, request.removing, inventory, overlap=True) for p in dependencies):
        return Decision(result=dict(kind='context-gateway', action='unchanged', reason='independent-gateway-runtime'))
    if request.platform != 'darwin':
        raise GatewayBlocked('Gateway service detachment requires its macOS launchctl owner')
    original = inventory.file(config)
    cleaned = _owned_provider_removal(original.raw.decode(), state).encode() if original.raw is not None else None
    item = dict(kind='context-gateway', action='would-detach', reason='owned-context-gateway',
                path=str(receipt_path), config_path=str(config), launch_agent=str(agent))
    guards = [inventory.file(p) for p in (config, agent, request.registration, receipt_path, state_path)]
    for path, expected in component_baselines(receipt).items():
        artifact = inventory.file(Path(path))
        regular(artifact)
        if artifact.raw is None or digest(artifact.raw) != expected:
            raise GatewayBlocked('A gateway runtime component changed; retain Core for review: ' + path)
        guards.append(artifact)
    staged = (Mutation(original, cleaned, original.mode or 0o600),) if original.exists else ()
    committed = (Mutation(agent_item, None), Mutation(registration, None))
    return Decision(plan=Plan('detach', domain, receipt_path.parent, agent, tuple(guards), staged,
        committed, (), (), state, None, item))


def replacement_plan(request, inventory, candidates, *, domain, updated_at):
    decision = removal_plan(request, inventory, domain=domain)
    if decision.plan is None:
        return decision
    prior = decision.plan
    receipt_path, receipt = authenticated(inventory, request.registration)
    # A generic Core update may replace payloads, not gateway state/config or
    # registration. Those changes require the explicit installation operation.
    protected = [request.registration, receipt_path, prior.agent, prior.state_dir,
                 Path(receipt['config_path'])]
    if any(affected(p, request.removing, inventory, overlap=True) for p in protected):
        raise GatewayBlocked('General payload replacement overlaps gateway control/state; use the explicit gateway installation operation')
    updated = dict(component_baselines(receipt))
    guards = {a.path: a for a in prior.guards}
    roots = receipt.get('shared_dirs', [receipt['runtime_shared_dir']])
    retired = {str(Path(root) / name) for root in (roots if isinstance(roots, list) else ())
               if _absolute(root) for name in RETIRED_MODULES}
    for path, candidate in candidates.items():
        regular(candidate)
        if candidate.kind != 'file':
            if str(path) not in retired or str(path) not in updated:
                raise GatewayBlocked('Candidate payload removes a required gateway dependency: ' + str(path))
            # removal_plan already required the owned copy to match its baseline;
            # publishing the payload moves it aside, so the receipt stops owning it.
            del updated[str(path)]
            continue
        current = inventory.file(path)
        regular(current)
        if str(path) not in updated and current.exists and current.raw != candidate.raw:
            raise GatewayBlocked('Candidate payload would adopt a differing unowned gateway component: ' + str(path))
        updated[str(path)] = digest(candidate.raw)
        guards[path] = current
    if not candidates:
        raise GatewayBlocked('Affected replacement lacks a complete staged gateway dependency inventory')
    receipt = dict(receipt, files=updated, updated_at=updated_at)
    receipt_raw = encoded(receipt)
    binding = encoded(dict(schema_version='context-gateway-binding.v1', installation_path=str(receipt_path),
        installation_digest=digest(receipt_raw)))
    committed = (Mutation(inventory.file(receipt_path), receipt_raw), Mutation(inventory.file(request.registration), binding))
    return Decision(plan=Plan('replace', domain, prior.state_dir, prior.agent, tuple(guards.values()),
        (), committed, (), (), prior.prior_state, prior.prior_state,
        dict(status='updated', service=LABEL, listener_ready=True)))


def remove_provider(text):
    text = re.sub(re.escape(TOP_BEGIN) + r'\n.*?\n' + re.escape(TOP_END) + r'\n', '', text, flags=re.S)
    return re.sub(r'\n' + re.escape(TABLE_BEGIN) + r'\n.*?\n' + re.escape(TABLE_END) + r'\n', '', text, flags=re.S)


def render_provider(text, port, scope):
    if type(port) is not int or not 0 < port < 65536 or not isinstance(scope, str) or not scope:
        raise ValueError('An explicit listener port and private local scope are required')
    cleaned = remove_provider(text)
    original = tomllib.loads(cleaned)
    if original.get('model_provider') is not None or PROVIDER in original.get('model_providers', {}):
        raise ValueError('An unowned provider must be preserved')
    result = (f'{TOP_BEGIN}\nmodel_provider = "{PROVIDER}"\n{TOP_END}\n' + cleaned +
        f'\n{TABLE_BEGIN}\n[model_providers.{PROVIDER}]\nname = "Ghost-ALICE context gateway"\n' +
        f'base_url = "http://127.0.0.1:{port}/codex"\nwire_api = "responses"\n' +
        'requires_openai_auth = true\nsupports_websockets = false\n' +
        f'http_headers = {{ "X-Ghost-Context-Scope" = {json.dumps(scope)} }}\n{TABLE_END}\n')
    tomllib.loads(result)
    return result


def _owned_provider_removal(text, state):
    if not any(marker in text for marker in (TOP_BEGIN, TOP_END, TABLE_BEGIN, TABLE_END)):
        current = tomllib.loads(text)
        if current.get('model_provider') == PROVIDER or PROVIDER in current.get('model_providers', {}):
            raise ValueError('An unowned context provider reference prevents safe detachment')
        return text
    expected = render_provider('', state['port'], state['scope'])
    for begin, end in ((TOP_BEGIN, TOP_END), (TABLE_BEGIN, TABLE_END)):
        pattern = re.escape(begin) + r'\n.*?\n' + re.escape(end) + r'\n'
        actual = re.findall(pattern, text, flags=re.S)
        wanted = re.findall(pattern, expected, flags=re.S)
        if actual != wanted or text.count(begin) != 1 or text.count(end) != 1:
            raise ValueError('Managed context provider blocks changed; preserve the configuration for review')
    cleaned = remove_provider(text)
    remainder = tomllib.loads(cleaned)
    if remainder.get('model_provider') == PROVIDER or PROVIDER in remainder.get('model_providers', {}):
        raise ValueError('An additional context provider reference prevents safe detachment')
    return cleaned
