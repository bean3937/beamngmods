"""JBeam table helpers mirroring lua/common/jbeam/tableSchema.lua and io.lua (BeamNG 0.40).

A JBeam table is a list: first row is the header, then rows. A dict row sets
"local options" that apply to all following rows; a trailing dict inside a row
adds options to that row only.
"""
import copy

SLOTS2_HEADER = ["name", "allowTypes", "denyTypes", "default", "description"]


def table_rows(tbl):
    """Yield row dicts (header-keyed, with inherited options)."""
    if not isinstance(tbl, list) or not tbl or not isinstance(tbl[0], list):
        return []
    header = tbl[0]
    opts, out = {}, []
    for row in tbl[1:]:
        if isinstance(row, dict):
            opts.update(row)
            continue
        if not isinstance(row, list):
            continue
        r = copy.deepcopy(opts)
        for i, v in enumerate(row):
            if isinstance(v, dict) and i >= len(header) - 1:
                r.update(v)
                break
            if i < len(header):
                r[header[i]] = v
        if r.get('disable') is True:
            continue
        out.append(r)
    return out


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def part_slots(part):
    """Normalized slot list (like io.lua processSlotsDestructive): dicts with
    name, allowTypes, denyTypes, default, description, opts."""
    res = []
    if isinstance(part.get('slots2'), list):
        rows = table_rows(part['slots2'])
        for r in rows:
            name = r.pop('name', None)
            allow = _as_list(r.pop('allowTypes', None))
            deny = _as_list(r.pop('denyTypes', None))
            default = r.pop('default', None)
            desc = r.pop('description', name)
            if not name:
                continue
            res.append(dict(name=name, allowTypes=allow, denyTypes=deny,
                            default=default, description=desc, opts=r))
        return res
    slots = part.get('slots')
    if not isinstance(slots, list) or not slots:
        return res
    if isinstance(slots[0], list) and (not slots[0] or slots[0][0] != 'type'):
        slots = [["type", "default", "description"]] + slots  # game adds missing header too
    for r in table_rows(slots):
        typ = r.pop('type', None)
        if not isinstance(typ, str) or not typ:
            continue
        default = r.pop('default', None)
        desc = r.pop('description', typ)
        name = r.pop('name', None) or typ
        res.append(dict(name=name, allowTypes=[typ], denyTypes=[], default=default,
                        description=desc if isinstance(desc, str) else typ, opts=r))
    return res


def slot_types(part):
    st = part.get('slotType')
    return _as_list(st) if isinstance(st, (str, list)) else []


def part_fits(part, slot):
    """io.lua getCompatiblePartNamesForSlot (slots2 semantics; slots v1 are upgraded)."""
    types = slot_types(part)
    if not any(t in slot['allowTypes'] for t in types):
        return False
    return not any(t in slot['denyTypes'] for t in types)


def slots2_table(slots):
    """Write normalized slots back as a slots2 table."""
    tbl = [list(SLOTS2_HEADER)]
    for s in slots:
        row = [s['name'], list(s['allowTypes']), list(s['denyTypes']),
               s['default'] if s['default'] is not None else '', s['description']]
        if s.get('opts'):
            row.append(dict(s['opts']))
        tbl.append(row)
    return tbl
