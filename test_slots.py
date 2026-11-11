import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat.index import PartIndex, resolve, Choice, COMMON  # noqa: E402
from swapcat.tables import part_slots, table_rows, part_fits  # noqa: E402


class _Src:
    kind, name, title, is_zip = 'vanilla', 'test', 'test', True


def make_index(parts_by_ns):
    idx = PartIndex()
    for ns, parts in parts_by_ns.items():
        for name, data in parts.items():
            idx.add_part(ns, name, data, f'{ns.strip("/")}/{name}.jbeam', _Src())
    return idx


class TestTables(unittest.TestCase):
    def test_option_rows_apply_to_following_rows(self):
        rows = table_rows([['id', 'x'], {'group': 'g'}, ['a', 1], ['b', 2, {'w': 3}], {'group': ''}, ['c', 3]])
        self.assertEqual(rows[0], {'group': 'g', 'id': 'a', 'x': 1})
        self.assertEqual(rows[1], {'group': 'g', 'id': 'b', 'x': 2, 'w': 3})
        self.assertEqual(rows[2]['group'], '')

    def test_slots_v1(self):
        p = {'slots': [['type', 'default', 'description'],
                       ['eng_ecu', 'eng_ecu_a', 'ECU', {'coreSlot': True}],
                       ['n2o_system', '', 'Nitrous']]}
        s = part_slots(p)
        self.assertEqual(s[0]['name'], 'eng_ecu')
        self.assertEqual(s[0]['allowTypes'], ['eng_ecu'])
        self.assertEqual(s[0]['opts'], {'coreSlot': True})
        self.assertEqual(s[1]['default'], '')

    def test_slots_v1_missing_header(self):
        s = part_slots({'slots': [['a', 'a1', 'A']]})
        self.assertEqual(s[0]['allowTypes'], ['a'])

    def test_slots2(self):
        p = {'slots2': [['name', 'allowTypes', 'denyTypes', 'default', 'description'],
                        ['engine', ['eng_a', 'eng_b'], ['eng_bad'], 'e1', 'Engine', {'coreSlot': True}]]}
        s = part_slots(p)[0]
        self.assertEqual((s['name'], s['allowTypes'], s['denyTypes'], s['default']),
                         ('engine', ['eng_a', 'eng_b'], ['eng_bad'], 'e1'))

    def test_part_fits_allow_deny(self):
        slot = {'allowTypes': ['a', 'b'], 'denyTypes': ['x']}
        self.assertTrue(part_fits({'slotType': 'a'}, slot))
        self.assertTrue(part_fits({'slotType': ['z', 'b']}, slot))
        self.assertFalse(part_fits({'slotType': 'c'}, slot))
        self.assertFalse(part_fits({'slotType': ['a', 'x']}, slot))


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.idx = make_index({
            '/vehicles/car/': {
                'car': {'slotType': 'main', 'slots': [['type', 'default'], ['car_engine', 'car_engine_v8']]},
                'car_engine_v8': {'slotType': 'car_engine',
                                  'slots': [['type', 'default'], ['car_ecu', 'car_ecu_a'], ['car_trans', 'car_trans_4M']]},
                'car_engine_i4': {'slotType': 'car_engine', 'slots': [['type', 'default'], ['car_ecu', 'car_ecu_a']]},
                'car_ecu_a': {'slotType': 'car_ecu'},
                'car_ecu_b': {'slotType': 'car_ecu'},
                'shared': {'slotType': 'car_ecu', 'information': {'name': 'vehicle copy'}},
            },
            COMMON: {
                'car_trans_4M': {'slotType': 'car_trans'},
                'shared': {'slotType': 'car_ecu', 'information': {'name': 'common copy'}},
            },
        })
        self.car = self.idx.get('/vehicles/car/', 'car')

    def paths(self, tree):
        return {n.path: n.part.name for n in tree.walk() if n.slot}

    def test_defaults(self):
        t = resolve(self.idx, '/vehicles/car/', self.car)
        self.assertEqual(self.paths(t), {'/car_engine/': 'car_engine_v8', '/car_engine/car_ecu/': 'car_ecu_a',
                                         '/car_engine/car_trans/': 'car_trans_4M'})

    def test_common_namespace_and_vehicle_wins(self):
        self.assertEqual(self.idx.get('/vehicles/car/', 'car_trans_4M').ns, COMMON)
        self.assertEqual(self.idx.get('/vehicles/car/', 'shared').data['information']['name'], 'vehicle copy')
        names = [p.name for p in self.idx.candidates('/vehicles/car/', part_slots(self.idx.get('/vehicles/car/', 'car_engine_v8').data)[0])]
        self.assertEqual(sorted(names), ['car_ecu_a', 'car_ecu_b', 'shared'])

    def test_user_choice_tree_and_flat(self):
        tree_cfg = {'partsTree': {'children': {'car_engine': {'chosenPartName': 'car_engine_i4', 'children': {
            'car_ecu': {'chosenPartName': 'car_ecu_b'}}}}}}
        t = resolve(self.idx, '/vehicles/car/', self.car, Choice(tree_cfg))
        self.assertEqual(self.paths(t), {'/car_engine/': 'car_engine_i4', '/car_engine/car_ecu/': 'car_ecu_b'})
        t = resolve(self.idx, '/vehicles/car/', self.car, Choice({'parts': {'car_ecu': 'car_ecu_b'}}))
        self.assertEqual(self.paths(t)['/car_engine/car_ecu/'], 'car_ecu_b')

    def test_empty_and_missing_and_wrong_type(self):
        probs = []
        t = resolve(self.idx, '/vehicles/car/', self.car,
                    Choice({'parts': {'car_ecu': 'none', 'car_trans': 'car_ecu_a'}}), probs)
        p = self.paths(t)
        self.assertNotIn('/car_engine/car_ecu/', p)                    # 'none' = user-empty
        self.assertEqual(p['/car_engine/car_trans/'], 'car_trans_4M')   # wrong type -> default
        self.assertTrue(any('wrong slot type' in m for _, m in probs))
        probs = []
        resolve(self.idx, '/vehicles/car/', self.car, Choice({'parts': {'car_ecu': 'nope'}}), probs)
        self.assertTrue(any('not found' in m for _, m in probs))

    def test_override_by_path(self):
        t = resolve(self.idx, '/vehicles/car/', self.car, Choice(None, {'/car_engine/car_ecu/': 'car_ecu_b'}))
        self.assertEqual(self.paths(t)['/car_engine/car_ecu/'], 'car_ecu_b')

    def test_cycle_is_bounded(self):
        idx = make_index({'/vehicles/x/': {
            'x': {'slotType': 'main', 'slots': [['type', 'default'], ['loop', 'loop_a']]},
            'loop_a': {'slotType': 'loop', 'slots': [['type', 'default'], ['loop', 'loop_a']]}}})
        probs = []
        resolve(idx, '/vehicles/x/', idx.get('/vehicles/x/', 'x'), None, probs)
        self.assertTrue(any('recursion' in m for _, m in probs))


if __name__ == '__main__':
    unittest.main()
