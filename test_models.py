"""Engine-model maths, model-file reading and fit check."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swapcat import dae, placement as P  # noqa: E402
from swapcat.visuals import eval_num      # noqa: E402


def box_points(lo, hi, step=0.02):
    pts = []
    nx, ny, nz = (max(1, int(round((hi[i] - lo[i]) / step))) for i in range(3))
    for i in range(nx + 1):
        for j in range(ny + 1):
            for k in range(nz + 1):
                if i in (0, nx) or j in (0, ny) or k in (0, nz):
                    pts.append((lo[0] + (hi[0] - lo[0]) * i / nx, lo[1] + (hi[1] - lo[1]) * j / ny,
                                lo[2] + (hi[2] - lo[2]) * k / nz))
    return pts


def box_geometry(gid, lo, hi, material='engine_mat'):
    """Collada <geometry> for an axis-aligned box (cm units, as BeamNG exports)."""
    c = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    pos = ' '.join(f'{v * 100:g}' for p in c for v in p)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, cc, d in quads for t in ((a, b, cc), (a, cc, d))]
    p = ' '.join(str(i) for t in tris for i in t)
    return (f'<geometry id="{gid}" name="{gid}"><mesh><source id="{gid}-positions"><float_array id="{gid}-positions-array" '
            f'count="24">{pos}</float_array></source><vertices id="{gid}-vertices"><input semantic="POSITION" '
            f'source="#{gid}-positions"/></vertices><triangles material="{material}-material" count="{len(tris)}">'
            f'<input semantic="VERTEX" source="#{gid}-vertices" offset="0"/><p>{p}</p></triangles></mesh></geometry>').encode()


def box_dae(meshes):
    """meshes: {mesh name: (lo, hi)} -> .dae bytes using the package's own writer (node matrix cm->m)."""
    items = []
    for name, (lo, hi) in meshes.items():
        items.append({'name': name, 'geometry_raw': box_geometry(name + '-mesh', lo, hi), 'geometry_id': name + '-mesh',
                      'matrix': [[0.01, 0, 0, 0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 1]],
                      'materials': [('engine_mat-material', 'engine_mat-material')],
                      'material_raw': {'m': b'<material id="engine_mat-material" name="engine_mat">'
                                            b'<instance_effect url="#fx"/></material>'},
                      'effect_raw': {'fx': b'<effect id="fx"><profile_COMMON/></effect>'}, 'image_raw': {}})
    return dae.write_dae(items)


class TestLuaPorts(unittest.TestCase):
    def test_rot_param_reproduces_rotation(self):
        for deg in (90, -90, 180, 45):
            got = P.flexbody_apply((1.0, 0.0, 0.0), rot=(0, 0, P.rot_param_for(deg)))
            want = P.rotz((1.0, 0.0, 0.0), deg)
            self.assertLess(math.dist(got, want), 1e-9, deg)

    def test_flexbody_pos_is_added_after_rotation(self):
        got = P.flexbody_apply((1.0, 0.0, 0.0), pos=(0, 0, 2), rot=(0, 0, P.rot_param_for(90)))
        self.assertLess(math.dist(got, (0.0, 1.0, 2.0)), 1e-9)

    def test_quat_identity(self):
        self.assertEqual(P.quat_from_euler(0, 0, 0), (0.0, 0.0, 0.0, 1.0))


class TestPlacement(unittest.TestCase):
    def test_same_engine_is_identity(self):
        pts = box_points((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9))
        deg, t = P.place(pts, (0, 1), pts, (0, 1))
        self.assertEqual(deg, 0)
        self.assertLess(max(abs(v) for v in t), 1e-9)

    def test_known_rotation_and_offset(self):
        # transverse donor (gearbox to -x) into a longitudinal car (gearbox behind, +y)
        donor = box_points((-0.2, -1.5, 0.2), (0.4, -1.1, 0.7))
        deg, t = P.place(donor, (-1, 0), box_points((-0.25, -1.7, 0.3), (0.25, -1.1, 0.9)), (0, 1))
        self.assertEqual(deg, 270)
        placed = P.transform_points(donor, deg, t)
        lo, hi = P.box(placed)
        self.assertAlmostEqual(hi[1], -1.1, places=6)                     # gearbox faces meet
        self.assertAlmostEqual((lo[0] + hi[0]) / 2, 0.0, places=6)        # centred
        self.assertAlmostEqual(lo[2], 0.3, places=6)                      # bottoms aligned
        self.assertAlmostEqual(hi[1] - lo[1], 0.6, places=6)              # long side now along y

    def test_compose_with_row_transform(self):
        deg, pos = P.compose(90, (1.0, 2.0, 3.0), row_pos=(0.5, 0, 0), row_rotz=0)
        self.assertEqual(deg, 90)
        self.assertLess(math.dist(pos, (1.0, 2.5, 3.0)), 1e-9)

    def test_snap_and_angle(self):
        self.assertEqual(P.snap_dir(0.1, 0.9), (0, 1))
        self.assertEqual(P.snap_dir(-0.8, 0.1), (-1, 0))
        self.assertEqual(P.angle_between((0, 1), (0, 1)), 0)
        self.assertEqual(P.angle_between((1, 0), (0, 1)), 90)
        v = P.rotz((1, 0, 0), P.angle_between((1, 0), (0, 1)))
        self.assertLess(math.dist(v, (0, 1, 0)), 1e-9)


class TestFit(unittest.TestCase):
    def wall(self, a, b, c, d, label='body'):
        cells = P.tri_voxels(a, b, c) | P.tri_voxels(a, c, d)
        return {x: label for x in cells}

    def test_triangle_intersection(self):
        firewall = self.wall((-1, -0.9, 0), (1, -0.9, 0), (1, -0.9, 1.5), (-1, -0.9, 1.5))
        inside = P.voxels(box_points((-0.3, -1.6, 0.3), (0.3, -1.05, 0.9)))
        through = P.voxels(box_points((-0.3, -1.6, 0.3), (0.3, -0.7, 0.9)))
        self.assertEqual(P.hits(inside, firewall), {})
        self.assertGreater(P.hits(through, firewall).get('body', 0), 10)

    def test_fit_verdicts(self):
        floor = self.wall((-1, -2, 0.18), (1, -2, 0.18), (1, 0, 0.18), (-1, 0, 0.18), 'undertray')
        pts = box_points((-0.3, -1.6, 0.25), (0.3, -1.0, 0.9))
        self.assertEqual(P.fit(pts, 0, (0, 0, 0), floor, (0, -1))[0], 'fits')
        verdict, off, first = P.fit(pts, 0, (0, 0, -0.1), floor, (0, -1))   # pokes out of the bottom
        self.assertEqual(verdict, 'fits_after_move')
        self.assertGreater(off[2], 0)                                         # moved up
        self.assertIn('undertray', first)
        cage = self.wall((-1, -2, 0.18), (1, -2, 0.18), (1, 0, 0.18), (-1, 0, 0.18))
        for z in (0.3, 0.5, 0.7, 0.95):          # a wall too high to escape by moving 12 cm up
            cage.update(self.wall((-1, -2, z), (1, -2, z), (1, 0, z), (-1, 0, z)))
        self.assertEqual(P.fit(pts, 0, (0, 0, 0), cage, (0, -1))[0], 'does_not_fit')


class TestDae(unittest.TestCase):
    def test_read_written_box(self):
        raw = box_dae({'engine_block': ((-0.3, -1.6, 0.3), (0.3, -1.0, 0.9))})
        self.assertIn('engine_block', dae.scan_names(raw))
        nodes = dae.scene_nodes(raw)
        verts, tris = dae.read_mesh(raw, nodes['engine_block'])
        lo, hi = P.box(verts)
        self.assertLess(math.dist(lo, (-0.3, -1.6, 0.3)), 1e-6)            # cm -> m via node matrix
        self.assertLess(math.dist(hi, (0.3, -1.0, 0.9)), 1e-6)
        self.assertEqual(len(tris), 12)
        pts = dae.sample_surface(verts, tris, spacing=0.05)
        self.assertGreater(len(pts), 100)
        mats = dae.library_items(raw, 'materials', 'material')
        self.assertEqual(mats['engine_mat-material'][0], 'engine_mat')

    def test_rename_ids(self):
        raw = b'<geometry id="g"><source id="g-p"/><input source="#g-p"/></geometry>'
        self.assertEqual(dae.rename_ids(raw, b'x_'), b'<geometry id="x_g"><source id="x_g-p"/><input source="#x_g-p"/></geometry>')


class TestExpressions(unittest.TestCase):
    def test_eval_num(self):
        v = {'$a': 2.0, '$b': 0.5}
        self.assertEqual(eval_num(1, v), 1.0)
        self.assertEqual(eval_num('$a', v), 2.0)
        self.assertEqual(eval_num('$=$a*$b + 1', v), 2.0)
        self.assertEqual(eval_num('$=-($a)', v), -2.0)
        self.assertIsNone(eval_num('$missing', v))
        self.assertIsNone(eval_num('$=__import__("os")', v))


if __name__ == '__main__':
    unittest.main()
