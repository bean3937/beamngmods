"""Static validator: loads the generated files on top of the scanned sources and resolves
full slot trees for car x catalog-engine pairs, the way slotSystem.lua does.

Checks per pair (only problems the swap introduces count as errors; issues that already
exist in the car's own default config are reported as baseline):
  - every chosen/default part exists and fits its slot; no duplicate esc_ part names
  - generated JBeam parses (strict JSON and our SJSON reader)
  - exactly one combustion device 'mainEngine'; every powertrain inputName resolves
  - powertrain device / controller lua files exist
  - engine has a torque curve; soundConfig sections exist; blend + sample files exist
  - $variables referenced are declared; beams reference existing nodes
  - switching donor: no tuning slot path of the old donor exists under the new donor
"""
import json
import re

from . import jbeam
from .analyze import analyze_target, is_combustion
from .build import CATALOG_SLOT, TRANS_SLOT, var_refs, var_decls, strings_in
from .transmission import GEARBOX_TYPES
from .index import PartIndex, Choice, resolve, read_pc, namespace_of, COMMON
from .tables import table_rows, part_fits

_PATHISH = re.compile(r"[A-Za-z0-9_\-./ ]+\.(?:wav|ogg|mp3|flac|csv|xlsx)", re.I)
BUILTIN_DEVICES = {'combustionEngineMF'}


class Overlay(PartIndex):
    """Base index + generated parts, without mutating the base."""

    def __init__(self, base):
        super().__init__()
        self.ns = {k: dict(v) for k, v in base.ns.items()}
        self.by_type = {k: {t: list(n) for t, n in v.items()} for k, v in base.by_type.items()}


class FileSet:
    """Existence checks across sources + generated files."""

    def __init__(self, vfs, files):
        self.vfs = vfs
        self.out = {k.lower(): k for k in files}
        # controllers/devices may live in sub folders and are found by name at runtime
        # (controller.lua registerRelocatedController, powertrain.lua device factories)
        self.lua = {}
        for p in list(vfs.index) + list(self.out):
            if p.startswith(('lua/vehicle/', 'vehicles/')) and p.endswith('.lua'):
                self.lua.setdefault(p.rsplit('/', 1)[1][:-4], []).append(p)

    def lua_exists(self, kind, name):
        name = name.lower()
        return f'lua/vehicle/{kind}/{name}.lua' in self.vfs.index or name.split('/')[-1] in self.lua

    def exists(self, p):
        p = p.replace('\\', '/').lstrip('/')
        return p.lower() in self.out or self.vfs.exists(p)

    def read(self, p, files):
        p = p.replace('\\', '/').lstrip('/')
        if p.lower() in self.out:
            return files[self.out[p.lower()]]
        return self.vfs.read(p)


class _Src:
    kind, name, title, is_zip = 'output', 'engine_swap_catalog.zip', 'engine_swap_catalog', True


def merged(tree):
    """Approximate unifyParts: pre-order, later/deeper dict keys win, list rows appended."""
    out = {}
    for node in tree.walk():
        for k, v in node.part.data.items():
            if k in ('information', 'slotType', 'slots', 'slots2'):
                continue
            if isinstance(v, dict):
                if not isinstance(out.get(k), dict):
                    out[k] = {}
                out[k].update(v)
            elif isinstance(v, list):
                out.setdefault(k, []).append(v)    # list of tables
            else:
                out[k] = v
    return out


def rows_of(m, key):
    res = []
    for tbl in m.get(key, []) if isinstance(m.get(key), list) else []:
        res.extend(table_rows(tbl))
    return res


def check_tree(tree, fs, files, vfs):
    """Return list of issue strings for a resolved tree (not baseline-filtered)."""
    issues = []
    m = merged(tree)
    pt = rows_of(m, 'powertrain')
    names = [r.get('name') for r in pt]
    engines = [r for r in pt if is_combustion(r.get('type'))]
    if len(engines) != 1 or engines[0].get('name') != 'mainEngine':
        issues.append(f'powertrain: expected one combustion device named mainEngine, found '
                      f'{[(r.get("type"), r.get("name")) for r in engines]}')
    for r in pt:
        inp = r.get('inputName')
        if inp not in (None, 'dummy') and inp not in names:
            issues.append(f'powertrain: device {r.get("name")} input "{inp}" does not exist')
        if names.count(r.get('name')) > 1:
            issues.append(f'powertrain: duplicate device name {r.get("name")}')
        t = r.get('type')
        if isinstance(t, str) and t not in BUILTIN_DEVICES and not fs.lua_exists('powertrain', t):
            issues.append(f'powertrain: device type {t} has no lua/vehicle/powertrain/{t}.lua')
    gbs = [r for r in pt if r.get('name') == 'gearbox']
    if len(gbs) != 1 or gbs[0].get('type') not in GEARBOX_TYPES:
        issues.append(f'powertrain: vehicleController needs exactly one gearbox device named "gearbox", found '
                      f'{[r.get("type") for r in gbs]}')
    for r in rows_of(m, 'controller'):
        f = r.get('fileName')
        if isinstance(f, str) and not fs.lua_exists('controller', f):
            issues.append(f'controller: lua/vehicle/controller/{f}.lua missing')
    me = m.get('mainEngine', {})
    if 'torque' not in me:
        issues.append('mainEngine: no torque curve')
    for key in ('soundConfig', 'soundConfigExhaust'):
        sec_name = me.get(key)
        if not isinstance(sec_name, str):
            continue
        sec = m.get(sec_name)
        if not isinstance(sec, dict):
            issues.append(f'sound: section "{sec_name}" ({key}) missing')
            continue
        sample = sec.get('sampleName')
        if not isinstance(sample, str):
            continue
        folder = (sec.get('sampleFolder') or 'art/sound/blends/')
        bp = folder.rstrip('/') + '/' + sample + '.sfxBlend2D.json'
        if not fs.exists(bp):
            issues.append(f'sound: blend {bp} missing')
            continue
        try:
            blend = jbeam.loads(fs.read(bp, files).decode('utf-8', 'replace'))
        except jbeam.JBeamError as e:
            issues.append(f'sound: blend {bp} unreadable: {e}')
            continue
        for s in strings_in(blend.get('samples', [])):
            if '/' in s and not fs.exists(s):
                issues.append(f'sound: sample {s} (from {bp}) missing')
                break
    for s in strings_in(me):
        for p in _PATHISH.findall(s):
            p = p.strip()
            if '/' in p and not fs.exists(p):
                issues.append(f'file reference {p} missing')
    declared = set()
    for node in tree.walk():
        declared |= set(var_decls(node.part.data))
    used = var_refs({k: v for k, v in m.items() if k != 'variables'})
    for v in sorted(used - declared):
        issues.append(f'variable ${v} used but not declared')
    nodes = {r.get('id') for r in rows_of(m, 'nodes')}
    nodes.discard(None)
    dangling = 0
    for r in rows_of(m, 'beams'):
        a, b = r.get('id1:'), r.get('id2:')
        if isinstance(a, str) and isinstance(b, str) and (a not in nodes or b not in nodes) and not r.get('optional'):
            dangling += 1
    if dangling:
        issues.append(f'beams: {dangling} beams reference missing nodes')
    return issues


def _category(target_src_kind, donor_src_kind):
    return f'{"vanilla" if donor_src_kind == "vanilla" else "mod"}->{"vanilla" if target_src_kind == "vanilla" else "mod"}'


def validate_build(index, vfs, files, report, log=print, donors_per_target=6, broad_targets=2, tr=str):
    res = {'pairs_checked': 0, 'errors': [], 'baseline_issues': {}, 'by_category': {},
           'persistence_checks': 0, 'note': 'static checks only; nothing was run in BeamNG'}
    ov = Overlay(index)
    ours = {}
    for path, data in files.items():
        if not path.endswith('.jbeam'):
            continue
        try:
            json.loads(data.decode('utf-8'))
            doc = jbeam.loads(data.decode('utf-8'))
        except (ValueError, jbeam.JBeamError) as e:
            res['errors'].append(f'{path}: does not parse: {e}')
            continue
        ns = namespace_of(path.lower())
        for name, part in doc.items():
            if name in ours:
                res['errors'].append(f'duplicate generated part name {name}')
            ours[name] = ns
            # a common part is visible from every car, so it must not clash with any folder
            for d in (index.ns if ns == COMMON else ov.dirs(ns)):
                if name in index.ns.get(d, {}):
                    res['errors'].append(f'part name {name} clashes with an existing part in {d}')
            ov.add_part(ns, name, part, path, _Src())
    fs = FileSet(vfs, files)

    def by_source_kind(store):
        out = {'vanilla': [], 'mod': []}
        for did in sorted(store):
            out[kind_of(store, did)].append(did)
        return out

    def kind_of(store, did):
        src = store[did]['origins'][0]['source']
        return next((s.kind for s in vfs.sources if s.name == src), 'mod')

    def spread(seq, n):
        if len(seq) <= n:
            return list(seq)
        step = len(seq) / n
        return [seq[int(i * step)] for i in range(n)]

    donors = report['donors']
    trans = report.get('transmissions', {})
    eng_kind, trans_kind = by_source_kind(donors), by_source_kind(trans)
    ttargets = report.get('transmission_targets', {})

    vehicles = index.vehicles()
    targets = []
    for ns, info in sorted(report['targets'].items()):
        t = analyze_target(index, vfs, ns, vehicles[ns], tr)
        if t.reason is None:
            targets.append((t, info))
    log(f'  validating {len(targets)} cars...')

    def check_pair(t, pc, baseline, overrides, slot_path, did, store, label):
        """Resolve one configuration and record new issues; return the catalog node (or None)."""
        probs = []
        tree = resolve(ov, t.ns, t.main, Choice(pc, overrides), probs)
        new = sorted(i for i in set(check_tree(tree, fs, files, vfs)) if i not in baseline)
        new += sorted({f'{p}: {m}' for p, m in probs if m not in baseline})
        node = next((n for n in tree.walk() if n.path == slot_path), None)
        if node is None or node.part.name != did:
            new.append(f'catalog slot {slot_path} did not resolve to {did}')
        cat = label + _category(t.main.source.kind, kind_of(store, did))
        c = res['by_category'].setdefault(cat, {'pairs': 0, 'failed': 0})
        c['pairs'] += 1
        res['pairs_checked'] += 1
        if new:
            c['failed'] += 1
            res['errors'].append({'car': t.ns, 'part': did, 'name': store[did]['name'],
                                  'kind': label.strip() or 'engine', 'issues': new[:8]})
        return node

    def leak_check(t, did, node, prev):
        """The previous donor's tuning slot paths must not exist under this donor."""
        paths = {n.path for n in node.walk()} - {node.path} if node else set()
        if prev is not None:
            res['persistence_checks'] += 1
            leak = prev & paths
            if leak:
                res['errors'].append({'car': t.ns, 'part': did,
                                      'issues': [f'tuning slot paths shared with previous donor: {sorted(leak)[:3]}']})
        return paths

    for ti, (t, info) in enumerate(targets):
        pc = read_pc(vfs, t.pc_path) if t.pc_path else None
        base_probs = []
        base_tree = resolve(index, t.ns, t.main, Choice(pc), base_probs)
        eng_probs = []
        resolve(index, t.ns, t.base, None, eng_probs)     # issues the car's own engine already has
        baseline = set(check_tree(base_tree, fs, files, vfs)) | {m for _p, m in base_probs + eng_probs}
        if baseline:
            res['baseline_issues'][t.ns] = sorted(baseline)

        def eng_over(did):
            return {t.slot_path: info['catalog_part'], t.slot_path + CATALOG_SLOT + '/': did}

        # engines
        pick = [info['default_catalog_engine']] if info.get('default_catalog_engine') else []
        pick += spread(eng_kind['vanilla'], donors_per_target // 2) + spread(eng_kind['mod'], donors_per_target // 2)
        if ti < broad_targets:
            pick = sorted(donors)          # every catalog engine in the first cars
        prev = None
        for did in dict.fromkeys(pick):
            if _offered(ov, t.ns, info['catalog_part'], CATALOG_SLOT, did):
                node = check_pair(t, pc, baseline, eng_over(did), t.slot_path + CATALOG_SLOT + '/', did, donors, '')
                prev = leak_check(t, did, node, prev)

        # transmissions: with the car's own engine, then together with catalog engines
        tinfo = ttargets.get(t.ns)
        if not tinfo:
            continue
        tslot = tinfo['slot_path'] + TRANS_SLOT + '/'

        def tover(xid):
            return {tinfo['slot_path']: tinfo['catalog_part'], tslot: xid}
        pick = [tinfo['default_catalog_transmission']] if tinfo.get('default_catalog_transmission') else []
        pick += spread(trans_kind['vanilla'], donors_per_target // 2) + spread(trans_kind['mod'], donors_per_target // 2)
        if ti < broad_targets:
            pick = sorted(trans)
        prev = None
        for xid in dict.fromkeys(pick):
            if _offered(ov, t.ns, tinfo['catalog_part'], TRANS_SLOT, xid):
                node = check_pair(t, pc, baseline, tover(xid), tslot, xid, trans, 'transmission ')
                prev = leak_check(t, xid, node, prev)
        # under the engine catalog the transmission slot keeps its path only when it is a child of the
        # engine part (the catalog keeps that slot by name); test those combinations too
        if tinfo['slot_path'].startswith(t.slot_path):
            for did, xid in zip(spread(sorted(donors), 3), spread(sorted(trans), 3)):
                if _offered(ov, t.ns, tinfo['catalog_part'], TRANS_SLOT, xid):
                    check_pair(t, pc, baseline, dict(eng_over(did), **tover(xid)), tslot, xid, trans,
                               'engine+transmission ')
    res['errors_count'] = len(res['errors'])
    log(f'  {res["pairs_checked"]} car x part pairs checked, {len(res["errors"])} errors')
    return res


def _offered(ov, ns, catalog_part, slot_name, did):
    part = ov.get(ns, did)
    cat = ov.get(ns, catalog_part)
    if part is None or cat is None:
        return True        # resolution will report it
    slot = next((s for s in cat.slots if s['name'] == slot_name), None)
    return slot is None or part_fits(part.data, slot)
