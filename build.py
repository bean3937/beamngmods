"""Generator: builds engine_swap_catalog.zip from the scanned sources.

Output layout (all part names prefixed 'esc_' so they never clash with game/mod parts):
  vehicles/common/engine_swap_catalog/esc_engines_NN.jbeam  donor engines (one copy each)
  vehicles/common/engine_swap_catalog/esc_tuning_NN.jbeam   donor tuning parts (deduplicated)
  vehicles/<car>/esc_catalog_<car>.jbeam                    the "Engine catalog" part of each car
  art/sound/esc/<source>/...                                 sound files copied from mods

Part structure per car (see NOTES.md "Design"):
  <car engine slot>  -> esc_catalog_<car>   (car's own engine physics/visuals, mounts,
                                             transmission, exhaust, oil pan, radiator, n2o)
     esc_engine slot  -> esc_e_<id>         (donor mainEngine perf + sound + powertrain row)
        esc<id>_<slot> -> esc_t_<id>        (donor tuning parts: ECU, intake/turbo, internals...)
Tuning slot *names* contain the donor id, so their slot paths differ per donor and a
previous donor's tuning choices can never be applied to another donor.
"""
import copy
import hashlib
import json
import os
import re
import time
import traceback
import zipfile

from . import jbeam
from .analyze import (INTERFACE_KEYS, GEOMETRY_LISTS, ENGINE_NAME, analyze_target, classify_slot,
                      engine_row, find_donors, is_combustion, is_node_ref, referenced_sections,
                      powertrain_rows, donor_label, Donor)
from .index import COMMON, Choice, resolve, read_pc
from .transmission import (analyze_transmission, find_transmission_donors, TRANS_SECTIONS, GEARBOX_NAME,
                           TransDonor)
from .tables import table_rows, part_slots, slots2_table

CATALOG_SLOT = 'esc_engine'
TRANS_SLOT = 'esc_transmission'
# controllers that belong to the engine itself (others, e.g. 'large_roller', drive car hardware)
ENGINE_CONTROLLERS = {'compressionBrake', 'twoStepLaunch', 'nitrousOxideInjection'}
OUT_DIR = 'vehicles/common/engine_swap_catalog/'
SOUND_DIR = 'art/sound/esc/'
VAR_HEADER = ["name", "type", "unit", "category", "default", "min", "max", "title", "description"]
DONOR_DICTS = ('vehicleController', 'turbocharger', 'supercharger', 'govenor')
ASSET_EXT = ('.wav', '.ogg', '.mp3', '.flac', '.csv', '.xlsx', '.json')
_VAR_REF = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)')
_PATHISH = re.compile(r"[A-Za-z0-9_\-./ ]+\.(?:wav|ogg|mp3|flac|csv|xlsx|json)", re.I)


def chash(obj):
    s = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha1(s.encode('utf-8')).hexdigest()


def safe(s):
    return re.sub(r'[^A-Za-z0-9_]+', '_', s).strip('_').lower()[:40] or 'x'


def strings_in(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings_in(v)


def var_refs(obj):
    return {m for s in strings_in(obj) for m in _VAR_REF.findall(s)}


def var_decls(part_data):
    out = {}
    for r in table_rows(part_data.get('variables')):
        n = r.get('name')
        if isinstance(n, str):
            out[n.lstrip('$')] = r
    return out


def var_table(rows):
    tbl = [list(VAR_HEADER)]
    for r in rows:
        row = [r.get(h, '') for h in VAR_HEADER]
        extra = {k: v for k, v in r.items() if k not in VAR_HEADER}
        if extra:
            row.append(extra)
        tbl.append(row)
    return tbl


def strip_node_refs(d):
    return {k: v for k, v in d.items() if not is_node_ref(k, v)}


def map_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [map_strings(v, fn) for v in obj]
    return obj


class Report:
    def __init__(self):
        self.data = {'generated': time.strftime('%Y-%m-%d %H:%M:%S'), 'game_version': None,
                     'sources': [], 'skipped_sources': [], 'parse_errors': [],
                     'targets': {}, 'skipped_targets': {}, 'donors': {}, 'skipped_donors': [],
                     'transmission_targets': {}, 'skipped_transmission_targets': {}, 'transmissions': {},
                     'skipped_transmissions': [],
                     'excluded_pairs': [], 'assets': {'copied': 0, 'missing': []},
                     'warnings': [], 'stats': {}}

    def warn(self, msg):
        self.data['warnings'].append(msg)


class Builder:
    def __init__(self, index, vfs, log=print, progress=None, include_car_mod_engines=False,
                 tr=str, excluded_sources=(), engine_models=True, hide_unfit=True, fit_tolerance='strict',
                 show_clipping=False, visuals_v2=None):
        self.index, self.vfs, self.log, self.tr = index, vfs, log, tr
        # EXPERIMENTAL: engine visuals architecture 2 (vis2.py). {'cars': [car folder names] or None = every
        # vanilla car, 'engines': [label substrings] or None = every engine}
        self.v2 = visuals_v2
        self.v2_rows = None       # vis2.RowMaker
        self._vis_groups = None   # (side groups, bind groups) of the donor being converted
        self._last_groups = None
        self.v2_info = {}         # car ns -> report info
        self.engine_models, self.hide_unfit = engine_models, hide_unfit
        self.fit_tolerance, self.show_clipping = fit_tolerance, show_clipping
        self.models = None        # ModelBuilder (vanilla cars only)
        self.fit = None           # Phase 4: fitmesh.FitStore (generated adapters / pipes)
        self.model_parts = {}     # car ns -> {part name: data} for vehicles/<car>/esc_models_<car>.jbeam
        self.model_hdr = {}       # car ns -> {header slot name: (new slot type, {part: copy})}
        self.excluded_sources = set(excluded_sources)   # mod names the user unticked as donors
        self.progress = progress or (lambda stage, i, n: None)
        self.include_car_mod_engines = include_car_mod_engines
        self.rep = Report()
        self.tuning = {}          # part name -> part data (slotType list grows)
        self.tuning_cache = {}    # (file, name) -> part name or None
        self.tuning_notes = {}    # (file, name) -> notes from converting that part (incl. children)
        self.tuning_missing = {}  # (file, name) -> missing asset paths (incl. children)
        self.live_tuning = set()
        self.engines = {}         # donor id -> {'data':..., 'labels':[...], 'origins':[...]}
        self.assets = {}          # out path -> bytes
        self.asset_by_hash = {}   # sha1 -> out path
        self.asset_alias = {}     # wanted out path -> stored out path
        self.donor_of_part = {}   # (ns, part name) -> donor id
        self.trans = {}           # transmission id -> same shape as self.engines
        self.trans_of_part = {}   # (ns, part name) -> transmission id
        self.tcatalogs = {}       # ns -> (filename, {name: data})
        self.catalogs = {}        # ns -> (filename, {name: data})

    # ------------------------------------------------------------ assets
    def _src_key(self, src):
        return safe(src.name.rsplit('.', 1)[0])

    def _copy_asset(self, path, src):
        """Copy a mod file into the output; return new game path (or None if missing)."""
        p = path.replace('\\', '/').lstrip('/')
        if not self.vfs.exists(p):
            return None
        owner = self.vfs.source_of(p)
        if owner.kind == 'vanilla':
            return p
        new = SOUND_DIR + self._src_key(owner) + '/' + self.vfs.real_path(p)
        if new in self.asset_alias:
            return self.asset_alias[new]
        data = self.vfs.read(p)
        h = hashlib.sha1(data).hexdigest()
        if h in self.asset_by_hash:          # identical file shipped by several mods: store once
            new_final = self.asset_by_hash[h]
        else:
            new_final = self.asset_by_hash[h] = new
            self.assets[new] = data
        self.asset_alias[new] = new_final
        return new_final

    def _rewrite_paths(self, s, src, missing):
        def fix(m):
            raw = m.group(0)
            cand = raw.strip()
            if '/' not in cand:
                return raw
            new = self._copy_asset(cand, src)
            if new is None:
                missing.append(cand)
                return raw
            return raw.replace(cand, new)
        return _PATHISH.sub(fix, s) if _PATHISH.search(s) else s

    def _blend_path(self, folder, sample):
        base = (folder or 'art/sound/blends/').replace('\\', '/').lstrip('/')
        if not base.endswith('/'):
            base += '/'
        for ext in ('.sfxBlend2D.json',):
            p = base + sample + ext
            if self.vfs.exists(p):
                return p
        return None

    def _fix_sound_section(self, sec, src, missing):
        """Copy a mod sound blend (+ its samples) and point sampleFolder at the copy."""
        name = sec.get('sampleName')
        if not isinstance(name, str):
            return sec
        bp = self._blend_path(sec.get('sampleFolder'), name)
        if bp is None:
            missing.append(f"{sec.get('sampleFolder') or 'art/sound/blends/'}{name}.sfxBlend2D.json")
            return sec
        owner = self.vfs.source_of(bp)
        if owner.kind == 'vanilla':
            return sec
        try:
            blend = jbeam.loads(self.vfs.read(bp).decode('utf-8', 'replace'))
        except jbeam.JBeamError as e:
            missing.append(f'{bp} (unreadable: {e})')
            return sec
        blend = map_strings(blend, lambda s: self._rewrite_paths(s, owner, missing))
        folder = SOUND_DIR + self._src_key(owner) + '/' + bp.rsplit('/', 1)[0] + '/'
        new_bp = folder + self.vfs.real_path(bp).rsplit('/', 1)[1]
        self.assets[new_bp] = jbeam.dumps(blend).encode('utf-8')
        sec = dict(sec)
        sec['sampleFolder'] = folder
        sec['sampleName'] = new_bp.rsplit('/', 1)[1][:-len('.sfxBlend2D.json')]
        return sec

    # ------------------------------------------------------------ section carrying
    def carry(self, part, is_engine, notes, missing):
        """Performance/sound content of a donor part, without geometry or car-bound keys."""
        d, out = part.data, {}
        me = d.get('mainEngine')
        if isinstance(me, dict):
            out['mainEngine'] = {k: v for k, v in strip_node_refs(me).items() if k not in INTERFACE_KEYS}
        refs = set(referenced_sections(me)) | set(DONOR_DICTS) | {'soundConfig', 'soundConfigExhaust'}
        for k in sorted(refs):
            if isinstance(d.get(k), dict):
                out[k] = strip_node_refs(d[k])
        ctrl = []
        for r in table_rows(d.get('controller')):
            f = str(r.get('fileName', ''))
            if f == 'vehicleController':
                continue
            if f.startswith('powertrainControl/') or f.split('/')[-1] in ENGINE_CONTROLLERS:
                ctrl.append(r)
            else:
                notes.append(f'{part.name}: controller "{f}" is car-specific, left out')
        if ctrl:
            keys = ['fileName'] + sorted({k for r in ctrl for k in r if k != 'fileName'})
            out['controller'] = [keys] + [[r.get(k, '') for k in keys] for r in ctrl]
            for r in ctrl:
                n = r.get('name') or r.get('fileName')
                if isinstance(d.get(n), dict):
                    out[n] = strip_node_refs(d[n])
        if is_engine:
            r = engine_row(d)[0]
            extra = {k: v for k, v in r.items() if k not in ('type', 'name', 'inputName', 'inputIndex')}
            row = [r['type'], ENGINE_NAME, 'dummy', r.get('inputIndex', 0)] + ([extra] if extra else [])
            out['powertrain'] = [['type', 'name', 'inputName', 'inputIndex'], row]
        else:
            extra_pt = [r for r in powertrain_rows(d)]
            if extra_pt:
                notes.append(f'{part.name}: dropped powertrain devices {[r.get("type") for r in extra_pt]}')
        if d.get('variables'):
            out['variables'] = var_table(var_decls(d).values())
        dropped = [k for k, v in d.items() if k not in out and isinstance(v, dict)
                   and k not in ('information',) and k not in GEOMETRY_LISTS and k != 'mainTank'
                   and not k.startswith(('soundConfig',))]
        if dropped and is_engine:
            notes.append(f'{part.name}: car-bound sections left with the car: {sorted(dropped)}')
        # sounds and other referenced files
        for k in list(out):
            if isinstance(out[k], dict) and 'sampleName' in out[k]:
                out[k] = self._fix_sound_section(out[k], part.source, missing)
        out = map_strings(out, lambda s: self._rewrite_paths(s, part.source, missing))
        return out

    def carry_trans(self, part, stage, sections, notes, missing):
        """Transmission data of a donor part: stage devices, their config sections, shift data."""
        d, out = part.data, {}
        for k in sorted(sections):
            if isinstance(d.get(k), dict):
                out[k] = strip_node_refs(d[k])
        if stage:
            hdr = ['type', 'name', 'inputName', 'inputIndex']
            rows = []
            for r in stage:
                extra = {k: v for k, v in r.items() if k not in hdr}
                rows.append([r.get('type'), r.get('name'), r.get('inputName'), r.get('inputIndex', 1)]
                            + ([extra] if extra else []))
            out['powertrain'] = [hdr] + rows
        elif powertrain_rows(d):
            notes.append(f'{part.name}: dropped powertrain devices {[r.get("type") for r in powertrain_rows(d)]}')
        for r in table_rows(d.get('controller')):
            notes.append(f'{part.name}: controller "{r.get("fileName")}" is car-specific, left out')
        if d.get('variables'):
            out['variables'] = var_table(var_decls(d).values())
        return map_strings(out, lambda s: self._rewrite_paths(s, part.source, missing))

    # ------------------------------------------------------------ tuning tree
    def convert_slot(self, ns, slot, notes, missing, stack, name_prefix='', perf=None):
        cls = classify_slot(self.index, ns, slot, perf)
        if cls != 'tuning':
            return None, cls
        conv = {}
        for c in self.index.candidates(ns, slot):
            if perf and powertrain_rows(c.data):
                notes.append(f'option {c.name} in slot {slot["name"]} not offered: it adds powertrain devices')
                continue
            mine = []
            n = self.convert_tuning_part(ns, c, mine, missing, stack, perf)
            broken = [x for x in mine if x.startswith('BROKEN: ')]
            if broken and c.name != slot['default']:
                # a bug inside an optional choice only removes that choice
                notes.append(f'option {c.name} in slot {slot["name"]} not offered: {broken[0][8:]}')
                notes.extend(x for x in mine if not x.startswith('BROKEN: '))
                continue
            notes.extend(mine)
            if n:
                conv[c.name] = n
        if not conv:
            notes.append(f'tuning slot {slot["name"]}: no usable parts')
            return None, 'empty'
        stype = 'esc_s_' + chash([sorted(set(conv.values())), slot['allowTypes']])[:12]
        for n in set(conv.values()):
            st = self.tuning[n]['slotType']
            if stype not in st:
                st.append(stype)
        default = slot['default'] or ''
        if default:
            if default in conv:
                default = conv[default]
            elif self.index.get(ns, default) is not None:
                # the game loads a default even when its slotType does not match (slotSystem.lua),
                # but it can never be re-selected in the menu; treat as broken, do not guess a fix
                notes.append(f'BROKEN: tuning slot {slot["name"]} (type {slot["allowTypes"]}) defaults to '
                             f'"{default}" which has slotType {self.index.get(ns, default).data.get("slotType")}')
                default = ''
            else:
                notes.append(f'tuning slot {slot["name"]}: default part "{default}" missing or unusable -> left empty')
                default = ''
        opts = {k: v for k, v in slot['opts'].items() if k in ('coreSlot',)}
        return dict(name=name_prefix + slot['name'], allowTypes=[stype], denyTypes=[],
                    default=default, description=slot['description'], opts=opts), 'tuning'

    def convert_tuning_part(self, ns, part, notes, missing, stack, perf=None):
        key = (part.file, part.name, frozenset(perf) if perf else None,
               (frozenset(self._vis_groups[0]), frozenset(self._vis_groups[1])) if self._vis_groups else None)
        if key in self.tuning_cache:
            notes.extend(self.tuning_notes.get(key, []))   # cached parts still report problems
            missing.extend(self.tuning_missing.get(key, []))
            return self.tuning_cache[key]
        if part.name in stack or len(stack) > 20:
            notes.append(f'slot cycle at {part.name}')
            return None
        self.tuning_cache[key] = None
        mine, mm = [], []
        content = self.carry_trans(part, None, perf, mine, mm) if perf else self.carry(part, False, mine, mm)
        slots, hdr = [], []
        for s in part_slots(part.data):
            # 'esc_' prefix: old flat .pc "parts" maps (keyed by slot name) never hit our slots
            ns_slot, _cls = self.convert_slot(ns, s, mine, mm, stack + [part.name], 'esc_', perf)
            if ns_slot:
                slots.append(ns_slot)
            elif _cls == 'exhaust' and s['default'] and self.index.get(ns, s['default']) is not None:
                hdr.append(self.index.get(ns, s['default']))
        if slots:
            content['slots2'] = slots2_table(slots)
        if self._vis_groups and not perf:
            from . import vis2
            vis2.add_vis(content, self.v2_rows.rows(part, ns, *self._vis_groups, mine, self.vfs, hdr),
                         var_table, var_decls)
        info = part.data.get('information', {})
        content['information'] = {'name': info.get('name') or part.name,
                                  'authors': info.get('authors', ''), 'value': info.get('value', 0)}
        name = 'esc_t_' + chash(content)[:12]
        if name not in self.tuning:
            content['slotType'] = []
            self.tuning[name] = content
        self.tuning_cache[key] = name
        self.tuning_notes[key] = mine
        self.tuning_missing[key] = mm
        notes.extend(mine)
        missing.extend(mm)
        return name

    # ------------------------------------------------------------ donors
    def donor_context(self, donor, targets):
        """Namespace in which the donor's child slots are looked up."""
        p = donor.part
        if p.ns != COMMON:
            return p.ns
        st = set(p.data.get('slotType') if isinstance(p.data.get('slotType'), list) else [p.data.get('slotType')])
        for t in targets:
            if t.base is not None and set(t.slot['allowTypes']) & st:
                return t.ns
        return COMMON

    def _has_torque(self, content, slots, depth=0):
        if 'torque' in content.get('mainEngine', {}):
            return True
        for s in slots:
            child = self.tuning.get(s['default']) if s['default'] else None
            if child and depth < 20 and self._has_torque(
                    child, [_slot_from_row(r) for r in child.get('slots2', [])[1:]], depth + 1):
                return True
        return False

    def _find_sound_sections(self, part, ctx_ns, content, slots, notes, missing, home_main=None):
        """Sound sections named by mainEngine must exist. Some car mods keep them in another part of the engine's
        subtree (FR17); carry them over from there, otherwise the engine would be silent -> skip it."""
        chain = [content] + list(self._default_chain(slots))
        merged_me = {}
        for c in chain:
            merged_me.update(c.get('mainEngine') or {})
        absent = []
        for key in ('soundConfig', 'soundConfigExhaust'):
            name = merged_me.get(key)
            if not isinstance(name, str) or any(isinstance(c.get(name), dict) for c in chain):
                continue
            roots = [part] + ([home_main] if home_main is not None else [])   # a car's own engine: its whole car
            found = next((n.part for r in roots for n in resolve(self.index, ctx_ns, r).walk()
                          if isinstance(n.part.data.get(name), dict)), None)
            if found is None:
                absent.append(key)
                continue
            content[name] = self._fix_sound_section(strip_node_refs(found.data[name]), found.source, missing)
            notes.append(f'sound section {name} taken from {found.name}')
        if len(absent) == 2:
            raise ValueError('neither engine nor exhaust sound section is defined anywhere in the engine')
        for key in absent:
            # the mod defines this sound nowhere; combustionEngine.lua skips a missing section, so the engine sounds
            # exactly as in its own car. Drop the dangling name (no reference to a section that does not exist).
            for c in chain:
                (c.get('mainEngine') or {}).pop(key, None)
            notes.append(f'source mod defines no {key} section: that sound is silent, as in its own car')

    def _default_chain(self, slots, depth=0):
        for s in slots:
            child = self.tuning.get(s['default']) if s['default'] else None
            if child and depth < 20:
                yield child
                yield from self._default_chain([_slot_from_row(r) for r in child.get('slots2', [])[1:]], depth + 1)

    def convert_donor(self, donor, ctx_ns, own_type=None):
        notes, missing = [], []
        part = donor.part
        self._vis_groups = self._donor_groups(part, ctx_ns) if self.v2 else None
        try:
            return self._convert_donor(donor, ctx_ns, own_type, notes, missing, part)
        finally:
            self._last_groups, self._vis_groups = self._vis_groups, None

    def _donor_groups(self, part, ctx_ns):
        """Node groups of the donor's engine side (classification as in visuals.analyze_engine)."""
        from .visuals import analyze_engine, tree_vars
        try:
            tree = resolve(self.index, ctx_ns, part)
            ev = analyze_engine(self.index, ctx_ns, tree, tree_vars(tree))
            return ev.side_groups, ev.bind_ok
        except Exception:          # no visuals for this donor; its performance is unaffected
            return None

    def _convert_donor(self, donor, ctx_ns, own_type, notes, missing, part):
        content = self.carry(part, True, notes, missing)
        slots = []
        for s in part_slots(part.data):
            ns_slot, _cls = self.convert_slot(ctx_ns, s, notes, missing, [part.name])
            if ns_slot:
                slots.append(ns_slot)
        if not self._has_torque(content, slots):
            raise ValueError('no torque curve in the engine or its default tuning parts')
        self._find_sound_sections(part, ctx_ns, content, slots, notes, missing, getattr(donor, 'home_main', None))
        return self._finish_donor(part, ctx_ns, content, slots, notes, missing, 'esc_e_', 'esc',
                                  [own_type] if own_type else None, CATALOG_SLOT, 'esc_d_')

    def convert_trans_donor(self, donor, ctx_ns, own_type=None):
        notes, missing = [], []
        part = donor.part
        sections = {r.get('name') for r in donor.stage} | TRANS_SECTIONS
        content = self.carry_trans(part, donor.stage, sections, notes, missing)
        if donor.after:
            notes.append('devices after the gearbox stay with the car: '
                         + ', '.join(f'{r.get("type")}:{r.get("name")}' for r in donor.after))
        slots = []
        for s in part_slots(part.data):
            ns_slot, _cls = self.convert_slot(ctx_ns, s, notes, missing, [part.name], perf=sections)
            if ns_slot:
                slots.append(ns_slot)
        chain = [content] + list(self._default_chain(slots))
        # stepped gearboxes use gearRatios; cvtGearbox uses min/maxGearRatio (often in a child part)
        ratio_keys = ('gearRatios', 'minGearRatio', 'maxGearRatio')
        if not any(k in (c.get(GEARBOX_NAME) or {}) for c in chain for k in ratio_keys):
            raise ValueError('no gear ratios in the gearbox section or its default tuning parts')
        return self._finish_donor(part, ctx_ns, content, slots, notes, missing, 'esc_x_', 'esx',
                                  [own_type] if own_type else None, TRANS_SLOT, 'esc_dx_')

    def _finish_donor(self, part, ctx_ns, content, slots, notes, missing, id_prefix, slot_prefix,
                      own_types, catalog_type, deny_prefix):
        broken = list(dict.fromkeys(n[len('BROKEN: '):] for n in notes if n.startswith('BROKEN: ')))
        if broken:
            raise ValueError('child-slot defaults have the wrong slot type (mod bug): ' + '; '.join(broken[:3])
                             + (f' (+{len(broken) - 3} more)' if len(broken) > 3 else ''))
        if missing:
            raise ValueError('sound/asset files missing in the source: ' + ', '.join(sorted(set(missing))[:3]))
        chain = list(self._default_chain(slots))
        used = var_refs([{k: v for k, v in c.items() if k != 'variables'} for c in [content] + chain])
        decl = set(var_decls(content)) | {n for c in chain for n in var_decls(c)}
        if used - decl:
            raise ValueError('uses variables that no part declares: ' + ', '.join('$' + v for v in sorted(used - decl)))
        # variables referenced but declared in parts that stayed behind (exhaust, transmission)
        declared = set(var_decls(content).keys())
        need = var_refs({k: v for k, v in content.items() if k != 'variables'}) - declared
        if need:
            found = {}
            for node in resolve(self.index, ctx_ns, part).walk():
                for n, r in var_decls(node.part.data).items():
                    if n in need and n not in found:
                        found[n] = r
            if found:
                rows = list(var_decls(content).values()) + list(found.values())
                content['variables'] = var_table(rows)
        # tuning slot paths must be unique per donor (no tuning carry-over between donors)
        base_hash = chash([content, [s['allowTypes'] + [s['name'], s['default']] for s in slots]])
        did = id_prefix + base_hash[:12]
        for s in slots:
            s['name'] = f'{slot_prefix}{base_hash[:6]}_{s["name"]}'
        if slots:
            content['slots2'] = slots2_table(slots)
        info = part.data.get('information', {})
        content['information'] = {'authors': info.get('authors', ''), 'value': info.get('value', 0)}
        content['slotType'] = own_types or [catalog_type, deny_prefix + base_hash[:12]]
        return did, content, notes, missing

    # ------------------------------------------------------------ catalog part per car
    def build_catalog(self, t, default_donor, deny):
        base = t.base.data
        cat, notes = {}, []
        donor_owned = set(referenced_sections(base.get('mainEngine'))) | set(DONOR_DICTS) | \
            {'soundConfig', 'soundConfigExhaust'}
        ctrl_rows = table_rows(base.get('controller'))
        donor_owned |= {r.get('name') or r.get('fileName') for r in ctrl_rows if r.get('fileName') != 'vehicleController'}
        for k, v in base.items():
            if k in ('information', 'slotType', 'slots', 'slots2', 'variables') or k in donor_owned:
                continue
            if k == 'mainEngine':
                cat[k] = {kk: vv for kk, vv in v.items() if kk in INTERFACE_KEYS or is_node_ref(kk, vv)}
            elif k == 'powertrain':
                tbl = v
                hdr = tbl[0] if tbl and isinstance(tbl[0], list) else None
                rows = [r for r in tbl[1:] if not (isinstance(r, list) and r and is_combustion(r[0]))]
                if hdr and any(isinstance(r, list) for r in rows):
                    cat[k] = [hdr] + rows
            elif k == 'controller':
                vc = [r for r in v[1:] if isinstance(r, dict) or (isinstance(r, list) and r and r[0] == 'vehicleController')]
                if any(isinstance(r, list) for r in vc):
                    cat[k] = [v[0]] + vc
            else:
                cat[k] = copy.deepcopy(v)
        # child slots: keep car-side slots, flatten the car's own tuning parts to geometry only
        problems = []
        tree = resolve(self.index, t.ns, t.main, Choice(_pc(self, t)), problems)
        eng_node = next((n for n in tree.walk() if n.path == t.slot_path), None)
        chosen = {c.slot['name']: c for c in (eng_node.children if eng_node else [])}
        slots, var_pool, classes = [], dict(var_decls(base)), []
        for s in part_slots(base):
            cls = classify_slot(self.index, t.ns, s)
            if cls != 'tuning':
                slots.append(s)
                classes.append(cls)
                continue
            node = chosen.get(s['name'])
            if node is None:
                continue
            for sub in node.walk():
                self._merge_geometry(cat, sub.part.data, donor_owned)
                var_pool.update(var_decls(sub.part.data))
                notes.append(f'{s["name"]}: kept geometry of {sub.part.name}')
        if t.ns in self.v2_info:
            # v2: the car's engine meshes live in the Engine model parts, its exhaust slots in the per-engine parts
            drop = set(self.v2_info[t.ns]['remove_meshes'])
            tbl = cat.get('flexbodies')
            if isinstance(tbl, list) and tbl:
                cat['flexbodies'] = [tbl[0]] + [r for r in tbl[1:] if not (isinstance(r, list) and r and r[0] in drop)]
            moved = [s['name'] for s, c in zip(slots, classes) if c == 'exhaust']
            slots = [s for s, c in zip(slots, classes) if c != 'exhaust']
            classes = [c for c in classes if c != 'exhaust']
            notes.append(f'v2: engine meshes moved to the Engine model parts: {sorted(drop)}; '
                         f'exhaust slots moved to the catalog engine parts: {moved}')
        elif t.ns in self.model_parts:
            own = self.models.own_meshes(t.ns)
            tbl = cat.get('flexbodies')
            if isinstance(tbl, list) and tbl:
                cat['flexbodies'] = [tbl[0]] + [r for r in tbl[1:] if not (isinstance(r, list) and r and r[0] in own)]
            hmap = self.model_hdr.get(t.ns, {})
            for s in slots:
                if s['name'] in hmap:
                    stype, names, new_name = hmap[s['name']]
                    s['name'] = new_name
                    s['allowTypes'], s['denyTypes'] = [stype], []
                    s['default'] = names.get(s['default'], '') if s['default'] else ''
            notes.append(f'engine visuals moved to the Engine model slot: {sorted(own)}')
        # catalog slot before the drivetrain slots: siblings merge in slot order and later wins, so
        # the donor beats the car's exhaust etc., while the transmission still wins over the engine
        # (in vanilla the transmission is the engine's child, and children overwrite parents)
        at = classes.index('drivetrain') if 'drivetrain' in classes else len(slots)
        # Hidden engines are left out of an allow-list instead of using denyTypes: BeamNG's parts menu
        # (ui-vue PartsBranch.vue partsList) pushes "unsuitable" parts into the reactive suitablePartNames
        # array it is computed from, which loops forever and freezes the game as soon as a slot has any.
        if deny:
            allow = sorted(st for e in self.engines.values() for st in e['data']['slotType']
                           if st.startswith('esc_d_') and st not in deny)
        else:
            allow = [CATALOG_SLOT]
        own_t = [f'esc_own_{safe(t.ns)}'] if default_donor and default_donor[1] else []
        if t.ns in self.v2_info:
            from . import vis2
            n = vis2.add_vis_group(cat, self.v2_info[t.ns]['engine_groups'])
            self.v2_info[t.ns]['vis_nodes'] = n
            notes.append(f'v2: node group {vis2.VIS_GROUP} added to {n} engine nodes')
            slots.insert(at, dict(name=vis2.MODE_SLOT, allowTypes=[f'esc_vm_{vis2.car_key(t.ns)}'], denyTypes=[],
                                  default=vis2.mode_part(t.ns, 'donor'), description='Engine model',
                                  opts={'coreSlot': True}))
        else:
            slots.insert(at, dict(name=CATALOG_SLOT, allowTypes=allow + own_t, denyTypes=[],
                                  default=default_donor[0] if default_donor else '',
                                  description='Catalog engine', opts={'coreSlot': True}))
        need = var_refs(cat)
        rows = [var_pool[n] for n in sorted(need) if n in var_pool]
        if t.ns in self.v2_info:
            from . import vis2
            rows += vis2.SLIDER_DECLS + ([vis2.FLIP_DECL] if self.v2_info[t.ns].get('flip_any') else [])
        if rows:
            cat['variables'] = var_table(rows)
        cat['slots2'] = slots2_table(slots)
        info = base.get('information', {})
        cat['information'] = {'authors': 'engine_swap_catalog', 'name': 'Engine catalog',
                              'value': info.get('value', 0)}
        cat['slotType'] = copy.deepcopy(base['slotType'])
        return cat, notes

    def build_trans_catalog(self, tt, default_donor, deny):
        base = tt.base.data
        stage_names = {r.get('name') for r in tt.stage}
        perf = stage_names | TRANS_SECTIONS
        donor_owned = set(perf)
        cat, notes = {}, []
        for k, v in base.items():
            if k in donor_owned and isinstance(v, dict):
                # node references (e.g. "gearboxNode:") belong to the car's transmission body; the
                # donor's copy has them stripped, so the merged section gets the car's
                refs = {kk: vv for kk, vv in v.items() if is_node_ref(kk, vv)}
                if refs:
                    cat[k] = refs
                continue
            if k in ('information', 'slotType', 'slots', 'slots2', 'variables') or k in donor_owned:
                continue
            if k == 'powertrain':
                hdr = v[0] if v and isinstance(v[0], list) else None
                ni = hdr.index('name') if hdr and 'name' in hdr else 1
                rows = [r for r in v[1:] if not (isinstance(r, list) and len(r) > ni and r[ni] in stage_names)]
                if hdr and any(isinstance(r, list) for r in rows):
                    cat[k] = [hdr] + rows
            else:
                cat[k] = copy.deepcopy(v)
        tree = resolve(self.index, tt.ns, tt.target.main, Choice(_pc(self, tt.target)))
        node = next((n for n in tree.walk() if n.path == tt.slot_path), None)
        chosen = {c.slot['name']: c for c in (node.children if node else [])}
        slots, var_pool, classes = [], dict(var_decls(base)), []
        for s in part_slots(base):
            cls = classify_slot(self.index, tt.ns, s, perf)
            if cls != 'tuning':
                slots.append(s)
                classes.append(cls)
                continue
            sub_root = chosen.get(s['name'])
            for sub in (sub_root.walk() if sub_root else ()):
                self._merge_geometry(cat, sub.part.data, donor_owned)
                var_pool.update(var_decls(sub.part.data))
                notes.append(f'{s["name"]}: kept geometry of {sub.part.name}')
        at = classes.index('drivetrain') if 'drivetrain' in classes else len(slots)
        slots.insert(at, dict(name=TRANS_SLOT, allowTypes=[TRANS_SLOT] + ([f'esc_xown_{safe(tt.ns)}'] if default_donor and default_donor[1] else []),
                              denyTypes=sorted(deny), default=default_donor[0] if default_donor else '',
                              description='Catalog transmission', opts={'coreSlot': True}))
        rows = [var_pool[n] for n in sorted(var_refs(cat)) if n in var_pool]
        if rows:
            cat['variables'] = var_table(rows)
        cat['slots2'] = slots2_table(slots)
        cat['information'] = {'authors': 'engine_swap_catalog', 'name': 'Transmission catalog',
                              'value': base.get('information', {}).get('value', 0)}
        cat['slotType'] = copy.deepcopy(base['slotType'])
        return cat, notes

    def _merge_geometry(self, cat, data, donor_owned):
        for k, v in data.items():
            if k in ('information', 'slotType', 'slots', 'slots2', 'variables', 'powertrain',
                     'controller', 'mainEngine') or k in donor_owned or k in referenced_sections(data.get('mainEngine')):
                continue
            if isinstance(v, list) and v and isinstance(v[0], list):
                if k not in cat:
                    cat[k] = copy.deepcopy(v)
                else:
                    cat[k].extend(copy.deepcopy(v[1:]))       # same as game: rows appended
            elif isinstance(v, dict) and isinstance(cat.get(k, {}), dict):
                cat.setdefault(k, {}).update(copy.deepcopy(v))      # child keys win, as in game

    # ------------------------------------------------------------ main
    def run(self):
        R = self.rep.data
        vehicles = self.index.vehicles()
        self.log(f'Analysing {len(vehicles)} vehicles...')
        targets = []
        for i, (ns, mains) in enumerate(sorted(vehicles.items())):
            self.progress('vehicles', i, len(vehicles))
            try:
                t = analyze_target(self.index, self.vfs, ns, mains, self.tr)
            except Exception as e:      # one broken vehicle must not stop the build
                R['skipped_targets'][ns] = f'analysis failed: {e!r}'
                continue
            if t.reason:
                R['skipped_targets'][ns] = t.reason
            else:
                targets.append(t)
        self.log(f'  {len(targets)} vehicles have a usable engine slot')
        if self.v2:
            if self.v2.get('cars'):
                want = {c.lower() for c in self.v2['cars']}
                for t in targets:
                    if t.ns.strip('/').split('/')[1].lower() not in want:
                        R['skipped_targets'][t.ns] = 'not in the test selection'
                targets = [t for t in targets if t.ns.strip('/').split('/')[1].lower() in want]
            self.engine_models = True
            from .visuals import Meshes, short
            from . import vis2
            self.log('Engine visuals v2 (experimental): indexing model files...')
            self._v2_meshes = Meshes(self.vfs, self.log)
            self.v2_rows = vis2.RowMaker(self._v2_meshes, lambda f, m: f'esc_{short(f + "|" + m, 8)}_{m}'[:80],
                                         induction=bool(self.v2.get('phase3')))
            if self.v2.get('cars'):
                self.log(f'  test cars: {[t.display for t in targets]}')

        donors, skipped = find_donors(self.index, self.vfs, vehicles, self.include_car_mod_engines)
        titles = {s.name: s.title for s in self.vfs.sources}
        for p, why in skipped:
            R['skipped_donors'].append({'part': p.name, 'file': p.file, 'source': p.source.name, 'reason': why})
        self.log(f'Converting {len(donors)} donor engines...')
        for i, d in enumerate(donors):
            self.progress('engines', i, len(donors))
            if d.part.source.name in self.excluded_sources:
                R['skipped_donors'].append({'part': d.part.name, 'file': d.part.file, 'source': d.part.source.name,
                                            'reason': 'source unticked in the GUI'})
                continue
            d.label = donor_label(self.vfs, d.part, titles, self.tr)
            if self.v2 and self.v2.get('engines') and not any(
                    w.lower() in ' '.join(str(d.label).split()).lower() for w in self.v2['engines']):
                R['skipped_donors'].append({'part': d.part.name, 'file': d.part.file, 'source': d.part.source.name,
                                            'reason': 'not in the test selection'})
                continue
            self._add_donor(d, self.donor_context(d, targets))

        # each car's own engine is always offered in its own catalog, even if it is not a donor
        own_types = {}
        for t in targets:
            key = (t.base.ns, t.base.name)
            if key in self.donor_of_part:
                continue
            d = Donor(t.base, f'{t.display} (own): {self.tr(t.base.data.get("information", {}).get("name")) or t.base.name}')
            own = f'esc_own_{safe(t.ns)}'
            d.home_main = t.main          # its sound sections may live elsewhere in this car
            if self._add_donor(d, t.ns, own_type=own):
                own_types[t.ns] = own

        if self.engine_models:
            try:
                self._run_models(targets)
            except Exception as e:          # the swap catalog must still build without models
                self.models = None
                R['engine_models'] = {'error': f'engine models skipped: {e!r}'}
                self.log(f'WARNING: engine models skipped because of an error ({e!r}); '
                         'the catalog is built without donor engine models')
                self.rep.warn(traceback.format_exc(limit=5))

        self.log(f'Building catalog parts for {len(targets)} vehicles...')
        first = sorted(self.engines, key=lambda k: self.engines[k]['labels'][0])
        for i, t in enumerate(targets):
            self.progress('catalog', i, len(targets))
            try:
                own = self.donor_of_part.get((t.base.ns, t.base.name))
                default = (own, t.ns in own_types) if own else ((first[0], False) if first else None)
                deny = self.pair_exclusions(t)
                cat, notes = self.build_catalog(t, default, deny)
                name = f'esc_catalog_{safe(t.ns.split("/")[2])}'
                fn = f'{t.ns.strip("/")}/esc_catalog_{safe(t.ns.split("/")[2])}.jbeam'
                self.catalogs[t.ns] = (fn, {name: cat})
                R['targets'][t.ns] = {'vehicle': t.display, 'source': t.main.source.name,
                                      'engine_slot': t.slot['name'], 'slot_types': t.slot['allowTypes'],
                                      'slot_path': t.slot_path, 'base_engine': t.base.name,
                                      'catalog_part': name, 'default_catalog_engine': default[0] if default else None,
                                      'fuel': t.base.data.get('mainEngine', {}).get('requiredEnergyType', 'gasoline'),
                                      'notes': notes}
            except Exception as e:
                R['skipped_targets'][t.ns] = f'catalog build failed: {e!r}'
                self.rep.warn(traceback.format_exc(limit=3))
        self.run_transmissions(targets, vehicles, titles)
        return self

    def run_transmissions(self, targets, vehicles, titles):
        R = self.rep.data
        ttargets = []
        for t in targets:
            try:
                tt = analyze_transmission(self.index, self.vfs, t)
            except Exception as e:
                R['skipped_transmission_targets'][t.ns] = f'analysis failed: {e!r}'
                continue
            if tt.reason:
                R['skipped_transmission_targets'][t.ns] = tt.reason
            else:
                ttargets.append(tt)
        donors, skipped = find_transmission_donors(self.index, vehicles, self.include_car_mod_engines)
        for p, why in skipped:
            R['skipped_transmissions'].append({'part': p.name, 'file': p.file, 'source': p.source.name, 'reason': why})
        self.log(f'Converting {len(donors)} donor transmissions for {len(ttargets)} vehicles...')
        for i, d in enumerate(donors):
            self.progress('transmissions', i, len(donors))
            if d.part.source.name in self.excluded_sources:
                R['skipped_transmissions'].append({'part': d.part.name, 'file': d.part.file,
                                                   'source': d.part.source.name, 'reason': 'source unticked in the GUI'})
                continue
            d.label = donor_label(self.vfs, d.part, titles, self.tr)
            self._add_trans(d, self._trans_context(d.part, ttargets))
        own_types = {}
        for tt in ttargets:
            if (tt.base.ns, tt.base.name) in self.trans_of_part:
                continue
            d = TransDonor(tt.base, tt.stage, tt.after)
            d.label = f'{tt.target.display} (own): {self.tr(tt.base.data.get("information", {}).get("name")) or tt.base.name}'
            own = f'esc_xown_{safe(tt.ns)}'
            if self._add_trans(d, tt.ns, own_type=own):
                own_types[tt.ns] = own
        first = sorted(self.trans, key=lambda k: self.trans[k]['labels'][0])
        for tt in ttargets:
            try:
                own = self.trans_of_part.get((tt.base.ns, tt.base.name))
                default = (own, tt.ns in own_types) if own else ((first[0], False) if first else None)
                cat, notes = self.build_trans_catalog(tt, default, self.pair_exclusions(tt.target, 'transmission'))
                car = safe(tt.ns.split('/')[2])
                name = f'esc_tcatalog_{car}'
                self.tcatalogs[tt.ns] = (f'{tt.ns.strip("/")}/esc_tcatalog_{car}.jbeam', {name: cat})
                R['transmission_targets'][tt.ns] = {
                    'vehicle': tt.target.display, 'slot': tt.slot['name'], 'slot_types': tt.slot['allowTypes'],
                    'slot_path': tt.slot_path, 'base_transmission': tt.base.name, 'catalog_part': name,
                    'default_catalog_transmission': default[0] if default else None,
                    'stage': [f'{r.get("type")}:{r.get("name")}' for r in tt.stage],
                    'kept_after_gearbox': [f'{r.get("type")}:{r.get("name")}' for r in tt.after], 'notes': notes}
            except Exception as e:
                R['skipped_transmission_targets'][tt.ns] = f'catalog build failed: {e!r}'
                self.rep.warn(traceback.format_exc(limit=3))

    def _trans_context(self, part, ttargets):
        if part.ns != COMMON:
            return part.ns
        st = set(part.data.get('slotType') if isinstance(part.data.get('slotType'), list) else [part.data.get('slotType')])
        return next((tt.ns for tt in ttargets if set(tt.slot['allowTypes']) & st), COMMON)

    def _add_trans(self, d, ctx_ns, own_type=None):
        try:
            did, content, notes, missing = self.convert_trans_donor(d, ctx_ns, own_type)
        except Exception as e:
            self.rep.data['skipped_transmissions'].append({'part': d.part.name, 'file': d.part.file,
                                                           'source': d.part.source.name, 'reason': f'conversion failed: {e}'})
            return False
        gb = next(r for r in d.stage if r.get('name') == GEARBOX_NAME)
        self._store(self.trans, self.trans_of_part, d, did, content, notes, missing, own_type,
                    {'gearbox': gb.get('type'), 'stage': [f'{r.get("type")}:{r.get("name")}' for r in d.stage]})
        return True

    def _store(self, store, of_part, d, did, content, notes, missing, own_type, extra):
        if did in store:
            e = store[did]
            if own_type and own_type not in e['data']['slotType']:
                e['data']['slotType'].append(own_type)
        else:
            e = store[did] = dict({'data': content, 'labels': [], 'origins': [], 'notes': notes,
                                   'missing_assets': sorted(set(missing))}, **extra)
        e['labels'].append(' '.join(str(d.label).split()))
        e['origins'].append({'part': d.part.name, 'file': d.part.file, 'source': d.part.source.name})
        of_part[(d.part.ns, d.part.name)] = did

    def _run_models(self, targets):
        from .models import ModelBuilder
        vanilla = [t for t in targets if t.main.source.kind == 'vanilla']
        mb = self.models = ModelBuilder(self.index, self.vfs, log=self.log, hide_unfit=self.hide_unfit,
                                        tolerance=self.fit_tolerance, show_clipping=self.show_clipping,
                                        align='gearbox' if self.v2 else 'block', block_from_tuning=bool(self.v2),
                                        induction=bool(self.v2 and self.v2.get('phase3')),
                                        fit_meshes=bool(self.v2 and self.v2.get('phase4')))
        if self.v2 and self.v2.get('phase4'):
            from .fitmesh import FitStore
            self.fit = FitStore(self.log)
            mb.fit_shift_max = self.v2.get('shift_max', mb.fit_shift_max)
        natives = {}
        if self.v2:
            mb.meshes = self._v2_meshes
            natives = {t.ns: {d for d in self.engines if self._is_native(t, d)} for t in vanilla}
        mb.analyze(vanilla, self.engines)
        mb.fit_all({t.ns: self.donor_of_part.get((t.base.ns, t.base.name)) for t in vanilla}, natives)
        for t in vanilla:
            cm = mb.cars.get(t.ns)
            if cm is None or cm.reason:
                continue
            denied = mb.denied(t.ns)
            offered = [d for d in self.engines if d not in denied]
            if self.v2:
                try:
                    self._v2_car(mb, t, offered, natives.get(t.ns, set()))
                except Exception as e:      # this car keeps the normal catalog (its own engine model)
                    self.v2_info.pop(t.ns, None)
                    self.model_parts.pop(t.ns, None)
                    self.rep.data.setdefault('engine_models_v2_failed', {})[t.ns] = repr(e)
                    self.rep.warn(traceback.format_exc(limit=5))
                    self.log(f'WARNING: engine visuals v2 failed for {t.display} ({e!r}); it keeps the normal catalog')
                continue
            parts, hmap = mb.car_parts(t.ns, offered)
            self.model_parts[t.ns], self.model_hdr[t.ns] = parts, hmap
        if self.v2:
            mb.copies.update(self.v2_rows.copies)
            mb.meshes.load(set(self.v2_rows.copies))
        if self.fit is not None:
            self.fit.run_blender(self.v2.get('blender'))

    # ------------------------------------------------------------ engine visuals v2, per car
    def _is_native(self, t, did):
        """A catalog engine made for this car's engine slot (vanilla offers it there): factory position, its own
        exhaust chain and full visual set."""
        from .tables import part_fits
        e = self.engines[did]
        st = e['data']['slotType']
        if CATALOG_SLOT not in st and f'esc_own_{safe(t.ns)}' not in st:
            return False
        src = e.get('visual_src', (None, None))[0]
        return src is not None and src.ns in (t.ns, COMMON) and part_fits(src.data, t.slot)

    def _v2_has_model(self, mb, did):
        """The engine's block mesh made it into the shared rows of its default parts (not left out as a mod
        car-folder mesh, for example) - otherwise the car keeps its own model for this engine."""
        from . import vis2
        dm = mb.donors.get(did)
        if dm is None or dm.reason or not dm.ev or not dm.ev.block:
            return False
        e = self.engines[did]['data']
        chain = [e] + list(self._default_chain([_slot_from_row(r) for r in e.get('slots2', [])[1:]]))
        names = {r[0] for c in chain for r in (c.get('flexbodies') or [])[1:]
                 if isinstance(r, list) and len(r) > 3 and isinstance(r[3], dict) and r[3].get('disable') == vis2.DISABLE}
        b = dm.ev.block['mesh']
        return b in names or self.v2_rows.copies.get((dm.files.get(b), b)) in names

    def _node_ids(self, nodes):
        out = set()
        for n in nodes:
            for r in table_rows(n.part.data.get('nodes')):
                if isinstance(r.get('id'), str):
                    out.add(r['id'])
        return out

    def _native_exhaust(self, mb, t, did):
        """The native engine's own exhaust slots (default parts as chosen by the engine's defaults), if every beam
        of that chain reaches a node that exists in this car. Returns (slot dicts, None) or (None, reason)."""
        from .visuals import analyze_engine, tree_vars
        src = self.engines[did]['visual_src'][0]
        probs = []
        tree = resolve(self.index, t.ns, src, None, probs)
        ev = analyze_engine(self.index, t.ns, tree, tree_vars(tree))
        bad = [f'{p}: {m}' for p, m in probs if any(p.startswith(n.path) for n in ev.exhaust_nodes)]
        if bad:      # e.g. a default part whose slotType does not match (the game loads it anyway): stay safe
            return None, f'its exhaust chain has slot problems of its own: {bad[0]}'

        if mb.cars[t.ns].ev.nested_exhaust:
            return None, "the car's own exhaust is part of its default intake and stays in the catalog part"
        slots, seen, chain = [], set(), []
        for n in ev.exhaust_nodes + ev.nested_exhaust:
            if n.slot['name'] in seen:
                continue
            seen.add(n.slot['name'])
            slots.append(dict(n.slot, default=n.part.name))
            chain += list(n.walk())
        cm = mb.cars[t.ns]
        car_ex = [x for n in cm.ev.exhaust_nodes for x in n.walk()]
        skip = {id(x) for x in car_ex}
        have = self._node_ids([n for n in cm.tree.walk() if id(n) not in skip]) | self._node_ids(chain)
        miss = set()
        for n in chain:
            for r in table_rows(n.part.data.get('beams')):
                for k in ('id1:', 'id2:'):
                    v = r.get(k)
                    if isinstance(v, str) and v not in have and not r.get('optional'):
                        miss.add(v)
        if miss:
            return None, f'its exhaust needs nodes this car does not have: {sorted(miss)[:5]}'
        return slots, None

    def fit_previews(self, out_dir, blender, named=(), log=None):
        """Phase 4 checkpoint pictures: OBJ folders (donor engine, car gearbox, adapter / headers, car exhaust,
        body nearby, pipe) for chosen pairs, rendered by blender/preview.py into out_dir/*.png.
        named: [(car folder, engine label substring)]; plus 8 adapters spread over 5-20 cm of fit-check move,
        BX + ETK 4.4L V8 ttSport pipes, the longest pipe and 4 more pipes on other cars."""
        import shutil
        from . import placement as P
        from .fitmesh import flat as fitmesh_flat
        log = log or self.log
        mb, os_ = self.models, __import__('os')
        work = os_.path.join(out_dir, '_obj')
        shutil.rmtree(work, ignore_errors=True)
        os_.makedirs(work, exist_ok=True)
        lab = {d: self.engines[d]['labels'][0] for d in self.engines}
        rows = []                    # (ns, did, j)
        for ns, inf in self.v2_info.items():
            for did, j in inf.get('joints', {}).items():
                rows.append((ns, did, j, inf['kinds'][did]))
        picks, seen = [], set()

        def add(x, why):
            if x and (x[0], x[1], why[:4]) not in seen:
                seen.add((x[0], x[1], why[:4]))
                picks.append((x, why))
        for car, eng in named:
            c = [r for r in rows if r[0] == f'/vehicles/{car}/' and eng in lab[r[1]] and r[3] == 'foreign']
            add(sorted(c, key=lambda r: lab[r[1]])[0] if c else None, 'joint')
        ad = sorted([r for r in rows if r[2].get('grade') == 'adapter' and lab[r[1]].startswith('Vanilla')
                     and 0.05 <= (r[2].get('shift') or 0) <= 0.20], key=lambda r: r[2]['shift'])
        cars_used = set()
        for k in range(8):
            if not ad:
                break
            want = 0.05 + 0.15 * k / 7
            cand = sorted(ad, key=lambda r: (r[0] in cars_used, abs(r[2]['shift'] - want)))
            add(cand[0], 'joint')
            cars_used.add(cand[0][0])
        pipes_ = [r for r in rows if (r[2].get('exhaust') or {}).get('grade') == 'pipes']
        bx = [r for r in pipes_ if r[0] == '/vehicles/bx/' and 'ETK: 4.4L V8 ttSport' in lab[r[1]]]
        add(bx[0] if bx else None, 'pipe')

        def plen(r):
            return max((x.get('length') or 0) for x in r[2]['exhaust']['sides'].values() if isinstance(x, dict))
        if pipes_:
            add(max(pipes_, key=plen), 'pipe')
        used = {p[0][0] for p in picks if p[1] == 'pipe'}
        for r in sorted(pipes_, key=lambda r: (lab[r[1]], r[0])):
            if r[0] not in used and lab[r[1]].startswith('Vanilla') and sum(1 for p in picks if p[1] == 'pipe') < 6:
                add(r, 'pipe')
                used.add(r[0])
        mb.keep_body = True
        items = []
        for (ns, did, j, _k), why in picks:
            cm, dm, p = mb.cars[ns], mb.donors[did], mb.pairs[(ns, did)]
            tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
            tag = f"{why}_{ns.strip('/').split('/')[-1]}_{did}"
            d = os_.path.join(work, tag)
            os_.makedirs(d, exist_ok=True)
            objs = {}
            names = j.get('fit_rows') or []
            if why == 'joint':
                for k, (v, tr) in enumerate(dm.eng_geo):
                    objs[f'engine_{k}'] = (P.transform_points(v, p['deg'], tt), tr)
                for k, (v, tr) in enumerate(cm.gb_geo or []):
                    objs[f'gearbox_{k}'] = (v, tr)
                names = [n for n in names if n.startswith('esc_fit_')]
                title = (f"{cm.t.display} + {lab[did]} | {j.get('grade')}: gap {j.get('gap')} m, "
                         f"engine moved {j.get('shift')} m, gearbox mouth past engine {j.get('overhang')} m")
            else:
                from .visuals import row_geometry
                for k, r in enumerate(getattr(dm, 'ex_hdr_rows', []) or []):
                    g = row_geometry(r, mb.meshes, dm.files.get(r['mesh']))
                    if g:
                        objs[f'header_{k}'] = (P.transform_points(g[0], p['deg'], tt), g[1])
                for k, r in enumerate(cm.ex_rows):
                    g = row_geometry(r, mb.meshes, cm.files.get(r['mesh']))
                    if g:
                        objs[f'exhaust_{k}'] = g
                cm.obstacles = None
                mb._car_obstacles(cm)
                for k, g in enumerate(cm.body_geo):
                    objs[f'body_{k}'] = g
                names = [n for n in names if n.startswith('esc_pipe_')]
                ls = [round(x.get('length') or 0, 2) for x in j['exhaust']['sides'].values() if isinstance(x, dict)
                      and x.get('length')]
                title = f"{cm.t.display} + {lab[did]} | exhaust pipe(s), length {ls} m"
            for k, n in enumerate(names):
                v, _nr, tr = self.fit.clean.get(n) or fitmesh_flat(self.fit.meshes[n]['raw'])
                objs[f"{'adapter' if why == 'joint' else 'pipe'}_{k}"] = (v, [tuple(c[0] for c in t) for t in tr])
            for name, (v, tr) in objs.items():
                with open(os_.path.join(d, name + '.obj'), 'w') as fh:
                    fh.write(f'o {name}\n')
                    fh.writelines(f'v {a:.5f} {b:.5f} {c:.5f}\n' for a, b, c in v)
                    fh.writelines(f'f {t[0] + 1} {t[1] + 1} {t[2] + 1}\n' for t in tr)
            items.append({'dir': d, 'title': title, 'ax': 1 if cm.ev.dir[1] else 0,
                          'out': os_.path.join(out_dir, tag + '.png'), 'xray': why == 'pipe'})
        mb.keep_body = False
        jp = os_.path.join(work, 'job.json')
        with open(jp, 'w', encoding='utf-8') as fh:
            json.dump({'items': items, 'size': [800, 560]}, fh, indent=1)
        log(f'Previews: rendering {len(items)} pictures...')
        import subprocess
        subprocess.run([blender, '-b', '--factory-startup', '--python',
                        os_.path.join(os_.path.dirname(os_.path.abspath(__file__)), 'blender', 'preview.py'), '--', jp],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return [(i['out'], i['title']) for i in items]

    def _fit_rows(self, mb, t, pieces):
        """Phase 4: flexbody rows for generated pieces [(kind, mesh, groups, look-alike materials)] - shown only
        with the donor model (vis2.DISABLE), like the donor's engine rows."""
        from .fitmesh import choose_material
        from . import vis2
        rows = []
        for kind, mesh, groups, mats in pieces:
            name, mdict = choose_material(mats, mb.meshes.materials_index(), kind)
            if mdict:
                self.fit.materials[name] = mdict
            rows.append([self.fit.add(t.ns, kind, mesh, name), list(groups), [], {'disable': vis2.DISABLE}])
        return rows

    def _v2_car(self, mb, t, offered, natives):
        from .models import OFFERED, LABELS
        from .analyze import classify_slot as _cls
        from . import vis2
        from .visuals import HEADER_NAME, BODY_NAME
        own_t = f'esc_own_{safe(t.ns)}'
        own_did = self.donor_of_part.get((t.base.ns, t.base.name))
        car = mb.v2_car_rows(t.ns)
        hp, hmap = mb.header_parts(t.ns, switch=True)
        base_ex = [sl for sl in part_slots(t.base.data) if _cls(self.index, t.ns, sl) == 'exhaust']

        def car_exhaust(nomodel):
            out = []
            for sl in base_ex:
                if sl['name'] in hmap:
                    stype, names, new_name = hmap[sl['name']]
                    d = dict(sl, name=new_name, allowTypes=[stype],
                             default=names.get(sl['default'], '') if sl['default'] else '')
                    if nomodel:
                        d['opts'] = dict(sl.get('opts') or {}, variables={vis2.OWN_VAR: vis2.NOMODEL_OWN})
                    out.append(d)
                else:
                    out.append(sl)
            return out

        def etype(did):
            st = self.engines[did]['data']['slotType']
            return next((x for x in st if x.startswith('esc_d_')), own_t)
        specs, kinds, labels, placement, why, nat_ex, refused, joints_ = [], {}, {}, {}, {}, {}, {}, {}
        for did in offered:
            st = self.engines[did]['data']['slotType']
            if CATALOG_SLOT not in st and own_t not in st:
                continue                                   # another car's own engine
            dm, p = mb.donors.get(did), mb.pairs.get((t.ns, did))
            has = self._v2_has_model(mb, did)
            sp = {'did': did, 'type': etype(did)}
            if did in natives and has:
                ex, bad = self._native_exhaust(mb, t, did)
                if bad:
                    nat_ex[did] = {'fallback': bad}
                    ex = car_exhaust(False)
                else:
                    nat_ex[did] = {'slots': [x['name'] for x in ex]}
                sp.update(kind='native', exhaust=ex)
                if self.v2.get('phase4') and p is not None:
                    g, jinfo, _pieces = mb.fit_joint(t, did)   # natives: graded only (factory position)
                    joints_[did] = dict(jinfo, grade=g)
                elif self.v2.get('phase3') and p is not None:
                    g, jinfo, _row = mb.joint(t, did)          # natives: graded only (factory position)
                    joints_[did] = dict(jinfo, grade=g)
                placement[did] = {'native': True, 'deg': 0, 't': [0.0, 0.0, 0.0], 'method': 'native'}
                labels[did] = ''
            elif has and p is not None and p['verdict'] in OFFERED:
                opts, info = mb.v2_placement(t, did)
                placement[did] = info
                if opts is None:
                    refused[did] = info['error']
                    sp.update(kind='nomodel', exhaust=car_exhaust(True))
                    why[did] = f'placement refused: {info["error"]}'
                    labels[did] = ' (no model)'
                else:
                    sp.update(kind='foreign', exhaust=car_exhaust(False), opts=opts)
                    if self.v2.get('phase4'):
                        g, jinfo, jp = mb.fit_joint(t, did)
                        eg, einfo, ep = mb.fit_exhaust(t, did)
                        sp['extra_rows'] = self._fit_rows(mb, t, jp + ep)
                        joints_[did] = dict(jinfo, grade=g, exhaust=dict(einfo, grade=eg),
                                            fit_rows=[r[0] for r in sp['extra_rows']])
                    elif self.v2.get('phase3'):
                        g, jinfo, row = mb.joint(t, did)
                        eg, einfo, erows = mb.exhaust(t, did)
                        sp['extra_rows'] = ([row] if row else []) + erows
                        joints_[did] = dict(jinfo, grade=g, exhaust=dict(einfo, grade=eg),
                                            filler_rows=[r[3]['scale'] for r in sp['extra_rows']])
                    lab = LABELS.get(p['verdict'], p['verdict'])
                    labels[did] = ('' if p['verdict'] == 'fits' else f' ({lab})') + (' (intake clips)' if p.get('intake_clips') else '')
            else:
                if dm is None or dm.reason:
                    why[did] = dm.reason if dm is not None and dm.reason else 'no model data'
                    labels[did] = ' (no model)'
                elif not has:
                    why[did] = 'engine block mesh not loadable in other cars (left out of the shared parts)'
                    labels[did] = ' (no model)'
                else:
                    why[did] = f'fit check: {p["verdict"]}' if p else 'no fit-check result'
                    labels[did] = " (doesn't fit, car's own model)"
                sp.update(kind='nomodel', exhaust=car_exhaust(True))
            kinds[did] = sp['kind']
            specs.append(sp)
        parts = vis2.wrapper_and_modes(t.ns, specs, car, own_did, var_table)
        # the car's own meshes moved out of the catalog may use the car's variables: declare them there too
        pool = {}
        for n in mb.cars[t.ns].tree.walk():
            for k, r in var_decls(n.part.data).items():
                pool.setdefault(k, r)
        for p in parts.values():
            need = var_refs(p.get('flexbodies') or []) - {v.lstrip('$') for v in (vis2.VIS_VAR, vis2.OWN_VAR, vis2.NAT_VAR)}
            if need:
                vis2.add_decls(p, [pool[k] for k in sorted(need) if k in pool], var_table, var_decls)
        parts.update(hp)
        self.model_parts[t.ns], self.model_hdr[t.ns] = parts, hmap
        cm = mb.cars[t.ns]
        hdr_meshes, exh_meshes = set(), set()
        for n in cm.ev.exhaust_nodes:
            for x in n.walk():
                for r in table_rows(x.part.data.get('flexbodies')):
                    m = r.get('mesh')
                    if not isinstance(m, str):
                        continue
                    if x is n and n.slot['name'] in hmap and HEADER_NAME.search(m) and not BODY_NAME.search(m):
                        hdr_meshes.add(m)
                    else:
                        exh_meshes.add(m)
        counts = {k: sum(1 for v in kinds.values() if v == k) for k in vis2.KINDS}
        self.v2_info[t.ns] = {
            'wrappers': {sp['did']: vis2.wrapper_name(t.ns, sp['did']) for sp in specs}, 'kinds': kinds,
            'labels': labels, 'placement': placement, 'no_model_reason': why, 'native_exhaust': nat_ex,
            'placement_refused': refused, 'engine_groups': cm.ev.block['groups'], 'joints': joints_,
            'joint_ref': ({'gap': cm.joint_ref['gap'], 'off': cm.joint_ref['off'], 'gearbox_mesh': cm.gb_mesh}
                          if getattr(cm, 'joint_ref', None) else {'why': getattr(cm, 'joint_note', None)}),
            'default': own_did if own_did in kinds else None, 'counts': counts,
            'flip_any': any(i.get('flip') for i in placement.values()),
            'methods': {m: sum(1 for i in placement.values() if i.get('method') == m)
                        for m in ('native', 'gearbox', 'block')},
            'remove_meshes': sorted(set(car['own_meshes']) | {r[0] for r in car['s_rows'] + car['nested_hdr']}),
            'car_side': {'own_meshes': car['own_meshes'], 's_intake': car['s_intake'],
                         's_keep': car['s_keep_meshes'], 'header_meshes': sorted(hdr_meshes),
                         'exhaust_meshes': sorted(exh_meshes), 'exhaust_slots': [sl['name'] for sl in base_ex],
                         'header_slots': sorted(hmap), 'nested_header_meshes': car['nested_hdr_meshes']}}

    def _v2_sizes(self, files):
        """Zip contents per car (I): its Engine model parts, and the copied (vanilla) meshes by source folder."""
        out = {'cars': {}, 'copied_by_source': {}, 'shared_parts_bytes': 0}
        for ns in self.v2_info:
            car = safe(ns.split('/')[2])
            f = f'{ns.strip("/")}/esc_models_{car}.jbeam'
            out['cars'][ns] = {'engine_model_parts_bytes': len(files.get(f, b'')),
                               'catalog_bytes': len(files.get(self.catalogs.get(ns, ('', {}))[0], b'')),
                               'wrappers': len(self.v2_info[ns]['wrappers'])}
        for (src, mesh), _new in self.v2_rows.copies.items():
            d = self.models.meshes.data.get((src, mesh)) or {}
            folder = '/'.join(src.split('/')[:2])
            c = out['copied_by_source'].setdefault(folder, {'meshes': 0, 'geometry_bytes': 0})
            c['meshes'] += 1
            c['geometry_bytes'] += len(d.get('geometry_raw') or b'')
        out['copied_files_bytes'] = sum(len(b) for p, b in files.items() if p.startswith('vehicles/common/engine_swap_catalog/meshes/'))
        out['shared_parts_bytes'] = sum(len(b) for p, b in files.items() if p.startswith(OUT_DIR) and p.endswith('.jbeam'))
        return out

    def pair_exclusions(self, t, kind='engine'):
        """Slot types (esc_d_<id> / esc_dx_<id>) that must not be offered for this car.
        shortcut: no per-pair incompatibility is currently known statically (engine interface keys
        and everything after the gearbox always come from the car), so this returns an empty set;
        add rules here when one is found. Engine models: engines whose model does not fit a vanilla
        car are hidden there when hide_unfit is on."""
        if kind == 'engine' and self.models is not None and t.ns in self.model_parts:
            return {'esc_d_' + did[len('esc_e_'):] for did in self.models.denied(t.ns)}
        return set()

    def _add_donor(self, d, ctx_ns, own_type=None):
        R = self.rep.data
        try:
            did, content, notes, missing = self.convert_donor(d, ctx_ns, own_type)
        except Exception as e:
            R['skipped_donors'].append({'part': d.part.name, 'file': d.part.file,
                                        'source': d.part.source.name, 'reason': f'conversion failed: {e}'})
            return False
        fuel = d.part.data.get('mainEngine', {}).get('requiredEnergyType', 'gasoline')
        self._store(self.engines, self.donor_of_part, d, did, content, notes, missing, own_type,
                    {'fuel': fuel, 'device': engine_row(d.part.data)[0]['type']})
        e = self.engines[did]
        if self.v2 and 'visual_src' not in e and self._last_groups:
            from . import vis2
            hdr = [self.index.get(ctx_ns, sl['default']) for sl in part_slots(d.part.data)
                   if sl['default'] and classify_slot(self.index, ctx_ns, sl) == 'exhaust'
                   and self.index.get(ctx_ns, sl['default']) is not None]
            rows = self.v2_rows.rows(d.part, ctx_ns, *self._last_groups, e['notes'], self.vfs, hdr)
            vis2.add_vis(e['data'], rows, var_table, var_decls)       # after the id: ids unchanged
        e.setdefault('visual_src', (d.part, ctx_ns))     # for engine models
        return True

    # ------------------------------------------------------------ output
    def files(self):
        """Return {zip path: bytes} for the whole mod."""
        out = {}
        eng_parts = _named(self.engines)
        for ns, inf in self.v2_info.items():
            for did, w in inf['wrappers'].items():
                self.model_parts[ns][w]['information']['name'] = (eng_parts[did]['information']['name']
                                                                  + inf['labels'].get(did, ''))
        if self.model_parts and not self.v2:
            from .visuals import MODEL_SLOT_OWN
            for did, data in eng_parts.items():
                # added after the ID was computed: catalog engine IDs stay what they were
                tbl = data.setdefault('slots2', [['name', 'allowTypes', 'denyTypes', 'default', 'description']])
                if not any(r[0].endswith('_model') for r in tbl[1:]):
                    tbl.append([f'esc{did[6:12]}_model', [f'esc_model_{did}', MODEL_SLOT_OWN], [],
                                f'esc_m_{did}', 'Engine model'])
        if self.model_parts:
            for ns, parts in self.model_parts.items():
                car = safe(ns.split('/')[2])
                out[f'{ns.strip("/")}/esc_models_{car}.jbeam'] = jbeam.dumps(parts).encode('utf-8')
            out.update(self.models.copied_files())
            if self.fit is not None:
                out.update(self.fit.files())
            elif self.v2 and self.v2.get('phase3'):
                from . import joints
                from .models import MODEL_DIR
                out[f'{MODEL_DIR}esc_filler.dae'] = joints.filler_dae()
                out[f'{MODEL_DIR}esc_filler.materials.json'] = jbeam.dumps(joints.FILLER_MATERIAL).encode('utf-8')
        trans_parts = _named(self.trans)
        for i, chunk in enumerate(_chunks(list(eng_parts.items()), 60)):
            out[f'{OUT_DIR}esc_engines_{i:02d}.jbeam'] = jbeam.dumps(dict(chunk)).encode('utf-8')
        for i, chunk in enumerate(_chunks(list(trans_parts.items()), 100)):
            out[f'{OUT_DIR}esc_transmissions_{i:02d}.jbeam'] = jbeam.dumps(dict(chunk)).encode('utf-8')
        live = self.live_tuning = self._reachable_tuning(dict(eng_parts, **trans_parts))
        for i, chunk in enumerate(_chunks(sorted((k, v) for k, v in self.tuning.items() if k in live), 300)):
            out[f'{OUT_DIR}esc_tuning_{i:02d}.jbeam'] = jbeam.dumps(dict(chunk)).encode('utf-8')
        for ns, (fn, parts) in list(self.catalogs.items()) + list(self.tcatalogs.items()):
            out[fn] = jbeam.dumps(parts).encode('utf-8')
        out.update(self.assets)
        return out

    def _reachable_tuning(self, eng_parts):
        """Tuning parts used by an included engine (skipped donors may leave orphans)."""
        by_type = {}
        for n, d in self.tuning.items():
            for t in d['slotType']:
                by_type.setdefault(t, []).append(n)
        live, todo = set(), list(eng_parts.values())
        while todo:
            d = todo.pop()
            for r in d.get('slots2', [])[1:]:
                for t in r[1]:
                    for n in by_type.get(t, ()):
                        if n not in live:
                            live.add(n)
                            todo.append(self.tuning[n])
        return live

    def finish_report(self, files):
        R = self.rep.data
        for did, e in self.engines.items():
            R['donors'][did] = {'name': e['data']['information']['name'], 'labels': list(dict.fromkeys(e['labels'])),
                                'origins': e['origins'], 'fuel': e['fuel'], 'device': e['device'],
                                'notes': e['notes'], 'missing_assets': e['missing_assets'],
                                'tuning_slots': [r[0] for r in e['data'].get('slots2', [])[1:]]}
            if e['missing_assets']:
                R['assets']['missing'].extend(e['missing_assets'])
        for did, e in self.trans.items():
            R['transmissions'][did] = {'name': e['data']['information']['name'],
                                       'labels': list(dict.fromkeys(e['labels'])), 'origins': e['origins'],
                                       'gearbox': e['gearbox'], 'stage': e['stage'], 'notes': e['notes'],
                                       'tuning_slots': [r[0] for r in e['data'].get('slots2', [])[1:]]}
        R['assets']['copied'] = len(self.assets)
        if self.models is not None:
            mr = self.models.report
            R['engine_models'] = {
                'cars': mr['cars'], 'donors_without_model': mr['donors_without_model'],
                'copied_meshes': mr['copied_meshes'], 'missing_materials': mr['missing_materials'],
                'hide_unfit': self.hide_unfit, 'fit_tolerance': self.fit_tolerance,
                'show_clipping': self.show_clipping,
                'pairs': {ns: {did: [p['verdict'], [round(v, 3) for v in p['offset']], p['deg'],
                                     {k: v for k, v in p['blocked_hits'].items()}, p.get('size', {})]
                               for (n, did), p in self.models.pairs.items() if n == ns}
                          for ns in mr['cars']}}
        if self.v2:
            R.setdefault('engine_models', {})['v2'] = self.v2_info
            for ns in self.v2_info:
                if ns in R['targets']:
                    R['targets'][ns]['v2'] = True
            R['engine_models']['v2_sizes'] = self._v2_sizes(files)
            if self.v2.get('phase3'):
                card = {}
                for ns, inf in self.v2_info.items():
                    c = card[ns] = {'joint': {}, 'exhaust': {}}
                    for did, j in inf.get('joints', {}).items():
                        k = inf['kinds'][did]
                        c['joint'][f'{k}: {j["grade"]}'] = c['joint'].get(f'{k}: {j["grade"]}', 0) + 1
                        if 'exhaust' in j:
                            c['exhaust'][j['exhaust']['grade']] = c['exhaust'].get(j['exhaust']['grade'], 0) + 1
                R['engine_models']['report_card'] = card
            if self.fit is not None:
                tri = {n: self.fit.tri_count(n) for n in self.fit.meshes}
                R['engine_models']['fit_meshes'] = {
                    'count': len(tri), 'blender': self.fit.blender_used,
                    'by_kind': {k: sum(1 for n in tri if self.fit.meshes[n]['kind'] == k) for k in ('fit', 'pipe')},
                    'max_tris': {k: max([v for n, v in tri.items() if self.fit.meshes[n]['kind'] == k] or [0])
                                 for k in ('fit', 'pipe')},
                    'materials': sorted(self.fit.materials)}
            R['warnings'].insert(0, 'Engine visuals v2: the slot structure under the Engine catalog is new, so saved '
                                    'configs that use a catalog engine load with the default engine once')
        R['stats'] = {'targets': len(R['targets']), 'skipped_targets': len(R['skipped_targets']),
                      'catalog_engines': len(self.engines),
                      'tuning_parts': len(self.live_tuning),
                      'transmission_targets': len(R['transmission_targets']),
                      'catalog_transmissions': len(self.trans),
                      'skipped_transmissions': len(R['skipped_transmissions']),
                      'donor_parts_seen': sum(len(e['origins']) for e in self.engines.values()),
                      'skipped_donors': len(R['skipped_donors']), 'files': len(files),
                      'zip_bytes_uncompressed': sum(len(b) for b in files.values())}
        return R


def _named(store):
    """Set display names; same name but different data gets a [folder] (or [folder: part]) tag."""
    first = {}
    for did, e in store.items():
        first.setdefault(e['labels'][0], []).append(did)

    def where(e):
        w = list(dict.fromkeys(o['file'].split('/')[1] for o in e['origins']))
        return ', '.join(w[:3]) + (', ...' if len(w) > 3 else '')
    out = {}
    for did in sorted(store, key=lambda k: store[k]['labels'][0].lower()):
        e = store[did]
        name = e['labels'][0]
        group = first[name]
        if len(group) > 1:
            tags = [where(store[g]) for g in group]
            tag = where(e)
            if tags.count(tag) > 1:
                tag += ': ' + e['origins'][0]['part']
            name += f' [{tag}]'
        e['data']['information']['name'] = name
        out[did] = e['data']
    return out


def _slot_from_row(r):
    return {'name': r[0], 'allowTypes': r[1], 'default': r[3]}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _pc(builder, t):
    return read_pc(builder.vfs, t.pc_path) if t.pc_path else None


def write_zip(path, files):
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in sorted(files):
            data = files[name]
            # audio is already compressed; storing it keeps the build fast
            ctype = zipfile.ZIP_STORED if name.lower().endswith(('.ogg', '.mp3')) else zipfile.ZIP_DEFLATED
            z.writestr(name, data, compress_type=ctype)
    os.replace(tmp, path)
