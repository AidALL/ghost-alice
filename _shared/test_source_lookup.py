import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import source_lookup

class SourceLookupTests(unittest.TestCase):
    def test_large_original_is_not_returned_by_default_and_selection_is_exact(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.jsonl'
            p.write_text(json.dumps({'body': 'x'*100000, 'status': 'unresolved'})+'\n')
            before=p.read_bytes()
            result=source_lookup.lookup(p)
            self.assertNotIn('x'*100, json.dumps(result))
            result=source_lookup.lookup(p, record=1, pointer='/status')
            self.assertEqual(result['value'], 'unresolved')
            result=source_lookup.lookup(p, record=1, pointer='/body')
            self.assertEqual(result['value'], 'x'*100000)
            self.assertTrue(result['complete'])
            self.assertEqual(p.read_bytes(),before)
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()),['source.jsonl'])

    def test_literal_search_and_long_line_pagination_preserve_negative_result(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.txt';p.write_text('irrelevant\nNOT VERIFIED: '+ '가'*1000+'\n')
            result=source_lookup.lookup(p, query='NOT VERIFIED')
            self.assertNotIn('text', result['matches'][0])
            self.assertEqual(result['matches'][0]['line'],2)
            page=source_lookup.lookup(p, line=2, offset=0, length=80)
            self.assertTrue(page['text'].startswith('NOT VERIFIED:'))
            self.assertEqual(page['next_offset'],80)
            remainder=source_lookup.lookup(p,line=2,offset=80)
            self.assertTrue(remainder['complete'])
            self.assertEqual(page['text']+remainder['text'],p.read_text().splitlines()[1])

    def test_json_pointer_escape_and_missing_field(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.json';p.write_text(json.dumps({'a/b':{'~': [False]}}))
            self.assertEqual(source_lookup.lookup(p,pointer='/a~1b/~0/0')['value'],False)
            with self.assertRaises((ValueError, KeyError)):
                source_lookup.lookup(p,pointer='/missing')

    def test_search_returns_original_offsets_after_casefold_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.txt'
            for text, query, offset, excerpt in [
                ('ß TARGET', 'target', 2, 'TARGET'),
                ('İ ß TARGET', 'target', 4, 'TARGET'),
                ('a Straße', 'STRASSE', 2, 'Straße'),
                ('a ß z', 's', 2, 'ß'),
                ('ordinary TARGET', 'target', 9, 'TARGET'),
            ]:
                with self.subTest(text=text, query=query):
                    path.write_text('unrelated\n' + text + '\n', encoding='utf-8')
                    result = source_lookup.lookup(path, query=query)
                    self.assertEqual(result['matches'], [dict(line=2, offset=offset)])
                    match = result['matches'][0]
                    self.assertEqual(source_lookup.lookup(path, **match, length=len(excerpt))['text'], excerpt)


    def test_json_pointer_array_indices_follow_pointer_syntax_without_restricting_object_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.json'
            malformed = ['-1', '01', '+1', ' 1', '1 ', '١', '-']
            value = dict(array=list(range(11)), object={key: key for key in malformed})
            path.write_text(json.dumps(value), encoding='utf-8')
            for token in malformed:
                with self.subTest(token=token):
                    with self.assertRaises(ValueError):
                        source_lookup.lookup(path, pointer='/array/' + token)
                    self.assertEqual(source_lookup.lookup(path, pointer='/object/' + token)['value'], token)
            self.assertEqual(source_lookup.lookup(path, pointer='/array/0')['value'], 0)
            self.assertEqual(source_lookup.lookup(path, pointer='/array/10')['value'], 10)


    def test_explicit_full_read_preserves_long_original_without_copying(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'original.txt'
            text = 'necessary original\n' + '가'*100000
            p.write_text(text)
            result = source_lookup.lookup(p, full=True)
            self.assertEqual(result['text'], text)
            self.assertTrue(result['complete'])
            self.assertEqual(list(Path(d).iterdir()), [p])
            with self.assertRaises(ValueError):
                source_lookup.lookup(p, full=True, line=1)

    def test_read_and_response_use_publication_share_the_same_observation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'plan.json'; p.write_text('{"start":{"status":"required"}}')
            source = {'GHOST_ALICE_SESSION_ID':'exact'}
            with patch.object(source_lookup, '_record_use', return_value={
                'status':'recorded', 'reference':'sha256:reference'}, create=True) as record:
                result = source_lookup.lookup_with_use(p, pointer='/start', source=source,
                    expected_input_event_id='event', purpose='answer the current status question')
            args, kwargs = record.call_args
            self.assertIs(args[0], source)
            self.assertEqual(args[1], 'event')
            self.assertEqual(args[2]['value'], {'status':'required'})
            self.assertNotIn('use_reference', args[2])
            self.assertEqual(result['use_reference'], 'sha256:reference')
            self.assertEqual(kwargs['purpose'], 'answer the current status question')

    def test_missing_purpose_does_not_publish_or_predict_source_lifetime(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'original.txt'; p.write_text('required body')
            with patch.object(source_lookup, '_record_use', create=True) as record:
                with self.assertRaises(ValueError):
                    source_lookup.lookup_with_use(p, full=True, source={},
                        expected_input_event_id=None, purpose='')
                record.assert_not_called()
