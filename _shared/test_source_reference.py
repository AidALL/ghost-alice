"""source_reference: the one canonical JSON form, text and JSON digests, and the staged-input comparison."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import source_reference


class CanonicalFormAndDigestTest(unittest.TestCase):
    def test_canonical_json_sorts_keys_and_keeps_non_ascii_without_spaces(self):
        text = source_reference.canonical_json({'b': [1, 2], 'a': '한글'})
        self.assertEqual(text, '{"a":"한글","b":[1,2]}')

    def test_text_digest_hashes_utf8_text_or_bytes_as_given(self):
        expected = 'sha256:' + hashlib.sha256('한글 text'.encode('utf-8')).hexdigest()
        self.assertEqual(source_reference.text_digest('한글 text'), expected)
        self.assertEqual(source_reference.text_digest('한글 text'.encode('utf-8')), expected)

    def test_json_digest_is_the_text_digest_of_the_canonical_form(self):
        value = {'z': 1, 'a': {'y': None, 'x': [True, 'é']}}
        self.assertEqual(source_reference.json_digest(value),
                         source_reference.text_digest(source_reference.canonical_json(value)))
        self.assertEqual(source_reference.json_digest({'a': 1, 'b': 2}), source_reference.json_digest({'b': 2, 'a': 1}))

    def test_source_reference_identity_is_the_json_digest_of_its_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            reference = source_reference.reference_source(str(path))
        fields = {k: reference[k] for k in ('storage', 'locator', 'digest', 'byte_length', 'appendable')}
        self.assertEqual(reference['source_id'], source_reference.json_digest(fields))
        self.assertEqual(reference['digest'], source_reference.text_digest(b'original bytes'))


class MatchesInputTest(unittest.TestCase):
    def binding(self, text):
        return {'input_char_count': len(text), 'input_digest': source_reference.text_digest(text)}

    def test_exact_text_matches_its_staged_count_and_digest(self):
        self.assertTrue(source_reference.matches_input('run the tests', self.binding('run the tests')))

    def test_changed_text_wrong_count_or_malformed_binding_never_matches(self):
        binding = self.binding('run the tests')
        self.assertFalse(source_reference.matches_input('run the test', binding))
        self.assertFalse(source_reference.matches_input('run the tests ', binding))
        self.assertFalse(source_reference.matches_input(['run the tests'], binding))
        self.assertFalse(source_reference.matches_input('run the tests', dict(binding, input_char_count='13')))
        self.assertFalse(source_reference.matches_input('run the tests', dict(binding, input_digest=None)))
        self.assertFalse(source_reference.matches_input('run the tests', None))

    def test_text_that_utf8_cannot_encode_is_a_mismatch_not_an_error(self):
        text = 'lone \udcff surrogate'
        self.assertFalse(source_reference.matches_input(text, {'input_char_count': len(text), 'input_digest': 'sha256:0'}))


class SourcePrefixReferenceTest(unittest.TestCase):
    def test_prefix_reference_keeps_the_snapshot_locator_length_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '한글.jsonl'
            prefix = 'first 한글 row\n'.encode('utf-8')
            path.write_bytes(prefix + b'later row\n')
            expected = dict(storage='source-reference.v1', locator=str(path),
                            digest='sha256:' + hashlib.sha256(prefix).hexdigest(),
                            byte_length=len(prefix), appendable=True)
            expected_id = 'sha256:' + hashlib.sha256(json.dumps(
                expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
            # Capture metadata still refers to the supplied snapshot when the
            # host file changes or disappears before construction.
            path.unlink()
            self.assertEqual(source_reference.reference_source_prefix(str(path), len(prefix), expected['digest']),
                             dict(expected, source_id=expected_id))

    def test_public_validator_preserves_extra_fields_only_when_exact_is_false(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            reference = source_reference.reference_source(str(path))
            self.assertIsNone(source_reference.validate_source_reference(reference, exact=True))
            extended = dict(reference, note='caller metadata')
            self.assertIsNone(source_reference.validate_source_reference(extended))
            self.assertIsNone(source_reference.verify_source(extended))
            with self.assertRaises(source_reference.SourceReferenceError):
                source_reference.validate_source_reference(extended, exact=True)

    def test_source_bytes_returns_the_authenticated_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            raw = b'original \xff bytes\n'
            path.write_bytes(raw)
            reference = source_reference.reference_source_prefix(
                str(path), len(raw), 'sha256:' + hashlib.sha256(raw).hexdigest())
            identity = path.stat()
            with path.open('ab') as stream:
                stream.write(b'later appended bytes\n')
            self.assertEqual(source_reference.source_bytes(reference,
                identity=dict(device=identity.st_dev, inode=identity.st_ino)), raw)
            self.assertEqual(source_reference.source_text(reference), raw.decode('utf-8', errors='replace'))
            self.assertIsNone(source_reference.verify_source(reference))

    def test_empty_prefix_allows_later_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'later bytes')
            reference = source_reference.reference_source_prefix(
                str(path), 0, 'sha256:' + hashlib.sha256(b'').hexdigest())
            self.assertEqual(source_reference.source_bytes(reference), b'')
            self.assertEqual(source_reference.source_text(reference), '')
            self.assertIsNone(source_reference.verify_source(reference))

    def test_changed_or_truncated_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            original = b'original bytes\n'
            reference = source_reference.reference_source_prefix(
                str(path), len(original), 'sha256:' + hashlib.sha256(original).hexdigest())
            for changed in (b'changed! bytes\n', original[:-1]):
                path.write_bytes(changed)
                for reader in (source_reference.source_bytes, source_reference.source_text,
                               source_reference.verify_source):
                    with self.subTest(changed=changed, reader=reader.__name__):
                        with self.assertRaises(source_reference.SourceReferenceError):
                            reader(reference)

    def test_full_source_still_requires_eof_unless_appendable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            fixed = source_reference.reference_source(str(path))
            appendable = source_reference.reference_source(str(path), appendable=True)
            self.assertEqual(source_reference.source_bytes(fixed), b'original bytes')
            with path.open('ab') as stream:
                stream.write(b' appended bytes')
            self.assertEqual(source_reference.source_bytes(appendable), b'original bytes')
            for reader in (source_reference.source_bytes, source_reference.source_text,
                           source_reference.verify_source):
                with self.subTest(reader=reader.__name__):
                    with self.assertRaises(source_reference.SourceReferenceError):
                        reader(fixed)

    def test_identity_checks_both_device_and_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            reference = source_reference.reference_source(str(path))
            stat = path.stat()
            expected = dict(device=stat.st_dev, inode=stat.st_ino)
            for key in ('device', 'inode'):
                with self.subTest(key=key):
                    with self.assertRaises(source_reference.SourceReferenceError):
                        source_reference.source_bytes(reference, identity=dict(expected, **{key: expected[key] + 1}))

    def test_replacement_with_equal_bytes_only_passes_without_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            reference = source_reference.reference_source(str(path))
            original_stat = path.stat()
            replacement = Path(directory) / 'replacement.txt'
            replacement.write_bytes(b'original bytes')
            self.assertNotEqual(original_stat.st_ino, replacement.stat().st_ino)
            replacement.replace(path)
            self.assertEqual(source_reference.source_bytes(reference), b'original bytes')
            with self.assertRaises(source_reference.SourceReferenceError):
                source_reference.source_bytes(reference,
                    identity=dict(device=original_stat.st_dev, inode=original_stat.st_ino))

    def test_public_validator_rejects_invalid_shape_or_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.txt'
            path.write_bytes(b'original bytes')
            reference = source_reference.reference_source(str(path))
            invalid = [None, [], {}, {k: v for k, v in reference.items() if k != 'source_id'},
                       dict(reference, locator='relative.txt'), dict(reference, byte_length=True),
                       dict(reference, byte_length=-1), dict(reference, appendable=1),
                       dict(reference, storage='other-format'), dict(reference, digest='sha256:changed')]
            for value in invalid:
                with self.subTest(reference=value):
                    with self.assertRaises(source_reference.SourceReferenceError):
                        source_reference.validate_source_reference(value)


if __name__ == '__main__':
    unittest.main()
