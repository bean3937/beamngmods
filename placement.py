"""Placement maths and the voxel fit check for donor engine models.

Flexbody transform (see NOTES.md section 9): vertex v -> R(rot)·v + pos, where
R(rot) = setRotate(setQuatFromEuler(-rx, -ry, -rz)) - ported 1:1 from lua/common/jbeam/utils.lua and
lua/common/mathlib.lua so no sign convention is guessed. Only rotations about Z are generated.
"""
import math


# ---------------------------------------------------------------- 1:1 ports of the game's Lua
def quat_from_euler(x, y, z):
    """jbeam/utils.lua setQuatFromEuler (radians, ZXY intrinsic) -> (qx, qy, qz, qw)."""
    x, y, z = x * 0.5, y * 0.5, z * 0.5
    sx, cx, sy, cy, sz, cz = math.sin(x), math.cos(x), math.sin(y), math.cos(y), math.sin(z), math.cos(z)
    return (cz * sx * cy - sz * cx * sy, cz * cx * sy + sz * sx * cy, sz * cx * cy + cz * sx * sy,
            cz * cx * cy - sz * sx * sy)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def set_rotate(q, v):
    """mathlib.lua LuaVec3:setRotate."""
    qv = q[:3]
    t = tuple(2 * c for c in _cross(qv, v))
    c2 = _cross(qv, t)
    return (v[0] - t[0] * q[3] + c2[0], v[1] - t[1] * q[3] + c2[1], v[2] - t[2] * q[3] + c2[2])


def flexbody_apply(v, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    """Where the game puts mesh vertex v for a flexbody row with pos/rot(degrees)/scale."""
    s = (v[0] * scale[0], v[1] * scale[1], v[2] * scale[2])
    q = quat_from_euler(math.radians(-rot[0]), math.radians(-rot[1]), math.radians(-rot[2]))
    r = set_rotate(q, s)
    return (r[0] + pos[0], r[1] + pos[1], r[2] + pos[2])


def rotz(v, deg):
    """Plain right-handed rotation about +Z by deg (the maths we want)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (c * v[0] - s * v[1], s * v[0] + c * v[1], v[2])


def rot_param_for(deg):
    """Flexbody rot.z value that turns a mesh by `deg` about +Z (checked against the Lua port)."""
    for cand in (deg, -deg):
        p = flexbody_apply((1.0, 0.0, 0.0), rot=(0, 0, cand))
        want = rotz((1.0, 0.0, 0.0), deg)
        if math.dist(p, want) < 1e-9:
            return cand
    raise AssertionError('no rot.z reproduces the rotation')   # would mean the port is wrong


# ---------------------------------------------------------------- engine frames
AXES = [(0, 1), (1, 0), (0, -1), (-1, 0)]          # +y, +x, -y, -x : snapped crank directions


def snap_dir(dx, dy):
    """Nearest axis to the engine -> transmission direction in the ground plane."""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return (0, 1)                               # unknown: assume longitudinal, gearbox behind (+y)
    return max(AXES, key=lambda a: a[0] * dx + a[1] * dy)


def angle_between(a, b):
    """Rotation (multiple of 90 deg) about +Z that turns axis a into axis b."""
    ia, ib = AXES.index(a), AXES.index(b)
    # AXES is ordered clockwise (+y, +x, -y, -x); counter-clockwise rotation goes backwards in the list
    return ((ia - ib) % 4) * 90


def box(points):
    xs, ys, zs = zip(*points)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def place(donor_block, donor_dir, target_block, target_dir):
    """Transform (deg, (tx, ty, tz)) mapping donor car coordinates onto the target engine:
    rotate about Z so the crank->gearbox axes match, then align the gearbox-side face, the lateral
    centre and the bottom of the two engine blocks."""
    deg = angle_between(donor_dir, target_dir)
    pts = [rotz(p, deg) for p in donor_block]
    (dlo, dhi), (tlo, thi) = box(pts), box(target_block)
    ax = 0 if target_dir[0] else 1                  # axis along the crank
    lat = 1 - ax
    t = [0.0, 0.0, 0.0]
    sign = target_dir[ax]
    face_d = dhi[ax] if sign > 0 else dlo[ax]
    face_t = thi[ax] if sign > 0 else tlo[ax]
    t[ax] = face_t - face_d
    t[lat] = (tlo[lat] + thi[lat]) / 2 - (dlo[lat] + dhi[lat]) / 2
    t[2] = tlo[2] - dlo[2]
    return deg, tuple(t)


def compose(deg, t, row_pos=(0, 0, 0), row_rotz=0.0):
    """Combine a placement (rotate deg about Z, then +t) with a mesh row's own pos/rot.z (in our plain
    rotation convention). Returns (total_deg, pos) for v -> rotz(rotz(v,row)+row_pos, deg) + t."""
    p = rotz(row_pos, deg)
    return deg + row_rotz, (p[0] + t[0], p[1] + t[1], p[2] + t[2])


# ---------------------------------------------------------------- voxel fit check
VOXEL = 0.04


def voxels(points, size=VOXEL):
    return {(math.floor(x / size), math.floor(y / size), math.floor(z / size)) for x, y, z in points}


def tri_voxels(a, b, c, size=VOXEL):
    """Voxel cells touched by a triangle (dense surface sampling at half the cell size)."""
    e = max(math.dist(a, b), math.dist(b, c), math.dist(c, a))
    k = max(1, int(e / (size * 0.5)) + 1)
    out = set()
    for i in range(k + 1):
        for j in range(k + 1 - i):
            u, v = i / k, j / k
            w = 1 - u - v
            p = (a[0] * w + b[0] * u + c[0] * v, a[1] * w + b[1] * u + c[1] * v, a[2] * w + b[2] * u + c[2] * v)
            out.add((math.floor(p[0] / size), math.floor(p[1] / size), math.floor(p[2] / size)))
    return out


def transform_points(points, deg, t):
    return [(q[0] + t[0], q[1] + t[1], q[2] + t[2]) for q in (rotz(p, deg) for p in points)]


def hits(cells, blocked):
    """blocked: {cell: label} -> {label: count} for cells of the donor model inside blocked cells."""
    out = {}
    for c in cells:
        lab = blocked.get(c)
        if lab is not None:
            out[lab] = out.get(lab, 0) + 1
    return out


def blocked_count(cells, blocked, limit):
    """Number of blocked cells, stopping early at `limit` (the move search only needs yes/no)."""
    n = 0
    for c in cells:
        if c in blocked:
            n += 1
            if n >= limit:
                break
    return n


MAX_UP_STEPS, MAX_AWAY_STEPS = 3, 4      # x VOXEL (4 cm): up to 12 cm up, 16 cm away from the gearbox
MIN_HITS = 2          # a single touching cell is noise at 4 cm resolution


def thin(points, size=0.02):
    """One point per 2 cm cell: same shape, far fewer points for the fit check."""
    seen = {}
    for p in points:
        seen.setdefault((round(p[0] / size), round(p[1] / size), round(p[2] / size)), p)
    return list(seen.values())


def shifted(cells, t, size=VOXEL):
    """Cells of a model moved by t, with t rounded to whole cells (fit check only; placement stays exact)."""
    d = tuple(int(round(v / size)) for v in t)
    return {(c[0] + d[0], c[1] + d[1], c[2] + d[2]) for c in cells}


def fit(points, deg, t, blocked, away_dir, size=VOXEL):
    """fit_cells() for raw points (rotated by deg, moved by t)."""
    return fit_cells(voxels(transform_points(points, deg, t), size), blocked, away_dir, size)


def fit_cells(cells, blocked, away_dir, size=VOXEL, up_steps=MAX_UP_STEPS, away_steps=MAX_AWAY_STEPS):
    """Try the aligned position, then move up and/or toward the nearest bumper in whole-cell steps.
    Returns (verdict, offset (dx,dy,dz) metres, hits_at_start)."""
    first = hits(cells, blocked)
    if sum(first.values()) < MIN_HITS:
        return 'fits', (0.0, 0.0, 0.0), first
    for total in range(1, up_steps + away_steps + 1):     # smallest total move first
        for u in range(0, min(total, up_steps) + 1):
            a = total - u
            if a > away_steps:
                continue
            d = (away_dir[0] * a, away_dir[1] * a, u)
            moved = ((c[0] + d[0], c[1] + d[1], c[2] + d[2]) for c in cells)
            if blocked_count(moved, blocked, MIN_HITS) < MIN_HITS:
                return 'fits_after_move', (d[0] * size, d[1] * size, d[2] * size), first
    return 'does_not_fit', (0.0, 0.0, 0.0), first


# ---------------------------------------------------------------- nodeRotate## / nodeOffset## / nodeMove## (slot options)
# 1:1 ports of lua/common/jbeam/utils.lua getNodeRotOffsetMoves, updateNodeTransformCache and
# getFlexbodyPosRotAfterNodeRotateOffsetMove, and of mathlib.lua quat multiplication / sign().
def quat_mul(a, b):
    """mathlib.lua LuaQuat:setMulXYZW (a * b)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by + ay * bw + az * bx - ax * bz,
            aw * bz + az * bw + ax * by - ay * bx, aw * bw - ax * bx - ay * by - az * bz)


def quat_to_euler(q):
    """jbeam/utils.lua quatToEuler -> radians."""
    x, y, z, w = q
    xx, yy, zz, ww = x * x, y * y, z * z, w * w
    cxsy, cxcy = 2 * (w * y - x * z), ww - xx - yy + zz
    cx = math.sqrt(cxsy * cxsy + cxcy * cxcy)
    ex = math.atan2(2 * (y * z + w * x), cx)
    if cx < 1e-7:
        return ex, 0.0, math.atan2(2 * (x * y + w * z), ww + xx - yy - zz)
    return ex, math.atan2(cxsy, cxcy), math.atan2(2 * (w * z - x * y), ww - xx + yy - zz)


def lua_sign(x):
    return max(min((x * 1e200) * 1e200, 1), -1)


def node_ops(opts):
    """Slot/row options -> {index: {'rot'|'offset'|'move': dict}} like getNodeRotOffsetMoves.
    Values must already be numbers (variables resolved)."""
    ops = {}
    for key, d in opts.items():
        if not (isinstance(key, str) and isinstance(d, dict)
                and all(isinstance(d.get(a), (int, float)) and not isinstance(d.get(a), bool) for a in 'xyz')):
            continue
        for prefix, kind in (('nodeRotate', 'rot'), ('nodeOffset', 'offset'), ('nodeMove', 'move')):
            if key.startswith(prefix):
                rest = key[len(prefix):]
                if kind == 'offset' and opts.get('ignoreNodeOffset'):
                    break
                # the Lua reads at most two characters after the name as digits (byte - 48)
                idx = 0
                if rest:
                    idx = ord(rest[0]) - 48
                    if len(rest) > 1:
                        idx = idx * 10 + ord(rest[1]) - 48
                ops.setdefault(idx, {})[kind] = d
                break
    return ops


def transform_cache(ops, pos_x):
    """updateNodeTransformCache: (rotation quat, translation, has_rot) for one posX sign."""
    rot, pos, has_rot = (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), any('rot' in o for o in ops.values())
    s = lua_sign(pos_x)
    for i in sorted(ops):
        o = ops[i]
        if 'rot' in o:
            op = o['rot']
            p = tuple(float(op.get(k)) if isinstance(op.get(k), (int, float)) else 0.0 for k in ('px', 'py', 'pz'))
            q1 = quat_from_euler(math.radians(-op['x']), math.radians(-op['y']), math.radians(-op['z']))
            t = set_rotate(q1, p)
            t = set_rotate(rot, (p[0] - t[0], p[1] - t[1], p[2] - t[2]))
            pos = (pos[0] + t[0], pos[1] + t[1], pos[2] + t[2])
            rot = quat_mul(q1, rot)
        if 'offset' in o:
            op = o['offset']
            t = set_rotate(rot, (s * op['x'], op['y'], op['z']))
            pos = (pos[0] + t[0], pos[1] + t[1], pos[2] + t[2])
        if 'move' in o:
            op = o['move']
            t = set_rotate(rot, (op['x'], op['y'], op['z']))
            pos = (pos[0] + t[0], pos[1] + t[1], pos[2] + t[2])
    return rot, pos, has_rot


def flexbody_after_ops(ops, pos, rot):
    """getFlexbodyPosRotAfterNodeRotateOffsetMove: a flexbody row's pos/rot (degrees) after the slot options."""
    if not ops:
        return tuple(pos), tuple(rot)
    q, c, has_rot = transform_cache(ops, pos[0])
    if not has_rot:
        return (pos[0] + c[0], pos[1] + c[1], pos[2] + c[2]), tuple(rot)
    p = set_rotate(q, pos)
    q1 = quat_mul(q, quat_from_euler(math.radians(-rot[0]), math.radians(-rot[1]), math.radians(-rot[2])))
    e = quat_to_euler(q1)
    return ((p[0] + c[0], p[1] + c[1], p[2] + c[2]),
            (math.degrees(-e[0]), math.degrees(-e[1]), math.degrees(-e[2])))


def vertex_after_ops(ops, v, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    """Where mesh vertex v of a flexbody row ends up in the game, slot options included."""
    p, r = flexbody_after_ops(ops, pos, rot)
    return flexbody_apply(v, p, r, scale)


PLACE_INDEX = 9       # our nodeRotate9 / nodeMove9 run after any index the car's own slots use (0-8)
_PROBE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.3, -0.7, 0.2)]


def solve_slot_placement(inherited, deg, t, idx=PLACE_INDEX, tol=1e-6):
    """Slot options {nodeRotate<idx>, nodeMove<idx>} that, added below the car's own slot options `inherited`
    (node_ops form), move a row at the origin exactly like: rotate `deg` about +Z, then translate t, then the
    car's own options. nodeMove is used, not nodeOffset: nodeOffset.x is multiplied by sign(pos.x) and is
    therefore ignored for meshes centred at x = 0. Returns (options dict, max error in m) or (None, err)."""
    if any(i >= idx for i in inherited):
        return None, float('inf')

    def want(v):
        return vertex_after_ops(inherited, tuple(a + b for a, b in zip(rotz(v, deg), t)))
    best = (None, float('inf'))
    for zr in dict.fromkeys((deg, -deg, 0.0)):
        rot_op = {'x': 0, 'y': 0, 'z': zr}
        base = dict(inherited)
        base[idx] = {'rot': rot_op, 'move': {'x': 0.0, 'y': 0.0, 'z': 0.0}}
        f0 = vertex_after_ops(base, (0.0, 0.0, 0.0))
        cols = []
        for e in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            b = dict(inherited)
            b[idx] = {'rot': rot_op, 'move': {'x': e[0], 'y': e[1], 'z': e[2]}}
            fe = vertex_after_ops(b, (0.0, 0.0, 0.0))
            cols.append(tuple(fe[k] - f0[k] for k in range(3)))
        d = tuple(a - b for a, b in zip(want((0.0, 0.0, 0.0)), f0))
        # cols form a rotation matrix (orthonormal): its inverse is its transpose
        m = tuple(sum(cols[j][k] * d[k] for k in range(3)) for j in range(3))
        opts = {f'nodeRotate{idx}': rot_op,
                f'nodeMove{idx}': {'x': round(m[0], 6), 'y': round(m[1], 6), 'z': round(m[2], 6)}}
        full = dict(inherited)
        full.update(node_ops(opts))
        err = max(math.dist(vertex_after_ops(full, v), want(v)) for v in _PROBE)
        if err < best[1]:
            best = (opts, err)
    return (best[0], best[1]) if best[1] < tol * 10 else (None, best[1])


# ---------------------------------------------------------------- gearbox-aligned placement (Phase 2, item C)
GEARBOX_PLAUSIBLE = 0.15     # m: a crank-axis shift larger than this vs the block alignment means odd gearbox nodes


def place_gearbox(donor_block, donor_dir, donor_trans, target_block, target_dir, target_trans):
    """Like place(), but the donor's crank axis meets the car's gearbox: along the crank the gearbox-side faces of
    the engine blocks are aligned (the bellhousing face), sideways and vertically the centre of the donor's own
    gearbox nodes (in its own car) is put on the centre of the car's gearbox nodes - the input shaft is coaxial with
    the crank in both cars, so the same error cancels out. Only for the same layout (0 / 180 deg); a transverse
    engine turned into a longitudinal bay (and back) keeps the block alignment, because a transaxle's node centre
    is not on the crank axis. Returns (deg, t, method, note)."""
    deg, t = place(donor_block, donor_dir, target_block, target_dir)
    if donor_trans is None or target_trans is None:
        return deg, t, 'block', 'no gearbox nodes'
    if deg % 180:
        return deg, t, 'block', f'{deg} deg turn (transverse <-> longitudinal)'
    d = rotz(donor_trans, deg)
    ax = 0 if target_dir[0] else 1
    g = list(t)
    for k in (1 - ax, 2):
        g[k] = target_trans[k] - d[k]
    shift = max(abs(g[k] - t[k]) for k in (1 - ax, 2))
    if shift > GEARBOX_PLAUSIBLE:
        return deg, t, 'block', f'gearbox alignment would shift the engine {shift:.2f} m (implausible)'
    return deg, tuple(g), 'gearbox', ''


# ---------------------------------------------------------------- free slot-option index (Phase 2, item H)
def free_index(inherited, need=2):
    """First index after every index the car's own slots use (our ops must run last), with `need` consecutive
    indices free; None if that does not fit in two digits (utils.lua reads at most two)."""
    idx = max([PLACE_INDEX] + [i + 1 for i in inherited])
    return idx if idx + need - 1 <= 99 else None


# ---------------------------------------------------------------- live nudge sliders + gearbox-end flip (D, F)
NUDGE_VARS = ('$esc_mx', '$esc_my', '$esc_mz')      # car x (right +), y (rear +), z (up +), metres
TURN_VAR, FLIP_VAR = '$esc_mr', '$esc_flip'


def _fmt(v):
    return f'{v:.6f}'.rstrip('0').rstrip('.') if abs(v) > 5e-7 else '0'


def _lin(c, coefs):
    """'c + a*$x + b*$y ...' (coefficients below 1e-7 dropped)."""
    out = _fmt(c)
    for a, var in zip(coefs, NUDGE_VARS):
        if abs(a) > 1e-7:
            out += f' + {_fmt(a)}*{var}'
    return out


def placement_options(inherited, deg, t, pivot, sliders=True, idx=None):
    """Slot options for the catalog engine's slot: rotate `deg` about Z + translate t (fit-check placement),
    exactly, at slider value 0; the nudge sliders move it in the car's own frame (exact: the move is linear
    in the nudge for a fixed rotation), the turn slider rotates it about its own centre `pivot` (donor coordinates),
    and for a 90 deg turn the flip switch turns it 180 deg more about that centre (gearbox end the other way).
    Returns (options, max error at slider 0, info) or (None, err, info)."""
    idx = free_index(inherited) if idx is None else idx
    info = {'index': idx}
    if idx is None:
        return None, float('inf'), dict(info, why='no free slot-option index')
    base, err = solve_slot_placement(inherited, deg, t, idx)
    if base is None:
        return None, err, info
    if not sliders:
        return base, err, info
    rk, mk = f'nodeRotate{idx}', f'nodeMove{idx}'

    def variant(d, tt):
        o, e = solve_slot_placement(inherited, d, tt, idx)
        if o is None:
            return None
        m0 = [o[mk][a] for a in 'xyz']
        cols = []
        for k in range(3):
            tk = tuple(tt[i] + (1.0 if i == k else 0.0) for i in range(3))
            ok, _e = solve_slot_placement(inherited, d, tk, idx)
            if ok is None or ok[rk] != o[rk]:
                return None
            cols.append([ok[mk][a] - m0[i] for i, a in enumerate('xyz')])
        return o[rk]['z'], [_lin(m0[i], [cols[k][i] for k in range(3)]) for i in range(3)]
    a = variant(deg, t)
    if a is None:
        return base, err, dict(info, why='sliders not expressible')
    zr, mv = a
    opts = {rk: {'x': 0, 'y': 0, 'z': zr}, mk: {k: '$=' + e for k, e in zip('xyz', mv)}}
    info['flip'] = False
    if deg % 180 == 90:
        c = rotz(pivot, deg)
        cf = rotz(pivot, deg + 180)
        b = variant((deg + 180) % 360, tuple(t[i] + c[i] - cf[i] for i in range(3)))
        if b is not None:
            zf, mf = b
            opts[rk]['z'] = f'$=case({FLIP_VAR} >= 0.5, {_fmt(zf)}, {_fmt(zr)})'
            opts[mk] = {k: f'$=case({FLIP_VAR} >= 0.5, {ef}, {ea})' for k, ea, ef in zip('xyz', mv, mf)}
            info['flip'] = True
    opts[f'nodeRotate{idx + 1}'] = {'x': 0, 'y': 0, 'z': TURN_VAR,
                                    'px': round(pivot[0], 6), 'py': round(pivot[1], 6), 'pz': round(pivot[2], 6)}
    return opts, err, info
