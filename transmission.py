"""Transmission analysis (see NOTES.md section 8).

Observed in BeamNG 0.40.5:
- every car's transmission part holds a "stage": coupling device(s) fed by mainEngine
  (frictionClutch / torqueConverter / centrifugalClutch, or none for DCTs) -> one gearbox
  device named exactly 'gearbox'. Some parts add devices after the gearbox (rangeBox,
  shafts, differentials) which the rest of the car reads from.
- vehicleController.lua finds the gearbox with powertrain.getDevice("gearbox") and picks the
  shift logic from its type; shift-RPM arrays of a different length are padded.
- powertrain.lua merges v.data[<device name>] into each device: config sections are keyed by name.
Swap unit = the stage. Devices after the gearbox, transfer cases, diffs stay with the car.
"""
from .analyze import SKIP_NAMESPACES
from .index import Choice, resolve, read_pc
from .tables import table_rows

GEARBOX_TYPES = ('manualGearbox', 'automaticGearbox', 'dctGearbox', 'cvtGearbox', 'sequentialGearbox')
GEARBOX_NAME = 'gearbox'
# config sections a transmission tuning part may carry, besides the stage device names
TRANS_SECTIONS = {'gearbox', 'clutch', 'torqueConverter', 'vehicleController'}


def gearbox_stage(rows):
    """rows: powertrain row dicts of one part.
    Returns (stage_rows, after_rows, error). stage = mainEngine -> ... -> gearbox."""
    gbs = [r for r in rows if r.get('type') in GEARBOX_TYPES]
    if len(gbs) != 1:
        return None, None, f'{len(gbs)} gearbox devices in one part'
    gb = gbs[0]
    if gb.get('name') != GEARBOX_NAME:
        return None, None, f'gearbox device is named "{gb.get("name")}", vehicleController needs "gearbox"'
    by_name = {r.get('name'): r for r in rows}
    chain, cur = [gb], gb
    while cur.get('inputName') != 'mainEngine':
        nxt = by_name.get(cur.get('inputName'))
        if nxt is None or nxt in chain:
            return None, None, f'device "{cur.get("name")}" is fed by "{cur.get("inputName")}", which is not in this part'
        chain.insert(0, nxt)
        cur = nxt
    names = {r.get('name') for r in chain}
    after = []
    for r in rows:
        if r in chain:
            continue
        if r.get('inputName') == 'mainEngine' or r.get('inputName') in names - {GEARBOX_NAME}:
            return None, None, f'extra device "{r.get("name")}" ({r.get("type")}) branches off the engine/coupling'
        after.append(r)
    return chain, after, None


class TransTarget:
    def __init__(self, target):
        self.target = target
        self.ns = target.ns
        self.slot = self.slot_path = self.base = None
        self.stage = self.after = None
        self.reason = None


def analyze_transmission(index, vfs, target):
    """Find the car's transmission part in its default config (any depth)."""
    tt = TransTarget(target)
    pc = read_pc(vfs, target.pc_path) if target.pc_path else None
    tree = resolve(index, target.ns, target.main, Choice(pc))
    for node in tree.walk():
        rows = table_rows(node.part.data.get('powertrain'))
        if node.slot is None or not any(r.get('type') in GEARBOX_TYPES for r in rows):
            continue
        stage, after, err = gearbox_stage(rows)
        if err:
            tt.reason = f'{node.part.name}: {err}'
            return tt
        if any(r.get('type') == 'combustionEngine' or 'combustion' in str(r.get('type')).lower() for r in rows):
            tt.reason = f'{node.part.name}: engine and gearbox are one part'
            return tt
        tt.slot, tt.slot_path, tt.base, tt.stage, tt.after = node.slot, node.path, node.part, stage, after
        return tt
    tt.reason = 'no gearbox device in the default config'
    return tt


class TransDonor:
    def __init__(self, part, stage, after):
        self.part, self.stage, self.after = part, stage, after
        self.label = None


def find_transmission_donors(index, vehicles, include_car_mod_parts=False):
    donors, skipped = [], []
    for ns, parts in index.ns.items():
        for part in parts.values():
            rows = table_rows(part.data.get('powertrain'))
            if not any(r.get('type') in GEARBOX_TYPES for r in rows):
                continue
            if ns in SKIP_NAMESPACES:
                skipped.append((part, 'simplified AI traffic part'))
                continue
            if part.source.kind == 'mod' and not include_car_mod_parts:
                if any(m.source is part.source for m in vehicles.get(ns, [])):
                    skipped.append((part, 'transmission of a car mod (enable "use car-mod parts" to include)'))
                    continue
            stage, after, err = gearbox_stage(rows)
            if err:
                skipped.append((part, err))
                continue
            if any('combustion' in str(r.get('type')).lower() for r in rows):
                skipped.append((part, 'engine and gearbox are one part'))
                continue
            donors.append(TransDonor(part, stage, after))
    return donors, skipped
