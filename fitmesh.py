"""Phase 4: generated fit meshes (bellhousing adapters, bent exhaust pipes) - collected per car, cleaned up in
Blender, written as one Collada file per car into that car's own folder (a car loads meshes from its own folder and
vehicles/common - NOTES.md section 9), materials in vehicles/common.

Blender 5.x has no Collada exporter (checked: wm.collada_export is gone in 5.2), so Blender reads and writes OBJ and
the .dae is written here. Without Blender (unit tests) the raw mesh is used with flat normals."""
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile

from . import dae

MAT_DIR = 'vehicles/common/engine_swap_catalog/meshes/'
TRI_MAX = {'fit': 2400, 'pipe': 1200}      # prompt limits: 3000 / 1500
BLENDER_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blender')


def _pbr(name, colour, metal, rough):
    # written like vanilla's texture-free PBR materials (e.g. bastion_gauges_break, version 1.5)
    return {'name': name, 'mapTo': name, 'class': 'Material', 'version': 1.5,
            'Stages': [{'baseColorFactor': list(colour) + [1], 'metallicFactor': metal, 'roughnessFactor': rough},
                       {}, {}, {}],
            'doubleSided': True, 'dynamicCubemap': True, 'translucentBlendOp': 'None'}


BASE_MATERIALS = {
    'esc_fit_alu': _pbr('esc_fit_alu', (0.52, 0.52, 0.53), 0.8, 0.55),       # cast aluminium bellhousing
    'esc_fit_iron': _pbr('esc_fit_iron', (0.11, 0.11, 0.115), 0.7, 0.6),     # dark cast iron
    'esc_fit_steel': _pbr('esc_fit_steel', (0.33, 0.31, 0.29), 0.85, 0.5),   # exhaust steel
}
DARK = ('iron', 'cast', 'black', 'dark')


def choose_material(names, lookup, kind):
    """(material name, material dict or None for a base one) for a generated piece that should look like a car
    part using `names` (its Collada material names). lookup: mapTo -> (material dict, file) (Meshes.materials_index).
    A texture-free car material is copied (colour / metal / roughness); otherwise the closest base material."""
    for n in names:
        m = (lookup.get(n) or (None,))[0]
        if not isinstance(m, dict):
            continue
        st = (m.get('Stages') or [{}])[0] or {}
        if any(k for k in st if k.lower().endswith('map')):
            continue
        col = st.get('baseColorFactor') or st.get('diffuseColor')
        if isinstance(col, list) and len(col) >= 3 and all(isinstance(v, (int, float)) for v in col[:3]):
            nm = 'esc_fit_' + hashlib.sha1(n.encode()).hexdigest()[:10]
            return nm, _pbr(nm, col[:3], float(st.get('metallicFactor', 0.8)), float(st.get('roughnessFactor', 0.5)))
    low = ' '.join(names).lower()
    if kind == 'pipe':
        return 'esc_fit_steel', None
    return ('esc_fit_iron' if any(w in low for w in DARK) else 'esc_fit_alu'), None


class FitStore:
    """Unique generated meshes, keyed by their rounded geometry, per car."""

    def __init__(self, log=print):
        self.log = log
        self.meshes = {}       # name -> {'kind', 'raw' (shapes.Mesh), 'car', 'material'}
        self.materials = dict(BASE_MATERIALS)
        self.clean = {}        # name -> (verts, normals, tris) after the Blender pass
        self.blender_used = False

    def add(self, car_ns, kind, mesh, material):
        """Register a mesh (shapes.Mesh, car coordinates) for car_ns; returns its name (same geometry = same name)."""
        h = hashlib.sha1(repr((kind, material, [tuple(round(c, 4) for c in v) for v in mesh.v], mesh.f)).encode())
        car = car_ns.strip('/').split('/')[-1]
        name = f'esc_{kind}_{car}_{h.hexdigest()[:12]}'
        if name not in self.meshes:
            self.meshes[name] = {'kind': kind, 'raw': mesh, 'car': car_ns, 'material': material}
        return name

    # ------------------------------------------------------------ Blender pass
    def run_blender(self, blender, workers=None):
        """Clean every mesh in Blender (bevel, weighted normals, triangle limit). blender: path to blender.exe or
        None (raw meshes, flat normals)."""
        if not self.meshes:
            return
        if not blender or not os.path.isfile(blender):
            self.log(f'Fit meshes: Blender not found ({blender}): using raw meshes with flat normals')
            for n, m in self.meshes.items():
                self.clean[n] = flat(m['raw'])
            return
        work = tempfile.mkdtemp(prefix='esc_fit_')
        try:
            names = sorted(self.meshes)
            workers = workers or max(1, min(8, (os.cpu_count() or 2) // 2))
            jobs = [names[i::workers] for i in range(workers)]
            procs = []
            for k, chunk in enumerate(jobs):
                if not chunk:
                    continue
                items = []
                for n in chunk:
                    src = os.path.join(work, n + '.raw.obj')
                    with open(src, 'w') as fh:
                        fh.write(obj_text(self.meshes[n]['raw'], n))
                    items.append({'in': src, 'out': os.path.join(work, n + '.obj'),
                                  'tri_max': TRI_MAX[self.meshes[n]['kind']]})
                jp = os.path.join(work, f'job{k}.json')
                with open(jp, 'w') as fh:
                    json.dump({'items': items}, fh)
                procs.append(subprocess.Popen([blender, '-b', '--factory-startup', '--python',
                                               os.path.join(BLENDER_SCRIPTS, 'cleanup.py'), '--', jp],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            self.log(f'Fit meshes: Blender cleanup of {len(names)} meshes in {len(procs)} processes...')
            for p in procs:
                p.wait()
            bad = 0
            for n in names:
                path = os.path.join(work, n + '.obj')
                if os.path.isfile(path):
                    self.clean[n] = read_obj(path)
                else:
                    bad += 1
                    self.clean[n] = flat(self.meshes[n]['raw'])
            if bad:
                self.log(f'Fit meshes: {bad} meshes not returned by Blender (raw mesh used)')
            self.blender_used = True
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------ output
    def files(self):
        """{zip path: bytes}: one .dae per car (in its own folder) + our materials.json in common."""
        by_car = {}
        for n in sorted(self.meshes):
            by_car.setdefault(self.meshes[n]['car'], []).append(n)
        out = {}
        for ns, names in by_car.items():
            car = ns.strip('/').split('/')[-1]
            out[f'{ns.strip("/")}/esc_fit_{car}.dae'] = self.dae(names)
        from . import jbeam
        out[f'{MAT_DIR}esc_fit.materials.json'] = jbeam.dumps(self.materials).encode('utf-8')
        return out

    def dae(self, names):
        items = []
        for n in names:
            v, nr, t = self.clean.get(n) or flat(self.meshes[n]['raw'])
            mat = self.meshes[n]['material']
            gid = n + '-mesh'
            items.append({'name': n, 'geometry_raw': geometry(gid, v, nr, t, mat), 'geometry_id': gid,
                          'matrix': [[0.001, 0, 0, 0], [0, 0.001, 0, 0], [0, 0, 0.001, 0], [0, 0, 0, 1]],
                          'materials': [(f'{mat}-material', f'{mat}-material')],
                          'material_raw': {mat: f'<material id="{mat}-material" name="{mat}">'
                                                f'<instance_effect url="#{mat}-fx"/></material>'.encode()},
                          'effect_raw': {mat: f'<effect id="{mat}-fx"><profile_COMMON><technique sid="common">'
                                              f'<lambert/></technique></profile_COMMON></effect>'.encode()},
                          'image_raw': {}})
        return dae.write_dae(items)

    def tri_count(self, name):
        return len((self.clean.get(name) or flat(self.meshes[name]['raw']))[2])


def obj_text(mesh, name):
    lines = [f'o {name}'] + [f'v {x:.5f} {y:.5f} {z:.5f}' for x, y, z in mesh.v]
    lines += ['f ' + ' '.join(str(i + 1) for i in f) for f in mesh.f]
    return '\n'.join(lines) + '\n'


def read_obj(path):
    """(verts, normals, tris [(v, n) x 3]) of an OBJ with v / vn / 'f a//na ...' lines (Blender's export)."""
    vs, ns, tris = [], [], []
    with open(path) as fh:
        for line in fh:
            if line.startswith('v '):
                vs.append(tuple(float(x) for x in line.split()[1:4]))
            elif line.startswith('vn '):
                ns.append(tuple(float(x) for x in line.split()[1:4]))
            elif line.startswith('f '):
                c = []
                for tok in line.split()[1:]:
                    p = tok.split('/')
                    c.append((int(p[0]) - 1, int(p[2]) - 1 if len(p) > 2 and p[2] else 0))
                for j in range(1, len(c) - 1):
                    tris.append((c[0], c[j], c[j + 1]))
    return vs, ns or [(0.0, 0.0, 1.0)], tris


def flat(mesh):
    """(verts, normals, tris) with one normal per face (no Blender)."""
    ns, tris = [], []
    for f in mesh.f:
        a, b, c = (mesh.v[i] for i in f[:3])
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        d = math.sqrt(sum(x * x for x in n)) or 1.0
        ns.append((n[0] / d, n[1] / d, n[2] / d))
        k = len(ns) - 1
        for j in range(1, len(f) - 1):
            tris.append(((f[0], k), (f[j], k), (f[j + 1], k)))
    return list(mesh.v), ns, tris


def geometry(gid, verts, normals, tris, mat):
    """Collada <geometry>, compact: positions in whole millimetres (the node matrix scales them to metres, as
    vanilla's cm matrices do), normals to 3 decimals (equal ones merged), one constant UV (texture-free material).
    Index layout as in vanilla files: VERTEX / NORMAL / TEXCOORD at offsets 0 / 1 / 2."""
    nkey, ns, nmap = {}, [], []
    for n in normals:
        k = tuple(round(c, 3) for c in n)
        if k not in nkey:
            nkey[k] = len(ns)
            ns.append(k)
        nmap.append(nkey[k])
    pos = ' '.join(str(int(round(c * 1000))) for v in verts for c in v)
    nrm = ' '.join(f'{c:g}' for v in ns for c in v)
    idx = ' '.join(f'{vi} {nmap[ni]} 0' for t in tris for vi, ni in t)

    def src(name, vals, count, stride, params):
        p = ''.join(f'<param name="{x}" type="float"/>' for x in params)
        return (f'<source id="{gid}-{name}"><float_array id="{gid}-{name}-array" count="{count * stride}">{vals}'
                f'</float_array><technique_common><accessor source="#{gid}-{name}-array" count="{count}" '
                f'stride="{stride}">{p}</accessor></technique_common></source>')
    return (f'<geometry id="{gid}" name="{gid}"><mesh>' + src('positions', pos, len(verts), 3, 'XYZ')
            + src('normals', nrm, len(ns), 3, 'XYZ') + src('map', '0 0', 1, 2, 'ST')
            + f'<vertices id="{gid}-vertices"><input semantic="POSITION" source="#{gid}-positions"/></vertices>'
            f'<triangles material="{mat}-material" count="{len(tris)}">'
            f'<input semantic="VERTEX" source="#{gid}-vertices" offset="0"/>'
            f'<input semantic="NORMAL" source="#{gid}-normals" offset="1"/>'
            f'<input semantic="TEXCOORD" source="#{gid}-map" offset="2" set="0"/><p>{idx}</p></triangles>'
            f'</mesh></geometry>').encode()
