"""Scan -> build -> write -> validate, shared by the GUI and the command line."""
import json
import os
import sys
import time

from . import settings
from .build import Builder, write_zip
from .index import PartIndex
from .names import Translator
from .vfs import build_vfs, OUTPUT_NAME


PHASE1_NAME = 'engine_swap_catalog_phase1.zip'
# Phase 1 test selection: two vanilla cars, a handful of engines (label substrings, see IN_GAME_TEST_PHASE1.md)
PHASE1 = {'cars': ['bastion', 'etk800'],
          'engines': ['Vanilla Bruckell Bastion: 6.5L V8', 'Vanilla ETK: 2.0L I4 Diesel', 'Vanilla ETK: 3.0L I6',
                      'Vanilla Cherrier FCV: 1.6L I4']}
PHASE2_NAME = 'engine_swap_catalog_phase2.zip'
# Phase 2 test build: engine visuals v2 on every vanilla car with every catalog engine (IN_GAME_TEST_ENGINE_VISUALS.md)
PHASE2 = {'cars': None, 'engines': None}
PHASE3_NAME = 'engine_swap_catalog_phase3.zip'
# Phase 3 test build: Phase 2 + foreign intake/turbo meshes + engine-gearbox / header-exhaust fillers
PHASE3 = {'cars': None, 'engines': None, 'phase3': True}
PHASE4_NAME = 'engine_swap_catalog_phase4.zip'
# Phase 4 test build: Phase 3 with generated bellhousing adapters and bent exhaust pipes (cleaned up in Blender)
# instead of the stretched unit tube. shift_max: the fit-check move (m) above which no adapter is made.
BLENDER_DEFAULT = settings.detect_blender()
# Phase 4 checkpoint pictures: the pairs the Phase 4 prompt names (car folder, engine label part)
PREVIEW_PAIRS = [('bastion', 'Vanilla Ibishu BX-Series: 5.5L V8'), ('bastion', 'Vanilla ETK: 4.4L V8 ttSport'),
                 ('etk800', 'Vanilla Bruckell Bastion: 3.5L V6'), ('bastion', 'Vanilla Autobello Piccolina: 1.1L'),
                 ('moonhawk', 'Vanilla Autobello Piccolina: 1.1L'), ('legran', 'Vanilla Soliad Lansdale: 2.2L I4')]
PHASE4 = {'cars': None, 'engines': None, 'phase3': True, 'phase4': True, 'shift_max': 0.15,
          'blender': BLENDER_DEFAULT}


def scan(game_dir, mods_dir, log=print, progress=None):
    progress = progress or (lambda stage, i, n: None)
    if not os.path.isdir(os.path.join(game_dir, 'content', 'vehicles')):
        raise FileNotFoundError(f'Not a BeamNG.drive folder (no content/vehicles): {game_dir}')
    log(f'Game folder: {game_dir} (version {settings.game_version(game_dir) or "unknown"})')
    log(f'Mods folder: {mods_dir}')
    vfs, skipped = build_vfs(game_dir, mods_dir, log=log)
    log(f'Mounted {len(vfs.sources)} sources, {len(vfs.index)} files')
    index = PartIndex()
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    index.load_from_vfs(vfs, progress=lambda i, n: progress('jbeam', i, n))
    log(f'Parsed {sum(len(v) for v in index.ns.values())} parts in {len(index.ns)} folders, '
        f'{len(index.errors)} unreadable files')
    return vfs, index, skipped


def _zip_locations(out_dir):
    # BeamNG's Mod Manager moves a zip dropped in mods\ into mods\repo\ on first start
    return [os.path.join(out_dir, 'repo', OUTPUT_NAME), os.path.join(out_dir, OUTPUT_NAME)]


def zip_path(out_dir):
    """Where the catalog zip is (or should go): the existing copy, else the mods folder root."""
    return next((p for p in _zip_locations(out_dir) if os.path.isfile(p)), _zip_locations(out_dir)[1])


def build(game_dir, mods_dir, out_dir=None, log=print, progress=None, include_car_mod_engines=False,
          validate=True, excluded_sources=(), scanned=None, engine_models=True, hide_unfit=True,
          fit_tolerance='strict', show_clipping=False, visuals_v2=None, output_name=None, previews=None):
    """scanned: (vfs, index, skipped) from an earlier scan() to avoid re-reading everything."""
    t0 = time.time()
    out_dir = out_dir or mods_dir
    vfs, index, skipped = scanned or scan(game_dir, mods_dir, log, progress)
    tr = Translator(game_dir, vfs)
    b = Builder(index, vfs, log=log, progress=progress or (lambda *a: None),
                include_car_mod_engines=include_car_mod_engines, tr=tr,
                excluded_sources=excluded_sources, engine_models=engine_models, hide_unfit=hide_unfit,
                fit_tolerance=fit_tolerance, show_clipping=show_clipping, visuals_v2=visuals_v2).run()
    R = b.rep.data
    R['game_version'] = settings.game_version(game_dir)
    R['game_dir'], R['mods_dir'] = game_dir, mods_dir
    R['sources'] = [{'name': s.name, 'kind': s.kind, 'title': s.title, 'files': len(s.files)}
                    for s in vfs.sources]
    R['skipped_sources'] = [{'name': n, 'reason': r} for n, r in skipped]
    R['parse_errors'] = [{'file': f, 'source': s, 'error': e} for f, s, e in index.errors]
    files = b.files()
    b.finish_report(files)
    os.makedirs(out_dir, exist_ok=True)
    # a test build gets its own name and never touches (or removes) the normal catalog zip
    zpath = os.path.join(out_dir, output_name) if output_name else zip_path(out_dir)
    log(f'Writing {zpath} ({len(files)} files)...')
    write_zip(zpath, files)
    for stale in ([] if output_name else _zip_locations(out_dir)):
        if stale != zpath and os.path.isfile(stale):
            os.remove(stale)          # an older copy of our own output; two copies = duplicate parts
            log(f'Removed old copy {stale}')
    if previews and b.fit is not None:
        R['previews'] = b.fit_previews(previews, visuals_v2.get('blender'), PREVIEW_PAIRS, log=log)
    if validate:
        from .validate import validate_build
        log('Validating...')
        R['validation'] = validate_build(index, vfs, files, R, log=log, tr=tr)
    rpath = os.path.join(out_dir, (output_name or OUTPUT_NAME).replace('.zip', '.report.json'))
    R['stats']['seconds'] = round(time.time() - t0, 1)
    with open(rpath, 'w', encoding='utf-8') as fh:
        json.dump(R, fh, indent=1, ensure_ascii=False, default=str)
    log(f'Report: {rpath}')
    st = R['stats']
    log(f"Done in {st['seconds']}s: {st['catalog_engines']} catalog engines, {st['tuning_parts']} tuning parts, "
        f"{st['targets']} vehicles, {st['skipped_targets']} vehicles skipped, {st['skipped_donors']} engines skipped; "
        f"{st['catalog_transmissions']} catalog transmissions for {st['transmission_targets']} vehicles, "
        f"{st['skipped_transmissions']} transmissions skipped")
    return zpath, rpath, R


def validate_zip(game_dir, mods_dir, out_dir, log=print, broad_targets=2):
    """Validate an already built zip (+ its .report.json) against the sources."""
    import zipfile
    from .validate import validate_build
    with open(os.path.join(out_dir, OUTPUT_NAME.replace('.zip', '.report.json')), encoding='utf-8') as fh:
        R = json.load(fh)
    with zipfile.ZipFile(zip_path(out_dir)) as z:
        # audio only needs to exist; reading 500 MB of samples is pointless
        files = {n: (z.read(n) if n.lower().endswith(('.jbeam', '.json', '.dae')) else b'') for n in z.namelist()}
    vfs, index, _ = scan(game_dir, mods_dir, log)
    return validate_build(index, vfs, files, R, log=log, tr=Translator(game_dir, vfs),
                          broad_targets=broad_targets)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Build or validate the BeamNG engine swap catalog mod')
    s = settings.load()
    ap.add_argument('command', nargs='?', default='build', choices=['build', 'validate'])
    ap.add_argument('--game', default=s.get('game_dir') or settings.default_game_dir())
    ap.add_argument('--mods', default=s.get('mods_dir'))
    ap.add_argument('--out', help='output folder (default: the mods folder)')
    ap.add_argument('--car-mod-engines', action='store_true', default=bool(s.get('car_mod_engines')),
                    help='also use engines of car mods as donors (default: the GUI setting)')
    ap.add_argument('--no-validate', action='store_true')
    # every option defaults to what the GUI saved last, so command-line and GUI builds match
    ap.add_argument('--no-engine-models', action='store_true', default=not s.get('engine_models', True),
                    help='keep every car\'s own engine model')
    ap.add_argument('--fit', choices=['strict', 'normal', 'loose'], default=s.get('fit_tolerance', 'strict'),
                    help='how much a donor engine model may clip into inner body panels')
    ap.add_argument('--show-clipping', action='store_true', default=bool(s.get('show_clipping')),
                    help='also offer engines that clip a little (labelled "clips"); never far too big ones')
    ap.add_argument('--show-unfit', action='store_true', default=not s.get('hide_unfit', True),
                    help='offer engines that do not fit a vanilla car (with the car\'s own engine model)')
    ap.add_argument('--phase1-test', action='store_true',
                    help='experimental engine visuals test build (Bastion + ETK 800 only) as '
                         'engine_swap_catalog_phase1.zip in --out; never replaces the normal catalog')
    ap.add_argument('--phase2-test', action='store_true',
                    help='experimental engine visuals v2 on every vanilla car, as engine_swap_catalog_phase2.zip in '
                         '--out; never replaces the normal catalog')
    ap.add_argument('--phase3-test', action='store_true',
                    help='experimental Phase 3 (Phase 2 + intake/turbo meshes on foreign engines + joint fillers), as '
                         'engine_swap_catalog_phase3.zip in --out; never replaces the normal catalog')
    ap.add_argument('--phase4-test', action='store_true',
                    help='experimental Phase 4 (Phase 3 with generated bellhousing adapters and bent exhaust pipes), '
                         'as engine_swap_catalog_phase4.zip in --out; never replaces the normal catalog')
    ap.add_argument('--shift-max', type=float, default=PHASE4['shift_max'],
                    help='with --phase4-test: largest fit-check move (m, sideways/up) that still gets an adapter')
    ap.add_argument('--blender', default=s.get('blender') or PHASE4['blender'],
                    help='path to blender.exe (optional: rounds and smooths the generated adapters and pipes)')
    ap.add_argument('--previews', help='with --phase4-test: render checkpoint pictures into this folder')
    ap.add_argument('--v2-cars', help='with --phase2/3/4-test: only these car folders (comma separated)')
    a = ap.parse_args(argv)
    mods = a.mods or settings.detect_mods_dir(a.game)
    if a.command == 'validate':
        res = validate_zip(a.game, mods, a.out or mods)
        print(json.dumps({k: v for k, v in res.items() if k != 'baseline_issues'}, indent=1)[:4000])
        return 1 if res.get('errors') else 0
    extra = {}
    if a.phase1_test:
        if not a.out:
            ap.error('--phase1-test needs --out (a folder outside the mods folder)')
        extra = {'visuals_v2': PHASE1, 'output_name': PHASE1_NAME}
    if a.phase2_test:
        if not a.out:
            ap.error('--phase2-test needs --out (a folder outside the mods folder)')
        cars = [c.strip() for c in a.v2_cars.split(',')] if a.v2_cars else None
        extra = {'visuals_v2': dict(PHASE2, cars=cars), 'output_name': PHASE2_NAME}
    if a.phase3_test:
        if not a.out:
            ap.error('--phase3-test needs --out (a folder outside the mods folder)')
        cars = [c.strip() for c in a.v2_cars.split(',')] if a.v2_cars else None
        extra = {'visuals_v2': dict(PHASE3, cars=cars), 'output_name': PHASE3_NAME}
    if a.phase4_test:
        if not a.out:
            ap.error('--phase4-test needs --out (a folder outside the mods folder)')
        cars = [c.strip() for c in a.v2_cars.split(',')] if a.v2_cars else None
        extra = {'visuals_v2': dict(PHASE4, cars=cars, shift_max=a.shift_max, blender=a.blender),
                 'output_name': PHASE4_NAME, 'previews': a.previews}
    if not extra and not a.no_engine_models:
        # version 2.0: the normal build uses the engine visuals of Phase 4 (same as the GUI)
        extra = {'visuals_v2': dict(PHASE4, shift_max=a.shift_max, blender=a.blender)}
    zpath, rpath, R = build(a.game, mods, a.out, include_car_mod_engines=a.car_mod_engines,
                            validate=not a.no_validate, engine_models=not a.no_engine_models,
                            hide_unfit=not a.show_unfit, fit_tolerance=a.fit, show_clipping=a.show_clipping,
                            **extra)
    v = R.get('validation') or {}
    return 1 if v.get('errors') else 0
