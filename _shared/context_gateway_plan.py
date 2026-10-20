"""Pure installer ownership policy and ordered plans; no filesystem I/O.

The install record's `service` section is the one record of the registered gateway: its code root
(`runtime_root`, the folder of the runtime `_shared` the installer publishes), its program, and the
three service files with their fingerprints. A registration refreshes those files when they are as the
record says and stops before any change when they are not; a detach removes them the same way.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import re
import tomllib

from context_gateway_model import (GatewayBlocked, LABEL, Mutation, Plan, Preparation, TABLE_BEGIN, TABLE_END,
    TOP_BEGIN, TOP_END, digest, encoded)
from hook_definitions import PROVIDER


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


def _absolute(value):
    return isinstance(value, str) and Path(value).is_absolute()


def valid_state(value):
    if (value.get('schema_version') != 'context-gateway.v1' or type(value.get('port')) is not int
            or not 0 < value['port'] < 65536 or not isinstance(value.get('scope'), str) or not value['scope']
            or any(not _absolute(value.get(k)) for k in ('ledger_root', 'codex_dir', 'usage_log'))
            or any(k in value and not _absolute(value[k]) for k in ('claude_dir', 'strict_log_root'))):
        raise GatewayBlocked('The prior owned listener/state binding is unavailable; preserve it for review')
    return value


def provider_blocks(text):
    """The two managed provider blocks of a Codex `config.toml` as they stand in `text`, markers included;
    empty when a block is missing or lacks its closing marker. The record's `config_block_digest` is the
    digest of this text."""
    found = []
    for begin, end in ((TOP_BEGIN, TOP_END), (TABLE_BEGIN, TABLE_END)):
        found.extend(re.findall(re.escape(begin) + r'\n.*?\n' + re.escape(end) + r'\n', text, flags=re.S))
    return ''.join(found)


def _block_digest(text):
    return digest(provider_blocks(text).encode())


def _markers(text):
    return any(marker in text for marker in (TOP_BEGIN, TOP_END, TABLE_BEGIN, TABLE_END))


def validate_install(request, inventory, service):
    """The registered listener's state for a refresh, or None for a first registration.

    `service` is the install record's section or None. The service files must be regular, the ledger
    present and the state directory outside the Core runtime. Without a section, no launch agent, state
    entry or provider block may exist. With one, the three service files must be as the section records
    them: a file changed outside the installer stops the command before any change.
    """
    config, agent, state_item = inventory.file(request.config), inventory.file(request.agent), inventory.file(request.state_path)
    regular(config, codex=True)
    for path in (request.agent, request.state_path, request.state_dir / 'usage.jsonl', request.state_dir / 'service.log',
                 request.state_dir / 'installed-files.json'):
        regular(inventory.file(path))
    if request.ledger / 'ghost-state.sqlite3' not in inventory.prerequisites:
        raise GatewayBlocked('The selected existing Core ledger is required')
    if request.state_dir == request.runtime_root or request.runtime_root in request.state_dir.parents:
        raise GatewayBlocked('Gateway state/logs/recovery material must be outside the Core runtime: ' + str(request.runtime_root))
    if inventory.file(request.program).kind != 'file':
        raise GatewayBlocked('Install Core first: the runtime program is missing: ' + str(request.program))
    original = (config.raw or b'').decode()
    if service is None:
        if agent.exists:
            raise GatewayBlocked('An unowned launch agent must be preserved; the install record has no service entry for it')
        if inventory.state_entries:
            raise GatewayBlocked('Existing gateway state lacks its install record entry; preserve it for review')
        if _markers(original):
            raise GatewayBlocked('Gateway provider blocks without an install record entry; preserve the configuration for review')
        # Rendering also rejects an unowned provider selection.
        render_provider(original, 1, 'preflight')
        return None
    coordinates = {'launch_agent': request.agent, 'state_path': request.state_path, 'config_path': request.config}
    if any(Path(service[key]) != path for key, path in coordinates.items()):
        raise GatewayBlocked('Detach or review the existing registered gateway before changing its installation coordinates')
    if (agent.raw is None or digest(agent.raw) != service['launch_agent_digest']
            or state_item.raw is None or digest(state_item.raw) != service['state_digest']
            or (service.get('config_block_digest') is not None and _block_digest(original) != service['config_block_digest'])):
        raise GatewayBlocked('Gateway service files changed outside the installer; review them, then run the gateway command again')
    prior = valid_state(document(state_item))
    if _markers(original):
        _owned_provider_removal(original, prior)
    render_provider(original, prior['port'], prior['scope'])
    return prior


def install_plan(request, inventory, prior, service, *, port, scope, interpreter, stamp, now, domain):
    """The three service files for the listener (`prior`'s port and scope when it is registered), the
    service start and the record's `service` section; the runtime is not written."""
    config, state_dir, state_path = request.config, request.state_dir, request.state_path
    if prior:
        port, scope = prior['port'], prior['scope']
    state = dict(schema_version='context-gateway.v1', port=port, scope=scope,
        ledger_root=str(request.ledger), codex_dir=str(request.codex), usage_log=str(state_dir / 'usage.jsonl'))
    if request.claude is not None:
        state['claude_dir'] = str(request.claude)
    if request.strict_log is not None:
        state['strict_log_root'] = str(request.strict_log)
    service_log = state_dir / 'service.log'
    plist = dict(Label=LABEL, ProgramArguments=[interpreter, '-B', str(request.program), '--config', str(state_path)],
        RunAtLoad=True, StandardOutPath=str(service_log), StandardErrorPath=str(service_log),
        WorkingDirectory=str(state_dir))
    state_raw, agent_raw = encoded(state), plistlib.dumps(plist)
    rendered = render_provider((inventory.file(config).raw or b'').decode(), port, scope).encode()
    backup = state_dir / 'backups' / (stamp + '-config.toml')
    archive = state_dir / 'backups' / (stamp + '-artifacts')
    staged = (Mutation(inventory.file(state_path), state_raw), Mutation(inventory.file(request.agent), agent_raw))
    committed = (Mutation(inventory.file(config), rendered, inventory.file(config).mode or 0o600),)
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
    section = dict(label=LABEL, runtime_root=str(request.runtime_root), program=str(request.program),
        launch_agent=str(request.agent), launch_agent_digest=digest(agent_raw),
        state_path=str(state_path), state_digest=digest(state_raw),
        config_path=str(config), config_block_digest=_block_digest(rendered.decode()), config_backup=str(backup),
        registered_at=service['registered_at'] if service else now, restarted_at=now, listener_ready=True)
    result = dict(status='installed', service=LABEL, listener_ready=True, launch_agent=str(request.agent))
    return Plan('install', domain, state_dir, request.agent, guards, staged, committed, retired,
                tuple(preparation), prior, state, result, archive,
                record_change=True, record_before=service, record_after=section)


def detach_plan(request, inventory, service, *, domain):
    """Stop the registered service, remove the provider blocks and the launch agent, delete the record's
    `service` section; the state directory stays. The service files must be as the section records them."""
    if request.platform != 'darwin':
        raise GatewayBlocked('Gateway service detachment requires its macOS launchctl owner')
    config, agent, state_path = (Path(service[k]) for k in ('config_path', 'launch_agent', 'state_path'))
    original, agent_item, state_item = inventory.file(config), inventory.file(agent), inventory.file(state_path)
    regular(original, codex=True)
    regular(state_item)
    regular(agent_item)
    if (state_item.raw is None or agent_item.raw is None or digest(state_item.raw) != service['state_digest']
            or digest(agent_item.raw) != service['launch_agent_digest']):
        raise GatewayBlocked('Gateway service or state changed; preserve owned coordinates for review')
    state = valid_state(document(state_item))
    cleaned = _owned_provider_removal(original.raw.decode(), state).encode() if original.raw is not None else None
    item = dict(kind='context-gateway', action='would-detach', reason='owned-context-gateway',
                config_path=str(config), launch_agent=str(agent))
    staged = (Mutation(original, cleaned, original.mode or 0o600),) if original.exists else ()
    committed = (Mutation(agent_item, None),)
    return Plan('detach', domain, state_path.parent, agent, (original, agent_item, state_item), staged,
                committed, (), (), state, None, item, record_change=True, record_before=service, record_after=None)


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
    if not _markers(text):
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
