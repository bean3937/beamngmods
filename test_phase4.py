"""Phase 4: generated bellhousing adapters and bent exhaust pipes (shapes.py, pipes.py, fitmesh.py, validator)."""
import math
import unittest

from swapcat import shapes, pipes, fitmesh, joints, dae
from swapcat.vis2 import check_fit_files


def box_surface(x0, x1, y0, y1, z0, z1, step=0.01):
    """Points on the six faces of an axis-aligned box, and the box as triangles."""
    pts = []
    xs = [x0 + (x1 - x0) * i / round((x1 - x0) / step) for i in range(round((x1 - x0) / step) + 1)]
    ys = [y0 + (y1 - y0) * i / round((y1 - y0) / step) for i in range(round((y1 - y0) / step) + 1)]
    zs = [z0 + (z1 - z0) * i / round((z1 - z0) / step) for i in range(round((z1 - z0) / step) + 1)]
    for x in xs:
        for z in zs:
            pts += [(x, y0, z), (x, y1, z)]
    for y in ys:
        for z in zs:
            pts += [(x0, y, z), (x1, y, z)]
        for x in xs:
            pts += [(x, y, z0), (x, y, z1)]
    v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, c, d in q for t in ((a, b, c), (a, c, d))]
    return pts, (v, tris)


def tube_surface(cx, cz, r, y0, y1, n=36, step=0.01):
    """A closed cylinder along +Y (a gearbox mouth): surface points and triangles."""
    pts, v, tris = [], [], []
    for k in range(round((y1 - y0) / step) + 1):
        y = y0 + (y1 - y0) * k / round((y1 - y0) / step)
        for i in range(n):
            a = 2 * math.pi * i / n
            pts.append((cx + r * math.cos(a), y, cz + r * math.sin(a)))
    for y in (y0, y1):                       # end discs
        for rr in (r * 0.33, r * 0.66):
            for i in range(n):
                a = 2 * math.pi * i / n
                pts.append((cx + rr * math.cos(a), y, cz + rr * math.sin(a)))
    for y in (y0, y1):
        for i in range(n):
            a = 2 * math.pi * i / n
            v.append((cx + r * math.cos(a), y, cz + r * math.sin(a)))
    v += [(cx, y0, cz), (cx, y1, cz)]
    for i in range(n):
        j = (i + 1) % n
        tris += [(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)]
    return pts, (v, tris)


def joint(gap=0.05, gz=0.20, ez=0.20):
    """Engine block ending at y = 0 (centre height ez), a gearbox mouth starting at y = gap (centre height gz)."""
    ep, eg = box_surface(-0.25, 0.25, -0.6, 0.0, ez - 0.25, ez + 0.25)
    gp, gg = tube_surface(0.0, gz, 0.18, gap, gap + 0.4)
    m = joints.measure(ep, gp, (0, 1))
    return ep, [eg], gp, [gg], m


class TestOutlines(unittest.TestCase):
    def test_hull_and_resample(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5), (0.2, 0.7)]
        h = shapes.hull(sq)
        self.assertEqual(len(h), 4)
        self.assertAlmostEqual(shapes.poly_area(h), 1.0)
        ring = shapes.resample(h, 16)
        self.assertEqual(len(ring), 16)
        self.assertAlmostEqual(ring[0][0], 1.0)            # starts on the +lateral side of the centre
        for q in ring:
            self.assertTrue(shapes.inside(h, q, 1e-9))

    def test_mouth_outline_is_the_gearbox_front(self):
        _ep, _eg, gp, _gg, m = joint()
        mouth = shapes.silhouette(gp, 0 if m['ax'] == 0 else 1, m['gearbox_face'].extreme, m['sign'])
        xs, zs = [p[0] for p in mouth], [p[1] for p in mouth]
        self.assertAlmostEqual(max(xs) - min(xs), 0.36, delta=0.01)
        self.assertAlmostEqual(max(zs) - min(zs), 0.36, delta=0.01)

    def test_clip(self):
        a = [(0, 0), (2, 0), (2, 2), (0, 2)]
        b = [(1, 1), (3, 1), (3, 3), (1, 3)]
        self.assertAlmostEqual(shapes.poly_area(shapes.clip(a, b)), 1.0)


class TestBellhousing(unittest.TestCase):
    def test_open_gap_closed_exactly(self):
        ep, eg, gp, gg, m = joint(gap=0.05)
        pl = shapes.plan(m, ep, gp, (0.0, 0.0), eg, gg)
        self.assertAlmostEqual(pl['gap'], 0.05, delta=0.002)
        self.assertEqual(pl['hits'], (1.0, 1.0))           # every end point sits on its part's surface
        self.assertEqual(pl['coverage'], 1.0)
        for a in pl['a']:                                  # engine end: 1 cm into the engine (its back is y = 0)
            self.assertAlmostEqual(a, -shapes.INTO, delta=1e-6)
        for b in pl['b']:                                  # gearbox end: 1 cm into the gearbox
            self.assertAlmostEqual(b, 0.05 + shapes.INTO, delta=1e-6)
        mesh = shapes.bellhousing(pl)
        self.assertLessEqual(mesh.tri_count(), fitmesh.TRI_MAX['fit'])
        ys = [v[1] for v in mesh.v]
        self.assertGreaterEqual(min(ys), -shapes.INTO - 1e-6)
        self.assertLessEqual(max(ys), 0.05 + shapes.INTO + 1e-6)

    def test_touching_joint_has_no_gap(self):
        ep, eg, gp, gg, m = joint(gap=0.0)
        pl = shapes.plan(m, ep, gp, (0.0, 0.0), eg, gg)
        self.assertLessEqual(pl['gap'], 0.002)

    def test_shift_moves_the_engine_end(self):
        """The fit check moved the engine 10 cm up: the engine end is the mouth outline 10 cm higher."""
        ep, eg, gp, gg, m = joint(gap=0.06, gz=0.10, ez=0.20)
        pl = shapes.plan(m, ep, gp, (0.0, 0.10), eg, gg)
        ce = shapes.area_centre(pl['ring_e'])
        cg = shapes.area_centre(pl['ring_g'])
        self.assertAlmostEqual(ce[1] - cg[1], 0.10, delta=0.005)
        self.assertAlmostEqual(ce[0] - cg[0], 0.0, delta=0.005)
        self.assertEqual(pl['hits'][0], 1.0)

    def test_mouth_not_behind_engine(self):
        ep, eg, gp, gg, m = joint(gap=0.05, gz=1.5)
        pl = shapes.plan(m, ep, gp, (0.0, 0.0), eg, gg)
        self.assertIsNone(pl['gap'])
        self.assertIn('why', pl)

    def test_stray_point_smoothed(self):
        """One end point deeper than its neighbours (a hollow) follows them instead of making a spike."""
        ring = [(math.cos(a), math.sin(a)) for a in [2 * math.pi * i / 32 for i in range(32)]]
        pts = [(q[0], 0.0, q[1]) for q in ring]
        pts[5] = (pts[5][0], -0.05, pts[5][2])            # one point 5 cm deeper
        vals, _hit = shapes._ends(pts, 1, ring, max, 0.0)
        self.assertTrue(all(abs(v) < 1e-9 for v in vals))


class TestPipes(unittest.TestCase):
    def _pipe_pts(self, start, d, length=0.2, r=0.025):
        """Surface points of a straight pipe whose open end is at `start`, pointing along d (out of its end)."""
        d = pipes._norm(d)
        u = pipes._norm(pipes._cross(d, (0, 0, 1)) if abs(d[2]) < 0.9 else pipes._cross(d, (1, 0, 0)))
        v = pipes._cross(d, u)
        out = []
        for k in range(int(length / 0.01) + 1):
            c = pipes._sub(start, pipes._mul(d, k * 0.01))
            for i in range(16):
                a = 2 * math.pi * i / 16
                out.append(pipes._add(c, pipes._add(pipes._mul(u, r * math.cos(a)), pipes._mul(v, r * math.sin(a)))))
        return out

    def test_pipe_end_measured_from_mesh(self):
        pts = self._pipe_pts((0.0, 0.0, 0.0), (0, 1, 0), r=0.03)
        e = pipes.pipe_end(pts, (0.0, 0.5, 0.0))
        self.assertAlmostEqual(e.radius, 0.03, delta=0.004)
        self.assertGreater(e.dir[1], 0.95)
        self.assertLess(math.dist(e.pos, (0.0, 0.0, 0.0)), 0.015)

    def test_route_limits(self):
        out = pipes.End((0.0, 0.0, 0.5), (0.0, 0.0, -1.0), 0.025)     # header pointing down
        inn = pipes.End((0.0, 0.4, 0.1), (0.0, -1.0, 0.0), 0.025)      # car pipe open toward the front
        path, info = pipes.route(out, inn)
        self.assertIsNotNone(path, info)
        self.assertLessEqual(pipes.length(path), pipes.MAX_LEN)
        self.assertGreaterEqual(pipes.min_bend(path), pipes.BEND_MIN * 0.025)
        self.assertEqual(path[0], out.pos)
        self.assertEqual(path[-1], inn.pos)

    def test_route_too_far_and_meets(self):
        out = pipes.End((0, 0, 0), (0, 1, 0), 0.025)
        self.assertEqual(pipes.route(out, pipes.End((0, 0.9, 0), (0, -1, 0), 0.025))[1]['why'], 'too far')
        self.assertEqual(pipes.route(out, pipes.End((0, 0.02, 0), (0, -1, 0), 0.025))[1]['why'], 'meets')

    def test_clearance(self):
        out = pipes.End((0.0, 0.0, 0.5), (0.0, 0.0, -1.0), 0.025)
        inn = pipes.End((0.0, 0.4, 0.1), (0.0, -1.0, 0.0), 0.025)
        free, _ = pipes.route(out, inn)
        # a wall of points across the whole region the pipe could use
        wall = [(x / 50, y / 50, z / 50) for x in range(-10, 11) for y in range(3, 18) for z in range(10, 22)]
        path, info = pipes.route(out, inn, pipes.Obstacles(wall))
        self.assertIsNone(path)
        self.assertEqual(info['why'], 'would clip')
        far = pipes.Obstacles([(2.0, 2.0, 2.0)])
        self.assertIsNotNone(pipes.route(out, inn, far)[0])
        for q in free:
            self.assertGreater(far.nearest(q, 0.05), 0.05)

    def test_tube_mesh(self):
        out = pipes.End((0.0, 0.0, 0.5), (0.0, 0.0, -1.0), 0.025)
        inn = pipes.End((0.0, 0.4, 0.1), (0.0, -1.0, 0.0), 0.03)
        path, _ = pipes.route(out, inn)
        m = pipes.tube(path, 0.025, 0.03)
        self.assertLessEqual(m.tri_count(), fitmesh.TRI_MAX['pipe'])


class TestFitStore(unittest.TestCase):
    def _mesh(self, dy=0.0):
        ep, eg, gp, gg, m = joint(gap=0.05 + dy)
        return shapes.bellhousing(shapes.plan(m, ep, gp, (0.0, 0.0), eg, gg))

    def test_dedupe_and_files(self):
        st = fitmesh.FitStore(log=lambda *a: None)
        a = st.add('/vehicles/bastion/', 'fit', self._mesh(), 'esc_fit_alu')
        b = st.add('/vehicles/bastion/', 'fit', self._mesh(), 'esc_fit_alu')
        c = st.add('/vehicles/bastion/', 'fit', self._mesh(0.02), 'esc_fit_alu')
        d = st.add('/vehicles/bx/', 'fit', self._mesh(), 'esc_fit_alu')
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)                        # another car's folder: its own copy
        self.assertTrue(a.startswith('esc_fit_bastion_'))
        st.run_blender(None)                             # no Blender: flat normals
        files = st.files()
        self.assertIn('vehicles/bastion/esc_fit_bastion.dae', files)
        self.assertIn('vehicles/bx/esc_fit_bx.dae', files)
        self.assertIn('vehicles/common/engine_swap_catalog/meshes/esc_fit.materials.json', files)
        data = files['vehicles/bastion/esc_fit_bastion.dae']
        nodes = dae.scene_nodes(data)
        self.assertEqual(sorted(nodes), sorted([a, c]))
        verts, tris = dae.read_mesh(data, nodes[a])
        self.assertEqual(len(tris), st.tri_count(a))
        ys = [v[1] for v in verts]                       # millimetres in the file, metres after the node matrix
        self.assertAlmostEqual(min(ys), -shapes.INTO, delta=0.002)
        self.assertAlmostEqual(max(ys), 0.05 + shapes.INTO, delta=0.002)
        self.assertIsNone(check_fit_files({'x.jbeam': b''}))
        res = check_fit_files(files)
        self.assertEqual(res['errors'], [])
        self.assertEqual(res['summary']['meshes'], 3)

    def test_validator_catches_limits_and_foreign_materials(self):
        st = fitmesh.FitStore(log=lambda *a: None)
        big = shapes.Mesh()
        for i in range(3100):                            # more triangles than the 3000 limit
            k = big.add((i * 0.001, 0, 0))
            big.add((i * 0.001, 0.01, 0))
            big.add((i * 0.001, 0, 0.01))
            big.tri(k, k + 1, k + 2)
        st.add('/vehicles/bx/', 'fit', big, 'esc_fit_alu')
        st.add('/vehicles/bx/', 'pipe', self._mesh(), 'bx_body')
        st.run_blender(None)
        errs = check_fit_files(st.files())['errors']
        self.assertTrue(any('triangles (limit 3000)' in e for e in errs), errs)
        self.assertTrue(any('bx_body' in e for e in errs), errs)

    def test_choose_material(self):
        look = {'plain': ({'Stages': [{'baseColorFactor': [0.2, 0.3, 0.4, 1], 'metallicFactor': 0.5}]}, 'f'),
                'tex': ({'Stages': [{'baseColorMap': 'x.png'}]}, 'f'),
                'gearbox_cast_dark': ({'Stages': [{'colorMap': 'y.png'}]}, 'f')}
        n, m = fitmesh.choose_material(['plain'], look, 'fit')
        self.assertTrue(n.startswith('esc_fit_'))
        self.assertEqual(m['Stages'][0]['baseColorFactor'], [0.2, 0.3, 0.4, 1])
        self.assertEqual(fitmesh.choose_material(['tex'], look, 'fit'), ('esc_fit_alu', None))
        self.assertEqual(fitmesh.choose_material(['gearbox_cast_dark'], look, 'fit'), ('esc_fit_iron', None))
        self.assertEqual(fitmesh.choose_material(['tex'], look, 'pipe'), ('esc_fit_steel', None))


if __name__ == '__main__':
    unittest.main()
