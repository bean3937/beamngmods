"""Engine detection, target (vehicle) analysis and child-slot classification.

Facts this relies on are in NOTES.md (observed in BeamNG 0.40.5 files):
- an engine part has a powertrain row whose device type is a combustion engine
  (vanilla 'combustionEngine'; mods: 'classic_combustionEngine', 'combustionEngine_rk',
  'combustionEngine_fixed', ...) named 'mainEngine' with input 'dummy'.
- child parts are merged into their parent, children first; a child's dict keys
  overwrite the parent's (slotSystem.unifyParts).
"""
import re

from .index import Choice, resolve, read_info, read_pc, COMMON
from .tables import table_rows, part_slots

ENGINE_NAME = 'mainEngine'

# mainEngine keys that describe how the engine is attached to *this* vehicle
# (node names, beam/deform groups, fuel tanks, radiator fan hardware, ports).
# They always come from the target car; everything else in mainEngine comes from the donor.
INTERFACE_KEYS = {
    'torqueReactionNodes:', 'waterDamage', 'radiator', 'engineBlock', 'breakTriggerBeam',
    'energyStorage', 'requiredEnergyType', 'deformGroups', 'deformGroups_oilPan',
    'deformGroups_oilRadiator', 'deformGroups_radiator', 'oilpanNodes:', 'uiName',
    'radiatorFanType', 'radiatorFanLoopEvent', 'radiatorFanPitchMultiplier', 'radiatorFanVolume',
    'radiatorFanMaxAirSpeed', 'radiatorFanTemperature', 'radiatorArea', 'radiatorEffectiveness',
    'numberOfOutputPorts', 'electricRadiatorFanOverrideIgnitionLevel', 'mechanicalFanRPMCoef',
}
# list sections that hold geometry / vehicle-bound data (never taken from a donor)
GEOMETRY_LISTS = {'nodes', 'beams', 'triangles', 'quads', 'flexbodies', 'props', 'hydros',
                  'slidenodes', 'torsionbars', 'torsionHydros', 'refNodes', 'rails', 'rails2',
                  'triggers', 'triggers2', 'triggerEventLinks2', 'pressureWheels', 'rotators',
                  'thrusters', 'mirrors', 'camerasInternal', 'soundscape', 'ties', 'glowMap',
                  'cameraExternal', 'cameraChase', 'sounds', 'components', 'airbags',
                  'dynDecalMaterials', 'cargoStorage', 'energyStorage', 'powertrainDamage',
                  'airbagBeamData', 'airbagBeamSoundData', 'airbagCrossFlow', 'doorBeamData',
                  'actionsEnabled', 'occupantSystem'}
SKIP_NAMESPACES = {'/vehicles/simple_traffic/'}   # low-detail AI traffic proxy (NOTES.md)

_GROUP_REF = re.compile(r'^\[.*\]:?$')


def is_combustion(dev_type):
    return isinstance(dev_type, str) and 'combustionengine' in dev_type.lower()


def powertrain_rows(part_data):
    return table_rows(part_data.get('powertrain'))


def engine_row(part_data):
    rows = [r for r in powertrain_rows(part_data) if is_combustion(r.get('type'))]
    return rows


def is_engine_part(part):
    return bool(engine_row(part.data))


def is_node_ref(key, value):
    """Keys ending with ':' hold node names; dicts like {"[engineGroup]:": [...]} select nodes."""
    if isinstance(key, str) and key.endswith(':'):
        return True
    return isinstance(value, dict) and any(isinstance(k, str) and _GROUP_REF.match(k) for k in value)


def referenced_sections(main_engine):
    """Sections named by string values in mainEngine (soundConfig, turbocharger, n2o, ...)."""
    out = []
    for k, v in (main_engine or {}).items():
        if isinstance(v, str) and k in ('soundConfig', 'soundConfigExhaust', 'turbocharger',
                                        'supercharger', 'nitrousOxideInjection') or \
                (isinstance(v, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v or '') and
                 k.lower().endswith(('config', 'charger', 'injection', 'section'))):
            out.append(v)
    return out


# --- slot classification -------------------------------------------------
# returns one of: 'tuning' (travels with the engine), or a keep-class that stays with the car:
# 'drivetrain', 'exhaust', 'mounts', 'oilpan', 'radiator', 'n2o', 'visual'
_PERF_SECTIONS = {'mainEngine', 'turbocharger', 'supercharger', 'soundConfig', 'soundConfigExhaust',
                  'vehicleController', 'n2o'}


def _subtree_parts(index, ns, part, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if part.name in seen or depth > 12:
        return
    seen.add(part.name)
    yield part
    for s in part_slots(part.data):
        for c in index.candidates(ns, s):
            yield from _subtree_parts(index, ns, c, depth + 1, seen)


def has_perf(part, perf=None):
    d = part.data
    if perf is not None:                      # transmission context: given section names
        return any(k in d for k in perf)
    if any(k in d for k in _PERF_SECTIONS):
        return True
    return any(k in d for k in referenced_sections(d.get('mainEngine')))


def classify_slot(index, ns, slot, perf=None):
    """perf: section names that make a slot 'tuning' (None = engine rules)."""
    text = ' '.join([slot['name'], str(slot['description'])] + slot['allowTypes']).lower()
    cands = index.candidates(ns, slot)
    if slot['allowTypes'] == ['n2o_system']:
        return 'n2o'
    # transmission context: mods add odd options to vanilla slots (e.g. a CJD clutch with its own
    # torque converter device in the UTV clutch slot), so judge by the slot's default part there
    judged = cands
    if perf is not None:
        dflt = [c for c in cands if c.name == slot['default']]
        judged = dflt or cands
    for c in judged:
        for r in powertrain_rows(c.data):
            if not is_combustion(r.get('type')):
                return 'drivetrain'
    if re.search(r'exhaust|muffler|header|downpipe', text) and 'intake' not in text:
        return 'exhaust'
    if 'mount' in text:
        return 'mounts'
    if re.search(r'oil ?pan|oil_pan|oilpan|sump', text):
        return 'oilpan'
    if re.search(r'radiator', text):
        return 'radiator'
    if any(has_perf(p, perf) for c in cands for p in _subtree_parts(index, ns, c)):
        return 'tuning'
    return 'visual'


# --- targets ---------------------------------------------------------------

class Target:
    def __init__(self, ns, main, display):
        self.ns, self.main, self.display = ns, main, display
        self.slot = None            # engine slot dict (in parent part)
        self.slot_path = None
        self.parent = None          # part holding the engine slot
        self.base = None            # base engine part (default config)
        self.reason = None          # why skipped (None = usable)
        self.pc_path = None


def vehicle_display(vfs, ns, tr=str):
    info = read_info(vfs, ns) or {}
    brand, name = tr(info.get('Brand') or ''), tr(info.get('Name') or '')
    if name and not name.startswith('vehiclesData.'):
        return f'{brand} {name}' if brand and brand not in name else name
    return ns.split('/')[2]


def default_pc(vfs, ns, info):
    d = ns.strip('/')
    name = (info or {}).get('default_pc')
    cands = ([f'{d}/{name}.pc'] if name else []) + sorted(vfs.files_under(d + '/', '.pc'))
    for p in cands:
        if vfs.exists(p) and '/' not in p[len(d) + 1:]:
            pc = read_pc(vfs, p)
            if pc:
                return p, pc
    return None, None


def analyze_target(index, vfs, ns, mains, tr=str):
    main = sorted(mains, key=lambda p: (p.name != ns.split('/')[2], p.name))[0]
    t = Target(ns, main, vehicle_display(vfs, ns, tr))
    if ns in SKIP_NAMESPACES:
        t.reason = 'simplified AI traffic proxy, not a drivable vehicle model'
        return t
    info = read_info(vfs, ns)
    t.pc_path, pc = default_pc(vfs, ns, info)
    problems = []
    tree = resolve(index, ns, main, Choice(pc), problems)
    for node in tree.walk():
        if node.slot is not None and is_engine_part(node.part):
            t.slot, t.slot_path, t.base = node.slot, node.path, node.part
            break
    if t.base is None:
        # default config has no combustion engine (electric car, trailer, prop): look for any slot
        for node in tree.walk():
            for s in part_slots(node.part.data):
                eng = [c for c in index.candidates(ns, s) if is_engine_part(c)]
                if eng and t.base is None:
                    t.slot, t.slot_path, t.base = s, node.path + s['name'] + '/', sorted(eng, key=lambda p: p.name)[0]
    if t.base is None:
        t.reason = 'no combustion-engine slot (electric, trailer or prop)'
        return t
    rows = engine_row(t.base.data)
    if len(rows) != 1 or rows[0].get('name') != ENGINE_NAME:
        t.reason = f'base engine {t.base.name} does not have exactly one combustion device named mainEngine'
    return t


# --- donors ------------------------------------------------------------------

class Donor:
    def __init__(self, part, label):
        self.part, self.label = part, label
        self.reason = None


def donor_label(vfs, part, src_titles, tr=str):
    eng = tr(part.data.get('information', {}).get('name')) or part.name
    if part.source.kind == 'vanilla':
        ns = part.ns
        who = 'Common' if ns == COMMON else vehicle_display(vfs, ns, tr)
        if ns == COMMON:
            # vehicles/common/etk/x.jbeam -> 'etk'; vehicles/common/engines/bastion/x.jbeam -> 'bastion'
            segs = part.file.split('/')[2:-1]
            sub = segs[1] if segs[:1] == ['engines'] and len(segs) > 1 else (segs[0] if segs else '')
            if sub:
                who = vehicle_display(vfs, f'/vehicles/{sub}/', tr)
                who = who.upper() if len(who) <= 3 else who.capitalize() if who == sub else who
        return f'Vanilla {who}: {eng}'
    return f'{src_titles.get(part.source.name, part.source.title)}: {eng}'


def find_donors(index, vfs, vehicles, include_car_mod_engines=False):
    """Every engine part that is a usable donor. Returns (donors, skipped[(part, reason)])."""
    donors, skipped = [], []
    for ns, parts in index.ns.items():
        for part in parts.values():
            rows = engine_row(part.data)
            if not rows:
                continue
            if ns in SKIP_NAMESPACES:
                skipped.append((part, 'simplified AI traffic engine'))
                continue
            if part.source.kind == 'mod' and not include_car_mod_engines:
                # car mods ship engines for their own vehicle; only engine packs donate by default
                mains = vehicles.get(ns, [])
                if any(m.source is part.source for m in mains):
                    skipped.append((part, 'engine of a car mod (enable "use car-mod engines" to include)'))
                    continue
            if len(rows) != 1:
                skipped.append((part, 'more than one combustion device'))
                continue
            r = rows[0]
            if r.get('name') != ENGINE_NAME or r.get('inputName') not in ('dummy', None):
                skipped.append((part, f'engine device is "{r.get("name")}" fed by "{r.get("inputName")}"; '
                                      'transmissions expect mainEngine fed by dummy'))
                continue
            me = part.data.get('mainEngine')
            if not isinstance(me, dict):
                skipped.append((part, 'no mainEngine section'))
                continue
            donors.append(Donor(part, None))
    return donors, skipped
