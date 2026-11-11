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
          validate=True, excluded_sources=(), scanned=None):
    """scanned: (vfs, index, skipped) from an earlier scan() to avoid re-reading everything."""
    t0 = time.time()
    out_dir = out_dir or mods_dir
    vfs, index, skipped = scanned or scan(game_dir, mods_dir, log, progress)
    tr = Translator(game_dir, vfs)
    b = Builder(index, vfs, log=log, progress=progress or (lambda *a: None),
                include_car_mod_engines=include_car_mod_engines, tr=tr,
                excluded_sources=excluded_sources).run()
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
    zpath = zip_path(out_dir)
    log(f'Writing {zpath} ({len(files)} files)...')
    write_zip(zpath, files)
    for stale in _zip_locations(out_dir):
        if stale != zpath and os.path.isfile(stale):
            os.remove(stale)          # an older copy of our own output; two copies = duplicate parts
            log(f'Removed old copy {stale}')
    if validate:
        from .validate import validate_build
        log('Validating...')
        R['validation'] = validate_build(index, vfs, files, R, log=log, tr=tr)
    rpath = os.path.join(out_dir, OUTPUT_NAME.replace('.zip', '.report.json'))
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
        files = {n: (z.read(n) if n.endswith(('.jbeam', '.json')) else b'') for n in z.namelist()}
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
    ap.add_argument('--car-mod-engines', action='store_true', help='also use engines of car mods as donors')
    ap.add_argument('--no-validate', action='store_true')
    a = ap.parse_args(argv)
    mods = a.mods or settings.detect_mods_dir(a.game)
    if a.command == 'validate':
        res = validate_zip(a.game, mods, a.out or mods)
        print(json.dumps({k: v for k, v in res.items() if k != 'baseline_issues'}, indent=1)[:4000])
        return 1 if res.get('errors') else 0
    zpath, rpath, R = build(a.game, mods, a.out, include_car_mod_engines=a.car_mod_engines,
                            validate=not a.no_validate)
    v = R.get('validation') or {}
    return 1 if v.get('errors') else 0
