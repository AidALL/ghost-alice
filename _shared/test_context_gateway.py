"""Actual HTTP contracts for the installed context gateway."""
import copy
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

try:
    from compression import zstd
except ImportError:
    zstd = None

import context_gateway
import context_lifecycle
from test_context_lifecycle import ContextLifecycleTests


class RedirectGatewayTests(unittest.TestCase):
    """Only loopback fixture services receive these synthetic credentials."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / 'usage.jsonl'
        self.routes, self.seen, self.foreign = {}, [], []
        self.answer = b'native response bytes\x00\xff'
        self.servers = []
        self.addCleanup(self.stop)
        owner = self

        class Upstream(BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                seen = owner.foreign if self.server.foreign else owner.seen
                seen.append((self.command, self.path, body, dict(self.headers)))
                status, locations = owner.routes.get(self.path, (200, [])) if not self.server.foreign else (200, [])
                payload = b'redirect response' if locations else owner.answer
                self.send_response(status)
                for key, value in locations:
                    self.send_header(key, value)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = handle_request

            def log_message(self, *_args):
                pass

        self.handler = Upstream
        self.target = self.start_server(Upstream, foreign=True)

    def start_server(self, handler, *, foreign=False, tls_context=None):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        if tls_context is not None:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        server.foreign = foreign
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def start_gateway(self, *, tls=False):
        context = None
        if tls:
            certificate, key = Path(self.tmp.name) / 'fixture.crt', Path(self.tmp.name) / 'fixture.key'
            subprocess.run([shutil.which('openssl'), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(key), '-out', str(certificate), '-days', '1',
                            '-subj', '/CN=localhost'], check=True, capture_output=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
        self.upstream = self.start_server(self.handler, tls_context=context)
        self.origin = f'{"https" if tls else "http"}://127.0.0.1:{self.upstream.server_port}'
        self.gateway = context_gateway.make_server('redirect-fixture', self.log,
            origins={'codex': self.origin, 'anthropic': self.origin})
        self.servers.append(self.gateway)
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()

    def stop(self):
        for server in reversed(self.servers):
            server.shutdown()
            server.server_close()

    def request(self, method='GET', provider='codex', path=None):
        suffix = 'models' if provider == 'codex' and method == 'GET' else (
            'responses' if provider == 'codex' else 'v1/messages')
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        try:
            connection.request(method, path or f'/{provider}/{suffix}',
                body=b'{ "input": [] }' if method == 'POST' else None,
                headers={'X-Ghost-Context-Scope': 'redirect-fixture',
                         'Authorization': 'Bearer REDIRECT_FIXTURE_AUTH', 'X-Api-Key': 'REDIRECT_FIXTURE_KEY'})
            reply = connection.getresponse()
            return reply.status, dict(reply.headers), reply.read()
        finally:
            connection.close()

    def assert_reason_body(self, provider, headers, body, phrase):
        self.assertEqual(headers['Content-Type'], 'application/json')
        envelope = json.loads(body)
        if provider == 'anthropic':
            self.assertEqual(envelope['type'], 'error')
        self.assertIn(phrase, envelope['error']['message'])

    def test_unknown_endpoint_of_a_known_provider_passes_through_unchanged(self):
        self.start_gateway()
        code, _, body = self.request('POST', path='/codex/future/endpoint?mode=1')
        self.assertEqual((code, body), (200, self.answer))
        self.assertEqual(self.seen[-1][:3], ('POST', '/future/endpoint?mode=1', b'{ "input": [] }'))

    def test_unknown_provider_has_no_destination(self):
        self.start_gateway()
        code, _, _ = self.request('POST', path='/unknown/v1/messages')
        self.assertEqual(code, 404)
        self.assertEqual(self.seen, [])

    def test_unreachable_model_server_returns_a_reason(self):
        self.start_gateway()
        self.upstream.shutdown()
        self.upstream.server_close()
        for provider in ('codex', 'anthropic'):
            with self.subTest(provider=provider):
                code, headers, body = self.request('POST', provider)
                self.assertEqual(code, 502)
                self.assert_reason_body(provider, headers, body, 'could not reach the model server')
        self.assertNotIn('REDIRECT_FIXTURE_AUTH', self.log.read_text())

    def test_cross_origin_redirects_never_receive_credentials_or_reach_native_client(self):
        self.start_gateway()
        target = f'http://127.0.0.1:{self.target.server_port}/foreign'
        for provider in ('codex', 'anthropic'):
            for method in ('GET', 'POST'):
                for status in (301, 302, 303, 307, 308):
                    with self.subTest(provider=provider, method=method, status=status):
                        self.foreign.clear()
                        self.routes = {path: (status, [('Location', target)])
                                       for path in ('/models', '/responses', '/v1/messages')}
                        code, headers, body = self.request(method, provider)
                        self.assertFalse(any('Authorization' in row[3] or 'X-Api-Key' in row[3]
                                             for row in self.foreign), 'foreign fixture received native credentials')
                        self.assertEqual(code, 502)
                        self.assertNotIn('Location', headers)
                        self.assert_reason_body(provider, headers, body, 'redirect')
                        self.assertEqual(self.foreign, [])
        self.assertNotIn('REDIRECT_FIXTURE_AUTH', self.log.read_text())
        self.assertNotIn('REDIRECT_FIXTURE_KEY', self.log.read_text())

    @unittest.skipUnless(shutil.which('openssl'), 'Local HTTPS fixture requires openssl')
    def test_https_downgrade_never_receives_native_credentials(self):
        # This trust override applies only to the generated loopback certificate.
        # Cover both opener construction and requests across Python versions.
        with patch.object(ssl, '_create_default_https_context', return_value=ssl._create_unverified_context()):
            self.start_gateway(tls=True)
            target = f'http://127.0.0.1:{self.target.server_port}/foreign'
            self.routes = {'/models': (302, [('Location', target)])}
            code, headers, body = self.request()
        self.assertEqual(code, 502)
        self.assert_reason_body('codex', headers, body, 'redirect')
        self.assertNotIn('Location', headers)
        self.assertEqual([entry[:2] for entry in self.seen], [('GET', '/models')])
        self.assertEqual(self.foreign, [])

    def test_network_path_duplicate_and_unhandled_redirects_cannot_escape(self):
        self.start_gateway()
        foreign = f'//127.0.0.1:{self.target.server_port}/foreign'
        for status, locations in ((302, [('Location', foreign)]),
                                  (307, [('Location', '/same'), ('Location', foreign)]),
                                  (300, [('Location', foreign)]), (308, [('URI', foreign)])):
            with self.subTest(status=status, locations=locations):
                self.routes = {'/responses': (status, locations)}
                code, headers, _ = self.request('POST')
                self.assertEqual(code, 502)
                self.assertNotIn('Location', headers)
                self.assertNotIn('URI', headers)
                self.assertEqual(self.foreign, [])

    def test_same_origin_redirects_preserve_existing_method_and_auth_behavior(self):
        self.start_gateway()
        for method in ('GET', 'POST'):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=status):
                    self.routes = {path: (status, [('Location', self.origin + '/final')])
                                   for path in ('/models', '/responses')}
                    code, headers, body = self.request(method)
                    if method == 'POST' and status in (307, 308):
                        self.assertEqual((code, body), (status, b'redirect response'))
                        self.assertEqual(headers['Location'], self.origin + '/final')
                    else:
                        self.assertEqual((code, body), (200, self.answer))
                        self.assertEqual(self.seen[-1][:3], ('GET', '/final', b''))
                        forwarded = {key.lower(): value for key, value in self.seen[-1][3].items()}
                        self.assertEqual(forwarded['authorization'], 'Bearer REDIRECT_FIXTURE_AUTH')
                        self.assertEqual(forwarded['x-api-key'], 'REDIRECT_FIXTURE_KEY')
        self.routes = {'/models': (302, [('Location', '/final')])}
        self.assertEqual(self.request()[::2], (200, self.answer))

    @unittest.skipUnless(shutil.which('openssl'), 'Local HTTPS fixture requires openssl')
    def test_relayed_redirects_resolve_from_native_loopback_to_validated_https_origin(self):
        # Cover opener construction as well as requests for the loopback certificate.
        with patch.object(ssl, '_create_default_https_context', return_value=ssl._create_unverified_context()):
            self.start_gateway(tls=True)
            authority = urllib.parse.urlsplit(self.origin).netloc
            native_base = f'http://127.0.0.1:{self.gateway.server_port}/codex/responses'
            targets = [(self.origin + '/next', self.origin + '/next'),
                       ('/next', self.origin + '/next'), ('next', self.origin + '/next'),
                       ('//' + authority + '/next', self.origin + '/next'),
                       ('?next=1', self.origin + '/responses?next=1')]
            for status in (300, 307, 308):
                for header in ('Location', 'URI'):
                    for location, expected in targets:
                        with self.subTest(status=status, header=header, location=location):
                            self.routes = {'/responses': (status, [(header, location)])}
                            code, headers, body = self.request('POST')
                            self.assertEqual((code, body), (status, b'redirect response'))
                            resolved = urllib.parse.urljoin(native_base, headers[header])
                            self.assertEqual(resolved, expected)
                            self.assertEqual(urllib.parse.urlsplit(resolved).scheme, 'https')
                            self.assertEqual(self.seen[-1][2], b'{ "input": [] }')
        self.assertEqual(self.foreign, [])

    def test_ambiguous_redirect_authorities_cannot_be_relayed_to_native_client(self):
        self.start_gateway()
        authority = f'127.0.0.1:{self.target.server_port}/foreign'
        for status in (307, 308):
            for prefix in ('\\\\', '/\\', '\\/', '///', '////', 'http:////'):
                with self.subTest(status=status, prefix=prefix):
                    self.routes = {'/responses': (status, [('Location', prefix + authority)])}
                    code, headers, body = self.request('POST')
                    self.assertEqual(code, 502)
                    self.assert_reason_body('codex', headers, body, 'Ghost-ALICE context gateway')
                    self.assertNotIn('Location', headers)
                    self.assertEqual(self.foreign, [])

    def test_same_origin_chain_cannot_redirect_credentials_to_another_origin(self):
        self.start_gateway()
        self.routes = {'/models': (302, [('Location', '/same')]),
                       '/same': (302, [('Location', f'http://127.0.0.1:{self.target.server_port}/foreign')])}
        code, headers, body = self.request()
        self.assertEqual(code, 502)
        self.assert_reason_body('codex', headers, body, 'redirect')
        self.assertNotIn('Location', headers)
        self.assertEqual(self.foreign, [])

    def test_direct_success_and_error_keep_native_request_and_response_bytes(self):
        self.start_gateway()
        for status in (200, 401):
            with self.subTest(status=status):
                self.routes = {'/responses': (status, [])}
                code, headers, body = self.request('POST')
                self.assertEqual((code, body), (status, self.answer))
                self.assertEqual(headers['Content-Type'], 'application/octet-stream')
                self.assertEqual(self.seen[-1][2], b'{ "input": [] }')


class ContextGatewayTests(unittest.TestCase):
    def setUp(self):
        self.core = ContextLifecycleTests()
        self.core.setUp()
        self.addCleanup(self.core.doCleanups)
        self.core.record()
        self.seen = []
        seen = self.seen
        self.answer = b'data: {"type":"response.completed","response":{"id":"response-1","model":"fixture","usage":{"input_tokens":80,"output_tokens":4,"input_tokens_details":{"cached_tokens":30}}}}\n\n'
        answer = self.answer

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.rfile.read(int(self.headers['Content-Length'])), dict(self.headers)))
                self.send_response(200)
                self.send_header('Content-Length', str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *_args):
                pass

        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.wire = Path(self.core.fixture.tmp.name) / 'usage.jsonl'
        self.server = context_gateway.make_server('test-scope', self.wire,
            origins={'codex': f'http://127.0.0.1:{self.upstream.server_port}'},
            selection=lambda request: context_lifecycle.select_request(request, self.core.fixture.root, self.core.home))
        for server in (self.upstream, self.server):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        for server in (self.server, self.upstream):
            server.shutdown()
            server.server_close()

    def request(self, body, scope='test-scope', encoding=None, path='/codex/responses'):
        headers = {'Authorization': 'Bearer PRIVATE_NATIVE_AUTH', 'Content-Type': 'application/json',
                   'X-Ghost-Context-Scope': scope}
        if encoding: headers['Content-Encoding'] = encoding
        return urllib.request.urlopen(urllib.request.Request(
            f'http://127.0.0.1:{self.server.server_port}{path}', data=body, headers=headers))

    def test_selected_input_and_auth_preserved_response_for_all_supported_encodings(self):
        self.assert_selected_encoding_contract([(None, lambda x:x, lambda x:x),
                                                ('gzip', gzip.compress, gzip.decompress)])

    @unittest.skipUnless(zstd is not None, 'zstd requires Python 3.14 compression.zstd')
    def test_selected_zstd_input_and_auth_preserved_response(self):
        self.assert_selected_encoding_contract([('zstd', zstd.compress, zstd.decompress)])

    def assert_selected_encoding_contract(self, encodings):
        body = json.dumps(self.core.request).encode()
        for encoding, encode, decode in encodings:
            with self.request(encode(body), encoding=encoding) as reply:
                self.assertEqual(reply.read(), self.answer)
            actual = json.loads(decode(self.seen[-1][0]))
            self.assertNotIn('consumed navigation body', json.dumps(actual))
            self.assertEqual(actual['input'][2]['output'][1], self.core.output['output'][1])
            headers = {k.lower():v for k,v in self.seen[-1][1].items()}
            self.assertEqual(headers['authorization'], 'Bearer PRIVATE_NATIVE_AUTH')
            self.assertNotIn('x-ghost-context-scope', headers)
        records = [json.loads(line) for line in self.wire.read_text().splitlines()]
        self.assertEqual(len([r for r in records if r['event']=='request-selection']), len(encodings))
        self.assertTrue(any(r.get('usage', {}).get('input_tokens')==80 for r in records))
        self.assertNotIn('PRIVATE_NATIVE_AUTH', self.wire.read_text())
        self.assertNotIn('required long source', self.wire.read_text())

    def test_foreign_scope_forwards_the_request_without_reduction(self):
        body = json.dumps(self.core.request).encode()
        with self.request(body, scope='foreign') as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(self.seen[-1][0], body)
        self.assertNotIn('x-ghost-context-scope', {k.lower() for k in self.seen[-1][1]})
        events = [json.loads(line) for line in self.wire.read_text().splitlines()]
        selection = next(e for e in events if e['event'] == 'request-selection')
        self.assertEqual((selection['state'], selection['reason']), ('unavailable', 'scope-mismatch'))

    def test_nonselection_and_compaction_keep_request_bytes(self):
        body = b'{ "input": [], "prompt_cache_key": "unknown" }'
        for path in ('/codex/responses', '/codex/responses/compact'):
            with self.request(body, path=path) as reply:
                self.assertEqual(reply.read(), self.answer)
            self.assertEqual(self.seen[-1][0], body)

    def test_health_check_makes_no_upstream_request(self):
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/health',
                                         headers={'X-Ghost-Context-Scope':'test-scope'})
        with urllib.request.urlopen(request) as reply:
            self.assertEqual(json.load(reply), {'status':'ready'})
        self.assertEqual(self.seen, [])

    def test_selection_read_failure_forwards_the_original_request(self):
        body = json.dumps(self.core.request).encode()
        with patch.object(context_lifecycle, '_native', side_effect=OSError('fixture read failure')):
            with self.request(body) as reply:
                self.assertEqual(reply.read(), self.answer)
        self.assertEqual(self.seen[-1][0], body)
        events = [json.loads(line) for line in self.wire.read_text().splitlines()]
        selection = next(e for e in events if e['event'] == 'request-selection')
        self.assertEqual((selection['state'], selection['reason']), ('unavailable', 'storage-unavailable'))
        self.assertEqual(selection['forwarded_wire_sha256'], selection['original_wire_sha256'])

    def test_selection_bug_forwards_the_original_request_without_its_message(self):
        body = json.dumps(self.core.request).encode()
        for error in (KeyError('fixture bug'), ValueError('fixture bug')):
            with patch.object(context_lifecycle, '_native', side_effect=error):
                with self.request(body) as reply:
                    self.assertEqual(reply.read(), self.answer)
            self.assertEqual(self.seen[-1][0], body)
        events = [json.loads(line) for line in self.wire.read_text().splitlines()]
        failed = [e for e in events if e['event'] == 'request-selection' and e['state'] == 'error']
        self.assertEqual([(e['reason'], e['error_type']) for e in failed],
                         [('selection-error', 'KeyError'), ('selection-error', 'ValueError')])
        self.assertNotIn('fixture bug', self.wire.read_text())

    def test_preservation_is_observed_without_reencoding(self):
        body = b'{ "input": [], "prompt_cache_key": "unknown" }'
        with self.request(body) as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(self.seen[-1][0], body)
        events = [json.loads(line) for line in self.wire.read_text().splitlines()]
        self.assertTrue(any(e['event'] == 'request-selection' and e['state'] == 'preserved' for e in events))


class ClaudeGatewayTransportTests(unittest.TestCase):
    session = '32b88550-62f3-4d25-b76e-2ca261877b54'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / 'usage.jsonl'
        self.seen, self.dispatched = [], []
        seen = self.seen
        self.answer = b'data: {"type":"message_start","message":{"id":"msg-fixture","model":"fixture","usage":{"input_tokens":7,"output_tokens":1}}}\n\n'
        answer = self.answer

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.rfile.read(int(self.headers['Content-Length'])), dict(self.headers)))
                self.send_response(200)
                self.send_header('Content-Length', str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *_args):
                pass

        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.close_server, self.upstream)

    @staticmethod
    def close_server(server):
        server.shutdown()
        server.server_close()

    def start(self, **kwargs):
        origin = f'http://127.0.0.1:{self.upstream.server_port}'
        self.server = context_gateway.make_server('fixture-scope', self.log,
            origins={'codex': origin, 'anthropic': origin}, **kwargs)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.close_server, self.server)

    def selector(self, request, session_id):
        self.dispatched.append((request, session_id))
        forwarded = dict(request, selected_fixture=True)
        return SimpleNamespace(state='selected', request=forwarded, reason='explicit-fixture-selection',
                               observations=[dict(record=3, block_index=0)])

    def request(self, path='/anthropic/v1/messages?beta=true', body=b'{"messages":[]}', session_id=None,
                encoding=None):
        headers = {'X-Ghost-Context-Scope': 'fixture-scope', 'Authorization': 'Bearer NATIVE_SUBSCRIPTION',
                   'anthropic-beta': 'oauth-2025-04-20,fixture-capability', 'anthropic-version': '2023-06-01'}
        if session_id is not None:
            headers['X-Ghost-Context-Session'] = session_id
        if encoding is not None:
            headers['Content-Encoding'] = encoding
        return urllib.request.urlopen(urllib.request.Request(
            f'http://127.0.0.1:{self.server.server_port}{path}', data=body, headers=headers))

    def test_installed_main_reconciles_current_input_before_forwarding_first_request(self):
        from test_context_lifecycle import ClaudeContextLifecycleTests
        core = ClaudeContextLifecycleTests()
        core.setUp()
        self.addCleanup(core.doCleanups)
        core.fixture.coords['session_id'] = self.session
        core.source['GHOST_ALICE_SESSION_ID'] = self.session
        core.event = core.fixture.intake('continue actual work')
        core.log = core.log.with_name(self.session + '.jsonl')
        for row in core.rows:
            row['sessionId'] = self.session
        core.write_rows()
        core.record([dict(core.choice, disposition='omit')])
        import test_response_route as fixtures
        text = 'Latest native request must retain this exact instruction'
        fixtures.ledger.stage_native_input(root=core.fixture.root, platform='claude',
            session_id=self.session, raw_input=text, prompt_id='first-native-wire', transcript_path=str(core.log))
        row = core.row('user', text, promptId='first-native-wire', origin={'kind':'human'}, turnOrigin='human')
        row['sessionId'] = self.session
        core.rows.append(row)
        with core.log.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        config = Path(self.tmp.name) / 'gateway.json'
        config.write_text(json.dumps(dict(schema_version='context-gateway.v1', port=60000,
            scope='fixture-scope', ledger_root=str(core.fixture.root), codex_dir=str(core.home),
            claude_dir=str(core.home), usage_log=str(self.log))))
        factory = context_gateway.make_server
        def configure(*args, **kwargs):
            kwargs['port'] = 0
            kwargs['origins'] = {k:f'http://127.0.0.1:{self.upstream.server_port}' for k in kwargs['origins']}
            self.server = factory(*args, **kwargs)
            return SimpleNamespace(serve_forever=lambda: None)
        with patch.object(context_gateway, 'make_server', side_effect=configure):
            context_gateway.main(['--config', str(config)])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.close_server, self.server)
        request = dict(core.request(), thinking={'type':'adaptive'})
        headers = {'X-Ghost-Context-Scope':'fixture-scope','X-Ghost-Context-Session':self.session,
            'Authorization':'Bearer LOCAL_FIXTURE_ONLY', 'Anthropic-Beta':'context-management-2025-06-27'}
        native = core.log.read_bytes()
        with urllib.request.urlopen(urllib.request.Request(
            f'http://127.0.0.1:{self.server.server_port}/anthropic/v1/messages',
            data=json.dumps(request).encode(), headers=headers)) as reply:
            self.assertEqual(reply.read(), self.answer)
        forwarded = json.loads(self.seen[-1][1])
        self.assertEqual(forwarded['messages'], request['messages'])
        self.assertEqual(forwarded['context_management']['edits'][-1]['type'], 'clear_tool_uses_20250919')
        self.assertEqual(core.log.read_bytes(), native)
        state = fixtures.ledger.read_session_state(**core.fixture.coords)
        self.assertFalse(state['pending_native_inputs'])
        binding = context_lifecycle.core_api.response_input_binding(core.source, state['latest_input_event_id'])
        self.assertEqual(binding['input_char_count'], len(text))
        self.assertEqual(binding['input_digest'], 'sha256:' + hashlib.sha256(text.encode()).hexdigest())

    def test_default_origins_fix_anthropic_to_official_subscription_upstream(self):
        self.assertEqual(context_gateway.ORIGINS.get('anthropic'), 'https://api.anthropic.com')
        self.assertEqual(context_gateway.ORIGINS['codex'], 'https://chatgpt.com/backend-api/codex')

    def test_native_beta_headers_reach_server_selector_without_new_capability(self):
        import inspect
        self.assertIn('server_selectors', inspect.signature(context_gateway.make_server).parameters)
        observed = []
        def select(request, session, betas):
            observed.append((session, betas))
            return SimpleNamespace(state='preserved', request=request, reason='native-capability-observed', observations=())
        self.start(selectors={'claude': self.selector}, server_selectors={'claude': select})
        with self.request(session_id=self.session) as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(observed, [(self.session, ('oauth-2025-04-20', 'fixture-capability'))])
        self.assertEqual(self.dispatched, [])
        self.assertEqual({k.lower(): v for k,v in self.seen[0][2].items()}['anthropic-beta'],
                         'oauth-2025-04-20,fixture-capability')

    def test_repeated_native_beta_headers_remain_available_to_upstream(self):
        observed = []
        def select(request, session, betas):
            observed.append(betas)
            return SimpleNamespace(state='preserved', request=request, reason='observed', observations=())
        self.start(server_selectors={'claude': select})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        self.addCleanup(connection.close)
        connection.putrequest('POST', '/anthropic/v1/messages')
        for key, value in [('X-Ghost-Context-Scope', 'fixture-scope'), ('X-Ghost-Context-Session', self.session),
                           ('Anthropic-Beta', 'context-management-2025-06-27'),
                           ('Anthropic-Beta', 'oauth-2025-04-20'), ('Content-Length', '2')]:
            connection.putheader(key, value)
        connection.endheaders(b'{}')
        response = connection.getresponse(); response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(observed, [('context-management-2025-06-27', 'oauth-2025-04-20')])
        forwarded = {k.lower(): v for k,v in self.seen[-1][2].items()}['anthropic-beta']
        self.assertEqual(forwarded, 'context-management-2025-06-27,oauth-2025-04-20')

    def test_explicit_claude_dispatch_uses_session_and_preserves_native_auth(self):
        self.start(selectors={'claude': self.selector})
        with self.request(session_id=self.session) as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(self.dispatched, [({'messages': []}, self.session)])
        path, body, raw_headers = self.seen[0]
        self.assertEqual(path, '/v1/messages?beta=true')
        self.assertTrue(json.loads(body)['selected_fixture'])
        headers = {key.lower(): value for key, value in raw_headers.items()}
        self.assertEqual(headers['authorization'], 'Bearer NATIVE_SUBSCRIPTION')
        self.assertEqual(headers['anthropic-beta'], 'oauth-2025-04-20,fixture-capability')
        self.assertEqual(headers['anthropic-version'], '2023-06-01')
        self.assertNotIn('x-ghost-context-scope', headers)
        self.assertNotIn('x-ghost-context-session', headers)
        self.assertNotIn('NATIVE_SUBSCRIPTION', self.log.read_text())
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(row.get('platform') == 'claude' and row.get('native_session_id') == self.session
                            and row['event'] == 'request-selection' for row in records))

    def test_legacy_codex_callback_coexists_without_new_arguments(self):
        legacy = []
        def select(request):
            legacy.append(request)
            return SimpleNamespace(state='preserved', request=request, reason='fixture-preserved', observations=())
        self.start(selection=select, selectors={'claude': self.selector})
        codex_body = b'{ "input": [] }'
        with self.request('/codex/responses', codex_body, session_id=self.session) as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(legacy, [{'input': []}])
        self.assertEqual(self.seen[-1][1], codex_body)
        self.assertNotIn('x-ghost-context-session', {key.lower() for key in self.seen[-1][2]})
        with self.request(session_id=self.session):
            pass
        self.assertEqual(len(self.dispatched), 1)

    def test_anthropic_token_count_uses_the_same_explicit_selection(self):
        self.start(selectors={'claude': self.selector})
        with self.request('/anthropic/v1/messages/count_tokens', session_id=self.session):
            pass
        self.assertEqual(self.seen[-1][0], '/v1/messages/count_tokens')
        self.assertTrue(json.loads(self.seen[-1][1])['selected_fixture'])
        self.assertEqual(self.dispatched[-1][1], self.session)

    def test_claude_selected_gzip_body_is_forwarded_in_original_encoding(self):
        self.start(selectors={'claude': self.selector})
        with self.request(body=gzip.compress(b'{"messages":[]}'), session_id=self.session, encoding='gzip'):
            pass
        self.assertTrue(json.loads(gzip.decompress(self.seen[0][1]))['selected_fixture'])
        self.assertEqual({key.lower(): value for key, value in self.seen[0][2].items()}['content-encoding'], 'gzip')

    def test_missing_or_invalid_declared_session_forwards_without_selection(self):
        self.start(selectors={'claude': self.selector})
        for session in (None, '', 'not-a-native-uuid'):
            with self.subTest(session=session), self.request(session_id=session) as reply:
                self.assertEqual(reply.read(), self.answer)
                self.assertEqual(self.seen[-1][1], b'{"messages":[]}')
        self.assertEqual(self.dispatched, [])
        events = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([(e['state'], e['reason']) for e in events if e['event'] == 'request-selection'],
                         [('unavailable', 'native-session-unavailable')] * 3)

    def test_duplicate_declared_session_headers_forward_without_selection(self):
        self.start(selectors={'claude': self.selector})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        self.addCleanup(connection.close)
        connection.putrequest('POST', '/anthropic/v1/messages')
        connection.putheader('X-Ghost-Context-Scope', 'fixture-scope')
        connection.putheader('X-Ghost-Context-Session', self.session)
        connection.putheader('X-Ghost-Context-Session', self.session)
        connection.putheader('Content-Length', '2')
        connection.endheaders(b'{}')
        reply = connection.getresponse()
        self.assertEqual((reply.status, reply.read()), (200, self.answer))
        self.assertEqual(self.seen[-1][1], b'{}')
        self.assertEqual(self.dispatched, [])

    def test_unavailable_claude_selection_forwards_the_original_request(self):
        def unavailable(request, session):
            return SimpleNamespace(state='unavailable', reason='fixture-source-unavailable',
                                   request=dict(request, selected_fixture=True), observations=())
        self.start(selectors={'claude': unavailable})
        with self.request(session_id=self.session) as reply:
            self.assertEqual(reply.read(), self.answer)
        self.assertEqual(self.seen[-1][1], b'{"messages":[]}')
        events = [json.loads(line) for line in self.log.read_text().splitlines()]
        selection = next(e for e in events if e['event'] == 'request-selection')
        self.assertEqual((selection['state'], selection['reason']), ('unavailable', 'fixture-source-unavailable'))

    def test_dispatcher_rejects_unknown_platform_noncallable_and_overlap(self):
        for kwargs in (dict(selectors={'unknown': self.selector}), dict(selectors={'claude': 'not-callable'}),
                       dict(selection=lambda request: request, selectors={'codex': self.selector})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                context_gateway.make_server('fixture-scope', self.log, **kwargs)

    def configuration(self, claude=True):
        config = dict(schema_version='context-gateway.v1', port=12345, scope='fixture-scope',
                      ledger_root=str(Path(self.tmp.name) / 'ledger'), codex_dir=str(Path(self.tmp.name) / 'codex'),
                      usage_log=str(self.log))
        if claude:
            config['claude_dir'] = str(Path(self.tmp.name) / 'claude')
        path = Path(self.tmp.name) / 'config.json'
        path.write_text(json.dumps(config))
        return config, path

    def test_main_routes_explicit_claude_home_and_session_to_parent_selector(self):
        config, path = self.configuration()
        with patch.object(context_lifecycle, 'select_request') as select, patch.object(context_gateway, 'make_server') as make:
            context_gateway.main(['--config', str(path)])
            selectors = make.call_args.kwargs['selectors']
            self.assertEqual(set(selectors), {'codex', 'claude'})
            selectors['claude']({'messages': []}, self.session)
            select.assert_called_once_with({'messages': []}, config['ledger_root'], config['claude_dir'],
                                           platform='claude', session_id=self.session)
            self.assertEqual(make.call_args.kwargs['origins']['anthropic'], 'https://api.anthropic.com')

    def test_main_without_explicit_claude_home_retains_codex_only(self):
        config, path = self.configuration(claude=False)
        with patch.object(context_lifecycle, 'select_request') as select, patch.object(context_gateway, 'make_server') as make:
            context_gateway.main(['--config', str(path)])
            self.assertEqual(set(make.call_args.kwargs['selectors']), {'codex'})
            self.assertNotIn('anthropic', make.call_args.kwargs['origins'])
            make.call_args.kwargs['selectors']['codex']({'input': []}, None)
            select.assert_called_once_with({'input': []}, config['ledger_root'], config['codex_dir'])

    def test_main_ignores_strict_log_root_from_an_existing_installation(self):
        config, path = self.configuration()
        config['strict_log_root'] = str(Path(self.tmp.name) / 'strict')
        path.write_text(json.dumps(config))
        with patch.object(context_lifecycle, 'select_request') as select, patch.object(context_gateway, 'make_server') as make:
            context_gateway.main(['--config', str(path)])
            make.call_args.kwargs['selectors']['codex']({'input': []}, None)
            select.assert_called_once_with({'input': []}, config['ledger_root'], config['codex_dir'])

    def test_main_rejects_nonexplicit_claude_coordinates_before_server_start(self):
        config, path = self.configuration()
        for value in ('relative-home', '', None):
            with self.subTest(value=value), patch.object(context_gateway, 'make_server') as make:
                path.write_text(json.dumps(dict(config, claude_dir=value)))
                with self.assertRaises(ValueError):
                    context_gateway.main(['--config', str(path)])
                make.assert_not_called()


class ServerUsageParserTests(unittest.TestCase):
    def test_server_applied_context_edits_are_observed_separately_from_request(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        parser.observe(dict(type='message_start', message=dict(id='msg-fixture', usage={'input_tokens': 4})))
        policy = {'applied_edits': [{'type': 'clear_tool_uses_20250919', 'cleared_tool_uses': 1,
                                    'cleared_input_tokens': 700}]}
        observed = parser.observe(dict(type='message_delta', usage={'output_tokens': 8}, context_management=policy))
        self.assertEqual(observed.get('context_management'), policy)
        self.assertEqual(observed['server_id'], 'msg-fixture')

    def setUp(self):
        self.assertTrue(callable(getattr(context_gateway, 'UsageSnapshots', None)),
                        'Request-local cumulative usage parsing is missing')
        self.assertTrue(callable(getattr(context_gateway, 'forward_stream', None)),
                        'The production response-forwarding path must be memory-testable')
        self.start_usage = dict(input_tokens=17, cache_read_input_tokens=4096,
                                cache_creation_input_tokens=256, output_tokens=1,
                                cache_creation={'ephemeral_5m_input_tokens': 64,
                                                'ephemeral_1h_input_tokens': 192})
        self.start = dict(type='message_start', message=dict(id='msg-fixture', model='fixture-model',
                                                            usage=self.start_usage))
        self.delta = dict(type='message_delta', usage={'output_tokens': 9})

    def test_anthropic_start_and_delta_preserve_raw_fields_in_final_snapshot(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        start = parser.observe(self.start)
        final = parser.observe(self.delta)
        self.assertEqual(start['server_id'], 'msg-fixture')
        self.assertEqual(final['server_id'], start['server_id'])
        self.assertEqual(final['model'], 'fixture-model')
        self.assertEqual(start['raw_usage'], self.start_usage)
        self.assertEqual(final['raw_usage'], {'output_tokens': 9})
        self.assertEqual(start['usage'], self.start_usage)
        self.assertEqual(final['usage'], dict(self.start_usage, output_tokens=9))
        self.assertEqual(final['server_event'], 'message_delta')

    def test_missing_usage_fields_are_not_filled_with_zero(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        start = parser.observe(dict(type='message_start', message=dict(id='msg-small', usage={'input_tokens': 4})))
        final = parser.observe(dict(type='message_delta', usage={'output_tokens': 2}))
        self.assertEqual(start['usage'], {'input_tokens': 4})
        self.assertEqual(final['usage'], {'input_tokens': 4, 'output_tokens': 2})
        partial = context_gateway.UsageSnapshots('anthropic').observe(self.delta)
        self.assertEqual(partial['usage'], {'output_tokens': 9})
        self.assertIsNone(partial['server_id'])

    def test_reported_zero_is_preserved_and_cumulative_deltas_are_not_added(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        parser.observe(dict(type='message_start', message=dict(id='msg-zero', usage={'cache_read_input_tokens': 0})))
        parser.observe(dict(type='message_delta', usage={'output_tokens': 3}))
        final = parser.observe(dict(type='message_delta', usage={'output_tokens': 9}))
        repeated = parser.observe(dict(type='message_delta', usage={'output_tokens': 9}))
        self.assertEqual(final['usage'], {'cache_read_input_tokens': 0, 'output_tokens': 9})
        self.assertEqual(repeated['usage'], final['usage'])

    def test_new_server_message_id_resets_prior_snapshot_and_model(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        parser.observe(self.start)
        parser.observe(self.delta)
        next_message = parser.observe(dict(type='message_start', message=dict(id='msg-new', usage={'input_tokens': 6})))
        self.assertEqual(next_message['server_id'], 'msg-new')
        self.assertIsNone(next_message['model'])
        self.assertEqual(next_message['usage'], {'input_tokens': 6})

    def test_nested_native_usage_merges_only_observed_fields(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        parser.observe(self.start)
        final = parser.observe(dict(type='message_delta', usage={'cache_creation': {'ephemeral_5m_input_tokens': 80}}))
        expected = copy.deepcopy(self.start_usage)
        expected['cache_creation']['ephemeral_5m_input_tokens'] = 80
        self.assertEqual(final['usage'], expected)
        self.assertEqual(final['raw_usage'], {'cache_creation': {'ephemeral_5m_input_tokens': 80}})

    def test_codex_response_id_model_and_usage_are_preserved(self):
        parser = context_gateway.UsageSnapshots('codex')
        self.assertIsNone(parser.observe(dict(type='response.created', response=dict(id='resp-fixture', model='fixture'))))
        native = dict(input_tokens=80, output_tokens=4, input_tokens_details={'cached_tokens': 30})
        final = parser.observe(dict(type='response.completed', response=dict(id='resp-fixture', usage=native)))
        self.assertEqual(final['server_id'], 'resp-fixture')
        self.assertEqual(final['model'], 'fixture')
        self.assertEqual(final['usage'], native)
        self.assertEqual(final['raw_usage'], native)

    def test_parser_does_not_mutate_events_or_prior_observations(self):
        parser = context_gateway.UsageSnapshots('anthropic')
        original = copy.deepcopy(self.start)
        observed = parser.observe(self.start)
        parser.observe(self.delta)
        self.assertEqual(self.start, original)
        self.assertEqual(observed['usage'], self.start_usage)
        self.start['message']['usage']['input_tokens'] = 999
        self.assertEqual(observed['raw_usage']['input_tokens'], 17)

    def stream(self, raw, headers=None):
        class MemoryUpstream:
            def __init__(self):
                self.headers = headers or {}
                self.chunks = [raw[:31], raw[31:77], raw[77:]]

            def read1(self, size):
                return self.chunks.pop(0) if self.chunks else b''
        sink, rows = io.BytesIO(), []
        details = context_gateway.forward_stream(MemoryUpstream(), sink, 'anthropic', rows.append)
        return sink.getvalue(), rows, details

    def test_real_forwarding_helper_preserves_bytes_and_final_native_usage(self):
        raw = b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n' for event in (self.start, self.delta))
        forwarded, rows, details = self.stream(raw)
        self.assertEqual(forwarded, raw)
        self.assertEqual(details['wire_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(details['sse_events_observed'], 2)
        self.assertTrue(details['usage_stream_observable'])
        self.assertEqual([row['server_id'] for row in rows], ['msg-fixture', 'msg-fixture'])
        self.assertEqual(rows[-1]['usage'], dict(self.start_usage, output_tokens=9))

    def test_gzip_usage_and_applied_edits_are_observed_without_changing_wire(self):
        policy = {'applied_edits': [{'type': 'clear_tool_uses_20250919',
                                   'cleared_tool_uses': 1, 'cleared_input_tokens': 71}]}
        delta = dict(self.delta, context_management=policy)
        raw = gzip.compress(b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n'
                                    for event in (self.start, delta)))
        forwarded, rows, details = self.stream(raw, {'Content-Encoding': 'gzip'})
        self.assertEqual(forwarded, raw)
        self.assertEqual(details['wire_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(details['usage_stream_observable'])
        self.assertEqual(rows[-1]['context_management'], policy)
        self.assertEqual(rows[-1]['usage'], dict(self.start_usage, output_tokens=9))

    def test_zstd_usage_is_observed_without_changing_wire(self):
        try:
            from compression import zstd
        except ImportError:
            self.skipTest('The installed gateway requires Python 3.14 for zstd')
        raw = zstd.compress(b'data: ' + json.dumps(self.start).encode() + b'\n\n')
        forwarded, rows, details = self.stream(raw, {'Content-Encoding': 'zstd'})
        self.assertEqual(forwarded, raw)
        self.assertTrue(details['usage_stream_observable'])
        self.assertEqual(rows[-1]['usage'], self.start_usage)

    def test_unknown_encoding_preserves_bytes_and_usage_stays_unknown(self):
        raw = gzip.compress(b'data: ' + json.dumps(self.start).encode() + b'\n\n')
        forwarded, rows, details = self.stream(raw, {'Content-Encoding': 'unknown'})
        self.assertEqual(forwarded, raw)
        self.assertEqual(rows, [])
        self.assertFalse(details['usage_stream_observable'])

    def test_corrupt_compression_cannot_interrupt_native_output(self):
        raw = b'not a valid gzip frame' * 20
        forwarded, rows, details = self.stream(raw, {'Content-Encoding': 'gzip'})
        self.assertEqual(forwarded, raw)
        self.assertEqual(rows, [])
        self.assertFalse(details['usage_stream_observable'])

    def test_compressed_frames_observe_final_edits_independent_of_chunk_boundaries(self):
        self.assert_compressed_frame_contract('gzip', gzip.compress)

    @unittest.skipUnless(zstd is not None, 'zstd requires Python 3.14 compression.zstd')
    def test_zstd_frames_observe_final_edits_independent_of_chunk_boundaries(self):
        self.assert_compressed_frame_contract('zstd', zstd.compress)

    def assert_compressed_frame_contract(self, encoding, compress):
        policy = {'applied_edits': [{'type': 'clear_tool_uses_20250919', 'cleared_tool_uses': 1}]}
        delta = dict(self.delta, context_management=policy)
        raw = b''.join(compress(b'data: ' + json.dumps(event).encode() + b'\n\n')
                       for event in (self.start, delta))
        for size in (1, len(raw)):
            class Upstream:
                headers = {'Content-Encoding': encoding}
                def __init__(self):
                    self.body = io.BytesIO(raw)
                def read1(self, maximum):
                    return self.body.read(size)
            with self.subTest(encoding=encoding, size=size):
                sink, rows = io.BytesIO(), []
                details = context_gateway.forward_stream(Upstream(), sink, 'anthropic', rows.append)
                self.assertEqual(sink.getvalue(), raw)
                self.assertTrue(details['usage_stream_observable'])
                self.assertEqual(rows[-1]['usage']['output_tokens'], 9)
                self.assertEqual(rows[-1]['context_management'], policy)
        forwarded, _, details = self.stream(raw + b'invalid trailing frame', {'Content-Encoding': encoding})
        self.assertEqual(forwarded, raw + b'invalid trailing frame')
        self.assertFalse(details['usage_stream_observable'])

    def test_unknown_nonobject_sse_events_cannot_interrupt_byte_forwarding(self):
        raw = b'data: [1, 2]\n\ndata: null\n\n' + b'data: ' + json.dumps(self.start).encode() + b'\n\n'
        forwarded, rows, details = self.stream(raw)
        self.assertEqual(forwarded, raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(details['sse_events_observed'], 3)


if __name__ == '__main__':
    unittest.main()
