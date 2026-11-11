"""Engine model generation for vanilla cars: ties visuals.py (what the meshes are) and placement.py
(where they go, and whether they fit) into parts. See NOTES.md section 9."""
import copy
import json
import math

from . import dae
from . import jbeam
from . import placement as P
from .index import resolve, Choice, read_pc
from .tables import table_rows
from .analyze import is_engine_part
from .visuals import foreign_induction
from .visuals import (Meshes, analyze_engine, tree_vars, node_positions, block_row, row_points, row_plain_rot,
                      row_pos, row_scale, short, ALLOWED_PART, HEADER_NAME, BODY_NAME, MODEL_SLOT_OWN, INTAKE_NAME)

MODEL_DIR = 'vehicles/common/engine_swap_catalog/meshes/'
FLEX_HEADER = ['mesh', '[group]:', 'nonFlexMaterials']
DROP_OPTS = {'mesh', '[group]:'}
DEFORM_OPTS = ('deformGroup', 'deformMaterialBase', 'deformMaterialDamaged', 'deformSound')
FIT_THIN = 0.03
# Fit tolerance (NOTES.md 9): how far (in 4 cm cells) beyond the space used by the car's own factory engines a donor
# may clip into inner body panels, and how far it may be moved (up, toward the bumper) to make it fit.
TOLERANCE = {'strict': {'layers': 1, 'up': 3, 'away': 4},     # up to 12 cm up, 16 cm forward
             'normal': {'layers': 2, 'up': 4, 'away': 5},     # +4 cm clipping, 16 / 20 cm moves
             'loose': {'layers': 3, 'up': 5, 'away': 6}}      # +8 cm clipping, 20 / 24 cm moves
MAX_LAYERS = 4
# Engines far bigger than the car's bay are never added by a tolerance or by "show even if it clips"
# (ratio of the donor's size to the box of all the car's factory engines; measured over 31 000 pairs, 90% of
# engines that fit are <= 1.0 / 1.0 / 1.08 of the bay in width / length / height).
SIZE_LIMIT = {'width': 1.25, 'length': 1.5, 'height': 1.4, 'volume': 1.8}
OFFERED = ('fits', 'fits_after_move', 'fits_with_tolerance', 'clips')
LABELS = {'fits': 'fits', 'fits_after_move': 'moved to fit', 'fits_with_tolerance': 'clips slightly', 'clips': 'clips'}


def _is_header_row(row):
    return (isinstance(row, list) and bool(row) and isinstance(row[0], str)
            and bool(HEADER_NAME.search(row[0])) and not BODY_NAME.search(row[0]))


VIS_GROUP_ = 'esc_engine_vis'      # = vis2.VIS_GROUP (vis2 imports models; no import cycle)


def _side(x):
    return 0 if abs(x) < 0.1 else (1 if x > 0 else -1)


def _is_engine_body(row):
    """An engine-part mesh that is the engine itself (block, sump, covers) - not a header."""
    return row['kind'] == 'engine' and not (HEADER_NAME.search(row['mesh']) and not BODY_NAME.search(row['mesh']))


def flex_row(mesh, groups, opts):
    row = [mesh, list(groups)]
    extra = {k: v for k, v in opts.items() if k not in DROP_OPTS}
    nonflex = extra.pop('nonFlexMaterials', None)
    row.append(nonflex if nonflex is not None else [])
    if extra:
        row.append(extra)
    return row


class CarModel:
    def __init__(self, target):
        self.t = target
        self.ev = None            # EngineVisual of the car's own engine
        self.files = {}           # mesh -> model file visible to this car
        self.block = None         # points of the car's engine block
        self.cells_rot = None
        self.blocked = {}         # cell -> part label (not allowed to cross)
        self.away = (0, -1)
        self.reason = None


class DonorModel:
    def __init__(self, did):
        self.did = did
        self.ev = None
        self.files = {}
        self.points = None
        self.block = None
        self.cells = {}           # deg -> voxel cells of the rotated model (at the origin placement)
        self.dims = {}            # deg -> (width, length, height) of the rotated model
        self.reason = None
        self.sig = None


class ModelBuilder:
    def __init__(self, index, vfs, log=print, hide_unfit=True, tolerance='strict', show_clipping=False,
                 align='block', block_from_tuning=False, induction=False, fit_meshes=False):
        self.index, self.vfs, self.log, self.hide_unfit = index, vfs, log, hide_unfit
        # engine visuals v2: 'gearbox' = crank axis on the car's gearbox (placement.place_gearbox);
        # block_from_tuning: engines whose block mesh sits in a tuning part (engine packs) still get a model
        self.align, self.block_from_tuning = align, block_from_tuning
        # Phase 3: a foreign engine's induction meshes are checked for clipping too (label only, never moved)
        self.induction = induction
        # Phase 4: generated bellhousing adapters / bent exhaust pipes (shapes.py, pipes.py) instead of Phase 3's
        # stretched unit tube; needs the triangles of the meshes they meet (Meshes.keep)
        self.fit_meshes = fit_meshes
        self.keep_body = False    # keep the body triangles near the engine (preview pictures)
        if tolerance not in TOLERANCE:
            raise ValueError(f'tolerance must be one of {sorted(TOLERANCE)}')
        self.tolerance, self.show_clipping = tolerance, show_clipping
        self.meshes = None
        self.cars = {}            # ns -> CarModel
        self.donors = {}          # did -> DonorModel
        self.pairs = {}           # (ns, did) -> dict(verdict, deg, pos, offset, hits)
        self.copies = {}          # (file, mesh) -> copied mesh name
        self.report = {'cars': {}, 'donors_without_model': {}, 'copied_meshes': 0, 'missing_materials': []}

    # ------------------------------------------------------------ analysis
    def analyze(self, targets, engines):
        if self.meshes is None:            # the v2 build indexes them earlier, for the shared parts
            self.log('Engine models: indexing model files...')
            self.meshes = Meshes(self.vfs, self.log)
        wanted = set()
        for t in targets:
            cm = self.cars[t.ns] = CarModel(t)
            try:
                pc = read_pc(self.vfs, t.pc_path) if t.pc_path else None
                tree = resolve(self.index, t.ns, t.main, Choice(pc))
                node = next(n for n in tree.walk() if n.path == t.slot_path)
                vars_ = tree_vars(tree)
                cm.ev = analyze_engine(self.index, t.ns, node, vars_)
                cm.tree, cm.vars = tree, vars_
                if self.induction:          # Phase 3: the car's gearbox mesh, for the engine-gearbox joint
                    cm.gb_node, cm.gb_rows = self._gearbox_rows(tree)
                    # every exhaust of the car (also on the body), top level only, and its header meshes
                    cm.car_ex_nodes = self._top_exhaust(t.ns, tree)
                    cm.car_hdr_rows = self._header_rows(cm.ev, cm.ev.nested_exhaust)
                    cm.ex_rows = self._exhaust_rows(cm.car_ex_nodes) if self.fit_meshes else []
                    for r in cm.gb_rows + cm.car_hdr_rows + cm.ex_rows:
                        f = self.meshes.visible(r['mesh'], t.ns)
                        if f:
                            cm.files[r['mesh']] = f
                            wanted.add((f, r['mesh']))
                            if self.fit_meshes:
                                self.meshes.keep.add(r['mesh'])     # gearbox: surface hits; exhaust: previews
                # calibration: every engine the game itself offers for this car fits it by definition
                cm.options = []
                for cand in self.index.candidates(t.ns, t.slot):
                    if cand.source.kind == 'vanilla' and is_engine_part(cand) and cand.name != t.base.name:
                        sub = resolve(self.index, t.ns, cand)
                        cm.options.append(analyze_engine(self.index, t.ns, sub, dict(vars_, **tree_vars(sub))))
                for ev in [cm.ev] + cm.options:
                    for r in ev.rows:
                        f = self.meshes.visible(r['mesh'], t.ns)
                        if f:
                            cm.files[r['mesh']] = f
                            wanted.add((f, r['mesh']))
            except Exception as e:      # one car must never stop the build
                cm.reason = f'analysis failed: {e!r}'
        for did, e in engines.items():
            dm = self.donors[did] = DonorModel(did)
            part, ctx = e.get('visual_src', (None, None))
            if part is None:
                dm.reason = 'no source part'
                continue
            try:
                tree = resolve(self.index, ctx, part)
                dm.ev = analyze_engine(self.index, ctx, tree, tree_vars(tree))
                if self.induction:
                    dm.gb_node, dm.gb_rows = self._gearbox_rows(tree)
                    dm.ex_tree_nodes = dm.ev.exhaust_nodes + dm.ev.nested_exhaust
                    dm.ex_hdr_rows = self._header_rows(dm.ev, dm.ev.nested_exhaust)
                    dm.ex_vars = tree_vars(tree)
                    for r in dm.gb_rows + dm.ex_hdr_rows:
                        f = self.meshes.any_file(r['mesh'], ctx)
                        if f:
                            dm.files[r['mesh']] = f
                            wanted.add((f, r['mesh']))
                            if self.fit_meshes and r in dm.ex_hdr_rows:
                                self.meshes.keep.add(r['mesh'])    # preview pictures
                dm.extra_rows = [r for r in dm.ev.body_rows if r['kind'] in ('engine', 'tuning')
                                 and foreign_induction(r['mesh'], r['groups'], dm.ev.side_groups)] if self.induction else []
                for r in dm.ev.rows + dm.extra_rows:
                    f = self.meshes.any_file(r['mesh'], ctx)
                    if f:
                        dm.files[r['mesh']] = f
                        wanted.add((f, r['mesh']))
                        if self.fit_meshes and _is_engine_body(r):
                            self.meshes.keep.add(r['mesh'])
            except Exception as e:
                dm.reason = f'analysis failed: {e!r}'
        self.log(f'Engine models: reading {len(wanted)} meshes...')
        self.meshes.load(wanted)
        self._prepare_cars()
        self._prepare_donors()

    def _prepare_cars(self):
        for ns, cm in self.cars.items():
            if cm.reason:
                continue
            br = block_row(cm.ev, self.meshes, cm.files)
            pts = row_points(br, self.meshes, cm.files.get(br['mesh'])) if br else None
            if not pts:
                cm.reason = 'engine block mesh not found or not readable'
                continue
            cm.ev.block = br
            cm.block = pts
            own = []
            for ev in [cm.ev] + cm.options:
                for r in ev.rows:
                    rp = row_points(r, self.meshes, cm.files.get(r['mesh']))
                    own += rp or []
            c = cm.ev.centre
            cm.away = (0, -1) if c[1] < 0 else (0, 1)          # toward the nearest bumper
            lo, hi = P.box(own)
            cm.bay = tuple(max(hi[i] - lo[i], 1e-3) for i in range(3))
            layered = self._blocked(cm, P.voxels(own))
            cm.blocked_by = {k: {cell: lab for cell, (lab, d) in layered.items() if d > k} for k in range(1, MAX_LAYERS + 1)}
            cm.blocked = cm.blocked_by[1]
            cm.gb_points, cm.joint_ref, cm.joint_note, cm.ex_inlets = None, None, None, {}
            if self.induction:
                self._prepare_joint(cm)
                if self.fit_meshes:
                    self._prepare_fit(cm)
                from . import joints
                nodes, pts = self._exhaust_ends(cm.ev, cm.car_ex_nodes, cm.vars, cm.files, cm.car_hdr_rows)
                cm.ex_inlets = joints.nearest_per_side(nodes, pts) if pts else {}

    def _exhaust_rows(self, ex_nodes):
        """Flexbody rows of the car's exhaust (below its exhaust slots) that are not header meshes."""
        from .visuals import _row
        out = []
        for n in ex_nodes:
            for x in n.walk():
                out += [_row(r, 'exhaust', x.part) for r in table_rows(x.part.data.get('flexbodies'))
                        if isinstance(r.get('mesh'), str)
                        and not (HEADER_NAME.search(r['mesh']) and not BODY_NAME.search(r['mesh']))]
        return out

    def _prepare_fit(self, cm):
        """Phase 4, car side: gearbox triangles + mouth reference (the car's own engine against its gearbox), the
        exhaust and header points the pipes connect to."""
        from . import shapes
        from .visuals import row_geometry
        cm.fit_ref, cm.gb_geo, cm.gb_groups = None, None, []
        if cm.gb_points:
            r = self._largest_row(cm.gb_rows, cm.files)
            g = row_geometry(r, self.meshes, cm.files.get(r['mesh']))
            cm.gb_geo = [g] if g else None
            cm.gb_groups = list(r.get('groups') or [])
            cm.gb_materials = sorted(set(((self.meshes.data.get((cm.files.get(r['mesh']), r['mesh'])) or {})
                                          .get('materials') or {}).values()))
            own = []
            for x in cm.ev.rows:
                if _is_engine_body(x):
                    own += row_points(x, self.meshes, cm.files.get(x['mesh'])) or []
            if own:
                pl = shapes.plan(cm.joint_ref, P.thin(own, 0.02), cm.gb_points)
                cm.fit_ref = pl.get('gap') if pl else None
        ex = []
        for r in cm.ex_rows:
            ex += row_points(r, self.meshes, cm.files.get(r['mesh'])) or []
        hd = []
        for r in cm.car_hdr_rows:
            hd += row_points(r, self.meshes, cm.files.get(r['mesh'])) or []
        cm.ex_pts, cm.hdr_pts = ex, hd
        cm.ex_mats = sorted({v for r in cm.ex_rows for v in ((self.meshes.data.get((cm.files.get(r['mesh']), r['mesh']))
                                                                  or {}).get('materials') or {}).values()})
        cm.obstacles = None

    def _gearbox_rows(self, tree):
        """(TreeNode, flexbody row dicts) of the part holding the car's 'gearbox' device (default config)."""
        from .visuals import _row
        for n in tree.walk():
            if any(r.get('name') == 'gearbox' for r in table_rows(n.part.data.get('powertrain'))):
                return n, [_row(r, 'gearbox', n.part) for r in table_rows(n.part.data.get('flexbodies'))
                           if isinstance(r.get('mesh'), str)]
        return None, []

    def _top_exhaust(self, ns, node):
        out = []
        for c in node.children:
            if self._is_exhaust(ns, c):
                out.append(c)
            else:
                out += self._top_exhaust(ns, c)
        return out

    def _is_exhaust(self, ns, node):
        from .analyze import classify_slot
        return node is not None and node.slot is not None and classify_slot(self.index, ns, node.slot) == 'exhaust'

    def _prepare_joint(self, cm):
        """The car's gearbox mesh points and how its own engine meets them (the 'perfect' reference)."""
        from . import joints
        if cm.gb_node is None or not cm.gb_rows:
            cm.joint_note = 'no gearbox mesh in the default config'
            return
        if self.inherited_ops_at(cm, cm.gb_node.path) != self.inherited_ops_at(cm, cm.t.slot_path):
            cm.joint_note = 'the gearbox has slot options of its own (not measured)'
            return
        pts = self._largest_points(cm.gb_rows, cm.files)
        if not pts:
            cm.joint_note = 'gearbox mesh not readable'
            return
        cm.gb_points = pts
        cm.gb_mesh = self._largest_row(cm.gb_rows, cm.files)['mesh']
        cm.joint_ref = joints.measure(cm.block, pts, cm.ev.dir)

    def _largest_row(self, rows, files):
        best, size = None, -1
        for r in rows:
            d = self.meshes.data.get((files.get(r['mesh']), r['mesh'])) or {}
            if 'box' in d:
                (a, b) = d['box']
                v = (b[0] - a[0]) * (b[1] - a[1]) * (b[2] - a[2])
                if v > size:
                    best, size = r, v
        return best

    def _largest_points(self, rows, files):
        r = self._largest_row(rows, files)
        return row_points(r, self.meshes, files.get(r['mesh'])) if r else None

    def inherited_ops_at(self, cm, path):
        """Slot options (node_ops form) the car's own slots apply at `path`."""
        from .visuals import eval_num
        opts = {}
        for n in cm.tree.walk():
            if n.slot is not None and path.startswith(n.path):
                opts.update({k: v for k, v in (n.slot.get('opts') or {}).items() if k != 'variables'})
        return P.node_ops({k: ({a: (eval_num(x, cm.vars) if isinstance(x, str) else x) for a, x in v.items()}
                               if isinstance(v, dict) else v) for k, v in opts.items()})

    def _exhaust_ends(self, ev, tree_nodes, vars_, files, header_rows):
        """(chain node positions, header mesh points): the exhaust parts below `tree_nodes` (exhaust-class slot
        nodes) without the parts that hold header meshes, and the points of the header meshes."""
        from .visuals import node_positions
        parts = []
        for n in tree_nodes:
            for x in n.walk():
                if not any(isinstance(r.get('mesh'), str) and HEADER_NAME.search(r['mesh']) and not BODY_NAME.search(r['mesh'])
                           for r in table_rows(x.part.data.get('flexbodies'))):
                    parts.append(x.part)
        pos, _g = node_positions(parts, vars_)
        pts = []
        for r in header_rows:
            pts += row_points(r, self.meshes, files.get(r['mesh'])) or []
        return list(pos.values()), pts

    def _header_rows(self, ev, nodes):
        from .visuals import _row
        rows = [r for r in ev.rows if HEADER_NAME.search(r['mesh']) and not BODY_NAME.search(r['mesh'])]
        for n in nodes:
            rows += [_row(r, 'header', n.part) for r in table_rows(n.part.data.get('flexbodies'))
                     if isinstance(r.get('mesh'), str) and HEADER_NAME.search(r['mesh']) and not BODY_NAME.search(r['mesh'])]
        return rows

    def exhaust(self, t, did):
        """(grade, info, filler rows) for the donor's default headers against the car's exhaust (Phase 3 item 2)."""
        from . import joints
        from .vis2 import VIS_GROUP, DISABLE
        cm, dm, p = self.cars[t.ns], self.donors[did], self.pairs[(t.ns, did)]
        tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
        outlets = joints.nearest_per_side(P.transform_points(dm.ex_nodes, p['deg'], tt),
                                          P.transform_points(dm.ex_hdr_points, p['deg'], tt)) \
            if getattr(dm, 'ex_nodes', None) and getattr(dm, 'ex_hdr_points', None) else {}
        layer = cm.blocked_by[1]
        g, pipes, info = joints.pipe_plan(outlets, cm.ex_inlets,
                                          lambda path: P.blocked_count(P.voxels(path), layer, 2))
        rows = [joints.tube_row(a, b, (joints.PIPE_RADIUS, joints.PIPE_RADIUS), [VIS_GROUP], {'disable': DISABLE})
                for a, b in pipes]
        return g, {'distances': info}, rows

    def joint(self, t, did):
        """(grade, measurement, filler row or None) of a placed donor engine against the car's gearbox."""
        from . import joints
        from .vis2 import VIS_GROUP, DISABLE
        cm, dm, p = self.cars[t.ns], self.donors[did], self.pairs[(t.ns, did)]
        if cm.joint_ref is None:
            return 'not measured', {'why': cm.joint_note}, None
        if not dm.block:
            return 'not measured', {'why': 'donor block mesh not readable'}, None
        tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
        # gap: the donor block's gearbox-side face against the car's gearbox (bellhousing) face - what you see;
        # centring: the donor's OWN gearbox in its own car sits on its crank axis, so its engine-side face centre is
        # compared with the car's gearbox face centre (0 for every factory engine). Without a gearbox in the
        # donor's own tree the block face centre is compared with how the car's own engine looks (estimate).
        m = joints.measure(P.transform_points(dm.block, p['deg'], tt), cm.gb_points, cm.ev.dir)
        ref = cm.joint_ref
        m['method'] = 'block face'
        if getattr(dm, 'gb_points', None):
            fl = joints.measure_flange(P.transform_points(dm.gb_points, p['deg'], tt), cm.gb_points, cm.ev.dir)
            m['off'], m['flange_face'], m['method'], ref = fl['off'], fl['engine_face'], 'block gap + gearbox axis', None
        g, d = joints.grade(m, ref, cm.joint_ref['gap'])
        info = {'gap': m['gap'], 'off_error': round(d, 4), 'method': m['method']}
        row = None
        if g == 'filler':
            a, b, half = joints.filler_points(m)
            row = joints.tube_row(a, b, half, [VIS_GROUP], {'disable': DISABLE})
            info['filler'] = {'from': [round(v, 4) for v in a], 'to': [round(v, 4) for v in b],
                              'half': [round(v, 4) for v in half]}
        return g, info, row

    # ------------------------------------------------------------ Phase 4: generated fit meshes
    FIT_MAX_LEN = 0.30          # m: a longer adapter would look like a pipe, not part of the drivetrain
    FIT_MIN_COVER = 0.25        # share of the gearbox mouth that must sit in front of the engine
    fit_shift_max = 0.15        # m: fit-check move (sideways / up) above which no adapter is made (user cutoff, Phase 4)
    FIT_MIN_HITS = 0.5          # share of each end's points that must sit on the part's surface (else no adapter)
    FIT_ROUND = 0.01            # m: dedupe rounding (DECIDE in the Phase 4 prompt: 5 mm made the zip too big)

    def fit_joint(self, t, did):
        """(grade, info, pieces) - Phase 4 bellhousing adapter of a placed donor engine against the car's gearbox.
        pieces: [(kind, shapes.Mesh, groups, material names it should look like)]."""
        from . import shapes, joints
        cm, dm, p = self.cars[t.ns], self.donors[did], self.pairs[(t.ns, did)]
        if cm.joint_ref is None or not getattr(cm, 'gb_geo', None):
            return 'not measured', {'why': cm.joint_note or 'gearbox mesh not readable'}, []
        if not getattr(dm, 'eng_pts', None):
            return 'not measured', {'why': 'donor engine mesh not readable'}, []
        tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
        pts = P.transform_points(dm.eng_pts, p['deg'], tt)
        geo = [(P.transform_points(v, p['deg'], tt), tr) for v, tr in dm.eng_geo]
        m = joints.measure(pts, cm.gb_points, cm.ev.dir)
        lat = 1 - m['ax']
        native = p.get('method') == 'native'
        shift = (0.0, 0.0) if native or p.get('method') != 'gearbox' else (p['offset'][lat], p['offset'][2])
        pl = shapes.plan(m, pts, cm.gb_points, shift, geo, cm.gb_geo)
        info = {k: pl.get(k) for k in ('gap', 'gap_mean', 'overhang', 'coverage', 'hits', 'length', 'why')} if pl else {}
        info['shift'] = round(math.hypot(*shift), 4)
        if pl is None or pl.get('gap') is None:
            return 'not in front', info, []
        ok = 0.015 + max(0.0, cm.fit_ref or 0.0)     # a gap the car's own engine shows too is how it looks
        info['gap_ok'] = round(ok, 4)
        if pl['gap'] <= ok:
            return 'perfect', info, []
        if native:
            return 'factory gap', info, []
        if pl['length'] > self.FIT_MAX_LEN or pl['coverage'] < self.FIT_MIN_COVER or info['shift'] > self.fit_shift_max:
            return 'too far', info, []
        if not pl.get('hits') or min(pl['hits']) < self.FIT_MIN_HITS:
            return 'does not meet', info, []      # an end would hang in the air (a hollow or open back)
        # rounded to FIT_ROUND before building: the same joint in another engine variant gives the same mesh
        # (each end reaches shapes.INTO = 1 cm into its part and wraps the mouth by GB_GROW, so this never opens
        # a seam)
        q = self.FIT_ROUND
        for k in ('a', 'b'):
            pl[k] = [round(v / q) * q for v in pl[k]]
        for k in ('ring_e', 'ring_g'):
            pl[k] = [(round(x / q) * q, round(y / q) * q) for x, y in pl[k]]
        mesh = shapes.bellhousing(pl)
        groups = [VIS_GROUP_] + [g for g in cm.gb_groups if g != VIS_GROUP_]
        return 'adapter', info, [('fit', mesh, groups, getattr(cm, 'gb_materials', []))]

    def _car_obstacles(self, cm):
        """Surface points of the car's meshes around the engine bay (not the engine, not the exhaust) - what a
        pipe must not touch. Read once per car, only near the engine."""
        from . import pipes
        from .visuals import _row, row_pos, row_plain_rot, row_scale
        if cm.obstacles is not None:
            return cm.obstacles
        lo, hi = P.box(cm.block)
        lo = [lo[k] - 0.8 for k in range(3)]
        hi = [hi[k] + 0.8 for k in range(3)]
        ex_paths = [n.path for n in cm.car_ex_nodes]
        raw, pts = {}, []
        cm.body_geo = []
        for n in cm.tree.walk():
            if n.path.startswith(cm.t.slot_path) or any(n.path.startswith(x) for x in ex_paths):
                continue
            for r in table_rows(n.part.data.get('flexbodies')):
                if not isinstance(r.get('mesh'), str):
                    continue
                row = _row(r, 'body', n.part)
                f = self.meshes.visible(row['mesh'], cm.t.ns)
                if not f:
                    continue
                try:
                    if f not in raw:
                        data = self.vfs.read(f)
                        raw[f] = (data, dae.scene_nodes(data))
                    data, nodes = raw[f]
                    node = nodes.get(row['mesh'])
                    if node is None:
                        continue
                    verts, tris = dae.read_mesh(data, node)
                except Exception:
                    continue
                rp, (rr, ok), sc = row_pos(row), row_plain_rot(row), row_scale(row)
                if rp is None or not ok or sc is None:
                    continue
                vs = [(q[0] + rp[0], q[1] + rp[1], q[2] + rp[2])
                      for q in (P.rotz((v[0] * sc[0], v[1] * sc[1], v[2] * sc[2]), rr) for v in verts)]
                keep = [tr for tr in tris if max(tr) < len(vs)
                        and any(all(lo[k] <= vs[i][k] <= hi[k] for k in range(3)) for i in tr)]
                if keep:
                    pts += dae.sample_surface(vs, keep, spacing=0.015)
                    if self.keep_body:
                        cm.body_geo.append((vs, keep))
        cm.obstacles = pipes.Obstacles(P.thin(pts, 0.01))
        return cm.obstacles

    def _inlets(self, cm):
        """{side: (pipes.End, groups)} - where the car's own headers met its exhaust, per side."""
        from . import pipes
        if hasattr(cm, 'inlets'):
            return cm.inlets
        cm.inlets = {}
        for sd in (-1, 0, 1):
            hp = [q for q in cm.hdr_pts if _side(q[0]) == sd]
            ep = [q for q in cm.ex_pts if _side(q[0]) == sd] or cm.ex_pts
            if not hp or not ep:
                continue
            a, _b = min(((x, y) for x in hp[::2] for y in ep[::2]), key=lambda xy: math.dist(*xy))
            end = pipes.pipe_end(ep, a)
            if end is None:
                continue
            groups = []
            for r in cm.ex_rows:
                rp = row_points(r, self.meshes, cm.files.get(r['mesh'])) or []
                if rp and min(math.dist(q, end.pos) for q in rp) < 0.05:
                    groups = list(r.get('groups') or [])
                    break
            cm.inlets[sd] = (end, groups)
        return cm.inlets

    def fit_exhaust(self, t, did):
        """(grade, info, pieces) - Phase 4 bent pipes from the donor's default headers to the car's exhaust."""
        from . import pipes
        cm, dm, p = self.cars[t.ns], self.donors[did], self.pairs[(t.ns, did)]
        if not cm.ex_pts or not cm.hdr_pts:
            return 'no car exhaust', {}, []
        if not getattr(dm, 'ex_hdr_points', None):
            return 'no donor exhaust data', {}, []
        inlets = self._inlets(cm)
        if not inlets:
            return 'no car exhaust', {'why': 'no pipe end found on the car exhaust'}, []
        tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
        hp = P.transform_points(dm.ex_hdr_points, p['deg'], tt)
        info, pieces, grades = {}, [], []
        for sd, (inn, groups) in sorted(inlets.items()):
            side_pts = [q for q in hp if _side(q[0]) == sd] or [q for q in hp if _side(q[0]) == 0] or hp
            out = pipes.pipe_end(side_pts, inn.pos)
            if out is None:
                info[sd] = {'why': 'no header outlet found'}
                grades.append('no donor exhaust data')
                continue
            path, inf = pipes.route(out, inn, self._car_obstacles(cm))
            info[sd] = inf
            if path is None:
                grades.append(inf['why'])
                continue
            # 1 cm into the header and into the car pipe: no seam
            path = [tuple(out.pos[k] - out.dir[k] * 0.01 for k in range(3))] + path[1:-1] + \
                   [tuple(inn.pos[k] - inn.dir[k] * 0.01 for k in range(3))]
            path = [tuple(round(v / 0.002) * 0.002 for v in q) for q in path]
            mesh = pipes.tube(path, round(out.radius, 3), round(inn.radius, 3))
            pieces.append(('pipe', mesh, [VIS_GROUP_] + [g for g in groups if g != VIS_GROUP_], cm.ex_mats))
            grades.append('pipe')
        if pieces:
            g = 'pipes'
        elif 'meets' in grades:
            g = 'meets'
        else:
            g = next((x for x in ('would clip', 'too far', 'bend too tight', 'too long', 'no donor exhaust data')
                      if x in grades), 'no pipe')
        return g, {'sides': info}, pieces

    def _blocked(self, cm, own_cells):
        """Body cells near the engine that a donor must not cross (minus cells the car's own engine uses)."""
        t = cm.t
        lo, hi = P.box(cm.block)
        m = 0.8
        pos, _g = node_positions([n.part for n in cm.tree.walk()], cm.vars)
        # distance (in cells) of every cell near the car's own engines: 0 = inside, 1..MAX_LAYERS around them
        dist = {c: 0 for c in own_cells}
        ring = set(own_cells)
        for layer in range(1, MAX_LAYERS + 1):
            nxt = set()
            for c in ring:
                for i in (-1, 0, 1):
                    for j in (-1, 0, 1):
                        for k in (-1, 0, 1):
                            n = (c[0] + i, c[1] + j, c[2] + k)
                            if n not in dist:
                                dist[n] = layer
                                nxt.add(n)
            ring = nxt
        # direction rule: above the car's engine envelope = hood, ahead of it toward the nearest bumper =
        # grille/front; behind (cabin), outside its sides or below its bottom is not allowed
        if own_cells:
            xs, ys, zs = zip(*own_cells)
            elo, ehi = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
        else:
            elo = tuple(math.floor(v / P.VOXEL) for v in lo)
            ehi = tuple(math.floor(v / P.VOXEL) for v in hi)
        fwd = cm.away[1]

        def free(cell):
            inside_sides = elo[0] - 1 <= cell[0] <= ehi[0] + 1
            ahead = cell[1] < elo[1] if fwd < 0 else cell[1] > ehi[1]
            behind = cell[1] > ehi[1] if fwd < 0 else cell[1] < elo[1]
            if cell[2] > ehi[2] and not behind and inside_sides:
                return True                                  # through the hood / engine lid
            if ahead and inside_sides and cell[2] >= elo[2]:
                return True                                  # through the grille / front
            return False
        out = {}
        for n in cm.tree.walk():
            if n.slot is None and n.part is not t.main:
                continue
            if n.path.startswith(t.slot_path):
                continue                                   # the engine itself
            label = (n.slot['name'] if n.slot else 'body') + ' ' + n.part.name
            if ALLOWED_PART.search(label):
                continue
            for r in table_rows(n.part.data.get('triangles')):
                ids = (r.get('id1:'), r.get('id2:'), r.get('id3:'))
                if not all(i in pos for i in ids):
                    continue
                a, b, c = (pos[i] for i in ids)
                cen = [(a[k] + b[k] + c[k]) / 3 for k in range(3)]
                if not all(lo[k] - m <= cen[k] <= hi[k] + m for k in range(3)):
                    continue
                for cell in P.tri_voxels(a, b, c):
                    d = dist.get(cell, MAX_LAYERS + 1)
                    if d > 1 and not free(cell):
                        out.setdefault(cell, (n.slot['name'] if n.slot else 'body', d))
        return out

    def _prepare_donors(self):
        sigs = {}
        for did, dm in self.donors.items():
            if dm.reason:
                self.report['donors_without_model'][did] = dm.reason
                continue
            br = block_row(dm.ev, self.meshes, dm.files, allow_tuning=self.block_from_tuning)
            if br is None:
                dm.reason = 'engine block mesh not found or not readable'
                self.report['donors_without_model'][did] = dm.reason
                continue
            dm.ev.block = br
            rows, pts, bad = [], [], []
            for r in dm.ev.rows:
                rp = row_points(r, self.meshes, dm.files.get(r['mesh']))
                if rp is None:
                    bad.append(r['mesh'])
                    continue
                rows.append(r)
                pts += rp
            dm.ev.rows = rows
            if bad:
                dm.ev.problems.append(f'meshes left out (missing, unreadable or rotated off-axis): {sorted(set(bad))[:5]}')
            dm.block = row_points(br, self.meshes, dm.files.get(br['mesh']))
            dm.gb_points = self._largest_points(getattr(dm, 'gb_rows', []), dm.files)
            if self.fit_meshes:
                from .visuals import row_geometry
                body = [r for r in rows if _is_engine_body(r)] or [br]
                ep = []
                for r in body:
                    ep += row_points(r, self.meshes, dm.files.get(r['mesh'])) or []
                dm.eng_pts = P.thin(ep, 0.02)
                dm.eng_geo = [g for g in (row_geometry(r, self.meshes, dm.files.get(r['mesh'])) for r in body) if g]
            if self.induction:
                dm.ex_nodes, dm.ex_hdr_points = self._exhaust_ends(dm.ev, dm.ex_tree_nodes, dm.ex_vars, dm.files,
                                                                   dm.ex_hdr_rows)
            ext = []
            for r in getattr(dm, 'extra_rows', []):
                ext += row_points(r, self.meshes, dm.files.get(r['mesh'])) or []
            dm.extra_points = P.thin(ext, FIT_THIN) if ext else []
            dm.sig = json.dumps([[dm.files.get(r['mesh']), r['mesh'], r['pos'], r['rot'], r['scale']] for r in rows]
                                + [dm.ev.dir], sort_keys=True, default=str)
            if dm.sig in sigs:                             # same visuals as another engine: reuse its cells
                dm.cells, dm.dims, dm.points = sigs[dm.sig].cells, sigs[dm.sig].dims, sigs[dm.sig].points
                continue
            dm.points = P.thin(pts, FIT_THIN)
            dm.cells = {}
            sigs[dm.sig] = dm

    # ------------------------------------------------------------ fit check
    def fit_all(self, own_of=None, natives=None):
        """own_of: {car ns: donor id of the car's own engine} - always fits its own car.
        natives: {car ns: {donor ids made for this car's engine slot}} - fit by definition, factory position."""
        own_of, natives = own_of or {}, natives or {}
        self.log('Engine models: fit check...')
        for ns, cm in self.cars.items():
            rep = self.report['cars'][ns] = {'vehicle': cm.t.display, 'reason': cm.reason, 'counts': {}}
            if cm.reason:
                continue
            for did, dm in self.donors.items():
                if dm.reason:
                    continue
                # anchor on engine physics nodes; mesh blocks differ (oil pan in or out of the block mesh)
                if did in natives.get(ns, ()):
                    deg, t, method, note = 0, (0.0, 0.0, 0.0), 'native', ''
                elif self.align == 'gearbox':
                    deg, t, method, note = P.place_gearbox(dm.ev.node_points or dm.block, dm.ev.dir, dm.ev.trans_centre,
                                                           cm.ev.node_points or cm.block, cm.ev.dir, cm.ev.trans_centre)
                else:
                    deg, t = P.place(dm.ev.node_points or dm.block, dm.ev.dir, cm.ev.node_points or cm.block, cm.ev.dir)
                    method, note = 'block', ''
                if deg not in dm.cells:
                    pts = P.transform_points(dm.points, deg, (0, 0, 0))
                    dm.cells[deg] = P.voxels(pts)
                    lo, hi = P.box(pts)
                    dm.dims[deg] = tuple(hi[i] - lo[i] for i in range(3))
                if own_of.get(ns) == did or method == 'native':
                    p = {'verdict': 'fits', 'offset': (0.0, 0.0, 0.0), 'blocked_hits': {}, 'modes': {}}
                else:
                    p = self._judge(cm, P.shifted(dm.cells[deg], t), dm.dims[deg])
                p.update({'deg': deg, 'pos': t, 'confidence': 'mesh', 'method': method, 'method_note': note})
                if self.induction and method != 'native' and getattr(dm, 'extra_points', None):
                    tt = tuple(t[k] + p['offset'][k] for k in range(3))
                    tol = TOLERANCE[self.tolerance]
                    layers = min(tol['layers'] + (1 if self.show_clipping else 0), MAX_LAYERS)
                    cells = P.voxels(P.transform_points(dm.extra_points, deg, tt))
                    p['intake_clips'] = P.blocked_count(cells, cm.blocked_by[layers], P.MIN_HITS) >= P.MIN_HITS
                self.pairs[(ns, did)] = p
                rep['counts'][p['verdict']] = rep['counts'].get(p['verdict'], 0) + 1
                for mode, v in p['modes'].items():
                    mc = rep.setdefault('offered_by_mode', {}).setdefault(mode, 0)
                    rep['offered_by_mode'][mode] = mc + (v in OFFERED)

    def size_ratios(self, cm, dims):
        w, l, h = dims[0] / cm.bay[0], dims[1] / cm.bay[1], dims[2] / cm.bay[2]
        return {'width': w, 'length': l, 'height': h, 'volume': w * l * h}

    def _judge(self, cm, cells, dims):
        """Verdict under the chosen tolerance; also what Strict/Normal/Loose (+clips) would say, for the report."""
        def run(layers, up, away):
            return P.fit_cells(cells, cm.blocked_by[min(layers, MAX_LAYERS)], cm.away, up_steps=up, away_steps=away)
        strict = TOLERANCE['strict']
        verdict, off, first = run(strict['layers'], strict['up'], strict['away'])
        ratios = self.size_ratios(cm, dims)
        too_big = [k for k, v in ratios.items() if v > SIZE_LIMIT[k]]
        modes = {}
        for name, tol in TOLERANCE.items():
            for clips in (False, True):
                key = name + ('+clips' if clips else '')
                if verdict in ('fits', 'fits_after_move'):
                    modes[key] = verdict
                elif too_big:
                    modes[key] = 'too_big'
                else:
                    v = 'does_not_fit'
                    if name != 'strict' and run(tol['layers'], tol['up'], tol['away'])[0] != 'does_not_fit':
                        v = 'fits_with_tolerance'
                    elif clips and run(tol['layers'] + 1, tol['up'] + 1, tol['away'] + 1)[0] != 'does_not_fit':
                        v = 'clips'
                    modes[key] = v
        key = self.tolerance + ('+clips' if self.show_clipping else '')
        final = modes[key]
        if final in ('fits_with_tolerance', 'clips'):
            tol = TOLERANCE[self.tolerance]
            extra = 1 if final == 'clips' else 0
            _v, off, _f = run(tol['layers'] + extra, tol['up'] + extra, tol['away'] + extra)
        return {'verdict': final, 'offset': off, 'blocked_hits': first, 'modes': modes,
                'size': {k: round(v, 2) for k, v in ratios.items()}, 'too_big': too_big}

    def denied(self, ns):
        """Donor ids that do not fit this car (only when hide_unfit)."""
        if not self.hide_unfit:
            return set()
        return {did for (n, did), p in self.pairs.items() if n == ns and p['verdict'] not in OFFERED}

    # ------------------------------------------------------------ parts
    def own_meshes(self, ns):
        cm = self.cars.get(ns)
        if not cm or cm.reason:
            return set()
        return {r['mesh'] for r in cm.ev.rows}

    def _copied_name(self, f, mesh):
        key = (f, mesh)
        if key not in self.copies:
            self.copies[key] = f'esc_{short(f + "|" + mesh, 8)}_{mesh}'[:80]
        return self.copies[key]

    def car_parts(self, ns, donor_ids, own_did=None):
        """Parts for one vanilla car: esc_m_own, esc_m_<did>, header copies. Returns ({name: part}, header map).
        header map: slot name -> (new slot type, {original part: copy name})."""
        cm = self.cars[ns]
        parts = {}
        groups = cm.ev.block['groups']
        own_rows = [list(FLEX_HEADER)] + [flex_row(r['mesh'], r['groups'], self._raw_opts(r)) for r in cm.ev.rows]
        parts['esc_m_own'] = {'information': {'name': "Car's own engine model", 'authors': 'engine_swap_catalog', 'value': 0},
                              'slotType': MODEL_SLOT_OWN, 'flexbodies': own_rows}
        for did in donor_ids:
            p = self.pairs.get((ns, did))
            dm = self.donors.get(did)
            if p is None or dm is None or dm.reason or p['verdict'] not in OFFERED:
                if not self.hide_unfit or p is None:
                    parts[f'esc_m_{did}'] = self._proxy(did, 'no model available' if p is None else "doesn't fit")
                continue
            # a mesh this car cannot load is copied only from vanilla files (NOTES 9); mod engine-pack
            # models must already be loadable by the car, otherwise the car keeps its own model
            blocked = [r['mesh'] for r in dm.ev.rows if self.meshes.visible(r['mesh'], ns) != dm.files.get(r['mesh'])
                       and self.vfs.source_of(dm.files.get(r['mesh'])).kind != 'vanilla']
            if blocked:
                self.report['cars'].setdefault(ns, {}).setdefault('model_not_loadable', {})[did] = blocked[:3]
                parts[f'esc_m_{did}'] = self._proxy(did, 'model files not loadable by this car')
                continue
            rows = [list(FLEX_HEADER)]
            for r in dm.ev.rows:
                f = dm.files.get(r['mesh'])
                mesh = r['mesh'] if self.meshes.visible(r['mesh'], ns) == f else self._copied_name(f, r['mesh'])
                rr, _ok = row_plain_rot(r)
                rp = row_pos(r)
                total, pos = P.compose(p['deg'], p['pos'], rp, rr)
                pos = tuple(pos[k] + p['offset'][k] for k in range(3))
                opts = {k: v for k, v in self._raw_opts(r).items()
                        if k not in DEFORM_OPTS and k not in ('pos', 'rot', 'scale')}   # replaced below
                opts['pos'] = {'x': round(pos[0], 5), 'y': round(pos[1], 5), 'z': round(pos[2], 5)}
                ang = ((total + 180) % 360) - 180
                if abs(ang) > 1e-9:
                    opts['rot'] = {'x': 0, 'y': 0, 'z': P.rot_param_for(ang)}
                sc = row_scale(r)
                if sc and sc != (1.0, 1.0, 1.0):
                    opts['scale'] = {'x': sc[0], 'y': sc[1], 'z': sc[2]}
                rows.append(flex_row(mesh, groups, opts))
            label = LABELS.get(p['verdict'], p['verdict'])
            parts[f'esc_m_{did}'] = {'information': {'name': f'Donor engine model ({label})', 'authors': 'engine_swap_catalog', 'value': 0},
                                     'slotType': f'esc_model_{did}', 'flexbodies': rows}
        hp, hmap = self.header_parts(ns)
        parts.update(hp)
        return parts, hmap

    def header_parts(self, ns, switch=False):
        """Copies of the car's header parts without the header meshes (those come with the engine model).
        switch (engine visuals v2): the header meshes stay, shown only while $esc_own is 1 ("Car's own engine
        model", or an engine without a model)."""
        cm = self.cars[ns]
        parts, hmap = {}, {}
        for slot_name, cands in cm.ev.header_parts.items():
            slot = next((s for n in cm.tree.walk() if n.path == cm.t.slot_path
                         for s in n.part.slots if s['name'] == slot_name), None)
            if slot is None:
                continue
            stype = f'esc_hxs_{short(ns + slot_name)}'
            names = {}
            for c in self.index.candidates(ns, slot):
                cp = copy.deepcopy(c.data)
                tbl = cp.get('flexbodies')
                if isinstance(tbl, list) and tbl:
                    if switch:
                        from . import vis2
                        from .build import var_table, var_decls
                        cp['flexbodies'] = [tbl[0]] + [vis2.switched(row, vis2.DISABLE_OWN)
                                                       if _is_header_row(row) else row for row in tbl[1:]]
                        vis2.add_decls(cp, [vis2.OWN_DECL], var_table, var_decls)
                    else:
                        cp['flexbodies'] = [tbl[0]] + [row for row in tbl[1:] if not _is_header_row(row)]
                cp['slotType'] = stype
                info = dict(cp.get('information') or {})
                cp['information'] = info
                nm = f'esc_hx_{short(ns + c.name)}'
                parts[nm] = cp
                names[c.name] = nm
            # new slot name: an old config's header choice must not land in this slot (it would not fit and
            # BeamNG would log "wrong slot type"); the copy of the default header is used instead
            hmap[slot_name] = (stype, names, f'esc_{slot_name}')
        return parts, hmap

    def own_rows(self, ns):
        cm = self.cars[ns]
        return [list(FLEX_HEADER)] + [flex_row(r['mesh'], r['groups'], self._raw_opts(r)) for r in cm.ev.rows]

    def v2_car_rows(self, ns):
        """The car's own engine visuals for engine visuals v2, as flexbody table rows (no header row):
        own_rows: engine side (without the header meshes, those stay in the exhaust parts / header copies);
        s_rows: body-side meshes of the car's engine and its default tuning parts (hoses, airbox, intercooler...);
        s_keep: the part of s_rows a foreign engine keeps (everything except the induction, INTAKE_NAME)."""
        cm = self.cars[ns]
        own = [r for r in cm.ev.rows if r['kind'] != 'header']
        body = [r for r in cm.ev.body_rows if r['kind'] in ('engine', 'tuning')]
        keep = [r for r in body if not INTAKE_NAME.search(r['mesh'])]

        def mk(r):
            return flex_row(r['mesh'], r['groups'], self._raw_opts(r))
        # header meshes of an exhaust that hangs below the car's default intake (flattened into the catalog part):
        # the car's own look, hidden with a foreign engine (that brings the donor's default headers)
        from .visuals import _row
        nested = [_row(r, 'header', n.part) for n in cm.ev.nested_exhaust
                  for r in table_rows(n.part.data.get('flexbodies'))
                  if isinstance(r.get('mesh'), str) and HEADER_NAME.search(r['mesh']) and not BODY_NAME.search(r['mesh'])]
        return {'own_rows': [mk(r) for r in own], 's_rows': [mk(r) for r in body], 's_keep': [mk(r) for r in keep],
                'nested_hdr': [mk(r) for r in nested], 'nested_hdr_meshes': sorted({r['mesh'] for r in nested}),
                'own_meshes': sorted({r['mesh'] for r in own}),
                's_intake': sorted({r['mesh'] for r in body} - {r['mesh'] for r in keep}),
                's_keep_meshes': sorted({r['mesh'] for r in keep})}

    def v2_placement(self, t, did, sliders=True):
        """Slot options for the catalog engine slot of a foreign engine (placement.placement_options).
        Returns (options or None, report info)."""
        p, dm = self.pairs[(t.ns, did)], self.donors[did]
        tt = tuple(p['pos'][k] + p['offset'][k] for k in range(3))
        opts, err, info = P.placement_options(self.inherited_ops(t), p['deg'], tt, dm.ev.centre, sliders)
        info.update({'native': False, 'deg': p['deg'], 't': [round(v, 6) for v in tt], 'method': p.get('method'),
                     'method_note': p.get('method_note'), 'solve_error': err if math.isfinite(err) else None})
        if opts is None:
            info['error'] = info.pop('why', None) or f'placement not expressible with slot options (error {err:.3g} m)'
        return opts, info

    def inherited_ops(self, t):
        """Slot options (nodeRotate/Offset/Move) the car's own slots apply at its engine slot (node_ops form)."""
        from .visuals import eval_num
        cm = self.cars[t.ns]
        opts = {}
        for n in cm.tree.walk():
            if n.slot is not None and t.slot_path.startswith(n.path):
                opts.update({k: v for k, v in (n.slot.get('opts') or {}).items() if k != 'variables'})
        res = {}
        for k, v in opts.items():
            if isinstance(v, dict):
                res[k] = {a: (eval_num(x, cm.vars) if isinstance(x, str) else x) for a, x in v.items()}
            else:
                res[k] = v
        return P.node_ops(res)

    def _proxy(self, did, why):
        return {'information': {'name': f"Car's own engine model ({why})", 'authors': 'engine_swap_catalog', 'value': 0},
                'slotType': f'esc_model_{did}',
                'slots2': [['name', 'allowTypes', 'denyTypes', 'default', 'description'],
                           ['esc_own_model', [MODEL_SLOT_OWN], [], 'esc_m_own', "Car's own engine model", {'coreSlot': True}]]}

    def _raw_opts(self, r):
        out = {}
        for k in ('pos', 'rot', 'scale'):
            if r.get(k) is not None:
                out[k] = r[k]
        out.update(r.get('opts') or {})
        return out

    # ------------------------------------------------------------ copied meshes
    def copied_files(self):
        """{zip path: bytes} for copied meshes (.dae per source file) and their materials."""
        out, by_file, mats_out = {}, {}, {}
        index = self.meshes.materials_index() if self.copies else {}
        for (f, mesh), new in sorted(self.copies.items()):
            by_file.setdefault(f, []).append((mesh, new))
        for f, items in by_file.items():
            fp = f'esc{short(f, 8)}_'.encode()
            meshes = []
            for mesh, new in items:
                d = self.meshes.data.get((f, mesh)) or {}
                if 'geometry_raw' not in d or d['geometry_raw'] is None:
                    continue
                node = d['node']
                gp = fp + short(mesh, 6).encode() + b'_'
                geom = dae.rename_ids(d['geometry_raw'], gp)
                mat_raw, eff_raw, binds = {}, {}, []
                for sym, mid in node['materials']:
                    orig = d['materials'].get(mid, mid.replace('-material', ''))
                    # materials without any materials.json entry keep their name (same result as in vanilla)
                    newname = f'esc_{short(f, 6)}_{orig}' if orig in index else orig
                    nid = f'{newname}-material'
                    binds.append((sym, nid))
                    mat_raw[nid] = (f'<material id="{nid}" name="{newname}"><instance_effect url="#{nid}-fx"/></material>').encode()
                    eff_raw[nid] = (f'<effect id="{nid}-fx"><profile_COMMON><technique sid="common"><lambert/>'
                                    f'</technique></profile_COMMON></effect>').encode()
                    if newname not in mats_out:
                        src = index.get(orig)
                        if src is None:
                            self.report['missing_materials'].append(f'{orig} (mesh {mesh} from {f}): no materials.json '
                                                                    'entry anywhere, original name kept')
                        else:
                            m = copy.deepcopy(src[0])
                            m['name'] = newname
                            m['mapTo'] = newname
                            mats_out[newname] = m
                meshes.append({'name': new, 'geometry_raw': geom,
                               'geometry_id': (gp.decode() + node['geometry']), 'matrix': node['matrix'],
                               'materials': binds, 'material_raw': mat_raw, 'effect_raw': eff_raw, 'image_raw': {}})
            if meshes:
                out[f'{MODEL_DIR}esc_{short(f, 10)}.dae'] = dae.write_dae(meshes)
        if mats_out:
            out[f'{MODEL_DIR}esc_meshes.materials.json'] = jbeam.dumps(mats_out).encode('utf-8')
        self.report['copied_meshes'] = len(self.copies)
        return out
