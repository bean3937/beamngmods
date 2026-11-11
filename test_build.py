"""End-to-end build on a tiny synthetic game folder + mods folder (no BeamNG needed)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import jbeam, pipeline  # noqa: E402


def car_parts(car, torque, sound, ecu_rev):
    """A minimal car shaped like vanilla: main -> engine(mounts, ecu, exhaust, transmission)."""
    return {
        car: {'information': {'name': car}, 'slotType': 'main',
              'slots': [['type', 'default', 'description'], [f'{car}_engine', f'{car}_engine_v8', 'Engine'],
                        [f'{car}_fueltank', f'{car}_fueltank', 'Tank']],
              'nodes': [['id', 'posX', 'posY', 'posZ'], ['b1', 0, 0, 0], ['b2', 1, 0, 0]],
              'beams': [['id1:', 'id2:'], ['b1', 'b2']]},
        f'{car}_fueltank': {'information': {'name': 'Tank'}, 'slotType': f'{car}_fueltank',
                            'energyStorage': [['type', 'name'], ['fuelTank', 'mainTank']],
                            'mainTank': {'energyType': 'gasoline', 'fuelCapacity': 50}},
        f'{car}_engine_v8': {
            'information': {'name': f'{car} V8 Engine', 'value': 100}, 'slotType': f'{car}_engine',
            'slots2': [['name', 'allowTypes', 'denyTypes', 'default', 'description'],
                       [f'{car}_enginemounts', [f'{car}_enginemounts'], [], f'{car}_enginemounts', 'Engine Mounts'],
                       [f'{car}_engine_ecu', [f'{car}_engine_ecu'], [], f'{car}_engine_ecu_a', 'Engine Management',
                        {'coreSlot': True}],
                       [f'{car}_exhaust', [f'{car}_exhaust'], [], f'{car}_exhaust', 'Exhaust'],
                       [f'{car}_transmission', [f'{car}_transmission'], [], f'{car}_transmission_4M', 'Transmission']],
            'powertrain': [['type', 'name', 'inputName', 'inputIndex'], ['combustionEngine', 'mainEngine', 'dummy', 0]],
            'mainEngine': {'torque': [['rpm', 'torque'], [0, 0], [3000, torque], [6000, torque / 2]],
                           'idleRPM': 800, 'maxRPM': 6500, 'torqueReactionNodes:': ['e1', 'e2', 'e3'],
                           'engineBlock': {'[engineGroup]:': ['engine_block']},
                           'energyStorage': 'mainTank', 'soundConfig': 'soundConfig',
                           'soundConfigExhaust': 'soundConfigExhaust', 'revLimiterRPM': '$revLimiterRPM'},
            'soundConfig': {'sampleName': sound + '_engine'},
            'soundConfigExhaust': {'sampleName': sound + '_exhaust'},
            'vehicleController': {'highShiftUpRPM': 6000},
            'variables': [['name', 'type', 'unit', 'category', 'default', 'min', 'max', 'title', 'description'],
                          ['$revLimiterRPM', 'range', 'rpm', 'Engine', ecu_rev, 3000, 9000, 'Rev limit', '']],
            'nodes': [['id', 'posX', 'posY', 'posZ'], {'engineGroup': 'engine_block'},
                      ['e1', 0, 0, 0], ['e2', 0, 1, 0], ['e3', 0, 0, 1]],
            'beams': [['id1:', 'id2:'], ['e1', 'e2'], ['e2', 'e3'], ['e1', 'e3']],
            'flexbodies': [['mesh', '[group]:'], [f'{car}_engine_mesh', ['engine']]]},
        f'{car}_enginemounts': {'information': {'name': 'Mounts'}, 'slotType': f'{car}_enginemounts',
                                'beams': [['id1:', 'id2:'], ['e1', 'b1'], ['e2', 'b2']]},
        f'{car}_engine_ecu_a': {'information': {'name': 'ECU A'}, 'slotType': f'{car}_engine_ecu',
                                'mainEngine': {'revLimiterRPM': ecu_rev}},
        f'{car}_engine_ecu_b': {'information': {'name': 'ECU B'}, 'slotType': f'{car}_engine_ecu',
                                'mainEngine': {'revLimiterRPM': ecu_rev + 500},
                                'slots': [['type', 'default'], [f'{car}_turbo', f'{car}_turbo_a']]},
        f'{car}_turbo_a': {'information': {'name': 'Turbo'}, 'slotType': f'{car}_turbo',
                           'mainEngine': {'turbocharger': 'turbocharger'}, 'turbocharger': {'wastegateStart': 10},
                           'flexbodies': [['mesh', '[group]:'], ['turbo_mesh', ['engine']]]},
        f'{car}_exhaust': {'information': {'name': 'Exhaust'}, 'slotType': f'{car}_exhaust',
                           'mainEngine': {'torqueModExhaust': [['rpm', 'torque'], [0, 0]]},
                           'nodes': [['id', 'posX', 'posY', 'posZ'], ['ex1', 0, -1, 0, {'isExhaust': 'mainEngine'}]],
                           'beams': [['id1:', 'id2:'], ['ex1', 'e1']]},
        f'{car}_transmission_4M': {'information': {'name': '4M'}, 'slotType': f'{car}_transmission',
                                   'powertrain': [['type', 'name', 'inputName', 'inputIndex'],
                                                  ['frictionClutch', 'clutch', 'mainEngine', 1],
                                                  ['manualGearbox', 'gearbox', 'clutch', 1]],
                                   'gearbox': {'gearRatios': [-3, 0, 3, 2, 1.4, 1]}},
    }


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        g = self.game = os.path.join(self.tmp, 'BeamNG.drive')
        os.makedirs(os.path.join(g, 'content', 'vehicles'))
        for p in ('lua/vehicle/powertrain/combustionEngine.lua', 'lua/vehicle/powertrain/manualGearbox.lua',
                  'lua/vehicle/powertrain/frictionClutch.lua', 'lua/vehicle/powertrain/torqueConverter.lua',
                  'lua/vehicle/powertrain/automaticGearbox.lua',
                  'lua/vehicle/controller/vehicleController/vehicleController.lua'):
            os.makedirs(os.path.dirname(os.path.join(g, p)), exist_ok=True)
            open(os.path.join(g, p), 'w').close()
        with open(os.path.join(g, 'integrity.json'), 'w') as fh:
            fh.write('{"version": "0.40.5.0"}')
        for car, tq, snd in (('alpha', 300, 'V8_a'), ('beta', 200, 'I4_b')):
            with zipfile.ZipFile(os.path.join(g, 'content', 'vehicles', car + '.zip'), 'w') as z:
                z.writestr(f'vehicles/{car}/{car}.jbeam', json.dumps(car_parts(car, tq, snd, 6000)))
                z.writestr(f'vehicles/{car}/info.json', json.dumps({'Name': car.title(), 'Brand': 'Test'}))
        with zipfile.ZipFile(os.path.join(g, 'content', 'vehicles', 'common.zip'), 'w') as z:
            z.writestr('vehicles/common/readme.txt', 'x')
        with zipfile.ZipFile(os.path.join(g, 'content', 'art_sound.zip'), 'w') as z:
            for s in ('V8_a', 'I4_b'):
                for k in ('engine', 'exhaust'):
                    z.writestr(f'art/sound/blends/{s}_{k}.sfxBlend2D.json', '{"eventName": "event:>x", "samples": []}')
        # mods: an engine pack adding an engine (custom sound) to alpha, and a car mod "gamma"
        self.mods = os.path.join(self.tmp, 'mods')
        os.makedirs(os.path.join(self.mods, 'repo'))
        pack = car_parts('alpha', 500, 'PackV12', 7000)['alpha_engine_v8']
        pack['information']['name'] = 'Pack V12'
        pack_part = {'alpha_engine_v12_pack': pack,
                     # a transmission pack part: automatic with a torque-converter tuning slot
                     'alpha_transmission_6A_pack': {
                         'information': {'name': 'Pack 6-Speed Automatic'}, 'slotType': 'alpha_transmission',
                         'slots': [['type', 'default', 'description'],
                                   ['alpha_converter', 'alpha_converter_stock', 'Torque Converter']],
                         'powertrain': [['type', 'name', 'inputName', 'inputIndex'],
                                        ['torqueConverter', 'torqueConverter', 'mainEngine', 1],
                                        ['automaticGearbox', 'gearbox', 'torqueConverter', 1]],
                         'gearbox': {'gearRatios': [-2.9, 0, 4.2, 2.6, 1.7, 1.3, 1.0, 0.8]},
                         'vehicleController': {'automaticModes': 'PRND21'},
                         'nodes': [['id', 'posX', 'posY', 'posZ'], ['tr1', 0, 0, 0]],
                         'flexbodies': [['mesh', '[group]:'], ['pack_trans_mesh', ['trans']]]},
                     'alpha_converter_stock': {'information': {'name': 'Stock converter'},
                                               'slotType': 'alpha_converter',
                                               'torqueConverter': {'converterDiameter': 0.3}},
                     'alpha_converter_race': {'information': {'name': 'Race converter'},
                                              'slotType': 'alpha_converter',
                                              'torqueConverter': {'converterDiameter': 0.25}}}
        # SJSON without root braces, as some packs ship it
        pack_txt = jbeam.dumps(pack_part).strip()[1:-1]
        with zipfile.ZipFile(os.path.join(self.mods, 'repo', 'enginepack.zip'), 'w') as z:
            z.writestr('vehicles/alpha/pack_v12.jbeam', pack_txt)
            for k in ('engine', 'exhaust'):
                z.writestr(f'art/sound/blends/PackV12_{k}.sfxBlend2D.json',
                           json.dumps({'samples': [[[f'art/sound/engine/packv12/{k}_1000.wav', 1000]]]}))
                z.writestr(f'art/sound/engine/packv12/{k}_1000.wav', b'RIFF....')
            z.writestr('vehicles/common/broken.jbeam', '{"x": [1, 2}')       # must not stop the build
        with zipfile.ZipFile(os.path.join(self.mods, 'repo', 'gamma.zip'), 'w') as z:
            z.writestr('vehicles/gamma/gamma.jbeam', json.dumps(car_parts('gamma', 250, 'V8_a', 6200)))
        with zipfile.ZipFile(os.path.join(self.mods, 'repo', 'disabled.zip'), 'w') as z:
            z.writestr('vehicles/delta/delta.jbeam', json.dumps(car_parts('delta', 250, 'V8_a', 6200)))
        with open(os.path.join(self.mods, 'db.json'), 'w') as fh:
            json.dump({'mods': {'disabled': {'active': False, 'fullpath': '/mods/repo/disabled.zip'}}}, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_and_validate(self):
        logs = []
        zpath, rpath, R = pipeline.build(self.game, self.mods, log=logs.append)
        self.assertTrue(os.path.isfile(zpath))
        v = R['validation']
        self.assertEqual(v['errors'], [], json.dumps(v['errors'], indent=1))
        self.assertGreater(v['pairs_checked'], 0)
        self.assertIn('mod->vanilla', v['by_category'])
        self.assertIn('vanilla->mod', v['by_category'])
        self.assertGreater(v['persistence_checks'], 0)
        # cars: alpha, beta (vanilla) and gamma (mod); delta is in a disabled mod
        self.assertEqual(sorted(R['targets']), ['/vehicles/alpha/', '/vehicles/beta/', '/vehicles/gamma/'])
        self.assertTrue(any(e['file'] == 'vehicles/common/broken.jbeam' for e in R['parse_errors']))
        self.assertTrue(any(s['name'] == 'disabled.zip' for s in R['skipped_sources']))
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            parts = {}
            for n in names:
                if n.endswith('.jbeam'):
                    parts.update(jbeam.loads(z.read(n).decode()))
        # mod sound copied under art/sound/esc and blend points at the copy
        self.assertTrue(any(n.startswith('art/sound/esc/enginepack/art/sound/engine/packv12/') for n in names))
        pack_id = next(k for k, d in R['donors'].items() if 'Pack V12' in d['name'])
        self.assertTrue(parts[pack_id]['soundConfig']['sampleFolder'].startswith('art/sound/esc/'))
        # catalog part keeps car physics and slots, loses the car's own ECU slot, adds the catalog slot
        cat = parts['esc_catalog_alpha']
        slot_names = [r[0] for r in cat['slots2'][1:]]
        # catalog engine sits before the drivetrain slot, so the transmission still overrides the engine
        self.assertEqual(slot_names, ['alpha_enginemounts', 'alpha_exhaust', 'esc_engine', 'alpha_transmission'])
        self.assertIn('torqueReactionNodes:', cat['mainEngine'])
        self.assertNotIn('torque', cat['mainEngine'])
        self.assertEqual(cat['slotType'], 'alpha_engine')
        # donor carries performance + sound, no geometry or car-bound keys
        d = parts[pack_id]
        self.assertNotIn('nodes', d)
        self.assertNotIn('torqueReactionNodes:', d['mainEngine'])
        self.assertIn('torque', d['mainEngine'])
        # tuning slot names are unique per donor -> old tuning parts cannot carry over
        tuning_names = {}
        for k, p in parts.items():
            if k.startswith('esc_e_'):
                for r in p.get('slots2', [])[1:]:
                    tuning_names.setdefault(r[0], []).append(k)
        self.assertTrue(tuning_names)
        self.assertTrue(all(len(v) == 1 for v in tuning_names.values()), tuning_names)
        # identical engines stored once: alpha and gamma V8 differ, so both exist; ECU B + turbo kept
        ecu_b = [p for k, p in parts.items() if k.startswith('esc_t_') and p['information']['name'] == 'ECU B']
        self.assertTrue(ecu_b and 'slots2' in ecu_b[0])
        # own engine is the default of each car's catalog
        self.assertEqual(R['targets']['/vehicles/alpha/']['default_catalog_engine'],
                         next(k for k, d in R['donors'].items() if d['origins'][0]['part'] == 'alpha_engine_v8'))

        # --- transmissions
        self.assertEqual(sorted(R['transmission_targets']), ['/vehicles/alpha/', '/vehicles/beta/', '/vehicles/gamma/'])
        for cat_name in ('transmission mod->vanilla', 'transmission vanilla->mod', 'engine+transmission vanilla->vanilla'):
            self.assertTrue(any(k.startswith(cat_name.split(' ')[0]) for k in v['by_category']), v['by_category'])
        tcat = parts['esc_tcatalog_alpha']
        self.assertEqual(tcat['information']['name'], 'Transmission catalog')
        self.assertEqual(tcat['slotType'], 'alpha_transmission')
        self.assertNotIn('gearbox', tcat)                 # gearbox config comes from the donor
        self.assertNotIn('powertrain', tcat)              # car stage removed, nothing after the gearbox here
        self.assertEqual([r[0] for r in tcat['slots2'][1:]], ['esc_transmission'])
        pack_x = next(k for k, d in R['transmissions'].items() if 'Pack 6-Speed' in d['name'])
        x = parts[pack_x]
        self.assertEqual([r[0] for r in x['powertrain'][1:]], ['torqueConverter', 'automaticGearbox'])
        self.assertNotIn('nodes', x)
        self.assertIn('gearRatios', x['gearbox'])
        conv_slot = x['slots2'][1]
        self.assertTrue(conv_slot[0].startswith('esx'))  # unique per donor -> no carry-over
        convs = [p for k, p in parts.items() if conv_slot[1][0] in (p.get('slotType') or [])]
        self.assertEqual(sorted(p['information']['name'] for p in convs), ['Race converter', 'Stock converter'])
        own_x = R['transmission_targets']['/vehicles/alpha/']['default_catalog_transmission']
        self.assertEqual(R['transmissions'][own_x]['origins'][0]['part'], 'alpha_transmission_4M')

    def test_rebuild_reuses_repo_location(self):
        # BeamNG moves mods\engine_swap_catalog.zip into mods\repo\ on first start
        repo_copy = os.path.join(self.mods, 'repo', 'engine_swap_catalog.zip')
        root_copy = os.path.join(self.mods, 'engine_swap_catalog.zip')
        with zipfile.ZipFile(repo_copy, 'w') as z:
            z.writestr('old.txt', 'old build')
        with open(root_copy, 'wb') as fh:
            fh.write(b'stale')
        zpath, _r, _R = pipeline.build(self.game, self.mods, log=lambda m: None, validate=False)
        self.assertEqual(os.path.normcase(zpath), os.path.normcase(repo_copy))
        self.assertFalse(os.path.exists(root_copy))
        with zipfile.ZipFile(repo_copy) as z:
            self.assertNotIn('old.txt', z.namelist())
        res = pipeline.validate_zip(self.game, self.mods, self.mods, log=lambda m: None)
        self.assertEqual(res['errors'], [])

    def test_validator_detects_breakage(self):
        from swapcat.validate import validate_build
        zpath, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, validate=False)
        with zipfile.ZipFile(zpath) as z:
            files = {n: z.read(n) for n in z.namelist()}
        vfs, index, _ = pipeline.scan(self.game, self.mods, log=lambda m: None)
        self.assertEqual(validate_build(index, vfs, files, R, log=lambda m: None)['errors'], [])

        def broken(mutate):
            f = dict(files)
            mutate(f)
            return validate_build(index, vfs, f, R, log=lambda m: None)['errors']

        def drop_tuning(f):
            for n in [n for n in f if 'esc_tuning_' in n]:
                del f[n]
        errs = broken(drop_tuning)
        self.assertTrue(any('not found' in i for e in errs if isinstance(e, dict) for i in e['issues']), errs)

        def drop_sound(f):
            for n in [n for n in f if n.startswith('art/sound/esc/') and n.endswith('.wav')]:
                del f[n]
        errs = broken(drop_sound)
        self.assertTrue(any('sample' in i for e in errs if isinstance(e, dict) for i in e['issues']), errs)

        def bad_powertrain(f):
            n = next(n for n in f if 'esc_engines_' in n)
            d = jbeam.loads(f[n].decode())
            for p in d.values():
                p['powertrain'][1][1] = 'notMainEngine'
            f[n] = jbeam.dumps(d).encode()
        errs = broken(bad_powertrain)
        self.assertTrue(any('powertrain' in i for e in errs if isinstance(e, dict) for i in e['issues']), errs)

        def clash(f):
            n = next(n for n in f if 'esc_engines_' in n)
            d = jbeam.loads(f[n].decode())
            d['alpha_engine_v8'] = next(iter(d.values()))
            f[n] = jbeam.dumps(d).encode()
        errs = broken(clash)
        self.assertTrue(any('clashes' in e for e in errs if isinstance(e, str)), errs)

    def test_identical_engines_deduplicated(self):
        # a second pack shipping the very same engine data in another folder
        with zipfile.ZipFile(os.path.join(self.mods, 'repo', 'copy.zip'), 'w') as z:
            z.writestr('vehicles/beta/beta_copy.jbeam',
                       json.dumps({'beta_engine_copy': car_parts('beta', 200, 'I4_b', 6000)['beta_engine_v8']}))
        _z, _r, R = pipeline.build(self.game, self.mods, log=lambda m: None, validate=False)
        beta = [d for d in R['donors'].values() if any(o['part'] in ('beta_engine_v8', 'beta_engine_copy')
                                                         for o in d['origins'])]
        self.assertEqual(len(beta), 1)
        self.assertEqual(len(beta[0]['origins']), 2)


if __name__ == '__main__':
    unittest.main()
