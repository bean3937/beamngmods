"""Engine visuals architecture 2 (experimental test builds): Lua ports, placement through slot options (sliders,
flip, free index, gearbox alignment), visibility switches, native / foreign / no-model engines, car-side meshes,
exhaust slots, and validator mutation tests."""
import json
import math
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import jbeam, pipeline, vis2, placement as P   # noqa: E402
from swapcat.visuals import eval_num, block_row, EngineVisual   # noqa: E402
from tests.test_models import box_dae                         # noqa: E402
from tests import test_build_models as tbm                 # noqa: E402
make_car = tbm.make_car

V2 = {'cars': ['x'], 'engines': None}
NAME = 'engine_swap_catalog_phase2.zip'


def apply_opts(opts, vars_):
    """Slot options after variables (as variables.lua processParts does) -> node_ops."""
    return P.node_ops({k: {a: eval_num(v, vars_) if isinstance(v, str) else v for a, v in d.items()}
                       for k, d in opts.items()})


class TestLuaPorts(unittest.TestCase):
    def test_node_ops_index_parsing(self):
        ops = P.node_ops({'nodeOffset': {'x': 1, 'y': 0, 'z': 0}, 'nodeMove12': {'x': 0, 'y': 1, 'z': 0},
                          'nodeRotate9': {'x': 0, 'y': 0, 'z': 90}, 'nodeOffsetX': 5, 'other': {'x': 1}})
        self.assertEqual(sorted(ops), [0, 9, 12])
        self.assertIn('offset', ops[0])
        self.assertIn('move', ops[12])
        self.assertIn('rot', ops[9])

    def test_node_offset_x_ignored_for_centred_rows(self):
        # utils.lua multiplies nodeOffset.x by sign(pos.x): a mesh row at x = 0 does not move sideways
        ops = P.node_ops({'nodeOffset': {'x': 0.2, 'y': 0.1, 'z': 0}})
        self.assertEqual(P.flexbody_after_ops(ops, (0, 0, 0), (0, 0, 0))[0], (0.0, 0.1, 0.0))
        self.assertEqual(P.flexbody_after_ops(ops, (-1, 0, 0), (0, 0, 0))[0], (-1.2, 0.1, 0.0))
        ops = P.node_ops({'nodeMove': {'x': 0.2, 'y': 0.1, 'z': 0}})          # nodeMove has no sign
        self.assertEqual(P.flexbody_after_ops(ops, (0, 0, 0), (0, 0, 0))[0], (0.2, 0.1, 0.0))

    def test_solver_matches_wanted_placement(self):
        inherited_cases = [{}, P.node_ops({'nodeOffset': {'x': 0, 'y': 0.05, 'z': -0.02}}),
                           P.node_ops({'nodeRotate': {'x': 0, 'y': 0, 'z': 15, 'px': 0.1, 'py': 0, 'pz': 0}})]
        for inh in inherited_cases:
            for deg in (0, 90, 180, 270):
                t = (0.1, -0.2, 0.03)
                opts, err = P.solve_slot_placement(inh, deg, t)
                self.assertIsNotNone(opts)
                full = dict(inh)
                full.update(P.node_ops(opts))
                for v in ((0.3, 0.1, -0.2), (1, 2, 3)):
                    for pos, rot in (((0, 0, 0), (0, 0, 0)), ((0.2, -0.4, 0.1), (0, 0, P.rot_param_for(90)))):
                        got = P.vertex_after_ops(full, v, pos, rot)
                        native = P.flexbody_apply(v, pos, rot)
                        want = P.vertex_after_ops(inh, tuple(a + b for a, b in zip(P.rotz(native, deg), t)))
                        self.assertLess(math.dist(got, want), 1e-9, (inh, deg, pos))

    def test_solver_refuses_index_collision(self):
        opts, _e = P.solve_slot_placement(P.node_ops({'nodeMove9': {'x': 0, 'y': 0, 'z': 0}}), 0, (0, 0, 0))
        self.assertIsNone(opts)

    def test_free_index(self):                                   # H
        self.assertEqual(P.free_index({}), 9)
        self.assertEqual(P.free_index(P.node_ops({'nodeMove9': {'x': 0, 'y': 0, 'z': 0}})), 10)
        self.assertEqual(P.free_index(P.node_ops({'nodeMove12': {'x': 0, 'y': 0, 'z': 0}})), 13)
        self.assertIsNone(P.free_index(P.node_ops({'nodeMove99': {'x': 0, 'y': 0, 'z': 0}})))
        # a car that already uses index 9 still gets a placement (at 10)
        inh = P.node_ops({'nodeMove9': {'x': 0, 'y': 0.1, 'z': 0}})
        opts, err, info = P.placement_options(inh, 0, (0.1, 0.2, 0.0), (0, 0, 0))
        self.assertEqual(info['index'], 10)
        self.assertIn('nodeMove10', opts)

    def test_expressions(self):
        v = {'$a': 1.0, '$b': 0.0}
        self.assertEqual(eval_num('$=case($a >= 0.5, 3, 4)', v), 3.0)
        self.assertEqual(eval_num('$=case($b >= 0.5, 3, 4)', v), 4.0)
        self.assertEqual(eval_num('$=0.5 + 2*$a', v), 2.5)
        self.assertIs(eval_num('$=$a - $b < 0.5', v), False)
        self.assertIsNone(eval_num('$=$missing + 1', v))

    def test_disable_expressions(self):
        for vis, nat, own, d, dn, df, do in ((1, 0, 0, False, True, False, True),     # donor, foreign
                                             (1, 1, 0, False, False, True, True),     # donor, native
                                             (0, 0, 1, True, True, True, False),      # own
                                             (0, 0, 0, True, True, True, True)):      # none
            v = {'$esc_vis': float(vis), '$esc_nat': float(nat), '$esc_own': float(own)}
            self.assertEqual([vis2._eval_disable(x, v) for x in (vis2.DISABLE, vis2.DISABLE_NAT,
                                                                   vis2.DISABLE_FOREIGN, vis2.DISABLE_OWN)],
                             [d, dn, df, do], (vis, nat, own))
        self.assertIsNone(vis2._eval_disable(vis2.DISABLE, {}))         # missing variable: flagged

    def test_switch_keeps_the_rows_own_condition(self):
        row = ['m', ['g'], [], {'disable': '$= $components.luxeTrim ~= true'}]
        d = vis2.switched(row, vis2.DISABLE)[3]['disable']
        self.assertEqual(d, '$=($esc_vis < 0.5) or ( $components.luxeTrim ~= true)')
        self.assertIs(vis2._eval_disable(d, {'$esc_vis': 0.0}), True)      # ours hides it
        self.assertIsNone(vis2._eval_disable(d, {'$esc_vis': 1.0}))        # then the car's own condition decides
        self.assertEqual(vis2.switched(['m', ['g']], vis2.DISABLE)[3], {'disable': vis2.DISABLE})
        self.assertEqual(vis2.switched(['m', ['g'], [], {'disable': True}], vis2.DISABLE)[3], {'disable': True})

    def test_sliders_and_flip(self):                             # D, F
        pivot = (0.05, -1.3, 0.6)
        for inh in ({}, P.node_ops({'nodeOffset': {'x': 0, 'y': 0.05, 'z': -0.02}})):
            for deg in (0, 90, 180, 270):
                t = (0.1, -0.2, 0.03)
                opts, err, info = P.placement_options(inh, deg, t, pivot)
                self.assertLess(err, 1e-6)
                self.assertEqual(info['flip'], deg % 180 == 90)

                def where(vs, v, pos=(0.0, 0.0, 0.0)):
                    full = dict(inh)
                    full.update(apply_opts(opts, vs))
                    return P.vertex_after_ops(full, v, pos)
                zero = {'$esc_mx': 0.0, '$esc_my': 0.0, '$esc_mz': 0.0, '$esc_mr': 0.0, '$esc_flip': 0.0}
                for v in ((0.3, -1.1, 0.4), pivot):
                    want = P.vertex_after_ops(inh, tuple(a + b for a, b in zip(P.rotz(v, deg), t)))
                    self.assertLess(math.dist(where(zero, v), want), 1e-6)
                    # nudge: moves by exactly (dx, dy, dz) in the car frame (before the car's own options)
                    n = (0.1, -0.2, 0.05)
                    vs = dict(zero, **{'$esc_mx': n[0], '$esc_my': n[1], '$esc_mz': n[2]})
                    want = P.vertex_after_ops(inh, tuple(a + b + c for a, b, c in zip(P.rotz(v, deg), t, n)))
                    self.assertLess(math.dist(where(vs, v), want), 1e-5, (deg, inh))
                # turn: about the engine's own vertical axis (the pivot stays put)
                vs = dict(zero, **{'$esc_mr': 10.0})
                self.assertLess(math.dist(where(vs, pivot), where(zero, pivot)), 1e-6)
                p2 = (pivot[0] + 0.2, pivot[1], pivot[2])
                self.assertGreater(math.dist(where(vs, p2), where(zero, p2)), 0.02)
                if deg % 180 == 90:
                    vs = dict(zero, **{'$esc_flip': 1.0})
                    self.assertLess(math.dist(where(vs, pivot), where(zero, pivot)), 1e-6)   # turned about its centre
                    a, b = where(zero, (pivot[0], pivot[1] + 0.3, pivot[2])), where(vs, (pivot[0], pivot[1] + 0.3, pivot[2]))
                    c = where(zero, pivot)
                    self.assertLess(math.dist(tuple(2 * c[i] - a[i] for i in range(3)), b), 1e-6)  # 180 deg more

    def test_gearbox_alignment(self):                            # C
        blk = [(x, y, z) for x in (-0.3, 0.3) for y in (-1.6, -1.0) for z in (0.3, 0.9)]
        dblk = [(x, y, z) for x in (-0.25, 0.25) for y in (-1.5, -1.05) for z in (0.2, 0.8)]
        deg, t, method, note = P.place_gearbox(dblk, (0, 1), (0.0, -0.9, 0.55), blk, (0, 1), (0.0, -0.85, 0.6))
        self.assertEqual(method, 'gearbox')
        self.assertAlmostEqual(t[2], 0.05)               # gearbox centres level, not the block bottoms (0.1)
        self.assertAlmostEqual(t[1], 0.05)               # bellhousing faces as before
        self.assertEqual(P.place_gearbox(dblk, (0, 1), None, blk, (0, 1), (0, -0.85, 0.6))[2], 'block')
        self.assertEqual(P.place_gearbox(dblk, (1, 0), (0.3, -1.3, 0.5), blk, (0, 1), (0, -0.85, 0.6))[2], 'block')
        self.assertEqual(P.place_gearbox(dblk, (0, 1), (0.0, -0.9, 0.95), blk, (0, 1), (0, -0.85, 0.6))[2], 'block')

    def test_block_from_tuning(self):
        class M:
            data = {('f', 'b'): {'box': ((0, 0, 0), (1, 1, 1))}, ('f', 'c'): {'box': ((0, 0, 0), (0.1, 0.1, 0.1))}}
        ev = EngineVisual()
        ev.rows = [{'mesh': 'b', 'kind': 'tuning'}, {'mesh': 'c', 'kind': 'tuning'}]
        files = {'b': 'f', 'c': 'f'}
        self.assertIsNone(block_row(ev, M, files))
        self.assertEqual(block_row(ev, M, files, allow_tuning=True)['mesh'], 'b')

    def test_vis_group_added_only_to_engine_groups(self):
        cat = {'nodes': [['id', 'posX', 'posY', 'posZ'], {'group': 'e'}, ['a', 0, 0, 0], {'group': 'other'},
                         ['b', 0, 0, 0], ['c', 0, 0, 0, {'group': ['e', 'k']}], {'group': ''}]}
        vis2.add_vis_group(cat, ['e'])
        self.assertEqual(cat['nodes'][1], {'group': ['e', vis2.VIS_GROUP]})
        self.assertEqual(cat['nodes'][3], {'group': 'other'})
        self.assertEqual(cat['nodes'][5][-1], {'group': ['e', 'k', vis2.VIS_GROUP]})


class TestVis2Build(unittest.TestCase):
    """Fake cars of test_build_models. Car x (target) gets body-side meshes (radiator hose, airbox), an intake
    tuning part and a second native engine with its own exhaust; car y an intake with two options and an engine
    without a model."""
    tearDown = tbm.TestEngineModels.tearDown
    did = tbm.TestEngineModels.did

    def setUp(self):
        tbm.TestEngineModels.setUp(self)
        lo, hi = (-0.25, -1.5, 0.35), (0.25, -1.05, 0.8)
        parts, _model = make_car('y', lo, hi, 0.45, -0.85)
        eng = parts['y_engine_a']
        eng['slots'].append(['y_intake', 'y_intake_na', 'Intake'])
        for name, mesh in (('y_intake_na', 'y_airfilter'), ('y_intake_turbo', 'y_turbo')):
            parts[name] = {'information': {'name': name}, 'slotType': 'y_intake',
                           'mainEngine': {'torqueModIntake': [['rpm', 'torque'], [0, 0], [4000, 10]]},
                           'flexbodies': [['mesh', '[group]:', 'nonFlexMaterials'], [mesh, ['y_engine']]]}
        nomodel = json.loads(json.dumps(eng))
        nomodel['mainEngine']['torque'][-1][1] += 99
        nomodel['flexbodies'] = [['mesh', '[group]:', 'nonFlexMaterials'], ['y_missing_mesh', ['y_engine']]]
        parts['y_engine_e'] = nomodel
        meshes = {'y_block': (lo, hi),
                  'y_header_mesh': ((lo[0] - 0.08, lo[1] + 0.05, lo[2] + 0.15), (lo[0], lo[1] + 0.3, lo[2] + 0.3)),
                  'y_downpipe': ((lo[0] - 0.08, hi[1], 0.25), (lo[0] - 0.02, hi[1] + 0.3, 0.3)),
                  'y_airfilter': ((-0.1, -1.4, 0.8), (0.1, -1.2, 0.85)),
                  'y_turbo': ((0.1, -1.4, 0.6), (0.2, -1.3, 0.7))}
        with zipfile.ZipFile(os.path.join(self.game, 'content', 'vehicles', 'y.zip'), 'w') as z:
            z.writestr('vehicles/y/y.jbeam', json.dumps(parts))
            z.writestr('vehicles/y/y.dae', box_dae(meshes))
            z.writestr('vehicles/y/info.json', json.dumps({'Name': 'Y'}))
        # car x
        lo, hi = (-0.3, -1.6, 0.3), (0.3, -1.0, 0.9)
        parts, _model = make_car('x', lo, hi, 0.45, -0.85)
        ea = parts['x_engine_a']
        ea['flexbodies'].append(['x_radhose', ['x_engine', 'x_radiator']])        # body side, kept for foreign
        ea['slots'].append(['x_intake', 'x_intake_a', 'Intake'])
        parts['x_intake_a'] = {'information': {'name': 'Intake'}, 'slotType': 'x_intake',
                               'mainEngine': {'torqueModIntake': [['rpm', 'torque'], [0, 0], [4000, 5]]},
                               'flexbodies': [['mesh', '[group]:', 'nonFlexMaterials'],
                                              ['x_intake_mf', ['x_engine']], ['x_airbox', ['x_body_grp']]]}
        eb = json.loads(json.dumps(ea))                     # a second engine made for x: native, own exhaust
        eb['mainEngine']['torque'][-1][1] += 123
        # its exhaust manifold hangs below its intake (as on the Bastion V8)
        eb['slots'] = [['type', 'default', 'description'], ['x_intake_b', 'x_intake_b', 'Intake'],
                       ['x_transmission', 'x_transmission', 'Transmission']]
        parts['x_engine_b'] = eb
        parts['x_intake_b'] = {'information': {'name': 'Intake B'}, 'slotType': 'x_intake_b',
                               'mainEngine': {'torqueModIntake': [['rpm', 'torque'], [0, 0], [4000, 7]]},
                               'slots': [['type', 'default', 'description'],
                                         ['x_header_b', 'x_header_b', 'Exhaust Manifold']]}
        parts['x_header_b'] = {'information': {'name': 'Header B'}, 'slotType': 'x_header_b',
                               'nodes': [tbm.HDR, {'group': 'x_header_b'}, ['xhb1', lo[0] - 0.05, lo[1] + 0.1, lo[2] + 0.2],
                                         {'group': ''}],
                               'beams': [['id1:', 'id2:'], ['xhb1', 'xe1']],
                               'flexbodies': [['mesh', '[group]:', 'nonFlexMaterials'],
                                              ['x_header_b_mesh', ['x_engine', 'x_header_b']]]}
        meshes = {'x_block': (lo, hi),
                  'x_header_mesh': ((lo[0] - 0.08, lo[1] + 0.05, lo[2] + 0.15), (lo[0], lo[1] + 0.3, lo[2] + 0.3)),
                  'x_downpipe': ((lo[0] - 0.08, hi[1], 0.25), (lo[0] - 0.02, hi[1] + 0.3, 0.3)),
                  'x_radhose': ((0.2, -1.9, 0.7), (0.3, -1.6, 0.75)),
                  'x_intake_mf': ((-0.1, -1.4, 0.9), (0.1, -1.2, 0.95)),
                  'x_airbox': ((0.3, -1.9, 0.8), (0.4, -1.7, 0.9)),
                  'x_header_b_mesh': ((lo[0] - 0.08, lo[1] + 0.05, lo[2] + 0.15), (lo[0], lo[1] + 0.3, lo[2] + 0.3))}
        with zipfile.ZipFile(os.path.join(self.game, 'content', 'vehicles', 'x.zip'), 'w') as z:
            z.writestr('vehicles/x/x.jbeam', json.dumps(parts))
            z.writestr('vehicles/x/x.dae', box_dae(meshes))
            z.writestr('vehicles/x/info.json', json.dumps({'Name': 'X'}))

    def build2(self, **kw):
        zpath, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, visuals_v2=V2, output_name=NAME, **kw)
        with zipfile.ZipFile(zpath) as z:
            files = {n: z.read(n) for n in z.namelist()}
        parts = {}
        for n, b in files.items():
            if n.endswith('.jbeam'):
                for k, v in jbeam.loads(b.decode()).items():
                    parts[(n.split('/')[1], k)] = v
        return zpath, R, files, parts

    def test_v2_structure_and_validation(self):
        normal = os.path.join(self.mods, 'engine_swap_catalog.zip')
        with open(normal, 'wb') as fh:
            fh.write(b'keep me')
        zpath, R, files, parts = self.build2()
        self.assertEqual(os.path.basename(zpath), NAME)
        with open(normal, 'rb') as fh:
            self.assertEqual(fh.read(), b'keep me')                  # the normal catalog is never touched
        val = R['validation']
        self.assertEqual(val['errors'], [], json.dumps(val['errors'], indent=1)[:3000])
        self.assertGreater(val['engine_visuals_v2_checks'], 0)
        self.assertNotIn('/vehicles/y/', R['targets'])                 # only the selected car gets a catalog
        ex = val['engine_visuals_v2']['/vehicles/x/']
        self.assertGreater(ex['materials_checked'], 0)
        # catalog part: Engine model slot, no exhaust slot (moved), no car engine meshes, sliders declared
        cat = parts[('x', 'esc_catalog_x')]
        names = [r[0] for r in cat['slots2'][1:]]
        self.assertIn(vis2.MODE_SLOT, names)
        self.assertNotIn('esc_engine', names)
        self.assertNotIn('x_header', names)
        self.assertIn({'group': ['x_engine', vis2.VIS_GROUP]}, cat['nodes'])
        self.assertFalse({'x_block', 'x_radhose', 'x_airbox', 'x_intake_mf'} & {r[0] for r in cat.get('flexbodies', [[]])[1:]})
        decl = {r[0]: r for r in cat['variables'][1:]}
        for v in P.NUDGE_VARS + (P.TURN_VAR,):
            self.assertIn(v, decl)
            self.assertNotIn('hideInUI', decl[v][-1] if isinstance(decl[v][-1], dict) else {})
        inf = R['engine_models']['v2']['/vehicles/x/']
        y, z, own = self.did(R, 'y_engine_a'), self.did(R, 'z_engine_a'), self.did(R, 'x_engine_a')
        xb, ye = self.did(R, 'x_engine_b'), self.did(R, 'y_engine_e')
        self.assertEqual((inf['kinds'][own], inf['kinds'][xb], inf['kinds'][y], inf['kinds'][ye]),
                         ('native', 'native', 'foreign', 'nomodel'))
        self.assertNotIn(z, inf['wrappers'])                           # too big: not offered
        self.assertEqual(inf['placement'][y]['method'], 'gearbox')
        self.assertEqual(inf['car_side']['s_intake'], ['x_airbox'])
        self.assertEqual(inf['car_side']['s_keep'], ['x_radhose'])
        # foreign: the car's exhaust (header copy) first, then the placed engine with slider expressions
        wy = parts[('x', inf['wrappers'][y])]
        rows = wy['slots2'][1:]
        self.assertEqual(rows[0][0], 'esc_x_header')
        self.assertTrue(rows[0][3].startswith('esc_hx_'))
        eng = rows[-1]
        self.assertEqual((eng[0], eng[3]), (vis2.ENG_SLOT, y))
        self.assertTrue(eng[5]['nodeMove9']['x'].startswith('$='))
        self.assertEqual(eng[5]['nodeRotate10']['z'], P.TURN_VAR)
        self.assertEqual([r[0] for r in wy['flexbodies'][1:]], ['x_radhose'])
        self.assertTrue(parts[('x', 'esc_catalog_x')])
        # native: its own exhaust chain, $esc_nat = 1, no transform
        wb = parts[('x', inf['wrappers'][xb])]
        self.assertEqual([r[0] for r in wb['slots2'][1:]], ['x_header_b', vis2.ENG_SLOT])
        self.assertEqual(wb['slots2'][1][3], 'x_header_b')
        self.assertEqual(wb['slots2'][-1][5]['variables'], {vis2.NAT_VAR: 1})
        self.assertNotIn('nodeMove9', wb['slots2'][-1][5])
        self.assertNotIn('flexbodies', wb)
        self.assertEqual(inf['native_exhaust'][xb], {'slots': ['x_header_b']})
        self.assertEqual(parts[('x', inf['wrappers'][own])]['slots2'][1][0], 'x_header')     # own engine: original
        # no model: the car's model in donor mode, donor rows switched off below
        wn = parts[('x', inf['wrappers'][ye])]
        self.assertEqual(wn['slots2'][-1][5]['variables'], {vis2.VIS_VAR: 0})
        self.assertEqual(wn['slots2'][1][5]['variables'], {vis2.OWN_VAR: vis2.NOMODEL_OWN})
        self.assertEqual({r[0] for r in wn['flexbodies'][1:]}, {'x_block', 'x_intake_mf', 'x_radhose', 'x_airbox'})
        self.assertTrue(all(r[3]['disable'] == vis2.DISABLE for r in wn['flexbodies'][1:]))
        self.assertTrue(R['engine_models']['v2']['/vehicles/x/']['labels'][ye].startswith(' (no model'))
        # header copy: the car's header mesh switched, the downpipe untouched
        hx = parts[('x', rows[0][3])]
        fb = {r[0]: r for r in hx['flexbodies'][1:]}
        self.assertEqual(fb['x_header_mesh'][3]['disable'], vis2.DISABLE_OWN)
        self.assertEqual(len(fb['x_downpipe']), 2)
        # shared engine part: engine-side rows + the donor's default header for foreign cars
        srows = {r[0]: r for r in parts[('common', y)]['flexbodies'][1:]}
        blk = next(k for k in srows if k.endswith('y_block'))
        self.assertEqual((srows[blk][1], srows[blk][3]['disable']), ([vis2.VIS_GROUP], vis2.DISABLE))
        hdr = next(k for k in srows if k.endswith('y_header_mesh'))
        self.assertEqual(srows[hdr][3]['disable'], vis2.DISABLE_FOREIGN)
        decl = [r for r in parts[('common', y)]['variables'][1:] if r[0] == vis2.VIS_VAR]
        self.assertEqual(decl[0][4], 0)
        self.assertTrue(decl[0][-1]['hideInUI'])
        # native shared rows: x's own engine has a body-side mesh (radiator hose) shown only in x
        orow = {r[0]: r for r in parts[('common', own)]['flexbodies'][1:]}
        hose = next(k for k in orow if k.endswith('x_radhose'))
        self.assertEqual(orow[hose][3]['disable'], vis2.DISABLE_NAT)
        # mode parts pass the variables and hold the car's meshes
        for mode, v, o in (('donor', 1, 0), ('own', 0, 1), ('none', 0, 0)):
            p = parts[('x', vis2.mode_part('/vehicles/x/', mode))]
            self.assertEqual(p['slots2'][1][5]['variables'], {vis2.VIS_VAR: v, vis2.OWN_VAR: o})
        self.assertEqual({r[0] for r in parts[('x', vis2.mode_part('/vehicles/x/', 'own'))]['flexbodies'][1:]},
                         {'x_block', 'x_intake_mf', 'x_radhose', 'x_airbox'})
        self.assertEqual([r[0] for r in parts[('x', vis2.mode_part('/vehicles/x/', 'none'))]['flexbodies'][1:]],
                         ['x_radhose'])
        # turbo <-> NA intake changes the visible meshes
        self.assertTrue(any(c['engine'] == y and c['from'] == 'y_intake_na' for c in ex['tuning_changes']),
                        ex['tuning_changes'])
        # report: sizes and the config-reset warning
        self.assertIn('/vehicles/x/', R['engine_models']['v2_sizes']['cars'])
        self.assertTrue(any('saved configs' in w for w in R['warnings']))

    def test_native_exhaust_needs_its_nodes(self):
        # x_header_b ties to a node the catalog does not have: the native engine keeps the car's exhaust instead
        with zipfile.ZipFile(os.path.join(self.game, 'content', 'vehicles', 'x.zip')) as z:
            parts = json.loads(z.read('vehicles/x/x.jbeam'))
            dae_b = z.read('vehicles/x/x.dae')
        parts['x_header_b']['beams'] = [['id1:', 'id2:'], ['xhb1', 'nowhere1']]
        with zipfile.ZipFile(os.path.join(self.game, 'content', 'vehicles', 'x.zip'), 'w') as z:
            z.writestr('vehicles/x/x.jbeam', json.dumps(parts))
            z.writestr('vehicles/x/x.dae', dae_b)
            z.writestr('vehicles/x/info.json', json.dumps({'Name': 'X'}))
        _z, R, _f, mparts = self.build2()
        self.assertEqual(R['validation']['errors'], [], json.dumps(R['validation']['errors'], indent=1)[:2000])
        inf = R['engine_models']['v2']['/vehicles/x/']
        xb = self.did(R, 'x_engine_b')
        self.assertIn('nowhere1', inf['native_exhaust'][xb]['fallback'])
        self.assertEqual(mparts[('x', inf['wrappers'][xb])]['slots2'][1][0], 'esc_x_header')

    def test_v2_validator_catches_breakage(self):
        from swapcat.validate import validate_build
        zpath, R, files, parts = self.build2(validate=False)
        vfs, index, _ = pipeline.scan(self.game, self.mods, log=lambda m: None)
        y = self.did(R, 'y_engine_a')
        inf = R['engine_models']['v2']['/vehicles/x/']

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
        eng_file = next(n for n in files if 'esc_engines_' in n and y.encode() in files[n])
        models = 'vehicles/x/esc_models_x.jbeam'

        def no_disable(f):
            edit(f, eng_file, lambda d: [r[3].pop('disable') for r in d[y]['flexbodies'][1:]])
        self.assertIn('without the visibility switch', errors(no_disable))

        def no_group(f):
            def fn(d):
                cat = d['esc_catalog_x']
                cat['nodes'] = [{'group': 'x_engine'} if isinstance(r, dict) and r.get('group') == ['x_engine', vis2.VIS_GROUP]
                                else r for r in cat['nodes']]
            edit(f, 'vehicles/x/esc_catalog_x.jbeam', fn)
        self.assertIn(f'no node has group {vis2.VIS_GROUP}', errors(no_group))

        def bad_move(f):
            w = inf['wrappers'][y]
            edit(f, models, lambda d: d[w]['slots2'][-1][5]['nodeMove9'].update(
                z=d[w]['slots2'][-1][5]['nodeMove9']['z'] + ' + 0.05'))
        self.assertIn('placement differs', errors(bad_move))

        def no_slot_var(f):
            edit(f, models, lambda d: d[vis2.mode_part('/vehicles/x/', 'donor')]['slots2'][1][5].pop('variables'))
        self.assertIn('donor mode shows no donor meshes', errors(no_slot_var))

        def own_left(f):
            edit(f, 'vehicles/x/esc_catalog_x.jbeam',
                 lambda d: d['esc_catalog_x'].setdefault('flexbodies', [['mesh', '[group]:', 'nonFlexMaterials']])
                 .append(['x_block', ['x_engine'], []]))
        self.assertIn("car's own engine meshes visible", errors(own_left))

        def header_unswitched(f):
            def fn(d):
                for k, p in d.items():
                    if k.startswith('esc_hx_'):
                        for r in p['flexbodies'][1:]:
                            if len(r) > 3:
                                r[3].pop('disable', None)
            edit(f, models, fn)
        self.assertIn("car's headers visible", errors(header_unswitched))

        def intake_kept(f):
            edit(f, models, lambda d: d[inf['wrappers'][y]]['flexbodies'].append(
                ['x_airbox', ['x_body_grp'], [], {'disable': vis2.DISABLE}]))
        self.assertIn("car's intake meshes visible", errors(intake_kept))

        def undeclared_slider(f):
            edit(f, 'vehicles/x/esc_catalog_x.jbeam', lambda d: d['esc_catalog_x'].update(
                variables=[r for r in d['esc_catalog_x']['variables'] if r[0] != P.TURN_VAR]))
        self.assertIn('undeclared variables', errors(undeclared_slider))

        def no_materials(f):                                         # G: copied meshes lose their materials
            for k in [k for k in f if k.endswith('materials.json') and 'engine_swap_catalog' in k]:
                del f[k]
        self.assertIn('materials this car does not load', errors(no_materials))


if __name__ == '__main__':
    unittest.main()
