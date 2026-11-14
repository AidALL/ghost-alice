"""The same Codex configuration bytes retain their values across readers and edits."""
import io
import json
from pathlib import Path
import tempfile
import unittest

import codex_config_toml as syntax
import context_gateway_plan as gateway
import install_doctor
import runtime_config
from test_install_transaction_publish import PublishCase


ROOT = Path(__file__).resolve().parents[1]
TOML_11_CONFIG = '# user comment\nmodel = "native"\nmetadata = {\n label = "\\e\\x41",\n}\n'


def package_files(package):
    return {path.relative_to(package): path.read_bytes() for path in package.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}


class CodexConfigSyntaxTests(unittest.TestCase):
    def test_text_and_binary_load_preserve_toml_11_values_and_one_initial_bom(self):
        for label, text, expected in (
            ('plain', '# user comment\nmodel = "native"\n', {'model': 'native'}),
            ('bom', '\ufeff# user comment\nmodel = "native"\n', {'model': 'native'}),
            ('inline-table', 'metadata = {\n label = "retained",\n}\n', {'metadata': {'label': 'retained'}}),
            ('escape-e', 'metadata = "\\e"\n', {'metadata': '\x1b'}),
            ('escape-x', 'metadata = "\\x41"\n', {'metadata': 'A'}),
        ):
            with self.subTest(label=label):
                self.assertEqual(syntax.loads(text), expected)
                self.assertEqual(syntax.load(io.BytesIO(text.encode())), expected)
        self.assertEqual(syntax.loads(''), {})
        self.assertEqual(syntax.load(io.BytesIO(b'')), {})

    def test_invalid_syntax_and_invalid_utf8_stay_errors(self):
        for text in ('[broken', 'a=1\na=2\n', 'a=1\n\ufeffb=2\n', '\ufeff\ufeffa=1\n'):
            with self.subTest(text=text):
                with self.assertRaises(syntax.TOMLDecodeError):
                    syntax.loads(text)
                with self.assertRaises(syntax.TOMLDecodeError):
                    syntax.load(io.BytesIO(text.encode()))
        with self.assertRaises(UnicodeDecodeError):
            syntax.load(io.BytesIO(b'key="\xff"'))

    def test_runtime_and_doctor_read_bom_and_toml_11_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / 'node'
            node.write_text('synthetic executable', encoding='utf-8')
            node.chmod(0o700)
            config = root / 'config.toml'
            env = {'HOME': directory, 'USERPROFILE': directory, 'CODEX_HOME': directory}
            text = ('\ufeff' + TOML_11_CONFIG + '[mcp_servers.node_repl.env]\nNODE_REPL_NODE_PATH = '
                    + json.dumps(node.as_posix()) + '\n')
            config.write_bytes(text.encode())
            self.assertEqual(runtime_config._codex_configured_node_runtime_paths(env), [node.resolve()])
            self.assertEqual(install_doctor._configured_node_runtime_from_codex_config(config), node.resolve())
            self.assertEqual(config.read_bytes(), text.encode())
            for raw in (b'[broken', b'key="\xff"'):
                with self.subTest(raw=raw):
                    config.write_bytes(raw)
                    self.assertEqual(runtime_config._codex_configured_node_runtime_paths(env), [])
                    self.assertIsNone(install_doctor._configured_node_runtime_from_codex_config(config))
                    self.assertEqual(config.read_bytes(), raw)

    def test_gateway_edit_rerender_and_owned_removal_preserve_original_bytes(self):
        for bom in ('', '\ufeff'):
            with self.subTest(bom=bool(bom)):
                text = bom + TOML_11_CONFIG
                rendered = gateway.render_provider(text, 23456, 'scope-one')
                updated = gateway.render_provider(rendered, 34567, 'scope-two')
                for value, port, scope in ((rendered, 23456, 'scope-one'), (updated, 34567, 'scope-two')):
                    parsed = syntax.loads(value)
                    self.assertEqual(parsed['model_provider'], gateway.PROVIDER)
                    provider = parsed['model_providers'][gateway.PROVIDER]
                    self.assertEqual(provider['base_url'], f'http://127.0.0.1:{port}/codex')
                    self.assertEqual(provider['http_headers'], {'X-Ghost-Context-Scope': scope})
                    self.assertEqual(parsed['model'], 'native')
                    self.assertEqual(parsed['metadata'], {'label': '\x1bA'})
                    self.assertEqual(value.count(gateway.TOP_BEGIN), 1)
                    self.assertEqual(value.count(gateway.TABLE_BEGIN), 1)
                    self.assertEqual(value.count('\ufeff'), int(text.startswith('\ufeff')))
                    self.assertEqual(value.startswith('\ufeff'), text.startswith('\ufeff'))
                    self.assertEqual(gateway._owned_provider_removal(value, {'port': port, 'scope': scope}), text)

    def test_gateway_does_not_strip_a_second_initial_bom(self):
        with self.assertRaises(ValueError):
            gateway.render_provider('\ufeff\ufeffmodel="native"', 23456, 'scope')


class CodexSyntaxDistributionTests(PublishCase):
    def test_skill_and_runtime_publish_include_parser_provenance_and_license_bytes(self):
        import install_transaction
        runtime = self.ghost / 'runtime/_shared'
        scanner = self.codex / 'agent-security-scan'
        install_transaction.publish(self.request('codex',
            self.target('_shared', runtime, 'copy', kind='runtime', source=ROOT / '_shared'),
            self.target('agent-security-scan', scanner, 'copy', source=ROOT / 'agent-security-scan')))
        expected = package_files(ROOT / '_shared/codex_config_toml')
        self.assertEqual(package_files(runtime / 'codex_config_toml'), expected)
        self.assertEqual(package_files(scanner / 'scripts/codex_config_toml'), expected)


if __name__ == '__main__':
    unittest.main()
