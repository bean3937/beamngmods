"""Phase 3: joint measurement and grading, generated filler tube, header->exhaust pipes, foreign intake/turbo rows,
and the Phase 3 build on the fake cars of test_vis2 (with gearbox meshes and a body-bound turbo pipe)."""
import json
import math
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import jbeam, pipeline, vis2, dae, joints as J   # noqa: E402
from tests.test_models import box_dae                         # noqa: E402
from tests import test_vis2 as tv                          # noqa: E402

V3 = {'cars': ['x'], 'engines': None, 'phase3': True}
NAME = 'engine_swap_catalog_phase3.zip'


def box(lo, hi, n=4):
    return [(lo[0] + (hi[0] - lo[0]) * i / n, lo[1] + (hi[1] - lo[1]) * j / n, lo[2] + (hi[2] - lo[2]) * k / n)
            for i in range(n + 1) for j in range(n + 1) for k in range(n + 1)]


class TestJoints(unittest.TestCase):
    def test_measure_gap_and_offset(self):
        eng = box((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9))
        gb = box((-0.2, -0.95, 0.4), (0.2, -0.6, 0.8))
        m = J.measure(eng, gb, (0, 1))
        self.assertAlmostEqual(m['gap'], 0.05)
        self.assertEqual(m['off'], (0.0, 0.0))
        m2 = J.measure(eng, box((-0.2, -1.1, 0.5), (0.2, -0.6, 0.9)), (0, 1))
        self.assertAlmostEqual(m2['gap'], -0.1)                    # bellhousing reaches over the block
        self.assertAlmostEqual(m2['off'][1], 0.1)
        # transverse (gearbox at -x)
        mt = J.measure(box((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9)), box((-0.6, -1.4, 0.4), (-0.32, -1.2, 0.7)), (-1, 0))
        self.assertAlmostEqual(mt['gap'], 0.02)

    def test_grades(self):
        m = {'gap': 0.01, 'off': (0.0, 0.01)}
        self.assertEqual(J.grade(dict(m))[0], 'perfect')
        self.assertEqual(J.grade({'gap': 0.08, 'off': (0.0, 0.05)})[0], 'filler')
        self.assertEqual(J.grade({'gap': 0.0, 'off': (0.0, 0.1)})[0], 'adapter')
        self.assertEqual(J.grade({'gap': 0.5, 'off': (0.0, 0.0)})[0], 'too far')
        self.assertEqual(J.grade({'gap': 0.0, 'off': (0.0, 0.4)})[0], 'too far')
        # a gap the car's own engine shows too counts as normal
        self.assertEqual(J.grade({'gap': 0.05, 'off': (0.0, 0.0)}, None, 0.05)[0], 'perfect')

    def test_filler_spans_the_gap(self):
        eng = box((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9))
        gb = box((-0.2, -0.9, 0.4), (0.2, -0.6, 0.8))
        m = J.measure(eng, gb, (0, 1))
        J.grade(m)
        a, b, half = J.filler_points(m)
        self.assertLess(a[1], -1.0)                 # 1 cm into the engine face
        self.assertGreater(b[1], -0.9)              # 1 cm into the gearbox face
        self.assertAlmostEqual(half[0], 0.3)        # covers the larger face
        p, q = J.tube_end_points(J.tube_row(a, b, half, ['g']))
        self.assertLess(math.dist(p, a), 1e-4)
        self.assertLess(math.dist(q, b), 1e-4)

    def test_tube_row_any_direction(self):
        import random
        random.seed(3)
        for _ in range(200):
            a = tuple(random.uniform(-1, 1) for _ in range(3))
            b = tuple(random.uniform(-1, 1) for _ in range(3))
            p, q = J.tube_end_points(J.tube_row(a, b, (0.02, 0.02), ['g']))
            self.assertLess(max(math.dist(p, a), math.dist(q, b)), 1e-4)

    def test_tube_mesh_is_readable(self):
        raw = J.filler_dae()
        nodes = dae.scene_nodes(raw)
        verts, tris = dae.read_mesh(raw, nodes[J.FILLER_MESH])
        ys = [v[1] for v in verts]
        self.assertAlmostEqual(min(ys), 0.0)
        self.assertAlmostEqual(max(ys), 1.0)                  # unit length after the cm -> m node matrix
        self.assertAlmostEqual(max(v[0] for v in verts), 1.0)
        self.assertEqual(len(tris), 4 * J.TUBE_SEGMENTS)       # double sided
        mats = dae.library_items(raw, 'materials', 'material')
        self.assertEqual([v[0] for v in mats.values()], [J.FILLER_MAT])

    def test_pipe_plan(self):
        free = lambda path: 0          # noqa: E731
        g, pipes, _ = J.pipe_plan({-1: (-0.3, -0.9, 0.3)}, {-1: (-0.3, -0.7, 0.25)}, free)
        self.assertEqual((g, len(pipes)), ('filler', 1))
        self.assertEqual(J.pipe_plan({-1: (-0.3, -0.9, 0.3)}, {-1: (-0.3, -0.89, 0.3)}, free)[0], 'meets')
        self.assertEqual(J.pipe_plan({-1: (-0.3, -0.9, 0.3)}, {-1: (-0.3, 0.0, 0.3)}, free)[0], 'too far')
        self.assertEqual(J.pipe_plan({-1: (-0.3, -0.9, 0.3)}, {-1: (-0.3, -0.7, 0.3)}, lambda p: 5)[0],
                         'pipe would cross the body')
        self.assertEqual(J.pipe_plan({}, {-1: (0, 0, 0)}, free)[0], 'no donor exhaust data')
        self.assertEqual(J.pipe_plan({-1: (0, 0, 0)}, {}, free)[0], 'no car exhaust')

    def test_induction_twins(self):
        from swapcat.visuals import foreign_induction
        self.assertTrue(foreign_induction('bx_intake_v8_turbo', ['bx_engine', 'bx_radiator'], ['bx_engine']))
        self.assertFalse(foreign_induction('bx_intercooler_v8', ['bx_engine', 'bx_radiator'], ['bx_engine']))
        self.assertFalse(foreign_induction('bx_icpipe_v8_L', ['bx_engine', 'bx_radiator'], ['bx_engine']))
        self.assertFalse(foreign_induction('bx_radhose', ['bx_engine', 'bx_radiator'], ['bx_engine']))
        self.assertFalse(foreign_induction('y_turbo_pipe', ['y_body'], ['y_engine']))   # not on the engine


class TestPhase3Build(unittest.TestCase):
    tearDown = tv.TestVis2Build.tearDown
    did = tv.TestVis2Build.did

    def setUp(self):
        tv.TestVis2Build.setUp(self)
        # gearbox meshes and a body-bound turbo pipe
        for car, lo, hi, gap in (('x', (-0.3, -1.6, 0.3), (0.3, -1.0, 0.9), 0.0),
                                 ('y', (-0.25, -1.5, 0.35), (0.25, -1.05, 0.8), 0.0)):
            path = os.path.join(self.game, 'content', 'vehicles', f'{car}.zip')
            with zipfile.ZipFile(path) as z:
                parts = json.loads(z.read(f'vehicles/{car}/{car}.jbeam'))
                info = z.read(f'vehicles/{car}/info.json')
            parts[f'{car}_transmission']['flexbodies'] = [['mesh', '[group]:', 'nonFlexMaterials'],
                                                         [f'{car}_gearbox', [f'{car}_transmission']]]
            meshes = {f'{car}_gearbox': ((lo[0] + 0.05, hi[1] + gap, lo[2] + 0.05), (hi[0] - 0.05, hi[1] + 0.3, hi[2] - 0.05))}
            if car == 'y':
                # y's block mesh ends 6 cm short of its engine nodes: in x it leaves a 6 cm gap to x's gearbox
                parts['y_engine_a']['flexbodies'] = [parts['y_engine_a']['flexbodies'][0],
                                                     ['y_block', ['y_engine', 'y_transmission'], [], {'pos': {'x': 0, 'y': -0.06, 'z': 0}}]]
                parts['y_intake_turbo']['flexbodies'] += [['y_turbo_pipe', ['y_engine', 'y_radiator']],
                                                          ['y_intercooler', ['y_engine', 'y_radiator']]]
                meshes.update({'y_turbo_pipe': ((0.1, -1.6, 0.6), (0.15, -1.4, 0.65)),
                               'y_intercooler': ((-0.3, -2.0, 0.3), (0.3, -1.95, 0.6))})
            old = dict(tv.TestVis2Build.__dict__)          # noqa: F841 (keeps the fixture meshes of test_vis2)
            with zipfile.ZipFile(path) as z:
                dae_old = z.read(f'vehicles/{car}/{car}.dae')
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr(f'vehicles/{car}/{car}.jbeam', json.dumps(parts))
                z.writestr(f'vehicles/{car}/{car}.dae', dae_old)
                z.writestr(f'vehicles/{car}/{car}_extra.dae', box_dae(meshes))
                z.writestr(f'vehicles/{car}/info.json', info)

    def build3(self, **kw):
        zpath, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, visuals_v2=V3, output_name=NAME, **kw)
        with zipfile.ZipFile(zpath) as z:
            files = {n: z.read(n) for n in z.namelist()}
        parts = {}
        for n, b in files.items():
            if n.endswith('.jbeam'):
                for k, v in jbeam.loads(b.decode()).items():
                    parts[(n.split('/')[1], k)] = v
        return R, files, parts

    def test_phase3_build(self):
        R, files, parts = self.build3()
        val = R['validation']
        self.assertEqual(val['errors'], [], json.dumps(val['errors'], indent=1)[:3000])
        self.assertEqual(val['intake_parts_invisible_abroad'], 0)
        inf = R['engine_models']['v2']['/vehicles/x/']
        y, own = self.did(R, 'y_engine_a'), self.did(R, 'x_engine_a')
        self.assertEqual(inf['joints'][own]['grade'], 'perfect')          # the car's own engine = the reference
        jy = inf['joints'][y]
        self.assertEqual(jy['grade'], 'filler', jy)                       # 6 cm open gap
        self.assertIn('vehicles/common/engine_swap_catalog/meshes/esc_filler.dae', files)
        self.assertIn('vehicles/common/engine_swap_catalog/meshes/esc_filler.materials.json', files)
        rows = [r for r in parts[('x', inf['wrappers'][y])]['flexbodies'][1:] if r[0] == J.FILLER_MESH]
        self.assertEqual(len(rows), len(jy['filler_rows']))
        self.assertTrue(rows and all(r[1] == [vis2.VIS_GROUP] and r[3]['disable'] == vis2.DISABLE for r in rows))
        self.assertIn('report_card', R['engine_models'])
        # the body-bound turbo pipe gets a foreign twin, the front intercooler does not
        tp = [p for (ns, k), p in parts.items() if ns == 'common' and p.get('information', {}).get('name') == 'y_intake_turbo']
        names = [(r[0][-12:], r[3].get('disable')) for p in tp for r in p.get('flexbodies', [])[1:]]
        self.assertIn(('y_turbo_pipe', vis2.DISABLE_FOREIGN), names)
        self.assertNotIn(('ntercooler', vis2.DISABLE_FOREIGN), [(a[-10:], b) for a, b in names])

    def test_phase3_validator_catches_breakage(self):
        from swapcat.validate import validate_build
        R, files, parts = self.build3(validate=False)
        vfs, index, _ = pipeline.scan(self.game, self.mods, log=lambda m: None)
        inf = R['engine_models']['v2']['/vehicles/x/']
        y = self.did(R, 'y_engine_a')

        def errors(mutate):
            f = dict(files)
            mutate(f)
            out = []
            for e in validate_build(index, vfs, f, R, log=lambda m: None)['errors']:
                out += e['issues'] if isinstance(e, dict) else [e]
            return ' | '.join(out)
        self.assertEqual(errors(lambda f: None), '')

        def edit(f, path, fn):
            d = jbeam.loads(f[path].decode())
            fn(d)
            f[path] = jbeam.dumps(d).encode()

        def filler_unswitched(f):
            edit(f, 'vehicles/x/esc_models_x.jbeam', lambda d: [r[3].pop('disable') for r in d[inf['wrappers'][y]]['flexbodies'][1:]
                                                                 if r[0] == J.FILLER_MESH])
        self.assertIn('filler pieces visible', errors(filler_unswitched))

        def no_filler_mesh(f):
            del f['vehicles/common/engine_swap_catalog/meshes/esc_filler.dae']
        self.assertIn('filler mesh', errors(no_filler_mesh))


if __name__ == '__main__':
    unittest.main()
