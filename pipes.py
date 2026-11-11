"""Phase 4: bent exhaust pipes from the donor's header outlets to the car's exhaust (replaces the Phase 3 straight
unit tubes). Built in car coordinates (metres, Z up) from mesh surface points:

- pipe_end(): where a pipe mesh ends near a point, its direction there and its radius (from the mesh, not guessed);
- route(): a smooth curve (cubic Hermite, optionally through one lowered via point = up to 3 bends) that leaves the
  outlet along the header's direction and enters the car pipe along its direction; checked for length, bend radius
  and clearance from the car's other meshes;
- tube(): the pipe mesh: a tube with a flange (and bolts) at the header end and a clamp band at the car end."""
import math

MEETS = 0.03               # m: outlet and inlet this close already meet
MAX_LEN = 0.80             # m: longest pipe
BEND_MIN = 2.0             # bend radius >= this x pipe radius
CLEAR = 0.01               # m: no pipe surface closer than this to another mesh
END_FREE = 0.06            # m: near each end the pipe meets its own parts (not counted as clipping)
SHORT = 0.12              # m: a gap this short gets a straight connector (a slip joint), no bends
SIDES = 12
SAMPLES = 20
R_MIN, R_MAX = 0.015, 0.045


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    d = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / d, a[1] / d, a[2] / d)


def _centroid(pts):
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


class End:
    def __init__(self, pos, direction, radius):
        self.pos, self.dir, self.radius = pos, direction, radius     # dir: pointing OUT of the pipe it belongs to


def pipe_end(points, toward, reach=0.12):
    """The open end of a pipe mesh (surface points) nearest to `toward`: its centre, the direction the pipe points
    there (out of its open end) and its radius. None if the points don't look like a pipe end."""
    if not points:
        return None
    tip = min(points, key=lambda p: math.dist(p, toward))
    near = [p for p in points if math.dist(p, tip) <= reach]
    if len(near) < 6:
        return None
    # direction: from the centre of the points further back to the centre of the points at the tip
    tipc = _centroid([p for p in near if math.dist(p, tip) <= 0.035])
    back = [p for p in near if math.dist(p, tip) >= reach * 0.5]
    if not back:
        return None
    d = _norm(_sub(tipc, _centroid(back)))
    # the end ring: points within 2 cm (along d) of the tip; centre and radius from them
    ring = [p for p in near if abs(_dot(_sub(p, tip), d)) <= 0.02]
    if len(ring) < 3:
        ring = [p for p in near if math.dist(p, tip) <= 0.035]
    c = _centroid(ring)
    # again from the ring's centre (the first estimate leans toward the edge point found first)
    back = [p for p in points if -reach <= _dot(_sub(p, c), d) <= -reach * 0.4 and math.dist(p, c) <= reach * 1.2]
    if len(back) >= 3:
        d = _norm(_sub(c, _centroid(back)))
        ring = [p for p in near if abs(_dot(_sub(p, c), d)) <= 0.02] or ring
        c = _centroid(ring)
    rad = [math.sqrt(max(0.0, _dot(_sub(p, c), _sub(p, c)) - _dot(_sub(p, c), d) ** 2)) for p in ring]
    rad.sort()
    r = rad[len(rad) * 3 // 4] if rad else 0.025
    return End(c, d, max(R_MIN, min(R_MAX, r)))


def _hermite(p0, t0, p1, t1, n):
    out = []
    for i in range(n + 1):
        s = i / n
        h00, h10, h01, h11 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s, -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
        out.append(tuple(h00 * p0[k] + h10 * t0[k] + h01 * p1[k] + h11 * t1[k] for k in range(3)))
    return out


def length(path):
    return sum(math.dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def min_bend(path):
    """Smallest radius of curvature along a sampled path (circumradius of consecutive triples)."""
    best = float('inf')
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
        cr = _cross(_sub(b, a), _sub(c, a))
        area2 = math.sqrt(_dot(cr, cr))
        if area2 > 1e-12:
            best = min(best, ab * bc * ca / (2 * area2))
    return best


class Obstacles:
    """Surface points of the car's other meshes in a 3 cm grid, for clearance checks."""
    CELL = 0.03

    def __init__(self, points):
        self.grid = {}
        for p in points:
            self.grid.setdefault(tuple(int(math.floor(v / self.CELL)) for v in p), []).append(p)

    def nearest(self, q, limit):
        r = int(math.ceil(limit / self.CELL))
        c = tuple(int(math.floor(v / self.CELL)) for v in q)
        best = float('inf')
        for i in range(-r, r + 1):
            for j in range(-r, r + 1):
                for k in range(-r, r + 1):
                    for p in self.grid.get((c[0] + i, c[1] + j, c[2] + k), ()):
                        best = min(best, math.dist(p, q))
        return best


def route(out, inn, obstacles=None):
    """(path, info) for a pipe from the outlet End `out` (dir: pointing out of the header) to the inlet End `inn`
    (dir: pointing out of the car pipe's open end, i.e. the pipe arrives along -inn.dir); path None + info['why']
    if no candidate passes."""
    d = math.dist(out.pos, inn.pos)
    info = {'distance': round(d, 4)}
    if d <= MEETS:
        info['why'] = 'meets'
        return None, info
    if d > MAX_LEN:
        info['why'] = 'too far'
        return None, info
    r = max(out.radius, inn.radius)
    if d <= SHORT:
        path = [tuple(out.pos[k] + (inn.pos[k] - out.pos[k]) * i / 6 for k in range(3)) for i in range(7)]
        info.update(length=round(d, 4), bend=None, drop=0.0, k=0.0, radius=(out.radius, inn.radius), short=True)
        return path, info
    tried = []
    for drop in (0.0, 0.06, 0.12):
        for k in (0.6, 0.9, 0.4, 0.25, 1.2, 0.15, 1.6):
            t0 = _mul(out.dir, d * k)
            t1 = _mul(inn.dir, -d * k)
            if drop:
                mid = _add(_mul(_add(out.pos, inn.pos), 0.5), (0.0, 0.0, -drop))
                tm = _mul(_norm(_sub(inn.pos, out.pos)), d * k * 0.7)
                path = _hermite(out.pos, t0, mid, tm, SAMPLES // 2)[:-1] + _hermite(mid, tm, inn.pos, t1, SAMPLES // 2)
            else:
                path = _hermite(out.pos, t0, inn.pos, t1, SAMPLES)
            L, bend = length(path), min_bend(path)
            why = None
            if L > MAX_LEN:
                why = 'too long'
            elif bend < BEND_MIN * r:
                why = 'bend too tight'
            elif obstacles is not None:
                for q in path:
                    if math.dist(q, out.pos) < END_FREE or math.dist(q, inn.pos) < END_FREE:
                        continue
                    if obstacles.nearest(q, r + CLEAR) < r + CLEAR:
                        why = 'would clip'
                        break
            tried.append(why)
            if why is None:
                info.update(length=round(L, 4), bend=round(bend, 4), drop=drop, k=k, radius=(out.radius, inn.radius))
                return path, info
    info['why'] = 'would clip' if 'would clip' in tried else tried[-1]
    return None, info


# ---------------------------------------------------------------- mesh
def _frames(path):
    """Rotation-minimising frames (u, v) along the path."""
    tang = []
    for i in range(len(path)):
        a = path[max(0, i - 1)]
        b = path[min(len(path) - 1, i + 1)]
        tang.append(_norm(_sub(b, a)))
    t = tang[0]
    u = _norm(_cross(t, (0, 0, 1)) if abs(t[2]) < 0.9 else _cross(t, (1, 0, 0)))
    out = []
    for i, t in enumerate(tang):
        u = _norm(_sub(u, _mul(t, _dot(u, t))))
        out.append((u, _cross(t, u)))
    return tang, out


def _circle(c, u, v, r, n=SIDES):
    return [_add(c, _add(_mul(u, r * math.cos(2 * math.pi * k / n)), _mul(v, r * math.sin(2 * math.pi * k / n))))
            for k in range(n)]


def tube(path, r0, r1):
    """Mesh (from shapes.Mesh) of a pipe along `path`, radius r0 at the start easing to r1 over the last 30%, a
    flange with 3 bolts at the start and a clamp band at the end."""
    from .shapes import Mesh, BOLT_R
    mesh = Mesh()
    tang, fr = _frames(path)
    n = len(path)
    rows = []
    for i, (c, (u, v)) in enumerate(zip(path, fr)):
        s = i / (n - 1)
        w = 0.0 if s < 0.7 else (s - 0.7) / 0.3
        r = r0 + (r1 - r0) * w * w * (3 - 2 * w)
        rows.append([mesh.add(p) for p in _circle(c, u, v, r)])
    for k in range(n - 1):
        for i in range(SIDES):
            j = (i + 1) % SIDES
            mesh.quad(rows[k][i], rows[k][j], rows[k + 1][j], rows[k + 1][i])
    _band(mesh, path[0], tang[0], fr[0], r0 - 0.001, r0 + 0.012, 0.006)             # flange
    u, v = fr[0]
    for k in range(3):
        a = 2 * math.pi * (k / 3 + 1 / 12)
        c = _add(_add(path[0], _mul(tang[0], 0.006)), _add(_mul(u, (r0 + 0.007) * math.cos(a)), _mul(v, (r0 + 0.007) * math.sin(a))))
        _band(mesh, c, tang[0], fr[0], 0.0, BOLT_R * 0.8, 0.005, sides=6)
    back = _sub(path[-1], _mul(tang[-1], 0.035))
    _band(mesh, back, tang[-1], fr[-1], r1 - 0.001, r1 + 0.004, 0.022)                  # clamp
    return mesh


def _band(mesh, c, t, frame, r_in, r_out, width, sides=SIDES):
    """Closed ring (washer / band) around c, from c to c + t * width."""
    u, v = frame
    c2 = _add(c, _mul(t, width))
    rings = [_circle(c, u, v, r_out, sides), _circle(c2, u, v, r_out, sides)]
    if r_in > 0:
        rings += [_circle(c2, u, v, r_in, sides), _circle(c, u, v, r_in, sides)]
    rows = [[mesh.add(p) for p in r] for r in rings]
    for k in range(len(rows)):
        if len(rows) == 2 and k == 1:
            break
        a, b = rows[k], rows[(k + 1) % len(rows)]
        for i in range(sides):
            j = (i + 1) % sides
            mesh.quad(a[i], a[j], b[j], b[i])
    if r_in <= 0:                          # solid: cap both ends
        for row in rows:
            cen = mesh.add(_centroid([mesh.v[i] for i in row]))
            for i in range(sides):
                mesh.tri(cen, row[i], row[(i + 1) % sides])
