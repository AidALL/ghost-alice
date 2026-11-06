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


if __name__ == '__main__':
    unittest.main()
