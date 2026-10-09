"""Stored classifier records supply no permission through the source hooks.

Dependencies: Python 3.11+ standard library, pytest and Node on PATH.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from install_destination import InstallDestination, binding_environment, decode_binding, encode_binding

CORE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retired_stage_ledger', CORE/'session-intent-analyzer/scripts/session_intent_ledger.py')
ledger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ledger
spec.loader.exec_module(ledger)


def fixture(tmp_path):
    destination = InstallDestination(mode='isolated', owned_root=str(tmp_path),
        platform_dir=str(tmp_path/'platform'), skills_dir=str(tmp_path/'skills'),
        ghost_root=str(tmp_path/'ghost'), ledger_root=str(tmp_path/'ledger'),
        project_root=str(tmp_path/'project'), core_source_root=str(CORE))
    # The public transport is a location descriptor, never a native permission receipt.
    transported = InstallDestination.parse(decode_binding(encode_binding(destination.binding()))['destination'])
    env = binding_environment(transported, dict(os.environ))
    env['GHOST_ALICE_PYTHON'] = sys.executable
    (tmp_path/'project').mkdir()
    session = 'retirement-source-fixture'
    payload = dict(session_id=session, prompt='Explain the authorized source fixture retirement.')
    observation = subprocess.run([sys.executable, '-B', str(CORE/'_shared/session_intent_analyzer_hook.py'),
        '--platform', 'claude', '--format', 'json', '--root', destination.ledger_root],
        input=json.dumps(payload), env=env, text=True, capture_output=True, check=True)
    context = json.loads(observation.stdout)['hookSpecificOutput']['additionalContext']
    receipt = json.loads(context.split('[session-intent-receipt]\n', 1)[1].split('\n[/session-intent-receipt]', 1)[0])
    assert receipt['ledger_root'] == destination.ledger_root
    assert receipt['session_id'] == session
    assert 'grants no additional permission' in context
    coordinates = dict(root=Path(destination.ledger_root), platform='claude', session_id=session)
    legacy = dict(decision='block', input_event_id=receipt['input_event_id'],
        risk_flags=['instruction-hierarchy-override'], reason='Legacy fixture history only')
    ledger.record_turn(**coordinates, expected_input_event_id=receipt['input_event_id'],
        intent_delta={'model_security_decision': legacy}, source='retirement-source-fixture')
    return destination, env, receipt, coordinates


def test_legacy_record_cannot_derive_gate_or_detector_permission(tmp_path):
    destination, env, receipt, coordinates = fixture(tmp_path)
    before = ledger.read_session_state(**coordinates)
    result = subprocess.run(['node', str(CORE/'_shared/ghost-alice-hook.mjs'), '--platform', 'claude',
        '--event', 'PreToolUse', '--hook', 'tool-checkpoint', '--session-intent-root', destination.ledger_root],
        input=json.dumps(dict(session_id=receipt['session_id'], tool_name='Bash', tool_input={'command':'true'})),
        env=env, text=True, capture_output=True, check=True)
    assert result.stderr == ''
    assert not (Path(destination.ledger_root)/'claude'/receipt['session_id']/'downstream-gates.json').exists()
    assert 'permissionDecision' not in json.loads(result.stdout).get('hookSpecificOutput', {})
    assert ledger.read_session_state(**coordinates) == before
    assert any(row['event'] == 'intent-updated' for row in ledger.read_session_events(**coordinates))


def test_router_uses_intake_without_legacy_classifier_or_backlog_authority(tmp_path):
    destination, env, receipt, coordinates = fixture(tmp_path)
    ledger.record_turn(**coordinates, expected_input_event_id=receipt['input_event_id'], intent_delta={
        'acceptance_criteria':[dict(id='old-local-id', summary='Historical unmet work',
            source='user-explicit', admitted=True, status='unmet')]})
    result = subprocess.run([sys.executable, '-B', str(CORE/'_shared/task_router_reminder_hook.py'),
        '--platform', 'claude', '--format', 'json', '--root', destination.ledger_root],
        input=json.dumps({'session_id':receipt['session_id']}), env=env, text=True, capture_output=True, check=True)
    context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    assert 'withheld' not in context
    assert 'jailbreak' not in context
    assert 'unresolved-work:' not in context
    assert 'read-session-intent-ledger' in context
    assert ledger.read_session_state(**coordinates)['model_security_decision']['decision'] == 'block'


@pytest.mark.parametrize('binding', ('missing', 'foreign'))
def test_retirement_preserves_exact_event_protected_writes(tmp_path, binding):
    coordinates = fixture(tmp_path)[3]
    before = ledger.read_session_state(**coordinates)
    expected = None
    if binding == 'foreign':
        foreign = dict(root=coordinates['root'], platform='claude', session_id='retirement-foreign-fixture')
        ledger.record_turn(**foreign, raw_user_input='Foreign fixture input')
        expected = ledger.read_session_state(**foreign)['latest_input_event_id']
    with pytest.raises(ValueError, match='expected-input-event-id is required|stale input event'):
        ledger.record_turn(**coordinates, expected_input_event_id=expected,
            intent_delta={'constraints':['Invalid fixture write must be rejected']})
    assert ledger.read_session_state(**coordinates) == before
