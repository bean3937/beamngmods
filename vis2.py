"""Engine visuals, architecture 2 (EXPERIMENTAL test builds - see IN_GAME_TEST_ENGINE_VISUALS.md).

Per vanilla car:

  <car engine slot> -> esc_catalog_<car>         adds node group esc_engine_vis to the car's engine nodes;
                                                 declares the Tuning sliders (engine model nudge / turn / flip)
     esc_vismode     -> esc_vm_donor_<car>        "Donor engine model"      $esc_vis = 1, $esc_own = 0
                      | esc_vm_own_<car>          "Car's own engine model"  $esc_vis = 0, $esc_own = 1 (+ car meshes)
                      | esc_vm_none_<car>         "No engine model"         $esc_vis = 0, $esc_own = 0
        esc_cat      -> esc_p_<car>_<id>          one per offered engine, named like the engine:
                                                   native  - the engine's own exhaust chain, $esc_nat = 1, no transform
                                                   foreign - the car's exhaust (header copies), car hoses, placement
                                                   nomodel - the car's exhaust + the car's own engine meshes
           <exhaust slots>                         (moved here from the catalog part)
           esc_eng   -> esc_e_<id>                the shared catalog engine; the slot carries nodeRotate/nodeMove
              esc<id>_<slot> -> esc_t_<id>        shared tuning parts

Shared engine/tuning parts carry three kinds of flexbody rows (all bound to esc_engine_vis unless noted):
  engine side                                     disable "$=$esc_vis < 0.5"
  native only (body side, original groups)        disable "$=$esc_vis + $esc_nat < 1.5"
  default headers of an exhaust slot (foreign)    disable "$=$esc_vis - $esc_nat < 0.5"
The slot options of esc_eng move every flexbody below it (jbeam/utils.lua getFlexbodyPosRotAfterNodeRotateOffsetMove);
slot `variables` reach the slot options and the parts below (jbeam/variables.lua processParts:
apply(slotOptions, svars), apply(part, svars)). Mod cars keep the normal catalog: no esc_engine_vis nodes and
$esc_vis = 0, so the shared meshes never load.
"""
import ast
import math
import re

from . import placement as P
from .tables import table_rows
from .visuals import BODY_NAME, HEADER_NAME, groups_of, eval_num, short, foreign_induction
from .joints import FILLER_MESH
FIT_PREFIX = ('esc_fit_', 'esc_pipe_')
FIT_MIN_HITS = 0.5        # = models.ModelBuilder.FIT_MIN_HITS

VIS_GROUP = 'esc_engine_vis'
VIS_VAR, NAT_VAR, OWN_VAR = '$esc_vis', '$esc_nat', '$esc_own'
DISABLE = '$=$esc_vis < 0.5'                     # engine-side donor rows; car meshes shown by a wrapper
DISABLE_NAT = '$=$esc_vis + $esc_nat < 1.5'      # native engines only: their body-side meshes
DISABLE_FOREIGN = '$=$esc_vis - $esc_nat < 0.5'  # foreign engines only: headers of the donor's default exhaust part
DISABLE_OWN = '$=$esc_own < 0.5'                 # the car's header meshes (header copies)
NOMODEL_OWN = '$=$esc_vis + $esc_own'            # engines without a model show the car's headers in donor mode too
MODE_SLOT, CAT_SLOT, ENG_SLOT = 'esc_vismode', 'esc_cat', 'esc_eng'
FLEX_HEADER = ['mesh', '[group]:', 'nonFlexMaterials']
DEFORM_OPTS = ('deformGroup', 'deformMaterialBase', 'deformMaterialDamaged', 'deformSound')
CATEGORY = 'Engine swap catalog'


def _decl(name, title, default=0, lo=0, hi=1, unit='', hidden=True, desc='internal switch set by the Engine model slot'):
    d = {'name': name, 'type': 'range', 'unit': unit, 'category': CATEGORY, 'default': default,
         'min': lo, 'max': hi, 'title': title, 'description': desc}
    if hidden:
        d['hideInUI'] = True      # ui-vue tuningStore.js skips these
    return d


VIS_DECL = _decl(VIS_VAR, 'Engine model visible')
NAT_DECL = _decl(NAT_VAR, 'Engine model native')
OWN_DECL = _decl(OWN_VAR, "Car's own engine model visible")
SLIDER_DECLS = [
    _decl(P.NUDGE_VARS[0], 'Engine model: left / right', 0, -0.3, 0.3, 'm', False,
          'moves the donor engine model sideways (+ = right); visual only'),
    _decl(P.NUDGE_VARS[1], 'Engine model: forward / back', 0, -0.4, 0.4, 'm', False,
          'moves the donor engine model along the car (+ = toward the rear); visual only'),
    _decl(P.NUDGE_VARS[2], 'Engine model: up / down', 0, -0.3, 0.3, 'm', False,
          'moves the donor engine model up (+) or down; visual only'),
    _decl(P.TURN_VAR, 'Engine model: turn', 0, -20, 20, 'deg', False,
          'turns the donor engine model about its own vertical axis; visual only')]
FLIP_DECL = _decl(P.FLIP_VAR, 'Engine model: flip gearbox end (turned engines)', 0, 0, 1, '', False,
                  'engines turned 90 deg into this car: 1 = gearbox end the other way (not fit-checked); visual only')
# mode, label, $esc_vis, $esc_own
MODES = (('donor', 'Donor engine model', 1, 0), ('own', "Car's own engine model", 0, 1),
         ('none', 'No engine model', 0, 0))
KINDS = ('native', 'foreign', 'nomodel')


def car_key(ns):
    return ns.strip('/').split('/')[1]


def mode_part(ns, mode):
    return f'esc_vm_{mode}_{car_key(ns)}'


def wrapper_name(ns, did):
    return f'esc_p_{car_key(ns)}_{did[len("esc_e_"):]}'


def with_opts(row, **extra):
    """A flexbody table row [mesh, groups, nonflex(, opts)] with extra options merged in."""
    row = list(row) + [[]] * (3 - len(row)) if len(row) < 3 else list(row)
    opts = dict(row[3]) if len(row) > 3 and isinstance(row[3], dict) else {}
    opts.update(extra)
    return row[:3] + [opts]


def switched(row, ours):
    """with_opts(row, disable=ours), keeping a row's own disable condition (vanilla rows use e.g.
    "$=$components.luxeTrim == true"): hidden when ours OR theirs says so (Lua `or`)."""
    old = row[3].get('disable') if len(row) > 3 and isinstance(row[3], dict) else None
    if old is None or old is False:
        return with_opts(row, disable=ours)
    if old is True:
        return list(row)
    theirs = old[2:] if isinstance(old, str) and old.startswith('$=') else old
    return with_opts(row, disable=f'$=({ours[2:]}) or ({theirs})')


OURS = (DISABLE, DISABLE_NAT, DISABLE_FOREIGN, DISABLE_OWN)      # our own switch expressions (always resolvable)
_COMBINED = re.compile(r'^\$=\((.*?)\) or \(')


# ---------------------------------------------------------------- shared rows (build side)
class RowMaker:
    """Flexbody rows of a donor part, rewritten for the shared engine/tuning parts."""

    def __init__(self, meshes, copy_name, induction=False):
        self.meshes, self.copy_name, self.induction = meshes, copy_name, induction
        self.copies = {}          # (file, mesh) -> copied name (vanilla car-folder meshes)

    def _mesh_name(self, part, mesh, ctx_ns, notes, vfs):
        f = self.meshes.any_file(mesh, ctx_ns)
        if f is None:
            notes.append(f'{part.name}: mesh {mesh} not found in any model file, left out')
            return None
        if f.startswith('vehicles/common/'):
            return mesh
        src = vfs.source_of(f)
        if src is None or src.kind != 'vanilla':
            notes.append(f'{part.name}: mesh {mesh} lives in a mod car folder ({f}), left out')
            return None
        return self.copies.setdefault((f, mesh), self.copy_name(f, mesh))

    def rows(self, part, ctx_ns, side_groups, bind_ok, notes, vfs, header_parts=()):
        """header_parts: default parts of exhaust-class slots of `part` (their slots stay with the car)."""
        own_groups = {g for r in table_rows(part.data.get('nodes')) for g in groups_of(r.get('group'))}
        out = []
        for r in table_rows(part.data.get('flexbodies')):
            mesh = r.get('mesh')
            if not isinstance(mesh, str):
                continue
            g = groups_of(r.get('[group]:'))
            engine_side = (set(g) & (side_groups | own_groups) and set(g) <= (bind_ok | own_groups)
                           and not BODY_NAME.search(mesh))
            if engine_side:
                groups, dis = [VIS_GROUP], DISABLE
            else:
                # body side: shown only in the engine's own car (native), where its groups exist; the part's own
                # nodes are not carried (performance-only shared part), so those groups become esc_engine_vis
                keep = [x for x in g if x not in own_groups]
                groups, dis = (keep + [VIS_GROUP]) if len(keep) < len(g) or not keep else keep, DISABLE_NAT
            row = self._row(part, r, mesh, groups, dis, ctx_ns, notes, vfs)
            if row:
                out.append(row)
                if self.induction and not engine_side and foreign_induction(mesh, g, side_groups | own_groups):
                    out.append([row[0], [VIS_GROUP], row[2], dict(row[3], disable=DISABLE_FOREIGN)])
        for hp in header_parts:
            hown = {x for rr in table_rows(hp.data.get('nodes')) for x in groups_of(rr.get('group'))}
            for r in table_rows(hp.data.get('flexbodies')):
                mesh = r.get('mesh')
                if not isinstance(mesh, str) or not HEADER_NAME.search(mesh) or BODY_NAME.search(mesh):
                    continue
                g = set(groups_of(r.get('[group]:')))
                if not (g & (side_groups | hown)) or not g <= (bind_ok | hown):
                    continue
                row = self._row(hp, r, mesh, [VIS_GROUP], DISABLE_FOREIGN, ctx_ns, notes, vfs)
                if row:
                    out.append(row)
        return out

    def _row(self, part, r, mesh, groups, dis, ctx_ns, notes, vfs):
        opts = {k: v for k, v in r.items() if k not in ('mesh', '[group]:') and k not in DEFORM_OPTS}
        if any(isinstance(s, str) and s.startswith('$') for s in _strings(opts)):
            notes.append(f'{part.name}: mesh {mesh} uses variables in its row, left out')
            return None
        name = self._mesh_name(part, mesh, ctx_ns, notes, vfs)
        if name is None:
            return None
        nonflex = opts.pop('nonFlexMaterials', None)
        opts['disable'] = dis
        return [name, groups, nonflex if nonflex is not None else [], opts]


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def add_vis(content, rows, var_table, var_decls):
    """Put rows + the hidden $esc_vis / $esc_nat declarations into a shared part (in place)."""
    if not rows:
        return
    tbl = content.setdefault('flexbodies', [list(FLEX_HEADER)])
    tbl.extend(rows)
    add_decls(content, [VIS_DECL, NAT_DECL], var_table, var_decls)


def add_decls(content, decls, var_table, var_decls):
    decl = dict(var_decls(content))
    for d in decls:
        decl[d['name'].lstrip('$')] = dict(d)
    content['variables'] = var_table(decl.values())


def add_vis_group(cat, engine_groups):
    """Engine catalog part: add esc_engine_vis to every node of the car's engine block groups. Returns count."""
    tbl = cat.get('nodes')
    if not isinstance(tbl, list):
        return 0
    want, n, cur = set(engine_groups), 0, []
    for i, r in enumerate(tbl):
        if isinstance(r, dict) and 'group' in r:
            g = groups_of(r['group'])
            cur = g
            if set(g) & want:
                tbl[i] = dict(r, group=g + [VIS_GROUP])
                cur = g + [VIS_GROUP]
        elif isinstance(r, list) and r and isinstance(r[-1], dict) and 'group' in r[-1]:
            g = groups_of(r[-1]['group'])
            if set(g) & want:
                r[-1] = dict(r[-1], group=g + [VIS_GROUP])
                n += 1
        elif isinstance(r, list) and i > 0 and VIS_GROUP in cur:
            n += 1
    return n


def slot_row(s, **opt_extra):
    """part_slots() dict -> slots2 row (options kept)."""
    opts = dict(s.get('opts') or {})
    opts.update(opt_extra)
    row = [s['name'], list(s['allowTypes']), [], s.get('default') or '', s.get('description') or s['name']]
    return row + [opts] if opts else row


SLOTS2_HEADER = ['name', 'allowTypes', 'denyTypes', 'default', 'description']


def wrapper_and_modes(ns, specs, car, default_did, var_table):
    """specs: [{'did', 'kind', 'opts' (slot options of esc_eng), 'exhaust' ([slot dicts]), 'type' (engine slot
    type)}]; car: {'own_rows', 's_rows', 's_keep'} flexbody rows (no header row) of the car's own engine.
    Returns {part name: data}."""
    key = car_key(ns)
    parts, pl_type = {}, f'esc_pl_{key}'
    for sp in specs:
        kind = sp['kind']
        eng_opts = {'coreSlot': True}
        eng_opts.update(sp.get('opts') or {})
        if kind == 'native':
            eng_opts['variables'] = {NAT_VAR: 1}
        elif kind == 'nomodel':
            eng_opts['variables'] = {VIS_VAR: 0}
        rows = [SLOTS2_HEADER] + [slot_row(s) for s in sp.get('exhaust', [])]
        rows.append([ENG_SLOT, [sp['type']], [], sp['did'], 'Engine', eng_opts])
        p = {'information': {'name': sp['did'], 'authors': 'engine_swap_catalog', 'value': 0},   # renamed in files()
             'slotType': pl_type, 'slots2': rows}
        nh = car.get('nested_hdr', [])
        shown = (car['s_keep'] if kind == 'foreign' else car['own_rows'] + car['s_rows'] + nh if kind == 'nomodel'
                 else nh)
        extra = sp.get('extra_rows') or []        # Phase 3 fillers (already switched)
        if shown or extra:
            p['flexbodies'] = [list(FLEX_HEADER)] + [switched(r, DISABLE) for r in shown] + [list(r) for r in extra]
            p['variables'] = var_table([VIS_DECL])
        parts[wrapper_name(ns, sp['did'])] = p
    offered = {sp['did'] for sp in specs}
    default = wrapper_name(ns, default_did) if default_did in offered else ''
    for mode, label, vis, own in MODES:
        p = {'information': {'name': label, 'authors': 'engine_swap_catalog', 'value': 0},
             'slotType': f'esc_vm_{key}',
             'slots2': [SLOTS2_HEADER,
                        [CAT_SLOT, [pl_type], [], default, 'Catalog engine',
                         {'coreSlot': True, 'variables': {VIS_VAR: vis, OWN_VAR: own}}]],
             'variables': var_table([VIS_DECL, OWN_DECL, NAT_DECL])}
        rows = (car['own_rows'] + car['s_rows'] + car.get('nested_hdr', []) if mode == 'own'
                else car['s_keep'] if mode == 'none' else [])
        if rows:
            p['flexbodies'] = [list(FLEX_HEADER)] + [list(r) for r in rows]
        parts[mode_part(ns, mode)] = p
    return parts


# ---------------------------------------------------------------- what the game would show (validator side)
def _eval_disable(v, vars_):
    """Row 'disable' value after variables (expressionParser: '$=' -> Lua expression). None = not resolvable."""
    if isinstance(v, bool):
        return v
    if not (isinstance(v, str) and v.startswith('$')):
        return v
    m = _COMBINED.match(v)
    if m:                      # ours OR the row's own condition: ours true -> hidden, else unknown (kept)
        r = eval_num('$=' + m.group(1), vars_)
        return True if r is True else None
    r = eval_num(v, vars_)
    return r if isinstance(r, bool) else None


def visible_flexbodies(tree, root_vars):
    """[(part name, row dict, ops, vars)] for every flexbody row the game would load in this tree:
    slot options accumulate down the tree (child keys win), slot variables apply to the subtree,
    rows whose 'disable' resolves to true are dropped. Unresolvable disable -> kept, flagged."""
    out = []

    def rec(node, opts, vars_):
        if node.slot is not None:
            o = node.slot.get('opts') or {}
            sv = o.get('variables') if isinstance(o.get('variables'), dict) else {}
            vars_ = dict(vars_, **{k: (eval_num(v, vars_) if isinstance(v, str) else float(v))
                                   for k, v in sv.items()})
            opts = dict(opts, **{k: v for k, v in o.items() if k != 'variables'})
        ops = P.node_ops(_resolved(opts, vars_))
        for r in table_rows(node.part.data.get('flexbodies')):
            if not isinstance(r.get('mesh'), str):
                continue
            d = _eval_disable(r.get('disable'), vars_) if 'disable' in r else False
            if d is True:
                continue
            out.append((node.part.name, dict(r, _disable_unresolved=d is None), ops))
        for c in node.children:
            rec(c, opts, vars_)
    rec(tree, {}, dict(root_vars))
    return out


def _resolved(opts, vars_):
    return {k: ({a: eval_num(x, vars_) if isinstance(x, str) else x for a, x in v.items()}
                if isinstance(v, dict) else v) for k, v in opts.items()}


def row_vertex(r, ops, v):
    pos = tuple(float((r.get('pos') or {}).get(k, 0) or 0) for k in 'xyz') if isinstance(r.get('pos'), dict) else (0, 0, 0)
    rot = tuple(float((r.get('rot') or {}).get(k, 0) or 0) for k in 'xyz') if isinstance(r.get('rot'), dict) else (0, 0, 0)
    sc = r.get('scale') if isinstance(r.get('scale'), dict) else {}
    scale = tuple(float(sc.get(k, 1) if sc.get(k) is not None else 1) for k in 'xyz')
    return P.vertex_after_ops(ops, v, pos, rot, scale), (pos, rot, scale)


def placement_error(r, ops, inherited, deg, t):
    """Max distance (m) between where the game puts this row's mesh and the fit-check placement."""
    err = 0.0
    for v in P._PROBE:
        got, (pos, rot, scale) = row_vertex(r, ops, v)
        native = P.flexbody_apply(v, pos, rot, scale)
        placed = tuple(a + b for a, b in zip(P.rotz(native, deg), t))
        want = P.vertex_after_ops(inherited, placed, (0.0, 0.0, 0.0))
        err = max(err, math.dist(got, want))
    return err


def tag(s):
    return short(s, 8)


def ops_at(tree, path, root_vars):
    """Slot options (node_ops form) in effect for parts at `path` (accumulated from the root)."""
    opts, vars_ = {}, dict(root_vars)
    for n in tree.walk():
        if n.slot is None or not path.startswith(n.path):
            continue
        o = n.slot.get('opts') or {}
        sv = o.get('variables') if isinstance(o.get('variables'), dict) else {}
        vars_.update({k: (eval_num(v, vars_) if isinstance(v, str) else float(v)) for k, v in sv.items()})
        opts.update({k: v for k, v in o.items() if k != 'variables'})
    return P.node_ops(_resolved(opts, vars_))


_VARREF = re.compile(r'\$([A-Za-z_]\w*)')


def slot_var_refs(tree):
    """$names used in slot options / slot variables anywhere in the tree."""
    out = set()
    for n in tree.walk():
        for s in _strings(n.slot.get('opts') if n.slot else {}):
            out |= {m for m in _VARREF.findall(s) if s.startswith('$')}
    return out


def _spread(seq, n):
    seq = list(seq)
    if len(seq) <= n:
        return seq
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)]


def sample_engines(inf, default, broad, per_kind=4):
    """Which wrappers of a car the validator resolves: the default, every native, a spread of the others."""
    kinds = inf['kinds']
    if broad:
        return sorted(inf['wrappers'])
    pick = [default] if default in inf['wrappers'] else []
    pick += sorted(d for d in inf['wrappers'] if kinds[d] == 'native')
    for k in ('foreign', 'nomodel'):
        pick += _spread(sorted(d for d in inf['wrappers'] if kinds[d] == k), per_kind)
    return list(dict.fromkeys(pick))


def check_vis2(ov, index, vfs, files, report, targets, loadable, materials=None, log=print, tuning_slots=8,
               broad_targets=2, tuning_engines=12):
    """Architecture-2 checks per car x sampled offered engine x Engine model mode. Returns (checked, errors, examples).
    materials(mesh, ns) -> (missing material names, also missing in the mesh's own car) or None."""
    from .index import Choice, resolve, read_pc
    from .visuals import tree_vars
    v2 = (report.get('engine_models') or {}).get('v2') or {}
    errors, checked, examples = [], 0, {}
    for ti, (t, info) in enumerate(targets):
        inf = v2.get(t.ns)
        if not inf:
            continue
        sp = t.slot_path
        mpath, cpath = sp + MODE_SLOT + '/', sp + MODE_SLOT + '/' + CAT_SLOT + '/'
        epath = cpath + ENG_SLOT + '/'
        cs = inf['car_side']
        own_eng, s_int, s_keep = set(cs['own_meshes']), set(cs['s_intake']), set(cs['s_keep'])
        car_hdr, car_exh = set(cs['header_meshes']), set(cs['exhaust_meshes'])
        nested_hdr = set(cs.get('nested_header_meshes', []))
        pc = read_pc(vfs, t.pc_path) if t.pc_path else None
        base_probs = []
        base = resolve(index, t.ns, t.main, Choice(pc), base_probs)
        base_meshes = {r.get('mesh') for n in base.walk() for r in table_rows(n.part.data.get('flexbodies'))}
        base_probs = {m for _p, m in base_probs}
        base_refs = slot_var_refs(base)            # the car's own slot options (e.g. $trackwidth_F) are not ours
        must_keep = base_meshes - own_eng - s_int - s_keep - car_hdr - car_exh - nested_hdr - {None}
        ex = examples.setdefault(t.ns, {'tuning_changes': [], 'rows_in_donor_mode': {}, 'tuning_slots_tried': 0,
                                        'native_rows_without_nodes': {}, 'materials_checked': 0,
                                        'materials_missing_in_vanilla_too': 0})
        car_parts = {n for n in ov.ns.get(t.ns, {}) if n.startswith(('esc_vm_', 'esc_p_', 'esc_hx_', 'esc_catalog_'))}
        for did in sample_engines(inf, info.get('default_catalog_engine'), ti < broad_targets):
            wrapper, kind = inf['wrappers'][did], inf['kinds'][did]
            pl = inf['placement'].get(did, {})
            for mode, _label, _vis, _own in MODES:
                over = {sp: info['catalog_part'], mpath: mode_part(t.ns, mode), cpath: wrapper, epath: did}
                probs = []
                tree = resolve(ov, t.ns, t.main, Choice(pc, over), probs)
                rv = tree_vars(tree)
                vis = visible_flexbodies(tree, rv)
                checked += 1
                issues = [f'{p}: {m}' for p, m in probs if p.startswith(mpath) and m not in base_probs]
                node = next((n for n in tree.walk() if n.path == epath), None)
                if node is None or node.part.name != did:
                    issues.append(f'catalog engine slot {epath} did not resolve to {did}')
                    errors.append({'car': t.ns, 'part': did, 'kind': f'engine visuals v2 ({kind}, {mode})',
                                   'issues': issues})
                    continue
                groups = set()
                for n in tree.walk():
                    for r in table_rows(n.part.data.get('nodes')):
                        groups |= set(groups_of(r.get('group')))
                if VIS_GROUP not in groups:
                    issues.append(f'no node has group {VIS_GROUP} (donor meshes would not load)')
                declared = {k.lstrip('$') for k in rv}
                miss = sorted(slot_var_refs(tree) - declared - base_refs)
                if miss:
                    issues.append(f'slot options use undeclared variables {miss}')
                if any(r.get('_disable_unresolved') and r.get('disable') in OURS for _pn, r, _o in vis):
                    issues.append('a row has a disable value that cannot be resolved')
                shared = [(pn, r, o) for pn, r, o in vis if pn not in car_parts
                          and (r.get('disable') in (DISABLE, DISABLE_NAT, DISABLE_FOREIGN)
                               or VIS_GROUP in groups_of(r.get('[group]:')))]
                if any(x[1].get('disable') not in (DISABLE, DISABLE_NAT, DISABLE_FOREIGN) for x in shared):
                    issues.append('donor meshes without the visibility switch: '
                                  f"{sorted({x[1]['mesh'] for x in shared if x[1].get('disable') not in (DISABLE, DISABLE_NAT, DISABLE_FOREIGN)})[:3]}")
                eng_rows = [x for x in shared if x[1].get('disable') not in (DISABLE_NAT, DISABLE_FOREIGN)]
                nat_rows = [x for x in shared if x[1].get('disable') == DISABLE_NAT]
                for_rows = [x for x in shared if x[1].get('disable') == DISABLE_FOREIGN]
                carside = {r['mesh'] for pn, r, _o in vis if pn in car_parts}
                fill = [r for pn, r, _o in vis if pn in car_parts and r['mesh'] == FILLER_MESH]
                want_fill = len(inf.get('joints', {}).get(did, {}).get('filler_rows', [])) if mode == 'donor' else 0
                if len(fill) != want_fill:
                    issues.append(f'{len(fill)} filler pieces visible, expected {want_fill} ({kind}, {mode})')
                # Phase 4: generated bellhousings / pipes - exactly the ones the build made for this engine
                fit = sorted(r['mesh'] for pn, r, _o in vis if pn in car_parts and r['mesh'].startswith(FIT_PREFIX))
                want = sorted(inf.get('joints', {}).get(did, {}).get('fit_rows', [])) if mode == 'donor' else []
                if fit != want:
                    issues.append(f'{len(fit)} generated fit meshes visible, expected {len(want)} ({kind}, {mode})')
                j = inf.get('joints', {}).get(did, {})
                if mode == 'donor' and j.get('grade') == 'adapter':
                    h = j.get('hits') or (0, 0)
                    if min(h) < FIT_MIN_HITS:
                        issues.append(f'bellhousing ends sit on the parts at only {h} of their points')
                for r in (r for pn, r, _o in vis if pn in car_parts and r['mesh'].startswith(FIT_PREFIX)):
                    if not loadable(r['mesh'], t.ns):
                        issues.append(f'fit mesh {r["mesh"]} is not in any model file this car loads')
                    elif materials is not None:
                        m = materials(r['mesh'], t.ns)
                        if m and m[0]:
                            issues.append(f'fit mesh uses materials this car does not load: {sorted(m[0])}')
                    if VIS_GROUP not in groups_of(r.get('[group]:')):
                        issues.append(f'fit mesh binds to {r.get("[group]:")}, without {VIS_GROUP}')
                    if r.get('disable') != DISABLE:
                        issues.append(f'fit mesh {r["mesh"]} without the donor-model switch')
                for r in fill:
                    if not loadable(r['mesh'], t.ns):
                        issues.append(f'filler mesh {r["mesh"]} is not in any model file this car loads')
                    elif materials is not None:
                        m = materials(r['mesh'], t.ns)
                        if m and m[0]:
                            issues.append(f'filler uses materials this car does not load: {sorted(m[0])}')
                    if groups_of(r.get('[group]:')) != [VIS_GROUP]:
                        issues.append(f'filler binds to {r.get("[group]:")}, not only {VIS_GROUP}')
                allv = {r['mesh'] for _pn, r, _o in vis}
                donor = mode == 'donor'
                want_eng = donor and kind != 'nomodel'
                if want_eng and not eng_rows:
                    issues.append('donor mode shows no donor meshes')
                if not want_eng and eng_rows:
                    issues.append(f'{len(eng_rows)} donor meshes visible ({kind}, "{mode}" mode: disable switch failed)')
                if nat_rows and not (donor and kind == 'native'):
                    issues.append(f'{len(nat_rows)} native-only meshes visible ({kind}, {mode})')
                if for_rows and not (donor and kind == 'foreign'):
                    issues.append(f'{len(for_rows)} foreign-engine header meshes visible ({kind}, {mode})')
                if donor and kind == 'native':
                    no_nodes = sorted({r['mesh'] for _p, r, _o in nat_rows
                                       if not set(groups_of(r.get('[group]:'))) & groups})
                    if no_nodes:      # the game skips such a flexbody silently (as it would in vanilla)
                        ex['native_rows_without_nodes'][did] = no_nodes[:6]
                if want_eng:
                    ex['rows_in_donor_mode'][did] = len(eng_rows) + len(for_rows) + len(nat_rows)
                    inherited = ops_at(tree, sp, rv)
                    worst = 0.0
                    for pn, r, o in eng_rows + for_rows:
                        if groups_of(r.get('[group]:')) != [VIS_GROUP]:
                            issues.append(f'{pn}: mesh {r["mesh"]} binds to {r.get("[group]:")}, not only {VIS_GROUP}')
                        worst = max(worst, placement_error(r, o, inherited, pl.get('deg', 0),
                                                           tuple(pl.get('t', (0, 0, 0)))))
                    if worst > 1e-4:
                        issues.append(f'mesh placement differs from the fit-check placement by {worst:.4f} m')
                    for pn, r, o in eng_rows + for_rows + nat_rows:
                        if not loadable(r['mesh'], t.ns):
                            issues.append(f'{pn}: mesh {r["mesh"]} is not in any model file this car loads')
                        elif materials is not None:
                            m = materials(r['mesh'], t.ns)
                            ex['materials_checked'] += 1
                            if m and m[0]:
                                if m[1]:
                                    ex['materials_missing_in_vanilla_too'] += 1
                                else:
                                    issues.append(f'{pn}: mesh {r["mesh"]} uses materials this car does not load: '
                                                  f'{sorted(m[0])[:3]}')
                # the car's own meshes, per kind and mode
                show_own = mode == 'own' or (donor and kind == 'nomodel')
                show_keep = show_own or mode == 'none' or (donor and kind == 'foreign')
                if show_own and not own_eng <= carside:
                    issues.append(f"car's own engine meshes missing: {sorted(own_eng - carside)[:4]}")
                if not show_own and own_eng & carside:
                    issues.append(f"car's own engine meshes visible ({kind}, {mode}): {sorted(own_eng & carside)[:4]}")
                if show_own and not s_int <= carside:
                    issues.append(f"car's intake meshes missing: {sorted(s_int - carside)[:4]}")
                if not show_own and s_int & carside:
                    issues.append(f"car's intake meshes visible ({kind}, {mode}): {sorted(s_int & carside)[:4]}")
                if show_keep and not s_keep <= carside:
                    issues.append(f"car's hoses/engine-bay meshes missing ({kind}, {mode}): {sorted(s_keep - carside)[:4]}")
                if not show_keep and s_keep & carside:
                    issues.append(f"car's hoses/engine-bay meshes visible ({kind}, {mode}): {sorted(s_keep & carside)[:4]}")
                show_nested = show_own or (donor and kind == 'native')
                if show_nested and not nested_hdr <= carside:
                    issues.append(f"car's intake-mounted headers missing ({kind}, {mode}): {sorted(nested_hdr - carside)[:4]}")
                if not show_nested and nested_hdr & carside:
                    issues.append(f"car's intake-mounted headers visible ({kind}, {mode}): {sorted(nested_hdr & carside)[:4]}")
                if kind != 'native':
                    if show_own and not car_hdr <= allv:
                        issues.append(f"car's headers missing ({kind}, {mode}): {sorted(car_hdr - allv)[:4]}")
                    if not show_own and car_hdr & carside:
                        issues.append(f"car's headers visible ({kind}, {mode}): {sorted(car_hdr & carside)[:4]}")
                    if not car_exh <= allv:
                        issues.append(f"car's exhaust meshes missing ({kind}, {mode}): {sorted(car_exh - allv)[:4]}")
                else:
                    want_ex = set((inf.get('native_exhaust') or {}).get(did, {}).get('slots', []))
                    have = {n.slot['name'] for n in tree.walk() if n.slot is not None and n.path.startswith(cpath)
                            and n.path.count('/') == cpath.count('/') + 1}
                    if not want_ex <= have:
                        issues.append(f'native exhaust slots missing: {sorted(want_ex - have)}')
                lost = must_keep - allv
                if lost:
                    issues.append(f'car meshes lost: {sorted(lost)[:4]}')
                if want_eng and not issues and ex.setdefault('tuning_engines', 0) < tuning_engines:
                    ex['tuning_engines'] += 1
                    _tuning_changes(ov, t, pc, over, node, eng_rows + for_rows, ex, tuning_slots, did, car_parts)
                if issues:
                    errors.append({'car': t.ns, 'part': did, 'kind': f'engine visuals v2 ({kind}, {mode})',
                                   'issues': issues[:8]})
    return checked, errors, examples


FIT_TRI_LIMIT = {'esc_fit_': 3000, 'esc_pipe_': 1500}     # Phase 4 prompt limits


def check_fit_files(files):
    """Phase 4: every generated mesh file (vehicles/<car>/esc_fit_<car>.dae) parses, each mesh is within its
    triangle limit, uses one of our materials and is listed in our materials.json. None when there are none."""
    import re
    from . import dae as _dae, jbeam as _jb
    paths = [p for p in files if re.match(r'vehicles/[^/]+/esc_fit_[^/]+\.dae$', p)]
    if not paths:
        return None
    errors, worst, count = [], {}, 0
    mats = {}
    for p, data in files.items():
        if p.endswith('esc_fit.materials.json'):
            mats = _jb.loads(data.decode('utf-8'))
    for p in paths:
        data = files[p]
        try:
            nodes = _dae.scene_nodes(data)
        except Exception as e:
            errors.append(f'{p}: unreadable ({e!r})')
            continue
        for name, node in nodes.items():
            count += 1
            verts, tris = _dae.read_mesh(data, node)
            pre = next((k for k in FIT_TRI_LIMIT if name.startswith(k)), None)
            if pre is None:
                errors.append(f'{p}: unexpected mesh {name}')
                continue
            worst[pre] = max(worst.get(pre, 0), len(tris))
            if not verts or len(tris) > FIT_TRI_LIMIT[pre]:
                errors.append(f'{p}: {name} has {len(tris)} triangles (limit {FIT_TRI_LIMIT[pre]})')
            for _sym, mid in node['materials']:
                m = mid[:-len('-material')] if mid.endswith('-material') else mid
                if not m.startswith('esc_fit_') or m not in mats:
                    errors.append(f'{p}: {name} uses material {m} that is not ours / not in esc_fit.materials.json')
    return {'errors': errors, 'summary': {'files': len(paths), 'meshes': count, 'max_tris': worst}}


def check_intake_twins(files):
    """Phase 3 report: shared intake/turbo parts that still show NOTHING on a foreign engine (the old bug where
    choosing a turbo kit showed nothing in other cars). What is left is body-mounted only (e.g. an airbox on the
    donor car's body), native-only by design: a count for the report, not an error."""
    from . import jbeam as _jb
    out = []
    for path, data in files.items():
        if not (path.endswith('.jbeam') and path.startswith('vehicles/common/engine_swap_catalog/')):
            continue
        for name, p in _jb.loads(data.decode('utf-8')).items():
            rows = [r for r in (p.get('flexbodies') or [])[1:] if isinstance(r, list) and len(r) > 3
                    and isinstance(r[3], dict)]
            abroad = [r for r in rows if r[3].get('disable') in (DISABLE, DISABLE_FOREIGN)]
            nat = [r[0] for r in rows if r[3].get('disable') == DISABLE_NAT
                   and foreign_induction(r[0], [VIS_GROUP], [VIS_GROUP])]
            if nat and not abroad:
                out.append(f'{p.get("information", {}).get("name")}: {sorted(set(nat))[:3]}')
    return out


def _tuning_changes(ov, t, pc, over, eng_node, ours, ex, limit, did, car_parts=()):
    """Switch tuning slots below the engine one at a time; record which change the visible donor meshes."""
    from .index import Choice, resolve
    from .visuals import tree_vars
    before = sorted(r['mesh'] for _p, r, _o in ours)
    n = 0
    for node in eng_node.walk():
        if node is eng_node or n >= limit:
            continue
        cands = [c for c in ov.candidates(t.ns, node.slot) if c.name != node.part.name]
        if not cands:
            continue
        n += 1
        ex['tuning_slots_tried'] += 1
        for alt in cands[:3]:
            tree = resolve(ov, t.ns, t.main, Choice(pc, dict(over, **{node.path: alt.name})))
            after = sorted(r['mesh'] for pn, r, _o in visible_flexbodies(tree, tree_vars(tree))
                           if r.get('disable') in (DISABLE, DISABLE_FOREIGN) and pn not in car_parts)
            if after != before:
                ex['tuning_changes'].append({
                    'engine': did, 'slot': node.slot.get('description') or node.slot['name'], 'path': node.path,
                    'from': node.part.data.get('information', {}).get('name'),
                    'to': alt.data.get('information', {}).get('name'),
                    'removed': sorted(set(before) - set(after))[:4], 'added': sorted(set(after) - set(before))[:4]})
                break
