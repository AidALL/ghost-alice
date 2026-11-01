"""Loopback native-request gateway with Core-authenticated input selection.

Native credentials and server answer bytes pass through unchanged. Logs contain
structure, hashes and server usage, never conversation or credential bodies: a
request field is recorded as its item count, byte total and counts by type and
role. `response-end` says how the response ended (`completed`, `client-closed`,
`upstream-closed`). The usage log and the service log keep the retention table's
limits (`state_retention`).
Dependencies: Python 3.11+; zstd requests require Python 3.14 compression.zstd.
"""
import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib

import state_retention

HOP = {'host', 'connection', 'transfer-encoding', 'content-length', 'proxy-connection', 'keep-alive'}
INTERNAL_HEADERS = {'x-ghost-context-scope', 'x-ghost-context-session'}
ORIGINS = {'codex': 'https://chatgpt.com/backend-api/codex', 'anthropic': 'https://api.anthropic.com'}
PLATFORMS = {'codex': 'codex', 'anthropic': 'claude'}
SELECTION_ENDPOINTS = {'codex': {'responses'}, 'anthropic': {'v1/messages', 'v1/messages/count_tokens'}}
REDIRECT_REFUSED = 'the model server sent a redirect outside its origin; it was not followed'
# A client that closed its connection makes the next write to it fail with one of these.
CLIENT_CLOSED = (BrokenPipeError, ConnectionResetError)


def _url_origin(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


def _redirect_allowed(origin, source_url, location):
    expected = _url_origin(origin)
    try:
        # Native clients may use WHATWG parsing, where backslashes and extra
        # authority slashes differ from urllib's relative-path interpretation.
        if any(ord(char) < 32 or ord(char) == 127 for char in location):
            return False
        location = location.strip()
        if '\\' in location or location.startswith('///'):
            return False
        target = urllib.parse.urlsplit(location)
        if target.scheme and not target.netloc:
            return False
        return expected is not None and _url_origin(urllib.parse.urljoin(source_url, location)) == expected
    except ValueError:
        return False


class _OriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep native headers inside the configured provider's origin."""

    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not _redirect_allowed(self.origin, request.full_url, new_url):
            fp.close()
            raise urllib.error.URLError(REDIRECT_REFUSED)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def field_shape(value):
    """Item count, byte total and counts by type and role of one request field; a value that is not a list
    is one item."""
    items = value if isinstance(value, list) else [value]
    types, roles, size = {}, {}, 0
    for item in items:
        size += len(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        if isinstance(item, dict):
            for key, counts in (('type', types), ('role', roles)):
                if isinstance(item.get(key), str):
                    counts[item[key]] = counts.get(item[key], 0) + 1
    return {'items': len(items), 'bytes': size, 'types': types, 'roles': roles}


def decode_body(raw, encoding):
    if encoding == 'gzip':
        return gzip.decompress(raw)
    if encoding == 'zstd':
        from compression import zstd
        return zstd.decompress(raw)
    if encoding and encoding != 'identity':
        raise ValueError('unsupported-content-encoding')
    return raw


def encode_body(raw, encoding):
    if encoding == 'gzip':
        return gzip.compress(raw)
    if encoding == 'zstd':
        from compression import zstd
        return zstd.compress(raw)
    return raw


def _merge_usage(previous, incoming):
    """Retain only observed native fields; cumulative counts replace, not add."""
    result = copy.deepcopy(previous)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_usage(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class UsageSnapshots:
    """Request-local native server identity and observed usage snapshots."""

    def __init__(self, provider):
        if provider not in ('codex', 'anthropic'):
            raise ValueError('A supported native usage provider is required')
        self.provider = provider
        self.server_id = self.model = None
        self.usage = {}

    def observe(self, event):
        if not isinstance(event, dict):
            return None
        container = event.get('response' if self.provider == 'codex' else 'message')
        container = container if isinstance(container, dict) else {}
        identifier = container.get('id')
        if isinstance(identifier, str) and identifier and identifier != self.server_id:
            self.server_id, self.model, self.usage = identifier, None, {}
        model = container.get('model')
        if isinstance(model, str):
            self.model = model
        usage = container.get('usage')
        if not isinstance(usage, dict):
            usage = event.get('usage')
        if not isinstance(usage, dict):
            return None
        self.usage = _merge_usage(self.usage, usage)
        observed = dict(server_id=self.server_id, server_event=event.get('type'), model=self.model,
                        usage=_recorded_usage(self.usage))
        context = event.get('context_management', container.get('context_management'))
        if self.provider == 'anthropic' and isinstance(context, dict):
            observed['context_management'] = copy.deepcopy(context)
        return observed


def _recorded_usage(usage):
    """The usage snapshot as recorded: a native attribution table keeps only its item count."""
    recorded = copy.deepcopy(usage)
    attribution = recorded.get('attribution')
    if isinstance(attribution, dict):
        items = attribution.get('items')
        recorded['attribution'] = {'item_count': len(items) if isinstance(items, (dict, list)) else 0}
    return recorded


def forward_stream(upstream, sink, provider, on_usage):
    """Copy original response bytes while observing native usage separately.

    Reading the upstream and writing the client are wrapped apart: a failed read, or an end short of the
    declared length, ends the copy with `upstream-closed`; a client that closed its connection ends it with
    `client-closed`; any other error is raised. The details name the result and, when the copy stopped early,
    the error type.
    """
    parser = UsageSnapshots(provider)
    pending = b''
    response_hash = hashlib.sha256()
    events = 0
    encoding = upstream.headers.get('Content-Encoding', '').lower()
    observable = encoding in ('', 'identity', 'gzip', 'zstd')
    decoder = None
    decoder_factory = None
    if encoding == 'gzip':
        decoder_factory = lambda: zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == 'zstd':
        try:
            from compression import zstd
            decoder_factory = zstd.ZstdDecompressor
        except ImportError:
            observable = False
    if decoder_factory is not None:
        decoder = decoder_factory()
    result, error_type = 'completed', None
    while True:
        try:
            chunk = upstream.read1(65536)
        except Exception as error:  # the upstream side ended the response
            result, error_type = 'upstream-closed', type(error).__name__
            break
        if not chunk:
            # http.client's read1 returns b'' at a premature EOF and leaves the undelivered count in `length`;
            # only read() raises IncompleteRead.
            if getattr(upstream, 'length', None):
                result, error_type = 'upstream-closed', 'IncompleteRead'
            break
        response_hash.update(chunk)
        try:
            sink.write(chunk)
            sink.flush()
        except CLIENT_CLOSED as error:
            result, error_type = 'client-closed', type(error).__name__
            break
        if not observable:
            continue
        try:
            if decoder is None:
                pending += chunk
            else:
                compressed = chunk
                while compressed:
                    if decoder.eof:
                        decoder = decoder_factory()
                    pending += decoder.decompress(compressed)
                    compressed = decoder.unused_data if decoder.eof else b''
        except Exception:
            # Observation is passive: invalid compression never interrupts or
            # filters the native response. Earlier observed events stay factual.
            observable = False
            pending = b''
            continue
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            if not line.startswith(b'data: '):
                continue
            try:
                event = json.loads(line[6:])
            except ValueError:
                continue
            events += 1
            observed = parser.observe(event)
            if observed is not None:
                on_usage(observed)
    if decoder is not None and not decoder.eof:
        observable = False
    details = dict(result=result, wire_sha256=response_hash.hexdigest(), sse_events_observed=events,
                   usage_stream_observable=observable, response_content_encoding=encoding)
    if error_type is not None:
        details['error_type'] = error_type
    return details


def request_shape(raw, encoding):
    try:
        body = json.loads(decode_body(raw, encoding))
        if not isinstance(body, dict):
            return {'shape_unavailable': 'not-an-object'}
        result = {'fields': list(body), 'model': body.get('model'), 'units': {}}
        for field in ('input', 'messages', 'system', 'tools'):
            if field in body:
                result['units'][field] = field_shape(body[field])
        return result
    except (ValueError, ImportError, OSError):
        return {'shape_unavailable': 'content-not-decoded'}


def make_server(scope, output, origins=None, selection=None, port=0, selectors=None, server_selectors=None):
    """Dispatch explicit platform callbacks with (request, declared_session).

    The legacy single-argument selection callback remains Codex-only. Explicit
    selectors use the native platform keys codex and claude; no signature is
    inferred and duplicate Codex declarations are rejected.
    """
    lock = threading.Lock()
    origins = dict(origins or ORIGINS)
    openers = {provider: urllib.request.build_opener(_OriginRedirectHandler(origin))
               for provider, origin in origins.items()}
    if (server_selectors is not None and (not isinstance(server_selectors, dict)
        or any(key != 'claude' or not callable(callback) for key, callback in server_selectors.items()))):
        raise ValueError('Only an explicit Claude native-capability selector is supported')
    server_dispatch = server_selectors or {}
    if selectors is not None and (not isinstance(selectors, dict) or any(
        key not in ('codex', 'claude') or not callable(callback) for key, callback in selectors.items())):
        raise ValueError('Explicit supported platform selection callbacks are required')
    dispatch = dict(selectors or {})
    if selection is not None:
        if not callable(selection) or 'codex' in dispatch:
            raise ValueError('One explicit Codex selection callback is required')
        dispatch['codex'] = lambda request, _session: selection(request)

    def record(value):
        value['observed_at'] = datetime.now(timezone.utc).isoformat()
        with lock:
            with Path(output).open('a') as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            state_retention.apply('gateway-usage', output)

    class Observer(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def reject(self, status, provider=None, reason=None):
            # The native client shows the provider-format error message instead of "Unknown error".
            body = b''
            if reason is not None:
                message = 'Ghost-ALICE context gateway: ' + reason
                body = json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': message}}
                                  if provider == 'anthropic' else
                                  {'error': {'message': message, 'type': 'ghost_alice_gateway_error'}}).encode()
            self.close_connection = True
            try:
                self.send_response(status)
                if body:
                    self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
            except CLIENT_CLOSED:
                pass  # The request's event is recorded; a client that left needs no answer.

        def read_body(self):
            if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                parts = []
                while True:
                    size = int(self.rfile.readline().split(b';', 1)[0], 16)
                    if not size:
                        while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                            pass
                        return b''.join(parts)
                    parts.append(self.rfile.read(size))
                    if self.rfile.read(2) != b'\r\n':
                        raise ValueError('invalid-chunk-boundary')
            return self.rfile.read(int(self.headers.get('Content-Length', '0')))

        def handle_request(self):
            in_scope = self.headers.get_all('X-Ghost-Context-Scope') == [scope]
            if self.path == '/health' and self.command == 'GET' and in_scope:
                body = b'{"status":"ready"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            parsed = urllib.parse.urlsplit(self.path)
            provider, _, suffix = parsed.path.lstrip('/').partition('/')
            if provider not in origins:
                record({'event': 'request-not-forwarded', 'reason': 'unknown-provider', 'status': 404})
                self.reject(404)
                return
            # architecture.md#context-and-original-evidence lists which requests go upstream unchanged.
            platform = PLATFORMS[provider]
            selector = dispatch.get(platform) if suffix in SELECTION_ENDPOINTS[provider] else None
            server_selector = server_dispatch.get(platform) if suffix in SELECTION_ENDPOINTS[provider] else None
            session_headers = self.headers.get_all('X-Ghost-Context-Session') or []
            session_id = session_headers[0] if len(session_headers) == 1 else None
            skipped = None if in_scope else 'scope-mismatch'
            if platform == 'claude' and skipped is None:
                try:
                    valid_session = (isinstance(session_id, str)
                                     and str(uuid.UUID(session_id)) == session_id.lower())
                except ValueError:
                    valid_session = False
                if not valid_session:
                    skipped = 'native-session-unavailable'
            request_id = str(uuid.uuid4())
            body = self.read_body()
            encoding = self.headers.get('Content-Encoding', '').lower()
            record({'event': 'request', 'request_id': request_id, 'provider': provider, 'platform': platform,
                    'native_session_id': session_id,
                    'endpoint': '/' + suffix, 'method': self.command,
                    'wire_sha256': digest(body), 'wire_bytes': len(body),
                    'native_authorization_present': bool(self.headers.get('Authorization') or self.headers.get('X-Api-Key')),
                    'shape': request_shape(body, encoding)})
            if selector is not None or server_selector is not None:
                betas = tuple(value.strip() for header in self.headers.get_all('anthropic-beta', [])
                              for value in header.split(',') if value.strip())
                observed = {'event': 'request-selection', 'request_id': request_id, 'provider': provider,
                            'platform': platform, 'native_session_id': session_id,
                            'native_betas': list(betas) if platform == 'claude' else [],
                            'original_wire_sha256': digest(body), 'selected_blocks': []}
                # An incomplete selection forwards the original request (architecture.md#context-and-original-evidence).
                if skipped is not None:
                    observed.update(state='unavailable', reason=skipped)
                else:
                    try:
                        decoded = json.loads(decode_body(body, encoding))
                        result = (server_selector(decoded, session_id, betas) if server_selector is not None
                                  else selector(decoded, session_id))
                        if result.state not in {'selected', 'preserved', 'unavailable'}:
                            raise ValueError('Invalid request selection state')
                        forwarded = body
                        if result.state == 'selected':
                            forwarded = encode_body(json.dumps(result.request, ensure_ascii=False,
                                                               separators=(',', ':')).encode(), encoding)
                            observed['selected_blocks'] = result.observations
                        observed.update(state=result.state, reason=result.reason)
                        body = forwarded
                    except Exception as error:
                        observed.update(state='error', reason='selection-error', error_type=type(error).__name__)
                observed.update(forwarded_wire_sha256=digest(body), forwarded_shape=request_shape(body, encoding))
                record(observed)
            url = origins[provider].rstrip('/') + '/' + suffix
            if parsed.query:
                url += '?' + parsed.query
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP and k.lower() not in INTERNAL_HEADERS
                       and k.lower() != 'anthropic-beta'}
            native_beta_headers = self.headers.get_all('anthropic-beta', [])
            if native_beta_headers:
                headers['Anthropic-Beta'] = ','.join(native_beta_headers)
            request = urllib.request.Request(url, data=body if self.command == 'POST' else None,
                                             headers=headers, method=self.command)
            try:
                upstream = openers[provider].open(request, timeout=300)
            except urllib.error.HTTPError as error:
                upstream = error
            except (OSError, urllib.error.URLError) as error:
                record({'event': 'transport-unavailable', 'request_id': request_id,
                        'provider': provider, 'error_type': type(error).__name__})
                self.reject(502, provider, REDIRECT_REFUSED if getattr(error, 'reason', None) == REDIRECT_REFUSED
                            else 'could not reach the model server (' + type(error).__name__ + ')')
                return
            with upstream:
                # Some methods/statuses (notably POST 307/308) become
                # HTTPError before redirect_request. Never pass an unsafe
                # redirect to the native client for it to follow instead.
                redirect_response = 300 <= upstream.status < 400
                if redirect_response and any(
                    not _redirect_allowed(origins[provider], upstream.geturl(), location)
                    for key in ('Location', 'URI') for location in upstream.headers.get_all(key, [])):
                    record({'event': 'redirect-not-forwarded', 'request_id': request_id,
                            'provider': provider, 'status': 502})
                    self.reject(502, provider, REDIRECT_REFUSED)
                    return
                self.close_connection = True
                try:
                    self.send_response(upstream.status)
                    for key, value in upstream.headers.items():
                        if key.lower() not in HOP:
                            if redirect_response and key.lower() in ('location', 'uri'):
                                # The native client sees an HTTP loopback base.
                                # Relay the absolute URL already validated against
                                # the upstream base, preserving its HTTPS origin.
                                value = urllib.parse.urljoin(upstream.geturl(), value.strip())
                            self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                except CLIENT_CLOSED as error:
                    record({'event': 'response-end', 'request_id': request_id, 'provider': provider,
                            'status': upstream.status, 'result': 'client-closed',
                            'error_type': type(error).__name__})
                    return
                details = forward_stream(upstream, self.wfile, provider, lambda usage: record(
                    dict(event='server-usage', request_id=request_id, provider=provider, **usage)))
                record({'event': 'response-end', 'request_id': request_id, 'provider': provider,
                        'status': upstream.status, **details})
                if details['result'] == 'upstream-closed':
                    print(f"context gateway: upstream-closed request {request_id} ({details['error_type']})",
                          file=sys.stderr, flush=True)

        do_GET = handle_request
        do_POST = handle_request

    return ThreadingHTTPServer(('127.0.0.1', port), Observer)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if (config.get('schema_version') != 'context-gateway.v1'
        or type(config.get('port')) is not int or not 0 < config['port'] < 65536
        or not isinstance(config.get('scope'), str) or not config['scope']):
        raise ValueError('An explicit installed gateway configuration is required')
    for key in ('ledger_root', 'codex_dir', 'usage_log'):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise ValueError('Absolute installed coordinates are required')
    if 'claude_dir' in config and (not isinstance(config['claude_dir'], str)
                                  or not Path(config['claude_dir']).is_absolute()):
        raise ValueError('An explicit absolute Claude native home is required')
    # launchd opens the service log beside the usage log for the service's output before it starts the program;
    # the program keeps it within its limit before it serves.
    state_retention.apply('gateway-service-log', Path(config['usage_log']).with_name('service.log'))
    from context_lifecycle import select_request, prepare_native_request
    selectors = {'codex': lambda request, _session: select_request(request, config['ledger_root'], config['codex_dir'])}
    origins = {'codex': ORIGINS['codex']}
    server_selectors = {}
    if 'claude_dir' in config:
        origins['anthropic'] = ORIGINS['anthropic']
        selectors['claude'] = lambda request, session: prepare_native_request(
            request, config['ledger_root'], config['claude_dir'], platform='claude', session_id=session)
        server_selectors['claude'] = lambda request, session, betas: prepare_native_request(
            request, config['ledger_root'], config['claude_dir'], platform='claude', session_id=session,
            native_betas=betas)
    server = make_server(config['scope'], config['usage_log'], port=config['port'],
                         origins=origins, selectors=selectors, server_selectors=server_selectors)
    server.serve_forever()


if __name__ == '__main__':
    main()
