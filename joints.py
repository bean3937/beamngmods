"""Phase 3: how a placed donor engine meets the car's gearbox and exhaust, measured from the meshes / nodes, graded
against the car's own engine (vanilla = perfect), and generated filler pieces (one shared unit tube mesh, placed per
car x engine through the flexbody row's pos / rot / scale - meshs.lua setInitialTransformation(pos, rot, scale)).

Nothing here moves an engine: positions stay exactly as the fit check placed them."""
import math

from . import placement as P

FACE_DEPTH = 0.04          # m: points this close to the extreme form a face (mesh sample spacing is 3 cm)
FILLER_MESH = 'esc_filler_tube'
FILLER_MAT = 'esc_filler_metal'
# a plain dark metal, written like vanilla's texture-free PBR materials (e.g. bastion_gauges_break, version 1.5)
FILLER_MATERIAL = {FILLER_MAT: {'name': FILLER_MAT, 'mapTo': FILLER_MAT, 'class': 'Material', 'version': 1.5,
                                'Stages': [{'baseColorFactor': [0.06, 0.06, 0.065, 1], 'metallicFactor': 0.9,
                                            'roughnessFactor': 0.45}, {}, {}, {}],
                                'dynamicCubemap': True, 'translucentBlendOp': 'None'}}


class Face:
    def __init__(self, extreme, centre, half):
        self.extreme, self.centre, self.half = extreme, centre, half     # centre / half: (lateral, z)


def face(points, ax, sign, depth=FACE_DEPTH):
    """The face of a point cloud at its extreme along axis `ax` in direction `sign`."""
    ext = max(p[ax] * sign for p in points) * sign
    lat = 1 - ax
    sel = [p for p in points if abs(p[ax] - ext) <= depth]
    ls, zs = [p[lat] for p in sel], [p[2] for p in sel]
    centre = ((min(ls) + max(ls)) / 2, (min(zs) + max(zs)) / 2)
    return Face(ext, centre, ((max(ls) - min(ls)) / 2, (max(zs) - min(zs)) / 2))


def measure_flange(home_gearbox_pts, gearbox_pts, direction):
    """Joint measured where it matters: the donor's bellhousing flange = the engine-side face of the donor's OWN
    gearbox in its own car (placed with the engine), against the car's gearbox face. Both gearboxes sit on the
    crank axis of their engine, so gap 0 / offset 0 is how it looks in vanilla."""
    ax = 0 if direction[0] else 1
    sign = direction[ax]
    e = face(home_gearbox_pts, ax, -sign)
    g = face(gearbox_pts, ax, -sign)
    return {'gap': round(sign * (g.extreme - e.extreme), 4),
            'off': (round(g.centre[0] - e.centre[0], 4), round(g.centre[1] - e.centre[1], 4)),
            'engine_face': e, 'gearbox_face': g, 'ax': ax, 'sign': sign, 'method': 'flange'}


def measure(engine_pts, gearbox_pts, direction):
    """Joint between an engine (its gearbox-side face) and a gearbox mesh (its engine-side face).
    direction: snapped engine -> gearbox axis. gap > 0 = open gap (m), < 0 = overlap (the gearbox's bellhousing
    reaches over the engine); off = gearbox face centre - engine face centre (lateral, vertical)."""
    ax = 0 if direction[0] else 1
    sign = direction[ax]
    e = face(engine_pts, ax, sign)
    g = face(gearbox_pts, ax, -sign)
    return {'gap': round(sign * (g.extreme - e.extreme), 4),
            'off': (round(g.centre[0] - e.centre[0], 4), round(g.centre[1] - e.centre[1], 4)),
            'engine_face': e, 'gearbox_face': g, 'ax': ax, 'sign': sign}


# grading thresholds (m) - see NOTES.md section 12 for the vanilla reference measurements they come from
GAP_OK = 0.015             # touching: up to 1.5 cm open
OFF_OK = 0.03              # centred like the car's own engine within 3 cm
FILL_MAX_GAP = 0.30        # longer than this: an adapter would look like a pipe, not part of the drivetrain
FILL_MAX_OFF = 0.20        # the adapter may be skewed this much (it then looks like a slanted adapter plate)


def grade(m, ref=None, ref_gap=0.0):
    """'perfect' | 'filler' (open gap) | 'adapter' (touching but off the gearbox axis: a short adapter plate) |
    'too far' (+ the offset error). ref: the car's own engine measured the same way (block-face centring only; the
    gearbox-axis centring's reference is 0 by construction)."""
    r = ref['off'] if ref else (0.0, 0.0)
    d = math.hypot(m['off'][0] - r[0], m['off'][1] - r[1])
    ok = GAP_OK + max(0.0, ref_gap or 0.0)     # a gap the car's own engine shows too is how that gearbox looks
    m['gap_ok'] = round(ok, 4)
    if m['gap'] <= ok:
        if d <= OFF_OK:
            return 'perfect', d
        return ('adapter' if d <= FILL_MAX_OFF else 'too far'), d
    if m['gap'] <= FILL_MAX_GAP and d <= FILL_MAX_OFF:
        return 'filler', d
    return 'too far', d


def filler_points(m):
    """End points (car coordinates) of the adapter: from the engine face to the gearbox face, 1 cm into each, and
    its half sizes (lateral, vertical) = the larger of the two faces, so it covers both."""
    ax, sign = m['ax'], m['sign']
    lat = 1 - ax
    e, g = m['engine_face'], m['gearbox_face']

    def point(axial, centre):
        p = [0.0, 0.0, 0.0]
        p[ax], p[lat], p[2] = axial, centre[0], centre[1]
        return tuple(p)
    f = m.get('flange_face') or e                 # the donor's crank axis (its own gearbox face), if known
    if m['gap'] <= m.get('gap_ok', GAP_OK):     # adapter plate over a touching but off-axis joint
        # a 4 cm plate at the gearbox's face (3 cm in front of it, 1 cm into it), from the donor's axis to the
        # gearbox's: an overlap behind it stays hidden inside the bellhousing as in vanilla
        a = point(g.extreme - sign * 0.03, f.centre)
        b = point(g.extreme + sign * 0.01, g.centre)
        return a, b, (max(f.half[0], g.half[0]), max(f.half[1], g.half[1]))
    a = point(e.extreme - sign * 0.01, f.centre)
    b = point(g.extreme + sign * 0.01, g.centre)
    half = (max(f.half[0], g.half[0]), max(f.half[1], g.half[1]))
    return a, b, half


# ---------------------------------------------------------------- headers -> exhaust pipe
PIPE_RADIUS = 0.025
PIPE_MEETS = 0.03          # m: outlet and inlet this close already meet
PIPE_MAX = 0.35            # longer than this a straight pipe would look made up


def nearest_per_side(nodes, targets):
    """{side: node position} - for each side (-1 left, +1 right, 0 centre within 10 cm) the node nearest to the
    target points (header meshes) on that side."""
    out = {}
    for side in (-1, 0, 1):
        tg = [p for p in targets if _side(p[0]) == side]
        nd = [p for p in nodes if _side(p[0]) == side]
        if tg and nd:
            out[side] = min(nd, key=lambda n: min(math.dist(n, q) for q in tg[::max(1, len(tg) // 60)]))
    return out


def _side(x):
    return 0 if abs(x) < 0.1 else (1 if x > 0 else -1)


def pipe_plan(outlets, inlets, blocked):
    """Pipes from donor header outlets to the car's exhaust inlets, side by side. blocked: callable(points) ->
    number of body cells the path crosses. Returns (grade, [(a, b)], info)."""
    if not inlets:
        return 'no car exhaust', [], {}
    if not outlets:
        return 'no donor exhaust data', [], {}
    pairs, info = [], {}
    for side, b in inlets.items():
        a = outlets.get(side) or (outlets.get(0) if side else None)
        if a is None:
            info[side] = 'no outlet on this side'
            continue
        d = math.dist(a, b)
        info[side] = round(d, 3)
        if d <= PIPE_MEETS:
            continue
        if d > PIPE_MAX:
            return 'too far', [], info
        n = max(2, int(d / 0.02))
        path = [tuple(a[i] + (b[i] - a[i]) * k / n for i in range(3)) for k in range(n + 1)]
        if blocked(path) >= 2:
            return 'pipe would cross the body', [], info
        pairs.append((a, b))
    return ('filler' if pairs else 'meets'), pairs, info


# ---------------------------------------------------------------- the unit tube and its rows
TUBE_SEGMENTS = 20


def tube_geometry(gid):
    """Collada <geometry> of an open unit tube: radius 1 about the +Y axis from y = 0 to y = 1, double sided, with
    normals and UVs (cm units like BeamNG exports; the node matrix scales to metres)."""
    pos, nrm, uv, idx = [], [], [], []
    n = TUBE_SEGMENTS
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        c, s = math.cos(a), math.sin(a)
        for y in (0.0, 1.0):
            pos += [c * 100, y * 100, s * 100]
            nrm += [c, 0.0, s]
            uv += [i / n, y]
    # outside faces, then the same faces reversed with inverted normals (seen from inside)
    base = len(pos) // 3
    pos += pos[:]
    nrm += [-v for v in nrm]
    uv += uv[:]
    for i in range(n):
        a0, a1, b0, b1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        idx += [a0, b0, b1, a0, b1, a1]
        idx += [base + a0, base + b1, base + b0, base + a0, base + a1, base + b1]

    def arr(name, vals, stride, params):
        p = ''.join(f'<param name="{x}" type="float"/>' for x in params)
        return (f'<source id="{gid}-{name}"><float_array id="{gid}-{name}-array" count="{len(vals)}">'
                f'{" ".join(f"{v:.5g}" for v in vals)}</float_array><technique_common><accessor '
                f'source="#{gid}-{name}-array" count="{len(vals) // stride}" stride="{stride}">{p}</accessor>'
                f'</technique_common></source>')
    tri = ' '.join(f'{i} {i} {i}' for i in idx)
    return (f'<geometry id="{gid}" name="{gid}"><mesh>' + arr('positions', pos, 3, 'XYZ')
            + arr('normals', nrm, 3, 'XYZ') + arr('map', uv, 2, 'ST')
            + f'<vertices id="{gid}-vertices"><input semantic="POSITION" source="#{gid}-positions"/></vertices>'
            f'<triangles material="{FILLER_MAT}-material" count="{len(idx) // 3}">'
            f'<input semantic="VERTEX" source="#{gid}-vertices" offset="0"/>'
            f'<input semantic="NORMAL" source="#{gid}-normals" offset="1"/>'
            f'<input semantic="TEXCOORD" source="#{gid}-map" offset="2" set="0"/><p>{tri}</p></triangles>'
            f'</mesh></geometry>').encode()


def filler_dae():
    from . import dae
    gid = FILLER_MESH + '-mesh'
    return dae.write_dae([{
        'name': FILLER_MESH, 'geometry_raw': tube_geometry(gid), 'geometry_id': gid,
        'matrix': [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 1]],
        'materials': [(f'{FILLER_MAT}-material', f'{FILLER_MAT}-material')],
        'material_raw': {FILLER_MAT: f'<material id="{FILLER_MAT}-material" name="{FILLER_MAT}">'
                                     f'<instance_effect url="#{FILLER_MAT}-fx"/></material>'.encode()},
        'effect_raw': {FILLER_MAT: f'<effect id="{FILLER_MAT}-fx"><profile_COMMON><technique sid="common"><lambert/>'
                                   f'</technique></profile_COMMON></effect>'.encode()},
        'image_raw': {}}])


def _rot_for(d):
    """Flexbody rot (degrees, the game's convention) that turns +Y onto unit vector d - solved on the Lua port
    (placement.flexbody_apply), not assumed."""
    def err(rx, rz):
        return math.dist(P.flexbody_apply((0.0, 1.0, 0.0), rot=(rx, 0.0, rz)), d)
    # coarse grid, then shrink the step around the best (the map is smooth; no Euler order is assumed)
    best = min(((err(rx, rz), rx, rz) for rx in range(-90, 91, 10) for rz in range(-180, 180, 10)))
    step = 5.0
    while step > 1e-7:
        e, rx, rz = best
        cand = [(err(rx + i * step, rz + j * step), rx + i * step, rz + j * step)
                for i in (-1, 0, 1) for j in (-1, 0, 1)]
        nb = min(cand)
        if nb[0] < best[0]:
            best = nb
        else:
            step /= 2
    best = (best[0], (best[1], 0.0, best[2]))
    if best[0] > 1e-6:
        raise ValueError(f'no flexbody rotation found for direction {d}')
    return best[1]


def tube_row(a, b, half, groups, opts=None):
    """Flexbody row for the unit tube from point a to point b with half sizes (sideways, up) - the tube's local X
    and Z. Returns [mesh, groups, [], options]."""
    v = tuple(b[i] - a[i] for i in range(3))
    length = math.sqrt(sum(x * x for x in v))
    if length < 1e-4:
        raise ValueError('zero-length filler')
    d = tuple(x / length for x in v)
    rot = _rot_for(d)
    o = dict(opts or {})
    o.update({'pos': {k: round(a[i], 5) for i, k in enumerate('xyz')},
              'rot': {k: round(rot[i], 5) for i, k in enumerate('xyz')},
              'scale': {'x': round(half[0], 5), 'y': round(length, 5), 'z': round(half[1], 5)}})
    return [FILLER_MESH, list(groups), [], o]


def tube_end_points(row):
    """Where the game puts the tube's two end centres for a row (check of tube_row)."""
    o = row[3]
    pos = tuple(o['pos'][k] for k in 'xyz')
    rot = tuple(o['rot'][k] for k in 'xyz')
    sc = tuple(o['scale'][k] for k in 'xyz')
    return P.flexbody_apply((0, 0, 0), pos, rot, sc), P.flexbody_apply((0, 1, 0), pos, rot, sc)
