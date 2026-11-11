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
            # per-car parts (esc_m_own, ...) repeat in every car folder; within one folder names must be unique,
            # and a common part must be unique everywhere
            if (ns, name) in ours or (ns != COMMON and (COMMON, name) in ours) or                     (ns == COMMON and any(n == name for _d, n in ours)):
                res['errors'].append(f'duplicate generated part name {name} in {ns}')
            ours[(ns, name)] = True
            # a common part is visible from every car, so it must not clash with any folder
            for d in (index.ns if ns == COMMON else ov.dirs(ns)):
                if name in index.ns.get(d, {}):
                    res['errors'].append(f'part name {name} clashes with an existing part in {d}')
            ov.add_part(ns, name, part, path, _Src())
            # BeamNG's parts menu freezes on any slot that has unsuitable (denied) parts (PartsBranch.vue)
            for r in (part.get('slots2') or [])[1:]:
                if isinstance(r, list) and len(r) > 2 and r[2]:
                    res['errors'].append(f'{name}: slot {r[0]} uses denyTypes - freezes the parts menu')
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
        # cars without engine-model parts (mod cars): the Engine model slot is simply empty, as intended
        has_models = bool(report.get('engine_models', {}).get('pairs', {}).get(t.ns))
        new += sorted({f'{p}: {m}' for p, m in probs if m not in baseline
                       and not (p.endswith('_model/') and 'default part' in m and not has_models)})
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

        v2 = ((report.get('engine_models') or {}).get('v2') or {}).get(t.ns) if info.get('v2') else None
        if v2:
            from . import vis2
            sp = t.slot_path + vis2.MODE_SLOT + '/'
            eng_path = sp + vis2.CAT_SLOT + '/' + vis2.ENG_SLOT + '/'

            def eng_over(did):
                return {t.slot_path: info['catalog_part'], sp: vis2.mode_part(t.ns, 'donor'),
                        sp + vis2.CAT_SLOT + '/': v2['wrappers'][did], eng_path: did}

            def offered(did):
                return did in v2['wrappers']
        else:
            eng_path = t.slot_path + CATALOG_SLOT + '/'

            def eng_over(did):
                return {t.slot_path: info['catalog_part'], eng_path: did}

            def offered(did):
                return _offered(ov, t.ns, info['catalog_part'], CATALOG_SLOT, did)

        # engines
        pick = [info['default_catalog_engine']] if info.get('default_catalog_engine') else []
        pick += spread(eng_kind['vanilla'], donors_per_target // 2) + spread(eng_kind['mod'], donors_per_target // 2)
        if ti < broad_targets:
            pick = sorted(donors)          # every catalog engine in the first cars
        if v2:
            from .vis2 import sample_engines
            pick = sample_engines(v2, info.get('default_catalog_engine'), ti < broad_targets)
        prev = None
        for did in dict.fromkeys(pick):
            if offered(did):
                node = check_pair(t, pc, baseline, eng_over(did), eng_path, did, donors, '')
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
            pool = sorted(v2['wrappers']) if v2 else sorted(donors)
            for did, xid in zip(spread(pool, 3), spread(sorted(trans), 3)):
                if _offered(ov, t.ns, tinfo['catalog_part'], TRANS_SLOT, xid) and offered(did):
                    check_pair(t, pc, baseline, dict(eng_over(did), **tover(xid)), tslot, xid, trans,
                               'engine+transmission ')
    old_targets = [(t, i) for t, i in targets if not i.get('v2')]
    n, errs = check_models(ov, index, vfs, files, report, old_targets, fs, log)
    res['engine_model_checks'] = n
    res['errors'] += errs
    if len(old_targets) != len(targets):
        from .vis2 import check_vis2, check_intake_twins, check_fit_files
        fit = check_fit_files(files)
        if fit is not None:
            res['fit_meshes'] = fit['summary']
            res['errors'] += fit['errors'][:50]
        if any((i.get('joints') for i in ((report.get('engine_models') or {}).get('v2') or {}).values())):
            twins = check_intake_twins(files)
            res['intake_parts_invisible_abroad'] = len(twins)
            res['intake_parts_invisible_abroad_examples'] = twins[:20]
        n2, errs2, ex = check_vis2(ov, index, vfs, files, report, targets, mesh_lookup(vfs, files),
                                   material_lookup(vfs, files), log, broad_targets=broad_targets)
        res['engine_visuals_v2_checks'], res['engine_visuals_v2'] = n2, ex
        res['errors'] += errs2
        n += n2
    res['errors_count'] = len(res['errors'])
    log(f'  {res["pairs_checked"]} car x part pairs + {n} engine-model configurations checked, {len(res["errors"])} errors')
    return res


def _offered(ov, ns, catalog_part, slot_name, did):
    part = ov.get(ns, did)
    cat = ov.get(ns, catalog_part)
    if part is None or cat is None:
        return True        # resolution will report it
    slot = next((s for s in cat.slots if s['name'] == slot_name), None)
    return slot is None or part_fits(part.data, slot)


def mesh_lookup(vfs, files):
    """loadable(mesh, car ns): is the mesh in a model file that car loads (own folder, common, or ours)?"""
    from . import dae as _dae
    names_by_file = {}
    for p in vfs.files_under('vehicles/', '.dae'):
        try:
            names_by_file[p] = _dae.scan_names(vfs.read(p))
        except Exception:
            pass
    ours = set()
    for p, data in files.items():
        if p.lower().endswith('.dae'):
            ours |= set(_dae.scan_names(data))

    def loadable(mesh, ns):
        if mesh in ours:
            return True
        own = ns.strip('/') + '/'
        return any(mesh in names for f, names in names_by_file.items()
                   if f.startswith(own) or f.startswith('vehicles/common/'))
    return loadable


def material_lookup(vfs, files):
    """materials(mesh, car ns) -> (material names the mesh uses that this car cannot load,
    whether they are missing in the mesh's own car as well) - or None when the mesh is not found.
    A car loads the materials.json files of its own folder, of vehicles/common and ours (Phase 2 item G)."""
    from . import dae as _dae
    known = {}                     # folder ('vehicles/<x>/') -> {material names}

    def add(path, data):
        try:
            doc = jbeam.loads(data.decode('utf-8', 'replace'))
        except Exception:
            return
        folder = '/'.join(path.lower().split('/')[:2]) + '/'
        for k, v in (doc.items() if isinstance(doc, dict) else []):
            if isinstance(v, dict):
                known.setdefault(folder, set()).update({k, v.get('name') or k, v.get('mapTo') or k})
    for p in vfs.files_under('vehicles/', 'materials.json'):
        try:
            add(p, vfs.read(p))
        except Exception:
            pass
    ours = set()
    for p, data in files.items():
        if p.endswith('materials.json'):
            add(p, data)
            ours |= known.get('/'.join(p.lower().split('/')[:2]) + '/', set())
    everywhere = set().union(*known.values()) if known else set()
    where, cache = {}, {}
    for p in vfs.files_under('vehicles/', '.dae'):
        try:
            for n in _dae.scan_names(vfs.read(p)):
                where.setdefault(n, []).append(p)
        except Exception:
            pass
    our_dae = {}
    for p, data in files.items():
        if p.lower().endswith('.dae'):
            for n in _dae.scan_names(data):
                our_dae[n] = p

    def mats_of(path, mesh):
        if path not in cache:
            try:
                raw = files[path] if path in files else vfs.read(path)
                nodes = _dae.scene_nodes(raw)
                lib = _dae.library_items(raw, 'materials', 'material')
                cache[path] = {m: [lib.get(mid, (mid.replace('-material', ''), b''))[0] for _s, mid in d['materials']]
                               for m, d in nodes.items()}
            except Exception:
                cache[path] = {}
        return cache[path].get(mesh)

    def lookup(mesh, ns):
        own = ns.strip('/') + '/'
        if mesh in our_dae:
            path = our_dae[mesh]
        else:
            cand = where.get(mesh, [])
            path = next((f for f in cand if f.startswith(own)), None) or next(
                (f for f in cand if f.startswith('vehicles/common/')), None)
        if path is None:
            return None
        names = mats_of(path, mesh)
        if names is None:
            return None
        common = known.get('vehicles/common/', set())
        have = known.get(own, set()) | common | ours
        miss = {n for n in names if n not in have}
        home = '/'.join(path.split('/')[:2]) + '/'
        if mesh in our_dae:
            # a copy: its renamed (esc_) materials must be ours; an original name kept because no materials.json
            # defines it anywhere is the same gap the mesh has in its own car
            also = bool(miss) and all(not n.startswith('esc_') and n not in everywhere for n in miss)
        else:
            also = bool(miss) and all(n not in (known.get(home, set()) | common) for n in miss)
        return miss, also
    return lookup


def check_models(ov, index, vfs, files, report, targets, fs, log=print, per_car=5):
    """Engine-model checks for vanilla cars (see module docstring). Returns (pairs_checked, errors)."""
    import math as _m
    from . import dae as _dae
    from .visuals import HEADER_NAME, BODY_NAME
    em = report.get('engine_models') or {}
    pairs = em.get('pairs') or {}
    if not pairs:
        return 0, []
    errors, checked = [], 0
    # meshes the game can find: installed .dae files + the ones we generate
    names_by_file = {}
    for p in vfs.files_under('vehicles/', '.dae'):
        try:
            names_by_file[p] = _dae.scan_names(vfs.read(p))
        except Exception:
            pass
    ours = {}
    for p, data in files.items():
        if p.lower().endswith('.dae'):
            ours.update({n: p for n in _dae.scan_names(data)})
    our_mats = {}
    for p, data in files.items():
        if p.endswith('materials.json'):
            our_mats.update(json.loads(data.decode('utf-8')))
    for p, data in files.items():
        if p.lower().endswith('.dae'):
            for mid, (name, _raw) in _dae.library_items(data, 'materials', 'material').items():
                if name.startswith('esc_') and name not in our_mats:
                    errors.append(f'{p}: material {name} has no materials.json entry')

    def loadable(mesh, ns):
        if mesh in ours:
            return True
        own = ns.strip('/') + '/'
        return any(mesh in names for f, names in names_by_file.items()
                   if f.startswith(own) or f.startswith('vehicles/common/'))

    def meshes_of(tree):
        out = set()
        for n in tree.walk():
            for r in table_rows(n.part.data.get('flexbodies')):
                if isinstance(r.get('mesh'), str):
                    out.add(r['mesh'])
        return out

    def node_groups(tree):
        g = set()
        for n in tree.walk():
            for r in table_rows(n.part.data.get('nodes')):
                v = r.get('group')
                g |= set(v if isinstance(v, list) else [v] if isinstance(v, str) else [])
        return g

    for t, info in targets:
        cp = pairs.get(t.ns)
        if not cp:
            continue
        own_part = ov.get(t.ns, 'esc_m_own')
        if own_part is None or own_part.ns != t.ns:
            errors.append(f'{t.ns}: esc_m_own missing')
            continue
        own_meshes = {r.get('mesh') for r in table_rows(own_part.data.get('flexbodies'))}
        # every engine-model and header-copy part of this car: meshes loadable, transforms finite
        for name, p in ov.ns.get(t.ns, {}).items():
            if not name.startswith(('esc_m_', 'esc_hx_')):
                continue
            for r in table_rows(p.data.get('flexbodies')):
                m = r.get('mesh')
                if isinstance(m, str) and not loadable(m, t.ns):
                    errors.append(f'{t.ns} {name}: mesh {m} is not in any model file this car loads')
                if name.startswith('esc_m_') and name != 'esc_m_own' and not isinstance(r.get('pos'), dict):
                    errors.append(f'{t.ns} {name}: donor mesh {m} has no placement (pos)')
                for k in ('pos', 'rot'):
                    v = r.get(k)
                    if isinstance(v, dict) and not all(isinstance(v.get(a, 0), (int, float)) and _m.isfinite(v.get(a, 0)) for a in 'xyz'):
                        errors.append(f'{t.ns} {name}: {k} of {m} is not finite')
        pc = read_pc(vfs, t.pc_path) if t.pc_path else None
        base = resolve(index, t.ns, t.main, Choice(pc))
        base_meshes = meshes_of(base)
        header_meshes = {m for m in base_meshes if HEADER_NAME.search(m) and not BODY_NAME.search(m)}
        must_keep = base_meshes - own_meshes - header_meshes
        from .models import OFFERED
        offered = [d for d, v in cp.items() if v[0] in OFFERED]
        sample = [info.get('default_catalog_engine')] + offered[:: max(1, len(offered) // per_car)][:per_car]
        for did in dict.fromkeys(d for d in sample if d):
            v = cp.get(did)
            if v is None or v[0] not in OFFERED:
                continue
            slot = f'esc{did[6:12]}_model/'
            for choice, label in ((f'esc_m_{did}', 'donor'), ('esc_m_own', 'own')):
                over = {t.slot_path: info['catalog_part'], t.slot_path + CATALOG_SLOT + '/': did,
                        t.slot_path + CATALOG_SLOT + '/' + slot: choice}
                probs = []
                tree = resolve(ov, t.ns, t.main, Choice(pc, over), probs)
                got = meshes_of(tree)
                node = next((n for n in tree.walk() if n.path == t.slot_path + CATALOG_SLOT + '/' + slot), None)
                checked += 1
                issues = []
                if node is None or node.part.name != choice:
                    issues.append(f'Engine model slot did not resolve to {choice}')
                elif label == 'donor' and node.part.data.get('flexbodies'):
                    if v[0] not in OFFERED:
                        issues.append(f'donor model offered although the fit check said {v[0]}')
                    donor_meshes = {r.get('mesh') for r in table_rows(node.part.data.get('flexbodies'))}
                    left = (own_meshes & got) - donor_meshes      # same-family donors reuse the car's meshes
                    if left:
                        issues.append(f"car's own engine meshes still present with the donor model: {sorted(left)[:4]}")
                    groups = node_groups(tree)
                    for r in table_rows(node.part.data.get('flexbodies')):
                        miss = [g for g in (r.get('[group]:') or []) if g not in groups]
                        if miss:
                            issues.append(f'mesh {r.get("mesh")} binds to missing node groups {miss}')
                            break
                if label == 'own' and not own_meshes <= got:
                    issues.append(f"car's own engine meshes missing with 'Car's own engine model': {sorted(own_meshes - got)[:4]}")
                lost = must_keep - got
                if lost:
                    issues.append(f'car meshes lost (exhaust from the downpipe back etc. must stay): {sorted(lost)[:4]}')
                if issues:
                    errors.append({'car': t.ns, 'part': did, 'kind': f'engine model ({label})', 'issues': issues})
    return checked, errors
