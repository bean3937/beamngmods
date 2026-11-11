"""End-to-end engine-model build on fake vanilla cars that have real (tiny) model files and body walls."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import jbeam, pipeline, dae  # noqa: E402
from tests.test_models import box_dae      # noqa: E402

HDR = ['id', 'posX', 'posY', 'posZ']


def corners(prefix, lo, hi, group):
    rows = [{'group': group}]
    k = 0
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                k += 1
                rows.append([f'{prefix}{k}', x, y, z])
    rows.append({'group': ''})
    return rows


def wall(ids, pts):
    """Two triangles for a quad given 4 node ids; returns (node rows, triangle rows)."""
    return [[i, *p] for i, p in zip(ids, pts)], [[ids[0], ids[1], ids[2]], [ids[0], ids[2], ids[3]]]


def make_car(car, eng_lo, eng_hi, fender_x, firewall_y):
    e = f'{car}_engine'
    body_nodes, body_tris = [], []
    for side, x in (('L', -fender_x), ('R', fender_x)):           # fenders (sides)
        n, t = wall([f'{car}f{side}{i}' for i in range(4)],
                    [(x, -2.2, 0.2), (x, firewall_y, 0.2), (x, firewall_y, 1.1), (x, -2.2, 1.1)])
        body_nodes += n
        body_tris += t
    n, t = wall([f'{car}fw{i}' for i in range(4)],                  # firewall (cabin)
                [(-0.8, firewall_y, 0.2), (0.8, firewall_y, 0.2), (0.8, firewall_y, 1.3), (-0.8, firewall_y, 1.3)])
    body_nodes += n
    body_tris += t
    n, t = wall([f'{car}fl{i}' for i in range(4)],                  # floor under the bay
                [(-0.8, -2.2, 0.2), (0.8, -2.2, 0.2), (0.8, firewall_y, 0.2), (-0.8, firewall_y, 0.2)])
    body_nodes += n
    body_tris += t
    hood_nodes, hood_tris = wall([f'{car}h{i}' for i in range(4)],
                                 [(-0.8, -2.2, 1.0), (0.8, -2.2, 1.0), (0.8, firewall_y, 1.0), (-0.8, firewall_y, 1.0)])
    tlo, thi = (eng_lo[0], eng_hi[1], eng_lo[2]), (eng_hi[0], eng_hi[1] + 0.3, eng_hi[2])
    parts = {
        car: {'information': {'name': car}, 'slotType': 'main',
              'slots': [['type', 'default', 'description'], [e, f'{e}_a', 'Engine'], [f'{car}_hood', f'{car}_hood', 'Hood']],
              'nodes': [HDR] + body_nodes, 'triangles': [['id1:', 'id2:', 'id3:']] + body_tris},
        f'{car}_hood': {'information': {'name': 'Hood'}, 'slotType': f'{car}_hood',
                        'nodes': [HDR] + hood_nodes, 'triangles': [['id1:', 'id2:', 'id3:']] + hood_tris},
        f'{e}_a': {
            'information': {'name': f'{car} engine'}, 'slotType': e,
            'slots': [['type', 'default', 'description'], [f'{car}_header', f'{car}_header', 'Exhaust Manifold'],
                      [f'{car}_transmission', f'{car}_transmission', 'Transmission']],
            'powertrain': [['type', 'name', 'inputName', 'inputIndex'], ['combustionEngine', 'mainEngine', 'dummy', 0]],
            'mainEngine': {'torque': [['rpm', 'torque'], [0, 0], [4000, 100 + 37 * sum(map(ord, car)) % 400]], 'idleRPM': 800, 'maxRPM': 6500,
                           'soundConfig': 'soundConfig', 'soundConfigExhaust': 'soundConfigExhaust'},
            'soundConfig': {'sampleName': 'S_engine'}, 'soundConfigExhaust': {'sampleName': 'S_exhaust'},
            'nodes': [HDR] + corners(f'{car}e', eng_lo, eng_hi, e),
            'flexbodies': [['mesh', '[group]:', 'nonFlexMaterials'],
                           [f'{car}_block', [e, f'{car}_transmission']]]},
        f'{car}_header': {'information': {'name': 'Header'}, 'slotType': f'{car}_header',
                          'nodes': [HDR, {'group': f'{car}_header'}, [f'{car}hd1', eng_lo[0] - 0.05, eng_lo[1] + 0.1, eng_lo[2] + 0.2],
                                    {'group': f'{car}_exhaust'}, [f'{car}dp1', eng_lo[0] - 0.05, eng_hi[1] + 0.1, eng_lo[2]], {'group': ''}],
                          'flexbodies': [['mesh', '[group]:', 'nonFlexMaterials'],
                                         [f'{car}_header_mesh', [e, f'{car}_header']],
                                         [f'{car}_downpipe', [f'{car}_exhaust']]]},
        f'{car}_transmission': {
            'information': {'name': '4M'}, 'slotType': f'{car}_transmission',
            'powertrain': [['type', 'name', 'inputName', 'inputIndex'], ['frictionClutch', 'clutch', 'mainEngine', 1],
                           ['manualGearbox', 'gearbox', 'clutch', 1]],
            'gearbox': {'gearRatios': [-3, 0, 3, 2, 1]},
            'nodes': [HDR] + corners(f'{car}t', tlo, thi, f'{car}_transmission')},
    }
    meshes = {f'{car}_block': (eng_lo, eng_hi),
              f'{car}_header_mesh': ((eng_lo[0] - 0.08, eng_lo[1] + 0.05, eng_lo[2] + 0.15), (eng_lo[0], eng_lo[1] + 0.3, eng_lo[2] + 0.3)),
              f'{car}_downpipe': ((eng_lo[0] - 0.08, eng_hi[1], 0.25), (eng_lo[0] - 0.02, eng_hi[1] + 0.3, 0.3))}
    return parts, box_dae(meshes)


class TestEngineModels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        g = self.game = os.path.join(self.tmp, 'BeamNG.drive')
        os.makedirs(os.path.join(g, 'content', 'vehicles'))
        for p in ('combustionEngine', 'manualGearbox', 'frictionClutch'):
            os.makedirs(os.path.join(g, 'lua', 'vehicle', 'powertrain'), exist_ok=True)
            open(os.path.join(g, 'lua', 'vehicle', 'powertrain', p + '.lua'), 'w').close()
        os.makedirs(os.path.join(g, 'lua', 'vehicle', 'controller', 'vehicleController'))
        open(os.path.join(g, 'lua', 'vehicle', 'controller', 'vehicleController', 'vehicleController.lua'), 'w').close()
        cars = {'x': ((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9), 0.45, -0.85),     # the target
                'y': ((-0.25, -1.5, 0.35), (0.25, -1.05, 0.8), 0.45, -0.85),  # smaller engine: fits x
                'z': ((-0.6, -1.6, 0.3), (0.6, -1.0, 0.9), 0.8, -0.85),       # 1.2 m wide: far too big for x
                'n': ((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9), 0.36, -0.85),      # tight bay: fenders 6 cm away
                'm': ((-0.37, -1.6, 0.3), (0.37, -1.0, 0.9), 0.6, -0.85)}     # 7 cm wider: a near miss in n
        for car, (lo, hi, fx, fw) in cars.items():
            parts, model = make_car(car, lo, hi, fx, fw)
            if car == 'y':      # 2nd engine option: same meshes, other torque -> shares the per-visual caches
                twin = json.loads(json.dumps(parts['y_engine_a']))
                twin['mainEngine']['torque'][-1][1] += 55
                parts['y_engine_b'] = twin
                half = json.loads(json.dumps(parts['y_engine_a']))      # mod bug: no intake/engine sound section
                half['mainEngine']['torque'][-1][1] += 77
                del half['soundConfig']
                parts['y_engine_c'] = half
                mute = json.loads(json.dumps(half))                     # no sound section at all
                mute['mainEngine']['torque'][-1][1] += 11
                del mute['soundConfigExhaust']
                parts['y_engine_d'] = mute
            with zipfile.ZipFile(os.path.join(g, 'content', 'vehicles', car + '.zip'), 'w') as z:
                z.writestr(f'vehicles/{car}/{car}.jbeam', json.dumps(parts))
                z.writestr(f'vehicles/{car}/{car}.dae', model)
                z.writestr(f'vehicles/{car}/info.json', json.dumps({'Name': car.upper()}))
        with zipfile.ZipFile(os.path.join(g, 'content', 'vehicles', 'common.zip'), 'w') as z:
            z.writestr('vehicles/common/main.materials.json', json.dumps(
                {'engine_mat': {'name': 'engine_mat', 'mapTo': 'engine_mat', 'class': 'Material',
                                'Stages': [{'baseColorMap': '/vehicles/common/engine_b.color.png'}]}}))
        with zipfile.ZipFile(os.path.join(g, 'content', 'art_sound.zip'), 'w') as z:
            for k in ('engine', 'exhaust'):
                z.writestr(f'art/sound/blends/S_{k}.sfxBlend2D.json', '{"samples": []}')
        self.mods = os.path.join(self.tmp, 'mods')
        os.makedirs(self.mods)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        zpath, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, **kw)
        with zipfile.ZipFile(zpath) as z:
            files = {n: z.read(n) for n in z.namelist()}
        parts = {}
        for n, b in files.items():
            if n.endswith('.jbeam'):
                ns = n.split('/')[1]
                for k, v in jbeam.loads(b.decode()).items():
                    parts[(ns, k)] = v
        return R, files, parts

    def did(self, R, part):
        return next(k for k, d in R['donors'].items() if d['origins'][0]['part'] == part)

    def test_models_fit_hide_and_validate(self):
        R, files, parts = self.build()
        self.assertEqual(R['validation']['errors'], [], json.dumps(R['validation']['errors'], indent=1)[:3000])
        self.assertGreater(R['validation']['engine_model_checks'], 0)
        x_pairs = R['engine_models']['pairs']['/vehicles/x/']
        y, z = self.did(R, 'y_engine_a'), self.did(R, 'z_engine_a')
        self.assertIn(x_pairs[y][0], ('fits', 'fits_after_move'))
        self.assertEqual(x_pairs[self.did(R, 'y_engine_b')][0], x_pairs[y][0])   # shared visuals, same verdict
        self.assertEqual(x_pairs[z][0], 'too_big')
        self.assertEqual(R['engine_models']['pairs']['/vehicles/n/'][self.did(R, 'm_engine_a')][0], 'does_not_fit')
        self.assertTrue(x_pairs[z][3])                                  # which car parts it crossed
        cat = parts[('x', 'esc_catalog_x')]
        esc_slot = next(r for r in cat['slots2'][1:] if r[0] == 'esc_engine')
        self.assertEqual(esc_slot[2], [])                               # no denyTypes: they freeze the parts menu
        self.assertIn('esc_d_' + y[6:], esc_slot[1])                    # offered: fits
        self.assertNotIn('esc_d_' + z[6:], esc_slot[1])                 # hidden: does not fit
        self.assertNotIn('esc_engine', esc_slot[1])
        self.assertNotIn('x_block', [r[0] for r in cat.get('flexbodies', [])[1:] if isinstance(r, list)])
        hdr = next(r for r in cat['slots2'][1:] if r[0] == 'esc_x_header')   # renamed: old choices cannot land here
        copy_part = parts[('x', hdr[3])]
        meshes = [r[0] for r in copy_part['flexbodies'][1:]]
        self.assertEqual(meshes, ['x_downpipe'])                        # header mesh moved, downpipe stays
        own = parts[('x', 'esc_m_own')]
        self.assertEqual(sorted(r[0] for r in own['flexbodies'][1:]), ['x_block', 'x_header_mesh'])
        donor_model = parts[('x', f'esc_m_{y}')]
        rows = donor_model['flexbodies'][1:]
        self.assertTrue(all(r[0].startswith('esc_') for r in rows))     # y's meshes copied (not loadable in x)
        self.assertTrue(all(r[1] == ['x_engine', 'x_transmission'] for r in rows))   # bound to x's engine
        # placement: y's gearbox face (y=-1.05) onto x's (-1.0), centred, bottoms aligned (0.35 -> 0.3), no turn
        pv = R['engine_models']['pairs']['/vehicles/x/'][y]
        block = next(r for r in rows if r[0].endswith('_y_block'))
        pos = block[3]['pos']
        self.assertAlmostEqual(pos['x'], 0.0, places=4)
        self.assertAlmostEqual(pos['y'], 0.05 + pv[1][1], places=4)
        self.assertAlmostEqual(pos['z'], -0.05 + pv[1][2], places=4)
        self.assertNotIn('rot', block[3])
        copied = {n for p, b in files.items() if p.endswith('.dae') for n in dae.scan_names(b)}
        self.assertTrue({r[0] for r in rows} <= copied)
        mats = json.loads(next(b for p, b in files.items() if p.endswith('esc_meshes.materials.json')))
        self.assertTrue(any(k.startswith('esc_') and v['mapTo'] == k for k, v in mats.items()))
        # the donor part has the Engine model slot; IDs did not change
        dp = parts[('common', y)]
        mslot = next(r for r in dp['slots2'][1:] if r[0].endswith('_model'))
        self.assertEqual(mslot[3], f'esc_m_{y}')

    def test_validator_catches_model_breakage(self):
        from swapcat.validate import validate_build
        zpath, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, validate=False)
        with zipfile.ZipFile(zpath) as zf:
            files = {n: zf.read(n) for n in zf.namelist()}
        vfs, index, _ = pipeline.scan(self.game, self.mods, log=lambda m: None)

        def errors(mutate):
            f = dict(files)
            mutate(f)
            out = []
            for e in validate_build(index, vfs, f, R, log=lambda m: None)['errors']:
                out += e['issues'] if isinstance(e, dict) else [e]
            return ' | '.join(out)
        self.assertEqual(errors(lambda f: None), '')

        def drop_meshes(f):
            for n in [n for n in f if n.endswith('.dae')]:
                del f[n]
        self.assertIn('not in any model file', errors(drop_meshes))

        def keep_car_engine(f):
            n = 'vehicles/x/esc_catalog_x.jbeam'
            d = jbeam.loads(f[n].decode())
            d['esc_catalog_x']['flexbodies'].append(['x_block', ['x_engine'], []])
            f[n] = jbeam.dumps(d).encode()
        self.assertIn("own engine meshes still present", errors(keep_car_engine))

        def lose_downpipe(f):
            n = 'vehicles/x/esc_models_x.jbeam'
            d = jbeam.loads(f[n].decode())
            for k, p in d.items():
                if k.startswith('esc_hx_'):
                    p['flexbodies'] = [p['flexbodies'][0]]
            f[n] = jbeam.dumps(d).encode()
        self.assertIn('car meshes lost', errors(lose_downpipe))

        def add_deny(f):          # would freeze BeamNG's parts menu (PartsBranch.vue)
            n = 'vehicles/x/esc_catalog_x.jbeam'
            d = jbeam.loads(f[n].decode())
            for r in d['esc_catalog_x']['slots2'][1:]:
                if r[0] == 'esc_engine':
                    r[2] = ['esc_d_000000000000']
            f[n] = jbeam.dumps(d).encode()
        self.assertIn('uses denyTypes', errors(add_deny))

    def test_missing_sound_sections(self):
        R, files, parts = self.build(validate=True)
        self.assertEqual(R['validation']['errors'], [], json.dumps(R['validation']['errors'], indent=1)[:2000])
        c = self.did(R, 'y_engine_c')
        self.assertNotIn('soundConfig', parts[('common', c)]['mainEngine'])      # dangling name dropped
        self.assertIn('soundConfigExhaust', parts[('common', c)])
        self.assertTrue(any('no soundConfig section' in n for n in R['donors'][c]['notes']))
        self.assertTrue(any(s['part'] == 'y_engine_d' and 'neither engine nor exhaust sound' in s['reason']
                            for s in R['skipped_donors']))

    def near_miss(self, **kw):
        R, files, parts = self.build(**kw)
        self.assertEqual(R['validation']['errors'], [], json.dumps(R['validation']['errors'], indent=1)[:2000])
        m, z = self.did(R, 'm_engine_a'), self.did(R, 'z_engine_a')
        slot = next(r for r in parts[('n', 'esc_catalog_n')]['slots2'][1:] if r[0] == 'esc_engine')
        verdict = R['engine_models']['pairs']['/vehicles/n/'][m][0]
        model = parts.get(('n', f'esc_m_{m}'))
        return verdict, 'esc_d_' + m[6:] in slot[1], 'esc_d_' + z[6:] in slot[1], model

    def test_tolerance_accepts_near_miss_only(self):
        verdict, offered, big, model = self.near_miss(fit_tolerance='normal')
        self.assertEqual((verdict, offered, big), ('fits_with_tolerance', True, False))
        self.assertIn('clips slightly', model['information']['name'])

    def test_show_clipping_offers_near_miss_but_never_too_big(self):
        verdict, offered, big, model = self.near_miss(show_clipping=True)
        self.assertEqual((verdict, offered, big), ('clips', True, False))
        self.assertIn('(clips)', model['information']['name'])
        verdict, offered, big, _m = self.near_miss(fit_tolerance='loose', show_clipping=True)
        self.assertTrue(offered)
        self.assertFalse(big)                                            # far too big stays hidden on Loose + clips

    def test_show_unfit_offers_with_own_model(self):
        R, files, parts = self.build(hide_unfit=False)
        z = self.did(R, 'z_engine_a')
        cat = parts[('x', 'esc_catalog_x')]
        esc_slot = next(r for r in cat['slots2'][1:] if r[0] == 'esc_engine')
        self.assertEqual(esc_slot[1], ['esc_engine'])                   # nothing hidden: plain catalog slot
        proxy = parts[('x', f'esc_m_{z}')]
        self.assertIn("own engine model", proxy['information']['name'])
        self.assertEqual(proxy['slots2'][1][3], 'esc_m_own')
        self.assertEqual(R['validation']['errors'], [], json.dumps(R['validation']['errors'], indent=1)[:3000])

    def test_no_engine_models_keeps_old_behaviour(self):
        R, files, parts = self.build(engine_models=False)
        cat = parts[('x', 'esc_catalog_x')]
        self.assertIn('x_block', [r[0] for r in cat['flexbodies'][1:] if isinstance(r, list)])
        self.assertFalse(any(n.endswith('.dae') for n in files))


if __name__ == '__main__':
    unittest.main()
