"""Phase 4: generated bellhousing adapters (engine back face -> car gearbox front face) as real meshes.

Everything is built in car coordinates (metres, Z up): the flexbody row needs no pos / rot / scale. The outlines come
from the meshes' surface points (dae.sample_surface, thinned to 2 cm), never from boxes.

Axis convention (same as joints.measure): `ax` = 0 or 1 is the engine -> gearbox axis, `sign` its direction,
`lat` = 1 - ax the sideways axis, 2 = up. A 2D outline point is (lateral, up)."""
import math

RING_N = 32                 # points per outline ring
SLAB = 0.03                 # m: points this deep behind / in front of a face form its outline
WALL = 0.006                # m: shell wall thickness
LIP = 0.014                 # m: flange lip height (outward)
LIP_W = 0.012               # m: flange lip width (along the axis)
INTO = 0.01                 # m: each end reaches this far into the engine / gearbox (hides the seam)
MIN_LEN = 0.045             # m: shortest bellhousing (a touching but shifted joint still gets a real part)
STATIONS = 4                # loft sections
RIBS = 5
BOLT_R = 0.0075             # bolt head (hex) radius
BOLT_H = 0.006
TRI_MAX = 3000


# ---------------------------------------------------------------- 2D outline helpers
def hull(pts):
    """Convex hull (counter-clockwise, no repeated end point) of 2D points."""
    p = sorted(set((round(a, 5), round(b, 5)) for a, b in pts))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    return lo[:-1] + hi[:-1]


def area_centre(poly):
    """Area centroid of a simple polygon."""
    a = cx = cy = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-12:
        return (sum(x for x, _ in poly) / len(poly), sum(y for _, y in poly) / len(poly))
    return (cx / (3 * a), cy / (3 * a))


def resample(poly, n=RING_N, centre=None):
    """n points along a closed polygon, evenly by arc length, starting where the ray from `centre` toward +lateral
    crosses it, counter-clockwise - two rings resampled this way correspond point by point."""
    c = centre or area_centre(poly)
    m = len(poly)
    # start: the edge crossed by the +lateral ray from c
    start, start_t = 0, 0.0
    for i in range(m):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % m]
        if (y0 - c[1]) * (y1 - c[1]) <= 0 and y0 != y1:
            t = (c[1] - y0) / (y1 - y0)
            if x0 + t * (x1 - x0) > c[0]:
                start, start_t = i, t
                break
    seq = []
    (x0, y0), (x1, y1) = poly[start], poly[(start + 1) % m]
    seq.append((x0 + start_t * (x1 - x0), y0 + start_t * (y1 - y0)))
    for k in range(1, m + 1):
        seq.append(poly[(start + k) % m])
    seq[-1] = seq[0]
    lens = [math.dist(seq[i], seq[i + 1]) for i in range(len(seq) - 1)]
    total = sum(lens)
    out, acc, j = [], 0.0, 0
    for k in range(n):
        target = total * k / n
        while j < len(lens) - 1 and acc + lens[j] < target:
            acc += lens[j]
            j += 1
        t = (target - acc) / lens[j] if lens[j] > 0 else 0.0
        a, b = seq[j], seq[j + 1]
        out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def radial(poly, centre, dirs):
    """Where rays from `centre` along the unit vectors `dirs` leave the convex polygon (None if centre is outside or
    a ray misses) - points of two outlines matched by direction, so ribs and seams run straight."""
    if not inside(poly, centre):
        return None
    out = []
    for dx, dy in dirs:
        best = None
        for i in range(len(poly)):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
            ex, ey = x1 - x0, y1 - y0
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            t = ((x0 - centre[0]) * ey - (y0 - centre[1]) * ex) / den
            u = ((x0 - centre[0]) * dy - (y0 - centre[1]) * dx) / den
            if t > 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best):
                best = t
        if best is None:
            return None
        out.append((centre[0] + dx * best, centre[1] + dy * best))
    return out


def normals2(ring):
    """Outward unit normals of a counter-clockwise ring (average of the two neighbouring edges)."""
    n = len(ring)
    out = []
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        e1 = (b[0] - a[0], b[1] - a[1])
        e2 = (c[0] - b[0], c[1] - b[1])
        nx, ny = (e1[1] + e2[1]), -(e1[0] + e2[0])
        d = math.hypot(nx, ny) or 1.0
        out.append((nx / d, ny / d))
    return out


def offset(ring, dist):
    return [(p[0] + n[0] * dist, p[1] + n[1] * dist) for p, n in zip(ring, normals2(ring))]


# ---------------------------------------------------------------- outlines of the two faces
class Outline:
    def __init__(self, station, poly, ring, centre, npts):
        self.station, self.poly, self.ring, self.centre, self.npts = station, poly, ring, centre, npts

    @property
    def size(self):
        xs, ys = [p[0] for p in self.poly], [p[1] for p in self.poly]
        return (max(xs) - min(xs), max(ys) - min(ys))


def outline(points, ax, lo, hi):
    """Outline (convex hull, resampled) of the points with lo <= p[ax] <= hi, projected on (lateral, up)."""
    lat = 1 - ax
    sel = [(p[lat], p[2]) for p in points if lo <= p[ax] <= hi]
    poly = hull(sel)
    if len(poly) < 3:
        return None
    c = area_centre(poly)
    return Outline((lo + hi) / 2, poly, resample(poly, RING_N, c), c, len(sel))


def poly_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


FACE_SEARCH = 0.15          # m: how far past small bumps (studs, shaft ends, sensors) a real face is looked for
FACE_AREA = 0.5             # a face = the first section with at least this share of the largest section nearby


def face_outline(points, ax, extreme, inward):
    """(station, Outline) of the real face of a part near its extreme along `ax`: walking `inward` from the extreme
    in 1 cm steps, the first SLAB-deep section whose outline area is at least FACE_AREA of the largest section within
    FACE_SEARCH. Small bumps at the very end (a shaft end, a sensor) are skipped that way."""
    cand = []
    for k in range(int(FACE_SEARCH / 0.01) + 1):
        s = extreme + inward * 0.01 * k
        o = outline(points, ax, *sorted((s, s + inward * SLAB)))
        cand.append((s, o, poly_area(o.poly) if o else 0.0))
    top = max(a for _s, _o, a in cand)
    if top <= 0:
        return None, None
    for s, o, a in cand:
        if a >= FACE_AREA * top:
            return s, o
    return None, None


def inside(poly, q, margin=0.0):
    """q inside the convex counter-clockwise polygon (grown by margin)."""
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        d = math.hypot(ex, ey) or 1.0
        if (ex * (q[1] - a[1]) - ey * (q[0] - a[0])) / d < -margin:
            return False
    return True


def clip(subject, window):
    """Sutherland-Hodgman: the part of convex polygon `subject` inside convex polygon `window` (both CCW)."""
    out = list(subject)
    for i in range(len(window)):
        a, b = window[i], window[(i + 1) % len(window)]
        if not out:
            break

        def side(q):
            return (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])
        src, out = out, []
        for j in range(len(src)):
            p, q = src[j], src[(j + 1) % len(src)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def dist_outside(poly, q):
    """How far q lies outside the convex polygon (0 inside)."""
    if inside(poly, q):
        return 0.0
    best = float('inf')
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        L2 = ex * ex + ey * ey or 1e-12
        t = max(0.0, min(1.0, ((q[0] - a[0]) * ex + (q[1] - a[1]) * ey) / L2))
        best = min(best, math.dist(q, (a[0] + t * ex, a[1] + t * ey)))
    return best


GB_GROW = 0.006             # m: the adapter wraps the gearbox's mouth this much outside it
MOUTH_DEPTH = 0.10          # m: the gearbox's front this deep forms its mouth outline (a slanted mouth included)
NEAR = 0.03                 # m: surface points this close (sideways) to an outline point give its depth
REAR_WINDOW = 0.15          # m: only engine surface this close to its rear end can meet the adapter
PROBE = (0.0, 0.008, 0.016, 0.03)   # m: how far inside an outline point its surface depth is looked for
STEP_MAX = 0.06             # m: an end point deeper / shallower than this from its ring's median is a hollow or a
                            #    stray part, and is put back at the median


class Surface:
    """Triangles of a part (car coordinates) binned on the plane square to `ax`, for exact depths: where a line
    along the axis through (lateral, up) meets the surface."""
    CELL = 0.02

    def __init__(self, geoms, ax, lo=None, hi=None):
        """geoms: [(verts, tris)]; only triangles with a vertex between lo and hi along the axis are kept."""
        self.ax, self.bins = ax, {}
        lat = 1 - ax
        for verts, tris in geoms:
            for t in tris:
                if max(t) >= len(verts):
                    continue
                a, b, c = verts[t[0]], verts[t[1]], verts[t[2]]
                if lo is not None and not any(lo <= p[ax] <= hi for p in (a, b, c)):
                    continue
                tri = ((a[lat], a[2], a[ax]), (b[lat], b[2], b[ax]), (c[lat], c[2], c[ax]))
                x0, x1 = min(p[0] for p in tri), max(p[0] for p in tri)
                y0, y1 = min(p[1] for p in tri), max(p[1] for p in tri)
                for i in range(int(math.floor(x0 / self.CELL)), int(math.floor(x1 / self.CELL)) + 1):
                    for j in range(int(math.floor(y0 / self.CELL)), int(math.floor(y1 / self.CELL)) + 1):
                        self.bins.setdefault((i, j), []).append(tri)

    def depths(self, q):
        """Axial positions where the line through q = (lateral, up) crosses the surface."""
        out = []
        for (ax_, ay, az), (bx, by, bz), (cx, cy, cz) in self.bins.get((int(math.floor(q[0] / self.CELL)),
                                                                        int(math.floor(q[1] / self.CELL))), ()):
            d = (by - cy) * (ax_ - cx) + (cx - bx) * (ay - cy)
            if abs(d) < 1e-14:
                continue
            l1 = ((by - cy) * (q[0] - cx) + (cx - bx) * (q[1] - cy)) / d
            l2 = ((cy - ay) * (q[0] - cx) + (ax_ - cx) * (q[1] - cy)) / d
            l3 = 1 - l1 - l2
            if l1 >= -1e-9 and l2 >= -1e-9 and l3 >= -1e-9:
                out.append(l1 * az + l2 * bz + l3 * cz)
        return out


def _ends(points, ax, ring, pick, fallback, surf=None, window=None):
    """(per-point depths, hit flags) of a ring on a part: exact surface hits (Surface) where the line along the
    axis meets the part inside `window`, else the nearest-points pick; missing ones and outliers -> the median."""
    vals, hit = [], []
    c = area_centre(ring)
    for q in ring:
        v = None
        if surf is not None:
            dq = math.dist(q, c) or 1.0
            for step in PROBE:          # an outline point sits on the part's edge: look just inside it
                t = min(step / dq, 0.5)
                qq = (q[0] + (c[0] - q[0]) * t, q[1] + (c[1] - q[1]) * t)
                ds = [z for z in surf.depths(qq) if window is None or window[0] <= z <= window[1]]
                if ds:
                    v = pick(ds)
                    break
        hit.append(v is not None)
        if v is None:
            v = _depth(points, ax, q, pick)
        if v is None:
            v = _depth(points, ax, q, pick, 2 * NEAR)
        vals.append(v)
    found = sorted(v for v in vals if v is not None)
    med = found[len(found) // 2] if found else fallback
    out = []
    for v, h in zip(vals, hit):
        bad = v is None or abs(v - med) > STEP_MAX
        out.append(med if bad else v)
    hit = [h and not (v is None or abs(v - med) > STEP_MAX) for v, h in zip(vals, hit)]
    n = len(out)            # circular median of 5 neighbours: one stray point never makes a spike
    sm = [sorted(out[(i + k) % n] for k in range(-2, 3))[2] for i in range(n)]
    hit = [h and abs(a - b) < 0.005 for h, a, b in zip(hit, sm, out)]
    return sm, hit


def _depth(points, ax, q, pick, radius=NEAR):
    """pick (max / min) of the axial positions of the points within `radius` of q (lateral, up), or None."""
    lat = 1 - ax
    r2 = radius * radius
    vals = [p[ax] for p in points if (p[lat] - q[0]) ** 2 + (p[2] - q[1]) ** 2 <= r2]
    return pick(vals) if vals else None


def silhouette(points, ax, extreme, inward, depth=MOUTH_DEPTH):
    lat = 1 - ax
    sel = [(p[lat], p[2]) for p in points if 0 <= inward * (p[ax] - extreme) <= depth]
    poly = hull(sel)
    return poly if len(poly) >= 3 else None


def plan(m, engine_pts, gearbox_pts, shift=(0.0, 0.0), engine_geo=None, gearbox_geo=None):
    """How the car's gearbox mouth meets the placed donor engine, measured at RING_N points around the mouth.

    Mouth = outline of the gearbox's front MOUTH_DEPTH (so a slanted mouth counts whole). At every mouth point the
    gearbox surface's frontmost depth and the engine surface's rearmost depth there give the local gap, so the
    adapter follows both surfaces (a wedge where only part of a slanted mouth is open). Mouth points that hang past
    the engine's rear outline are pulled in onto it (the adapter tapers there).

    shift (lateral, up): where the engine's crank axis sits relative to the gearbox's (the fit check's move after
    gearbox alignment) - the engine end is the mouth outline moved by it, so the adapter is an offset spacer whose
    two ends have the same shape.

    engine_geo / gearbox_geo: [(verts, tris)] of the parts - when given, the ends sit exactly on the surfaces
    (Surface); 'hits' = share of end points that do (the rest follow their neighbours: hollows, open edges).

    Returns {'gap' (largest local gap, m), 'gap_mean', 'overhang' (m), 'coverage', rings, per-point ends} or a dict
    with 'why' when nothing can be built."""
    ax, sign = m['ax'], m['sign']
    gx = m['gearbox_face'].extreme
    mouth = silhouette(gearbox_pts, ax, gx, sign)
    if mouth is None:
        return {'gap': None, 'overhang': None, 'coverage': 0.0, 'why': 'no gearbox outline'}
    grown = hull(offset(resample(mouth, RING_N * 2), GB_GROW))
    moved = [(q[0] + shift[0], q[1] + shift[1]) for q in grown]
    # the engine's back = its rearmost surface in front of the (moved) mouth - a part reaching back past the
    # gearbox beside or above it (a hose, a heat shield) is not where the gearbox bolts on
    lat = 1 - ax
    behind = [p[ax] for p in engine_pts if inside(moved, (p[lat], p[2]))]
    if not behind:
        return {'gap': None, 'overhang': None, 'coverage': 0.0, 'why': 'the gearbox mouth is not in front of the engine'}
    ex = max(v * sign for v in behind) * sign
    rear = silhouette(engine_pts, ax, ex, -sign)
    if rear is None:
        return {'gap': None, 'overhang': None, 'coverage': 0.0, 'why': 'no engine outline'}
    inner = clip(moved, rear)
    cov = poly_area(inner) / (poly_area(grown) or 1.0) if len(inner) >= 3 else 0.0
    over = max(dist_outside(rear, q) for q in moved)
    pl = {'ax': ax, 'sign': sign, 'overhang': round(over, 4), 'coverage': round(cov, 3), 'mouth': mouth, 'rear': rear,
          'shift': (round(shift[0], 4), round(shift[1], 4))}
    if len(inner) < 3 or cov <= 0.05:
        pl.update(gap=None, why='the gearbox mouth is not in front of the engine')
        return pl
    c = area_centre(mouth)
    ring_g = resample(grown, RING_N, c)
    ce = (c[0] + shift[0], c[1] + shift[1])
    dirs = [((q[0] - c[0]) / (math.dist(q, c) or 1), (q[1] - c[1]) / (math.dist(q, c) or 1)) for q in ring_g]
    ring_e = radial(inner, ce, dirs) or resample(inner, RING_N, ce)
    front = min if sign > 0 else max
    back = max if sign > 0 else min
    eng = [p for p in engine_pts if 0 <= sign * (ex - p[ax]) <= REAR_WINDOW]
    gbx = [p for p in gearbox_pts if 0 <= sign * (p[ax] - gx) <= MOUTH_DEPTH + NEAR]
    ew = sorted((ex, ex - sign * (REAR_WINDOW + 0.02)))
    gw = sorted((gx - sign * 0.02, gx + sign * (MOUTH_DEPTH + NEAR)))
    es = Surface(engine_geo, ax, *ew) if engine_geo else None
    gs = Surface(gearbox_geo, ax, *gw) if gearbox_geo else None
    dg, hg = _ends(gbx, ax, ring_g, front, gx, gs, gw)
    de, he = _ends(eng, ax, ring_e, back, ex, es, ew)
    pl['hits'] = (round(sum(he) / len(he), 3), round(sum(hg) / len(hg), 3)) if es or gs else None
    a, b, gaps = [], [], []
    for bg, be in zip(dg, de):
        g = sign * (bg - be)
        gaps.append(g)
        if g < 0:                       # the gearbox already reaches over the engine here: nothing to close
            be = bg
        a.append(be - sign * INTO)
        b.append(bg + sign * INTO)
    pl.update(ring_g=ring_g, ring_e=ring_e, a=a, b=b, gaps=gaps, gap=round(max(gaps), 4),
              gap_mean=round(sum(max(0.0, g) for g in gaps) / len(gaps), 4),
              length=round(max(abs(y - x) for x, y in zip(a, b)), 4))
    return pl


# ---------------------------------------------------------------- mesh building
class Mesh:
    def __init__(self):
        self.v, self.f = [], []          # vertices (x, y, z), faces (index tuples, tris or quads)

    def add(self, p):
        self.v.append(tuple(p))
        return len(self.v) - 1

    def quad(self, a, b, c, d):
        self.f.append((a, b, c, d))

    def tri(self, a, b, c):
        self.f.append((a, b, c))

    def tri_count(self):
        return sum(len(f) - 2 for f in self.f)


def _p3(ax, axial, q):
    p = [0.0, 0.0, 0.0]
    p[ax], p[1 - ax], p[2] = axial, q[0], q[1]
    return p


def _band(mesh, ax, rings, axials):
    """Quad strip through rings (lists of 2D points) at per-point axial positions (lists, or one number per ring);
    returns the vertex rows."""
    rows = []
    for r, z in zip(rings, axials):
        zs = z if isinstance(z, (list, tuple)) else [z] * len(r)
        rows.append([mesh.add(_p3(ax, zz, q)) for q, zz in zip(r, zs)])
    n = len(rings[0])
    for k in range(len(rows) - 1):
        r0, r1 = rows[k], rows[k + 1]
        for i in range(n):
            j = (i + 1) % n
            mesh.quad(r0[i], r0[j], r1[j], r1[i])
    return rows


def _ring_faces(mesh, ra, rb):
    n = len(ra)
    for i in range(n):
        j = (i + 1) % n
        mesh.quad(ra[i], ra[j], rb[j], rb[i])


def _lerp(r0, r1, t):
    return [(x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t) for x, y in zip(r0, r1)]


def _ease(t):
    return t * t * (3 - 2 * t)


def bellhousing(pl):
    """The adapter mesh for a plan(): a shell with wall thickness from the engine (ring_e at a) to the gearbox mouth
    (ring_g at b), a flange lip at each end, hex bolt heads around both flanges and ribs along the outside.
    Winding is not relied on (the Blender pass recalculates outward normals)."""
    ax, sign = pl['ax'], pl['sign']
    r0, r1, a, b = pl['ring_e'], pl['ring_g'], pl['a'], pl['b']
    n = len(r0)
    mesh = Mesh()
    ts = [k / STATIONS for k in range(STATIONS + 1)]
    outer = [_lerp(r0, r1, _ease(t)) for t in ts]
    inner = [offset(r, -WALL) for r in outer]
    zs = [[x + (y - x) * t for x, y in zip(a, b)] for t in ts]
    oro = _band(mesh, ax, outer, zs)
    iro = _band(mesh, ax, inner, zs)
    _ring_faces(mesh, oro[0], iro[0])
    _ring_faces(mesh, oro[-1], iro[-1])
    # flange lips at each end, on the face of the part they bolt to; bolts on the lip's free face
    length = [abs(y - x) - 2 * INTO for x, y in zip(a, b)]
    for ring, base, d in ((r0, [x + sign * INTO for x in a], 1), (r1, [y - sign * INTO for y in b], -1)):
        top_z = [z0 + d * sign * max(0.004, min(LIP_W, L / 3 + 0.004)) for z0, L in zip(base, length)]
        lo_r, hi_r = offset(ring, -0.001), offset(ring, LIP)
        top = _band(mesh, ax, [hi_r, hi_r], [base, top_z])
        bot = _band(mesh, ax, [lo_r, lo_r], [base, top_z])
        _ring_faces(mesh, top[0], bot[0])
        _ring_faces(mesh, top[1], bot[1])
        nb = bolt_count(ring)
        nrm = normals2(ring)
        for k in range(nb):
            i = int(round(k * n / nb)) % n
            c = (ring[i][0] + nrm[i][0] * LIP * 0.55, ring[i][1] + nrm[i][1] * LIP * 0.55)
            _hex(mesh, ax, c, top_z[i], d * sign)
    for k in range(RIBS):
        i = int(round((k + 0.5) * n / RIBS)) % n
        if length[i] > 2 * LIP_W:
            _rib(mesh, ax, outer, zs, i)
    return mesh


def bolt_count(ring):
    per = sum(math.dist(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
    return max(6, min(10, int(per / 0.12)))


def _hex(mesh, ax, c, z, d):
    """Hex bolt head standing on the plane `z`, pointing toward d (+1 / -1 along the axis)."""
    ring = [(c[0] + BOLT_R * math.cos(math.pi / 3 * k + math.pi / 6), c[1] + BOLT_R * math.sin(math.pi / 3 * k + math.pi / 6))
            for k in range(6)]
    rows = _band(mesh, ax, [ring, ring], [z - d * 0.001, z + d * BOLT_H])
    top = rows[1]
    cen = mesh.add(_p3(ax, z + d * BOLT_H, c))
    for i in range(6):
        mesh.tri(cen, top[i], top[(i + 1) % 6])


def _rib(mesh, ax, outer, zs, i):
    """A rib 8 mm wide, 10 mm high on the shell's outside along outline point i."""
    rows = []
    for ring, z in zip(outer, zs):
        nrm = normals2(ring)
        p, nn = ring[i], nrm[i]
        tg = (-nn[1], nn[0])
        w, h = 0.004, 0.010
        pts = [(p[0] - tg[0] * w - nn[0] * 0.002, p[1] - tg[1] * w - nn[1] * 0.002),
               (p[0] - tg[0] * w + nn[0] * h, p[1] - tg[1] * w + nn[1] * h),
               (p[0] + tg[0] * w + nn[0] * h, p[1] + tg[1] * w + nn[1] * h),
               (p[0] + tg[0] * w - nn[0] * 0.002, p[1] + tg[1] * w - nn[1] * 0.002)]
        rows.append([mesh.add(_p3(ax, z[i], q)) for q in pts])
    for k in range(len(rows) - 1):
        r0, r1 = rows[k], rows[k + 1]
        for s in range(3):
            mesh.quad(r0[s], r0[s + 1], r1[s + 1], r1[s])


def end_gaps(pl, engine_pts, gearbox_pts):
    """Check of a plan's two ends (m): the largest distance from an end point to the nearest surface point of the
    part it meets (engine for ring_e at a + INTO, gearbox for ring_g at b - INTO)."""
    ax, sign = pl['ax'], pl['sign']
    out = []
    for ring, ends, pts, d in ((pl['ring_e'], pl['a'], engine_pts, sign), (pl['ring_g'], pl['b'], gearbox_pts, -sign)):
        worst = 0.0
        for q, z in zip(ring, ends):
            p = _p3(ax, z + d * INTO, q)
            worst = max(worst, min(math.dist(p, s) for s in pts))
        out.append(round(worst, 4))
    return out


def to_obj(mesh, name):
    lines = [f'o {name}'] + [f'v {x:.5f} {y:.5f} {z:.5f}' for x, y, z in mesh.v]
    lines += ['f ' + ' '.join(str(i + 1) for i in f) for f in mesh.f]
    return '\n'.join(lines) + '\n'
