import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import jbeam  # noqa: E402


class TestJBeam(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(jbeam.loads('{"a": [1, 2.5, "x", true, false]}'), {'a': [1, 2.5, 'x', True, False]})

    def test_comments_and_trailing_commas(self):
        txt = '''{
          // line comment
          "a": [1, 2, 3,], /* block
          comment */ "b": {"c": 1,},
        }'''
        self.assertEqual(jbeam.loads(txt), {'a': [1, 2, 3], 'b': {'c': 1}})

    def test_missing_commas(self):
        # seen in vanilla etk_engine_i6_3.0_petrol.jbeam: rows and options without commas
        txt = '{"beams": [["id1:", "id2:"] {"deformGroup":"x"} ["e1r","e1l"] []{"pos":{"x":0}}]}'
        self.assertEqual(jbeam.loads(txt)['beams'],
                         [['id1:', 'id2:'], {'deformGroup': 'x'}, ['e1r', 'e1l'], [], {'pos': {'x': 0}}])

    def test_implicit_root_object(self):
        # Ford Engine Pack files have no outer braces (SJSON root)
        txt = '"part_a": {"slotType": "x"},\n"part_b": {"slotType": "y"},\n'
        self.assertEqual(jbeam.loads(txt), {'part_a': {'slotType': 'x'}, 'part_b': {'slotType': 'y'}})

    def test_bare_keys_and_equals(self):
        # RK pack: `mainEngine:{ connectingRodBearingsDamagedOverride:true }`
        self.assertEqual(jbeam.loads('{"m":{ k1:true k2 = 3 }}'), {'m': {'k1': True, 'k2': 3}})

    def test_numbers(self):
        d = jbeam.loads('[.5, -.25, 1., 1e3, -2E-2, +4, 7]')
        self.assertEqual(d[:6], [0.5, -0.25, 1.0, 1000.0, -0.02, 4])
        self.assertIsInstance(d[6], int)

    def test_special_literals(self):
        d = jbeam.loads('{"a": Infinity, "b": NaN}')
        self.assertTrue(math.isinf(d['a']))
        self.assertTrue(math.isnan(d['b']))

    def test_null_removes_key(self):
        self.assertEqual(jbeam.loads('{"a": 1, "b": null}'), {'a': 1})

    def test_duplicate_key_last_wins(self):
        self.assertEqual(jbeam.loads('{"a": 1, "a": 2}'), {'a': 2})

    def test_escapes_and_bom(self):
        self.assertEqual(jbeam.loads('﻿{"a": "x\\"y\\n"}'), {'a': 'x"y\n'})

    def test_strings_with_comment_markers(self):
        self.assertEqual(jbeam.loads('{"a": "http://x/*y*/"}'), {'a': 'http://x/*y*/'})

    def test_errors(self):
        for bad in ('{"a": [1, 2}', '{"a" 1}', '{"a": unknownword}', '', '{"a": 1'):
            with self.assertRaises(jbeam.JBeamError, msg=bad):
                jbeam.loads(bad)

    def test_roundtrip_dumps_is_strict_json(self):
        obj = {'p': {'slots2': [['name', 'allowTypes'], ['a', ['b']]], 'v': 1.5}}
        s = jbeam.dumps(obj)
        self.assertEqual(json.loads(s), obj)
        self.assertEqual(jbeam.loads(s), obj)


if __name__ == '__main__':
    unittest.main()
