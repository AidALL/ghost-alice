#!/usr/bin/env python3
"""Observe existing native server counters without a model call or transcript export.

Codex response IDs are preferred when they reconcile with cumulative counters;
older rows remain cumulative intervals. Claude snapshots reconcile by message ID.
Native Codex cache zero may originate in serde defaults, so it stays unknown.
Optional Claude result receipts supply session-attributed host counters only;
their duration scope, whole-work coverage and paid-call count remain unknown.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

TOKEN_FIELDS = ("server_input_tokens", "ordinary_input_tokens", "cache_read_tokens",
                "cache_write_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
                "output_tokens", "reasoning_output_tokens",
                "native_reported_cache_read_tokens", "native_reported_cache_write_tokens")
CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens", "total_tokens")


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def gap(gaps, locator, reason):
    gaps.append({"locator": locator, "reason": reason})


def _codex_session_binding(rows, path):
    owners = [(row.get('payload', {}).get('id'), f'{path}:{line}')
              for line, row in rows if row.get('type') == 'session_meta'
              and isinstance(row.get('payload'), dict)]
    return (owners[0] if owners and
        all(isinstance(identity, str) and identity and identity == owners[0][0]
            for identity, _ in owners) else (None, None))


def codex_intervals(rows, path, gaps):
    previous = dict.fromkeys(CODEX_FIELDS, 0)
    previous_timestamp = None
    session_id, session_locator = _codex_session_binding(rows, path)
    model = None
    observations = []
    for line, row in rows:
        payload = row.get("payload", {})
        if row.get("type") == "turn_context":
            model = payload.get("model")
        if row.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        locator = f"{path}:{line}"
        info = payload.get("info")
        if not isinstance(info, dict):
            continue  # rate-limit-only notifications contain no usage observation
        total = info.get("total_token_usage")
        if not isinstance(total, dict):
            gap(gaps, locator, "Missing cumulative usage")
            continue
        current = {key: number(total.get(key)) for key in CODEX_FIELDS}
        if current == previous:
            continue
        if any(current[k] is None for k in ("input_tokens", "output_tokens", "total_tokens")):
            gap(gaps, locator, "Missing required server counters")
            continue
        if current["total_tokens"] != current["input_tokens"] + current["output_tokens"]:
            gap(gaps, locator, "Synthetic/context-fill or inconsistent counter; excluded")
            previous = current
            previous_timestamp = row.get('timestamp')
            continue
        delta = {key: (current[key] - previous[key]
                       if current[key] is not None and previous[key] is not None else None)
                 for key in CODEX_FIELDS}
        if any(value is not None and value < 0 for value in delta.values()):
            gap(gaps, locator, "Counter reset; next interval starts at this snapshot")
            previous = current
            previous_timestamp = row.get('timestamp')
            continue
        last = info.get("last_token_usage", {})
        matches = all(delta[k] is not None and delta[k] == number(last.get(k))
                      for k in ("input_tokens", "output_tokens"))
        read = delta["cached_input_tokens"]
        write = delta["cache_write_input_tokens"]
        tokens = dict.fromkeys(TOKEN_FIELDS)
        tokens.update(server_input_tokens=delta["input_tokens"],
                      output_tokens=delta["output_tokens"],
                      reasoning_output_tokens=delta["reasoning_output_tokens"],
                      native_reported_cache_read_tokens=read,
                      native_reported_cache_write_tokens=write,
                      cache_read_tokens=read if matches and read and read > 0 else None,
                      cache_write_tokens=write if matches and write and write > 0 else None)
        if tokens["cache_read_tokens"] is not None and tokens["cache_write_tokens"] is not None:
            ordinary = delta["input_tokens"] - read - write
            if ordinary >= 0:
                tokens["ordinary_input_tokens"] = ordinary
            else:
                gap(gaps, locator, "Cache portions exceed total input")
        observations.append({"provider": "openai", "model": model,
                             "unit": "cumulative_interval", "locator": locator,
                             "timestamp": row.get("timestamp"),
                             "matches_last_usage": matches, "tokens": tokens,
                             "raw_usage": info,
                             "usage_snapshot_locators": [locator],
                             "interval_provenance": dict(session_id=session_id,
                                 session_locator=session_locator, source_path=str(path),
                                 start_usage=previous, start_timestamp=previous_timestamp,
                                 end_usage=current, end_timestamp=row.get('timestamp')),
                             "cache_provenance": "native zero can be a serde default; raw response unavailable"})
        previous = current
        previous_timestamp = row.get('timestamp')
    return observations


def _reconcile_codex_intervals(observations, gaps):
    """Reconcile copied intervals by native owner and original endpoints.

    Counter equality alone cannot identify a copied interval after a reset or
    in another session. Unaligned overlaps retain originals but supply no
    additive token values. Shared source order still distinguishes intervals.
    """
    intervals = [row for row in observations if row['unit'] == 'cumulative_interval']
    if len({row['interval_provenance']['source_path'] for row in intervals}) < 2:
        return observations
    signatures, counts = {}, defaultdict(Counter)
    for index, observation in enumerate(observations):
        if observation['unit'] != 'cumulative_interval':
            continue
        origin = observation['interval_provenance']
        if (origin['session_id'] is None or _snapshot_instant(origin['end_timestamp']) is None
                or (origin['start_timestamp'] is not None
                    and _snapshot_instant(origin['start_timestamp']) is None)):
            continue
        signature = json.dumps(dict(session_id=origin['session_id'],
            start_usage=origin['start_usage'], start_timestamp=origin['start_timestamp'],
            end_usage=origin['end_usage'], end_timestamp=origin['end_timestamp'],
            model=observation['model'], raw_usage=observation['raw_usage']), sort_keys=True)
        signatures[index] = signature
        counts[origin['source_path']][signature] += 1
    repeated = {key for source in counts.values() for key, count in source.items() if count > 1}
    unique, retained, source_paths = {}, [], []
    for index, observation in enumerate(observations):
        signature = signatures.get(index)
        origin = observation.get('interval_provenance')
        paths = {origin['source_path']} if origin else set()
        if signature is not None and signature not in repeated and signature in unique:
            position = unique[signature]
            retained[position]['usage_snapshot_locators'].extend(observation['usage_snapshot_locators'])
            source_paths[position].update(paths)
            continue
        if signature is not None and signature not in repeated:
            unique[signature] = len(retained)
        retained.append(observation)
        source_paths.append(paths)
    interval_sources = [paths for row, paths in zip(retained, source_paths)
                        if row['unit'] == 'cumulative_interval']
    if set.intersection(*interval_sources):
        return retained  # one original orders every retained interval
    ambiguous = set()
    for index, observation in enumerate(retained):
        if observation['unit'] != 'cumulative_interval':
            continue
        origin = observation['interval_provenance']
        for other_index in range(index):
            other = retained[other_index]
            if other['unit'] != 'cumulative_interval' or source_paths[index] & source_paths[other_index]:
                continue
            earlier = other['interval_provenance']
            if (origin['session_id'] is not None and earlier['session_id'] is not None
                    and origin['session_id'] != earlier['session_id']):
                continue
            if (max(origin['start_usage']['total_tokens'], earlier['start_usage']['total_tokens'])
                    < min(origin['end_usage']['total_tokens'], earlier['end_usage']['total_tokens'])):
                ambiguous.update((index, other_index))
    for index in sorted(ambiguous):
        observation = retained[index]
        observation['tokens'] = dict.fromkeys(TOKEN_FIELDS)
        gap(gaps, observation['locator'],
            'Ambiguous cross-source cumulative interval overlap; additive usage unknown')
    return retained


def _reconcile_codex_observations(observations, gaps):
    """Response identities deduplicate across exports; mixed coverage stays explicit."""
    retained, positions = [], {}
    bindings = defaultdict(list)
    for observation in observations:
        if observation['unit'] != 'server_response':
            retained.append(observation)
            continue
        key = observation['response_id']
        bindings[key].append(observation['session_provenance'])
        if key not in positions:
            positions[key] = len(retained)
            retained.append(observation)
        elif (observation.get('timestamp') or '') >= (retained[positions[key]].get('timestamp') or ''):
            retained[positions[key]] = observation
    for key, originals in bindings.items():
        if len({binding['session_id'] for binding in originals}) > 1:
            response = retained[positions[key]]
            response['session_provenance'] = dict(response['session_provenance'],
                session_id=None, session_locator=None, conflicting_bindings=originals)
            gap(gaps, response['locator'], 'Conflicting response-copy session identities; mixed-source ownership unknown')
    retained = _reconcile_codex_intervals(retained, gaps)
    responses = [(index, row) for index, row in enumerate(retained) if row['unit'] == 'server_response']
    ambiguous = set()
    for index, observation in enumerate(retained):
        if observation['unit'] != 'cumulative_interval':
            continue
        owner = observation['interval_provenance']['session_id']
        for other_index, response in responses:
            other_owner = response['session_provenance']['session_id']
            if owner is not None and other_owner is not None and owner != other_owner:
                continue
            ambiguous.update((index, other_index))
    for index in sorted(ambiguous):
        observation = retained[index]
        observation['tokens'] = dict.fromkeys(TOKEN_FIELDS)
        gap(gaps, observation['locator'],
            'Ambiguous mixed cumulative interval/server response coverage; additive usage unknown')
    return retained


def codex_observations(rows, path, gaps):
    records, model, endpoint, server_endpoint = [], None, None, None
    session_id, session_locator = _codex_session_binding(rows, path)
    has_session_metadata = any(row.get('type') == 'session_meta' for _, row in rows)
    for line, row in rows:
        payload = row.get("payload", {})
        if row.get("type") == "turn_context":
            model = payload.get("model")
        if row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                endpoint = info["total_token_usage"]
        if row.get("type") != "token_usage_record":
            continue
        locator = f"{path}:{line}"
        response_id, usage = payload.get("response_id"), payload.get("usage")
        if not response_id or not isinstance(usage, dict):
            gap(gaps, locator, "Incomplete response usage record")
            continue
        if isinstance(payload.get("thread_token_usage"), dict):
            server_endpoint = payload["thread_token_usage"]
        # Normalize through the same native converter policy as intervals.
        synthetic = {"type": "event_msg", "timestamp": row.get("timestamp"),
                                    "payload": {"type": "token_count", "info": {
                                        "total_token_usage": usage, "last_token_usage": usage}}}
        parsed = codex_intervals([(line, synthetic)], path, gaps)
        if not parsed:
            continue
        observation = parsed[0]
        observation.pop('interval_provenance')
        observation.pop('usage_snapshot_locators')
        owner, owner_locator = session_id, session_locator
        thread_id = payload.get('thread_id')
        if thread_id is not None:
            if (not isinstance(thread_id, str) or not thread_id
                    or (has_session_metadata and thread_id != session_id)):
                owner, owner_locator = None, None
                gap(gaps, locator, 'Conflicting/invalid native session identity; mixed-source ownership unknown')
            elif not has_session_metadata:
                owner, owner_locator = thread_id, locator
        observation.update(model=model, unit="server_response", response_id=response_id,
                           turn_id=payload.get("turn_id"), thread_id=payload.get("thread_id"),
                           root_turn_id=payload.get("root_turn_id"), raw_usage=usage,
                           session_provenance=dict(session_id=owner, session_locator=owner_locator,
                                                   source_path=str(path)))
        records.append(observation)
    if not records:
        return codex_intervals(rows, path, gaps)
    records = _reconcile_codex_observations(records, gaps)
    # Host display counters can omit compaction calls. Server counters own
    # coverage when response records supply them.
    if server_endpoint is not None:
        endpoint = server_endpoint
    # Never sum both response rows and their overlapping cumulative snapshots.
    fields = {"server_input_tokens": "input_tokens", "output_tokens": "output_tokens"}
    covered = endpoint is not None and all(
        all(o["tokens"][field] is not None for o in records)
        and sum(o["tokens"][field] for o in records) == number(endpoint.get(raw))
        for field, raw in fields.items())
    if covered:
        return records
    gap(gaps, str(path), "Response IDs do not cover cumulative endpoint; retain intervals without double counting")
    intervals = codex_intervals(rows, path, gaps)
    return intervals if intervals else records


def _snapshot_instant(value):
    """Parse zoned ISO instants without rounding fractional seconds or changing originals."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})', value)
    if not match:
        return None
    try:
        whole = datetime.fromisoformat(match[1] + match[3]).astimezone(timezone.utc)
    except ValueError:
        return None
    # Comparing canonical decimal digit strings preserves arbitrary precision:
    # .1 == .100, .099 < .1 < .100000001. The parsed whole second owns zone order.
    return whole, (match[2] or '').rstrip('0')


def _snapshot_order(snapshots):
    """Return occurrence nodes and transitive predecessors, never an arbitrary tie order."""
    signatures, counts = [], defaultdict(Counter)
    for snapshot in snapshots:
        instant = _snapshot_instant(snapshot['timestamp'])
        signature = (instant, json.dumps(snapshot['native_record_id'], sort_keys=True),
                     json.dumps(snapshot['raw_usage'], sort_keys=True))
        signatures.append(signature)
        counts[snapshot['source_path']][signature] += 1
    repeated = {signature for source in counts.values() for signature, count in source.items() if count > 1}
    nodes, anchors, source_nodes = [], {}, defaultdict(list)
    for snapshot, signature in zip(snapshots, signatures):
        # Native IDs constrain alignment independently of paid message IDs.
        # Legacy content anchors require absent IDs on both sides; a missing
        # ID never borrows a known occurrence. Repeated values stay distinct.
        record_id = snapshot['native_record_id']
        valid_id = record_id is None or (isinstance(record_id, str) and bool(record_id))
        anchored = valid_id and signature[0] is not None and signature not in repeated
        if anchored and signature in anchors:
            index = anchors[signature]
            nodes[index]['occurrences'].append(snapshot)
        else:
            index = len(nodes)
            nodes.append({'instant': signature[0], 'raw_usage': snapshot['raw_usage'],
                          'occurrences': [snapshot]})
            if anchored:
                anchors[signature] = index
        source_nodes[snapshot['source_path']].append(index)
    predecessors = [set() for _ in nodes]
    for sequence in source_nodes.values():
        for earlier, later in zip(sequence, sequence[1:]):
            if earlier != later:
                predecessors[later].add(earlier)
    for later, node in enumerate(nodes):
        if node['instant'] is None:
            continue
        predecessors[later].update(earlier for earlier, other in enumerate(nodes)
                                  if other['instant'] is not None and other['instant'] < node['instant'])
    # Expand ancestry only after direct predecessors have been processed. A
    # cycle means timestamp and source-order evidence disagree, not a total.
    ancestors, remaining = {}, set(range(len(nodes)))
    while remaining:
        ready = [i for i in remaining if predecessors[i].isdisjoint(remaining)]
        if not ready:
            return nodes, None
        for i in ready:
            ancestors[i] = set(predecessors[i])
            for parent in predecessors[i]:
                ancestors[i].update(ancestors[parent])
        remaining.difference_update(ready)
    return nodes, ancestors


def _resolve_usage_fields(nodes, ancestors):
    """Resolve latest evidenced writers recursively; arrays/scalars stay opaque."""
    absent, erased = object(), object()
    unresolved = []

    def write_at(usage, path):
        value = usage
        for key in path:
            if not isinstance(value, dict):
                return erased  # explicit ancestor replacement erased descendants
            if key not in value:
                return absent  # an omitted field is not a write
            value = value[key]
        return value

    def resolve(path):
        writes = {i: write_at(node['raw_usage'], path) for i, node in enumerate(nodes)}
        writes = {i: value for i, value in writes.items() if value is not absent}
        latest = [value for i, value in writes.items()
                  if not any(i in ancestors[j] for j in writes)]
        if not latest or all(value is erased for value in latest):
            return absent
        if all(isinstance(value, dict) for value in latest):
            keys = {key for value in writes.values() if isinstance(value, dict) for key in value}
            result = {}
            for key in sorted(keys):
                value = resolve(path + (key,))
                if value is not absent:
                    result[key] = value
            return result
        if all(value is not erased and not isinstance(value, dict) for value in latest):
            if len({json.dumps(value, sort_keys=True) for value in latest}) == 1:
                return deepcopy(latest[0])
        unresolved.append('/' + '/'.join(key.replace('~', '~0').replace('/', '~1') for key in path))
        return None

    return resolve(()), unresolved


def _reconcile_anthropic_snapshots(snapshots, gaps):
    grouped = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot['message_id']].append(snapshot)
    observations = []
    for message_id, originals in sorted(grouped.items()):
        originals.sort(key=lambda o: (o['source_path'], o['source_line']))
        models = {o['model'] for o in originals if o['model']}
        if len(models) > 1:
            raise ValueError('Conflicting models for the same provider message ID')
        nodes, ancestors = _snapshot_order(originals)
        maxima = [] if ancestors is None else [i for i in ancestors
            if not any(i in ancestors[j] for j in ancestors)]
        representative = nodes[maxima[0]]['occurrences'][0] if len(maxima) == 1 else originals[0]
        observation = {key: deepcopy(representative[key]) for key in
                       ('provider', 'unit', 'message_id', 'locator', 'timestamp')}
        observation['model'] = next(iter(models), None)
        # A locator with multiple maxima represents the group, not a latest event.
        if len(maxima) != 1:
            observation['timestamp'] = None
        observation['usage_snapshot_locators'] = [o['locator'] for o in originals]
        observation['usage_snapshots'] = [{key: deepcopy(o[key]) for key in
            ('locator', 'source_path', 'source_line', 'native_record_id', 'timestamp', 'raw_usage')} for o in originals]
        if ancestors is None:
            observation['raw_usage'], unresolved = {}, ['/']
            chronology = 'conflicting'
        else:
            observation['raw_usage'], unresolved = _resolve_usage_fields(nodes, ancestors)
            chronology = 'total' if all(i in ancestors[j] or j in ancestors[i]
                for i in ancestors for j in ancestors if i != j) else 'partial'
        observation['usage_ordering'] = {'chronology': chronology,
            'unresolved_fields': unresolved,
            'maximal_snapshot_locators': [o['locator'] for i in maxima for o in nodes[i]['occurrences']]}
        for field in unresolved:
            gap(gaps, observation['locator'], f'Unresolved snapshot chronology at {field}; no source-order or maximum-counter guess')
        _normalize_anthropic_observation(observation, gaps)
        observations.append(observation)
    return observations


def _normalize_anthropic_observation(observation, gaps):
    usage = observation["raw_usage"]
    if '/iterations' in observation.get('usage_ordering', {}).get('unresolved_fields', []):
        # An unresolved paid-unit array cannot establish that top-level reply
        # counters cover the request; a compaction reply may report only zeros.
        observation['usage_basis'] = 'provider-iterations-unresolved'
        observation['tokens'] = dict.fromkeys(TOKEN_FIELDS)
        return
    iterations = usage.get('iterations')
    if isinstance(iterations, list) and any(isinstance(item, dict) and item.get('type') == 'compaction'
                                          for item in iterations):
        # A compaction-only response has zero top-level reply counters.
        # Iterations are the paid units; never add top-level totals again,
        # and never replace a missing iteration field with that reply zero.
        effective = {}
        for field in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens'):
            values = [number(item.get(field)) if isinstance(item, dict) else None for item in iterations]
            effective[field] = sum(values) if values and None not in values else None
        for container, fields in [('cache_creation', ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens')),
                                  ('output_tokens_details', ('thinking_tokens',))]:
            effective[container] = {}
            for field in fields:
                values = [number(item[container].get(field)) if isinstance(item, dict)
                          and isinstance(item.get(container), dict) else None for item in iterations]
                effective[container][field] = sum(values) if values and None not in values else None
        usage = effective
        observation['usage_basis'] = 'provider-iterations'
    else:
        observation['usage_basis'] = 'provider-message'
    ordinary, write, read, output = [number(usage.get(key)) for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")]
    tokens = dict.fromkeys(TOKEN_FIELDS)
    tokens.update(ordinary_input_tokens=ordinary, cache_write_tokens=write,
                  cache_read_tokens=read, output_tokens=output)
    tokens["server_input_tokens"] = (ordinary + write + read
                                      if None not in (ordinary, write, read) else None)
    detail = usage.get("cache_creation", {})
    if not isinstance(detail, dict):
        detail = {}
    for key, field in (("ephemeral_5m_input_tokens", "cache_write_5m_tokens"),
                       ("ephemeral_1h_input_tokens", "cache_write_1h_tokens")):
        tokens[field] = number(detail.get(key))
    ttl = [tokens["cache_write_5m_tokens"], tokens["cache_write_1h_tokens"]]
    if write is not None and None not in ttl and sum(ttl) != write:
        gap(gaps, observation["locator"], "TTL subtotal differs from cache creation total")
    output_detail = usage.get("output_tokens_details", {})
    tokens["reasoning_output_tokens"] = number(output_detail.get("thinking_tokens")) if isinstance(output_detail, dict) else None
    if None in (ordinary, write, read, output):
        gap(gaps, observation["locator"], "Missing server usage field; no zero fill")
    observation["tokens"] = tokens


def _anthropic_usage_snapshots(rows, path, gaps):
    for line, row in rows:
        if row.get("type") != "assistant":
            continue
        message = row.get("message", {})
        usage = message.get("usage")
        locator = f"{path}:{line}"
        if message.get("model") == "<synthetic>" or row.get("isApiErrorMessage"):
            gap(gaps, locator, "Synthetic error is not server usage; charge unobserved")
            continue
        if not isinstance(usage, dict):
            gap(gaps, locator, "Assistant record without usage; charge unobserved")
            continue
        message_id = message.get("id")
        if not message_id:
            gap(gaps, locator, "Missing message ID; cannot deduplicate usage")
            continue
        current = {"provider": "anthropic", "model": message.get("model"),
                   "unit": "server_message", "message_id": message_id,
                   "locator": locator, "timestamp": row.get("timestamp"),
                   "raw_usage": usage, "source_path": str(path), "source_line": line,
                   "native_record_id": row.get('uuid')}
        yield current


def anthropic_observations(rows, path, gaps):
    return _reconcile_anthropic_snapshots(list(_anthropic_usage_snapshots(rows, path, gaps)), gaps)


def _native_session_binding(rows, source):
    """Bind from observed Claude metadata, never a filename or external descriptor."""
    identities = {}
    for line, row in rows:
        if 'sessionId' not in row:
            continue
        identity = row['sessionId']
        if not isinstance(identity, str) or not identity:
            return None
        identities.setdefault(identity, f"{source['path']}:{line}")
    if len(identities) != 1:
        return None
    identity, locator = next(iter(identities.items()))
    return dict(source, session_id=identity, identity_locator=locator)


def _result_receipts(provider, paths, native_bindings):
    sources, receipts, snapshots, identified = [], [], {}, defaultdict(list)
    for index, original in enumerate(paths):
        path = Path(original).resolve()
        locator = f'{path}#'
        # Repeated input occurrences retain provenance over the same byte snapshot.
        if path not in snapshots:
            try:
                snapshots[path] = path.read_bytes(), None
            except OSError as error:
                snapshots[path] = None, str(error)
        data, error = snapshots[path]
        sources.append({'path': str(path),
                        'snapshot_sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
                        'snapshot_bytes': len(data) if data is not None else None})
        receipt = {'receipt_source_index': index, 'locator': locator, 'status': 'unavailable',
                   'session_id': None, 'uuid': None, 'duration_ms': None,
                   'duration_api_ms': None, 'num_turns': None, 'duration_scope': 'unknown',
                   'native_sources': [], 'raw_host_counters': {}, 'semantic_sha256': None,
                   'gaps': []}
        receipts.append(receipt)
        if error is not None:
            gap(receipt['gaps'], locator, f'Receipt source unavailable: {error}')
            continue
        try:
            row = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            gap(receipt['gaps'], locator, 'Empty/malformed result receipt; host counters unavailable')
            continue
        if not isinstance(row, dict) or row.get('type') != 'result':
            gap(receipt['gaps'], locator, 'Not a native result object; host counters unavailable')
            continue
        # The digest detects conflicting copies without exporting result text or
        # cumulative usage/modelUsage bodies into the new receipt summary.
        receipt['semantic_sha256'] = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        for field in ('session_id', 'uuid'):
            value = row.get(field)
            receipt[field] = value if isinstance(value, str) and value else None
            if receipt[field] is None:
                gap(receipt['gaps'], locator, f'Missing/invalid {field}; identity unobserved')
        for field in ('duration_ms', 'duration_api_ms', 'num_turns'):
            if field in row:
                receipt['raw_host_counters'][field] = row[field]
            receipt[field] = number(row.get(field))
            if receipt[field] is None:
                gap(receipt['gaps'], locator, f'Missing/invalid {field}; no zero fill')
        if provider != 'anthropic':
            gap(receipt['gaps'], locator, 'Claude result receipts are unsupported for this provider')
            continue
        receipt['native_sources'] = [deepcopy(source) for source in native_bindings
                                    if source['session_id'] == receipt['session_id']]
        if not receipt['native_sources']:
            gap(receipt['gaps'], locator, 'No selected native source has a single matching sessionId')
            continue
        receipt['status'] = 'matched'
        if receipt['uuid'] is not None:
            identified[(receipt['session_id'], receipt['uuid'])].append(receipt)
    for occurrences in identified.values():
        if len({receipt['semantic_sha256'] for receipt in occurrences}) > 1:
            for receipt in occurrences:
                gap(receipt['gaps'], receipt['locator'],
                    'Conflicting copies of the same session/result UUID; original counters retained')
    return sources, receipts, len(identified)


def _call_coverage(observations, receipts, identified_count):
    return {'observed_server_message_count': len({o['message_id'] for o in observations
                                                 if o['unit'] == 'server_message'}),
            'observed_server_response_count': len({o['response_id'] for o in observations
                                                  if o['unit'] == 'server_response'}),
            'cumulative_interval_count': sum(o['unit'] == 'cumulative_interval' for o in observations),
            'identified_matched_result_count': identified_count,
            'unidentified_matched_result_occurrences': sum(
                r['status'] == 'matched' and r['uuid'] is None for r in receipts),
            'paid_call_count': None, 'model_api_call_count': None,
            'scope': 'Selected native usage observations and supplied result identities only; '
                     'intervals, result UUIDs and num_turns do not establish paid/model/API calls'}


def observe(provider, paths, *, through_line=None, receipt_paths=()):
    if through_line is not None and (type(through_line) is not int or through_line < 1 or len(paths) != 1):
        raise ValueError("A cutoff requires one original source and a positive line number")
    observations, gaps, sources, native_bindings = [], [], [], []
    seen_paths = set()
    for source in paths:
        path = Path(source).resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        # Snapshot once: a live append after this read belongs to the next report.
        data = path.read_bytes()
        if through_line is not None:
            original_lines = data.splitlines(keepends=True)
            if through_line > len(original_lines):
                raise ValueError("The original source has not reached the requested cutoff")
            data = b"".join(original_lines[:through_line])
        rows = []
        lines = data.splitlines()
        for line, value in enumerate(lines, 1):
            try:
                row = json.loads(value)
                if isinstance(row, dict):
                    rows.append((line, row))
            except (ValueError, UnicodeDecodeError):
                gap(gaps, f"{path}:{line}", "Malformed/incomplete record; usage unobserved")
        sources.append({"path": str(path), "snapshot_sha256": hashlib.sha256(data).hexdigest(),
                        "snapshot_bytes": len(data), "through_line": len(lines)})
        if provider == "anthropic":
            binding = _native_session_binding(rows, sources[-1]) if receipt_paths else None
            if binding is not None:
                native_bindings.append(binding)
            observations.extend(_anthropic_usage_snapshots(rows, path, gaps))
        else:
            observations.extend(codex_observations(rows, path, gaps))
    if provider == "anthropic":
        # Merge across exports before normalization so sparse snapshots do not
        # discard known fields or create false missing-counter gaps.
        observations = _reconcile_anthropic_snapshots(observations, gaps)
    elif provider == 'codex':
        observations = _reconcile_codex_observations(observations, gaps)
    totals, subtotals = {}, {}
    for field in TOKEN_FIELDS:
        values = [o["tokens"][field] for o in observations]
        subtotals[field] = sum(v for v in values if v is not None) if any(v is not None for v in values) else None
        totals[field] = sum(values) if values and None not in values else None
    by_model = {}
    for model in {o.get("model") or "unknown" for o in observations}:
        selected = [o for o in observations if (o.get("model") or "unknown") == model]
        by_model[model] = {}
        for field in TOKEN_FIELDS:
            values = [o["tokens"][field] for o in selected]
            by_model[model][field] = sum(values) if None not in values else None
    if provider == "codex" and observations and totals["cache_write_tokens"] is None:
        gap(gaps, "native response converter", "Cache-write zero may be a default; ordinary/write split unconfirmed")
    receipt_sources, receipts, identified_count = _result_receipts(provider, receipt_paths, native_bindings)
    return {"schema_version": "native-usage-observation.v1", "provider": provider,
            "sources": sources, "observations": observations, "totals": totals,
            "observed_subtotals": subtotals, "by_model": by_model, "gaps": gaps,
            "receipt_sources": receipt_sources, "invocation_receipts": receipts,
            "call_coverage": _call_coverage(observations, receipts, identified_count),
            "whole_work": {"duration_ms": None, "duration_api_ms": None,
                           "paid_call_count": None, "participating_models": None, "actual_charge": None,
                           "gaps": ["Receipt session matching establishes attribution only; duration scope and overlap are unknown",
                                    "Host duration_ms and duration_api_ms are distinct; neither is summed as whole-work elapsed time",
                                    "Native response IDs, cumulative intervals and num_turns do not prove whole-work paid-call coverage",
                                    "Parent, delegated/review models and unlogged calls may be outside the selected sources",
                                    "No billing receipt or complete participating-model evidence"]},
            "coverage": "existing server usage records through each source cutoff; unlogged paid calls remain unknown",
            "estimated_cost": None, "actual_charge": None,
            "cost_reason": "No verified rate for this exact model, service path and account; no billing receipt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "anthropic"), required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--receipt", action="append", default=[],
                        help="Original Claude CLI result JSON; session attribution does not establish duration scope")
    parser.add_argument("--output")
    args = parser.parse_args()
    target = Path(args.output).resolve() if args.output else None
    originals = [Path(p).resolve() for p in args.source + args.receipt]
    if target is not None and any(target == source or (target.exists() and source.exists()
                                  and target.samefile(source)) for source in originals):
        parser.error("Output must not overwrite a source or receipt")
    report = observe(args.provider, args.source, receipt_paths=args.receipt)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target.write_text(text)
        print(json.dumps({"output": str(target), "observations": len(report["observations"]),
                          "totals": report["totals"], "gap_count": len(report["gaps"])}))
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
