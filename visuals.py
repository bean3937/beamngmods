"""Visible donor engine models for vanilla cars (NOTES.md section 9).

Per vanilla car this produces, in vehicles/<car>/esc_models_<car>.jbeam:
  esc_m_own            the car's own engine-attached visuals (moved out of its Engine catalog part)
  esc_m_<donor id>     the donor engine's block + default intake/turbo/headers, placed on the car's engine
  esc_hx_<car>_*       copies of the car's header parts without the header meshes (physics unchanged)
Every catalog engine gets an "Engine model" slot: esc_m_<id> (default) | esc_m_own | empty.
"""
import ast
import hashlib
import math
import operator
import re

from . import dae
from . import placement as P
from .analyze import classify_slot, is_engine_part
from .index import resolve, Choice, read_pc, COMMON
from .tables import table_rows, part_slots

HEADER_NAME = re.compile(r'header|manifold|exhmanifold|turbo', re.I)
BODY_NAME = re.compile(r'downpipe|muffler|exhausttip|exhaust_?tip|tailpipe|radhose|radtube|radiator|intercooler|engbay|crap', re.I)
# parts a donor engine may poke through (the user can remove a hood/lid; bumpers/grille are the front)
ALLOWED_PART = re.compile(r'hood|bonnet|scoop|tailgate|decklid|trunk|engine_?lid|engine_?cover|bumper|fascia|grille|'
                          r'valance|radsupport|radiator|headlight|taillight|license|lightbar', re.I)
MODEL_SLOT_OWN = 'esc_mown'
# car-side meshes of the car's own engine (hoses, airbox, ...) that belong to its induction: a foreign engine hides
# them, radiator hoses and everything else stay (vis2, Phase 2 item 3; names checked against every vanilla car)
INTAKE_NAME = re.compile(r'intake|airbox|filter|icpipe|intercooler|turbo|supercharg|blower|heatshield', re.I)
# induction meshes that sit at the donor car's radiator (front-mounted intercoolers and their pipes): not shown on a
# foreign engine (they would land at the donor car's radiator position) - Phase 3 DECIDE default
FRONT_MOUNT = re.compile(r'intercooler|icpipe|ic_pipe|chargepipe|charge_pipe', re.I)


def foreign_induction(mesh, groups, engine_groups):
    """A body-bound induction mesh (turbo, intake, carb, filter) that also touches the engine or its own tuning
    part's nodes: a foreign engine shows it too, rigidly with the engine (Phase 3 item 3)."""
    return (bool(INTAKE_NAME.search(mesh)) and not FRONT_MOUNT.search(mesh)
            and bool(set(groups) & set(engine_groups)))


# ---------------------------------------------------------------- small helpers
def groups_of(v):
    if isinstance(v, str):
        return [v] if v else []
    return [g for g in v if isinstance(g, str) and g] if isinstance(v, list) else []


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Pow: operator.pow}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq,
        ast.NotEq: operator.ne}


def eval_num(v, vars_):
    """Numbers, "$var" and simple "$=" arithmetic (as used in node positions), single comparisons and
    expressionParser.lua case(selector, a, b). None if not resolvable."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str) or not v.startswith('$'):
        return None
    if not v.startswith('$='):
        return vars_.get(v)
    expr = re.sub(r'\$([A-Za-z_]\w*)', lambda m: f'V_{m.group(1)}', v[2:])
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        return None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id.startswith('V_'):
            val = vars_.get('$' + n.id[2:])
            if val is None:
                raise ValueError
            return val
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            return _CMP[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'case' and len(n.args) >= 2:
            sel = ev(n.args[0])               # Lua case(): boolean -> 2nd / 3rd argument, number n -> n+1th
            i = (1 if sel else 2) if isinstance(sel, bool) else int(math.floor(sel))
            return ev(n.args[min(i, len(n.args) - 1)])
        raise ValueError
    try:
        r = ev(tree)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    return float(r) if not isinstance(r, bool) else r


def tree_vars(tree):
    out = {}
    for node in tree.walk():
        for r in table_rows(node.part.data.get('variables')):
            n, d = r.get('name'), r.get('default')
            if isinstance(n, str) and isinstance(d, (int, float)) and not isinstance(d, bool):
                out[n if n.startswith('$') else '$' + n] = float(d)
    return out


def node_positions(parts, vars_):
    """{node id: (x, y, z), groups} for the nodes of the given parts."""
    pos, grp = {}, {}
    for p in parts:
        for r in table_rows(p.data.get('nodes')):
            nid = r.get('id')
            xyz = [eval_num(r.get(k), vars_) for k in ('posX', 'posY', 'posZ')]
            if isinstance(nid, str) and None not in xyz:
                pos[nid] = tuple(xyz)
                grp[nid] = groups_of(r.get('group'))
    return pos, grp


def short(s, n=10):
    return hashlib.sha1(s.encode('utf-8')).hexdigest()[:n]


# ---------------------------------------------------------------- mesh lookup / extraction
class Meshes:
    """Where every mesh name lives, and lazy extraction of points / raw geometry."""

    def __init__(self, vfs, log=print):
        self.vfs, self.log = vfs, log
        self.where = {}          # mesh name -> [lower vfs paths]
        for p in vfs.files_under('vehicles/', '.dae'):
            try:
                for name in dae.scan_names(vfs.read(p)):
                    self.where.setdefault(name, []).append(p)
            except Exception:     # unreadable model file: its meshes are simply unknown
                continue
        self.data = {}           # (path, mesh) -> dict
        self.keep = set()        # mesh names whose triangles are kept too (Phase 4 surface hits)
        self._mats = None

    def visible(self, mesh, ns):
        """Model file holding `mesh` that a car in namespace ns loads (own folder first), else None."""
        files = self.where.get(mesh, [])
        own = ns.strip('/') + '/'
        for f in files:
            if f.startswith(own):
                return f
        for f in files:
            if f.startswith('vehicles/common/'):
                return f
        return None

    def any_file(self, mesh, ns):
        return self.visible(mesh, ns) or (self.where.get(mesh) or [None])[0]

    def load(self, wanted):
        """wanted: {(path, mesh)} -> fills self.data with points, box, raw geometry and material ids."""
        by_file = {}
        for f, m in wanted:
            if (f, m) not in self.data:
                by_file.setdefault(f, set()).add(m)
        for f, names in sorted(by_file.items()):
            try:
                raw = self.vfs.read(f)
                nodes = dae.scene_nodes(raw)
                mats = dae.library_items(raw, 'materials', 'material')
            except Exception as e:
                for m in names:
                    self.data[(f, m)] = {'error': f'unreadable model file: {e}'}
                continue
            for m in names:
                node = nodes.get(m)
                if node is None:
                    self.data[(f, m)] = {'error': 'mesh not in scene'}
                    continue
                try:
                    verts, tris = dae.read_mesh(raw, node)
                except Exception as e:
                    self.data[(f, m)] = {'error': f'geometry unreadable: {e}'}
                    continue
                if not verts:
                    self.data[(f, m)] = {'error': 'no vertices'}
                    continue
                pts = P.thin(dae.sample_surface(verts, tris))
                self.data[(f, m)] = {'points': pts, 'box': P.box(verts), 'node': node,
                                     'geometry_raw': dae.geometry_bytes(raw, node['geometry']),
                                     'materials': {mid: mats[mid][0] for _s, mid in node['materials'] if mid in mats}}
                if m in self.keep:
                    self.data[(f, m)].update(verts=verts, tris=tris)

    def materials_index(self):
        """mapTo name -> (material dict, file) over every *materials.json the game would know."""
        if self._mats is None:
            from . import jbeam
            self._mats = {}
            for p in self.vfs.files_under('vehicles/', 'materials.json'):
                try:
                    doc = jbeam.loads(self.vfs.read(p).decode('utf-8', 'replace'))
                except Exception:
                    continue
                for k, v in (doc.items() if isinstance(doc, dict) else []):
                    if isinstance(v, dict):
                        self._mats.setdefault(v.get('mapTo') or k, (v, p))
        return self._mats


# ---------------------------------------------------------------- engine visuals of one engine part
class EngineVisual:
    def __init__(self):
        self.rows = []            # dicts: mesh, groups, pos, rot, scale, kind, file
        self.body_rows = []       # rows that stay with the car (body side)
        self.engine_groups = []   # groups the block binds to
        self.block = None         # row of the engine block mesh
        self.dir = (0, 1)         # engine -> gearbox direction (snapped)
        self.centre = (0.0, 0.0, 0.0)
        self.node_points = []     # engine part node positions: the placement anchor (same role in every car)
        self.header_parts = {}    # slot name -> [candidate part names with engine-side header meshes]
        self.exhaust_nodes = []   # TreeNodes of exhaust-class slots directly below the engine part
        self.nested_exhaust = []  # TreeNodes of exhaust-class slots inside its tuning parts (e.g. Bastion V8 intake)
        self.trans_centre = None  # centre of the default transmission's nodes (gearbox-aligned placement)
        self.problems = []


def _row(r, kind, part):
    return {'mesh': r.get('mesh'), 'groups': groups_of(r.get('[group]:')), 'pos': r.get('pos'),
            'rot': r.get('rot'), 'scale': r.get('scale'), 'kind': kind, 'part': part.name,
            'opts': {k: v for k, v in r.items() if k not in ('mesh', '[group]:', 'pos', 'rot', 'scale')}}


def analyze_engine(index, ns, engine_node, vars_):
    """engine_node: TreeNode of the engine part (children resolved). Classifies its visuals."""
    ev = EngineVisual()
    eng_parts, header_nodes, kinds = [engine_node.part], [], []
    trans_parts = []

    def walk(node, inside):
        for c in node.children:
            cls = classify_slot(index, ns, c.slot)
            if inside == 'tuning':
                if cls == 'exhaust':
                    ev.nested_exhaust.append(c)       # recorded only; the classification below is unchanged
                cls = 'header' if cls == 'exhaust' else 'tuning'
            if cls == 'tuning':
                eng_parts.append(c.part)
                kinds.append((c, 'tuning'))
                walk(c, 'tuning')
            elif cls == 'exhaust':
                header_nodes.append(c)
                kinds.append((c, 'header'))
            elif cls == 'drivetrain':
                trans_parts.append(c.part)
    walk(engine_node, None)
    pos, grp = node_positions(eng_parts + [h.part for h in header_nodes], vars_)
    side_groups = {g for gs in grp.values() for g in gs}
    # an engine block often also binds to the gearbox nodes (ETK: ["etk_engine", "etk_transmission"])
    _tp, tgrp = node_positions(trans_parts, vars_)
    bind_ok = side_groups | {g for gs in tgrp.values() for g in gs}
    ev.side_groups, ev.bind_ok = side_groups, bind_ok
    eng_pos, eng_grp = node_positions([engine_node.part], vars_)
    if eng_pos:
        ev.node_points = list(eng_pos.values())
        xs, ys, zs = zip(*eng_pos.values())
        ev.centre = (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
    ev.exhaust_nodes = list(header_nodes)
    tpos, _ = node_positions(trans_parts, vars_)
    if tpos:
        ev.trans_centre = tuple(sum(p[i] for p in tpos.values()) / len(tpos) for i in range(3))
    if tpos and eng_pos:
        tx = sum(p[0] for p in tpos.values()) / len(tpos)
        ty = sum(p[1] for p in tpos.values()) / len(tpos)
        ev.dir = P.snap_dir(tx - ev.centre[0], ty - ev.centre[1])
    ev.engine_groups = sorted({g for gs in eng_grp.values() for g in gs})

    def classify(kind, part):
        for r in table_rows(part.data.get('flexbodies')):
            if not isinstance(r.get('mesh'), str):
                continue
            row = _row(r, kind, part)
            g = set(row['groups'])
            engine_side = bool(g & side_groups) and g <= bind_ok and not BODY_NAME.search(row['mesh'])
            if kind == 'header':
                engine_side = engine_side and bool(HEADER_NAME.search(row['mesh']))
            (ev.rows if engine_side else ev.body_rows).append(row)

    classify('engine', engine_node.part)
    for c, k in kinds:
        classify(k, c.part)
        if k == 'header' and any(r['part'] == c.part.name for r in ev.rows):
            ev.header_parts.setdefault(c.slot['name'], []).append(c.part.name)
    return ev


# ---------------------------------------------------------------- transforms
def row_plain_rot(row):
    """(plain z rotation degrees, ok) of a mesh row's own rot; only rotations about Z are supported."""
    rot = row.get('rot') or {}
    rx, ry, rz = (float(rot.get(k, 0) or 0) for k in 'xyz') if isinstance(rot, dict) else (0, 0, 0)
    if abs(rx) > 1e-6 or abs(ry) > 1e-6:
        return 0.0, False
    if abs(rz) < 1e-9:
        return 0.0, True
    for plain in (rz, -rz):
        if P.rot_param_for(plain) == rz:
            return plain, True
    return 0.0, False


def row_pos(row):
    p = row.get('pos') or {}
    if not isinstance(p, dict):
        return (0.0, 0.0, 0.0)
    try:
        return tuple(float(p.get(k, 0) or 0) for k in 'xyz')
    except (TypeError, ValueError):
        return None


def row_scale(row):
    s = row.get('scale') or {}
    if not isinstance(s, dict):
        return (1.0, 1.0, 1.0)
    try:
        return tuple(float(s.get(k, 1) if s.get(k) is not None else 1) for k in 'xyz')
    except (TypeError, ValueError):
        return None


def row_geometry(row, meshes, f):
    """(vertices, triangles) of a kept mesh (Meshes.keep) after the row's own pos/rot/scale, or None."""
    d = meshes.data.get((f, row['mesh'])) or {}
    if 'verts' not in d:
        return None
    rp, (rr, ok), sc = row_pos(row), row_plain_rot(row), row_scale(row)
    if rp is None or not ok or sc is None:
        return None
    return ([(q[0] + rp[0], q[1] + rp[1], q[2] + rp[2]) for q in (P.rotz((p[0] * sc[0], p[1] * sc[1], p[2] * sc[2]), rr)
                                                              for p in d['verts'])], d['tris'])


def row_points(row, meshes, f):
    """Mesh points in the source car's coordinates after the row's own pos/rot/scale."""
    d = meshes.data.get((f, row['mesh'])) or {}
    pts = d.get('points')
    if not pts:
        return None
    rp, (rr, ok), sc = row_pos(row), row_plain_rot(row), row_scale(row)
    if rp is None or not ok or sc is None:
        return None
    return [(q[0] + rp[0], q[1] + rp[1], q[2] + rp[2])
            for q in (P.rotz((p[0] * sc[0], p[1] * sc[1], p[2] * sc[2]), rr) for p in pts)]


def block_row(ev, meshes, files, allow_tuning=False):
    """Largest engine-part mesh = the engine block (used to align donor and car). allow_tuning: engines whose
    engine part has no mesh of its own (block in a tuning part, e.g. engine packs) use the largest tuning mesh."""
    best, size = None, -1
    kinds = ('engine',)
    if allow_tuning and not any(r['kind'] == 'engine' for r in ev.rows):
        kinds = ('tuning',)
    for r in ev.rows:
        if r['kind'] not in kinds:
            continue
        d = meshes.data.get((files.get(r['mesh']), r['mesh'])) or {}
        if 'box' not in d:
            continue
        (a, b) = d['box']
        vol = (b[0] - a[0]) * (b[1] - a[1]) * (b[2] - a[2])
        if vol > size:
            best, size = r, vol
    return best
