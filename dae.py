"""Minimal Collada (.dae) reader/writer for engine meshes (stdlib only).

BeamNG vehicle .dae files are Collada 1.4, Z up, metres; each mesh is a <node name="..."> with a <matrix>
(row-major, usually a cm->m scale) and an <instance_geometry url="#geom">. Files can be 100+ MB, so we
never parse a whole file: we slice the <library_visual_scenes> block and single <geometry> elements out of
the raw bytes and parse only those.
"""
import math
import re
import xml.etree.ElementTree as ET

_NODE_NAME = re.compile(rb'<node\b[^>]*\bname="([^"]+)"')
_NS = re.compile(rb'\sxmlns(:\w+)?="[^"]*"')


def scan_names(data):
    """All <node name> values in a .dae (cheap regex scan)."""
    return {m.group(1).decode('utf-8', 'replace') for m in _NODE_NAME.finditer(data)}


def _strip_ns(tag):
    return tag.rsplit('}', 1)[-1]


def _block(data, open_tag, close_tag, start=0):
    i = data.find(open_tag, start)
    if i < 0:
        return None
    j = data.find(close_tag, i)
    if j < 0:
        return None
    return data[i:j + len(close_tag)]


def _parse_fragment(frag):
    frag = _NS.sub(b'', frag)
    return ET.fromstring(frag)


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


IDENTITY = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _node_matrix(el):
    m = IDENTITY
    for c in el:
        t = _strip_ns(c.tag)
        vals = [float(x) for x in (c.text or '').split()]
        if t == 'matrix' and len(vals) == 16:
            m = mat_mul(m, [vals[0:4], vals[4:8], vals[8:12], vals[12:16]])
        elif t == 'translate' and len(vals) == 3:
            m = mat_mul(m, [[1, 0, 0, vals[0]], [0, 1, 0, vals[1]], [0, 0, 1, vals[2]], [0, 0, 0, 1]])
        elif t == 'scale' and len(vals) == 3:
            m = mat_mul(m, [[vals[0], 0, 0, 0], [0, vals[1], 0, 0], [0, 0, vals[2], 0], [0, 0, 0, 1]])
        elif t == 'rotate' and len(vals) == 4:
            x, y, z, ang = vals
            n = math.sqrt(x * x + y * y + z * z) or 1.0
            x, y, z = x / n, y / n, z / n
            c_, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            C = 1 - c_
            m = mat_mul(m, [[c_ + x * x * C, x * y * C - z * s_, x * z * C + y * s_, 0],
                            [y * x * C + z * s_, c_ + y * y * C, y * z * C - x * s_, 0],
                            [z * x * C - y * s_, z * y * C + x * s_, c_ + z * z * C, 0], [0, 0, 0, 1]])
    return m


def scene_nodes(data):
    """{node name: {'matrix': 4x4 world matrix, 'geometry': id, 'materials': [(symbol, target_id)]}}"""
    blk = _block(data, b'<library_visual_scenes', b'</library_visual_scenes>')
    if blk is None:
        return {}
    root = _parse_fragment(blk)
    out = {}

    def walk(el, parent):
        for c in el:
            if _strip_ns(c.tag) != 'node':
                continue
            m = mat_mul(parent, _node_matrix(c))
            ig = next((g for g in c if _strip_ns(g.tag) == 'instance_geometry'), None)
            if ig is not None and c.get('name'):
                mats = [(im.get('symbol'), (im.get('target') or '').lstrip('#'))
                        for im in ig.iter() if _strip_ns(im.tag) == 'instance_material']
                out.setdefault(c.get('name'), {'matrix': m, 'geometry': (ig.get('url') or '').lstrip('#'),
                                               'materials': mats})
            walk(c, m)
    for vs in root:
        if _strip_ns(vs.tag) == 'visual_scene':
            walk(vs, IDENTITY)
    return out


def geometry_bytes(data, gid):
    return _block(data, b'<geometry id="' + gid.encode() + b'"', b'</geometry>')


def read_mesh(data, node):
    """World-space vertices and triangles (index triples) of one scene node."""
    raw = geometry_bytes(data, node['geometry'])
    if raw is None:
        return [], []
    g = _parse_fragment(raw)
    mesh = next((c for c in g if _strip_ns(c.tag) == 'mesh'), None)
    if mesh is None:
        return [], []
    sources = {}
    for s in mesh:
        if _strip_ns(s.tag) == 'source':
            fa = next((c for c in s if _strip_ns(c.tag) == 'float_array'), None)
            if fa is not None and fa.text:
                sources[s.get('id')] = [float(x) for x in fa.text.split()]
    pos_id = None
    for v in mesh:
        if _strip_ns(v.tag) == 'vertices':
            for inp in v:
                if inp.get('semantic') == 'POSITION':
                    pos_id = (inp.get('source') or '').lstrip('#')
    flat = sources.get(pos_id, [])
    m = node['matrix']
    verts = []
    for i in range(0, len(flat) - 2, 3):
        x, y, z = flat[i], flat[i + 1], flat[i + 2]
        verts.append((m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
                      m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
                      m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3]))
    tris = []
    for prim in mesh:
        t = _strip_ns(prim.tag)
        if t not in ('triangles', 'polylist', 'polygons'):
            continue
        inputs = [c for c in prim if _strip_ns(c.tag) == 'input']
        stride = max((int(c.get('offset', 0)) for c in inputs), default=0) + 1
        voff = next((int(c.get('offset', 0)) for c in inputs if c.get('semantic') == 'VERTEX'), 0)
        ps = [c for c in prim if _strip_ns(c.tag) == 'p']
        if t == 'polygons':
            polys = [[int(x) for x in (p.text or '').split()][voff::stride] for p in ps]
        else:
            idx = [int(x) for x in (ps[0].text or '').split()] if ps else []
            vi = idx[voff::stride]
            if t == 'triangles':
                polys = [vi[i:i + 3] for i in range(0, len(vi) - 2, 3)]
            else:
                vc = next((c for c in prim if _strip_ns(c.tag) == 'vcount'), None)
                counts = [int(x) for x in (vc.text or '').split()] if vc is not None else []
                polys, k = [], 0
                for n in counts:
                    polys.append(vi[k:k + n])
                    k += n
        for poly in polys:
            for j in range(1, len(poly) - 1):     # fan triangulation
                tris.append((poly[0], poly[j], poly[j + 1]))
    return verts, tris


def sample_surface(verts, tris, spacing=0.03, cap=40):
    """Points on the mesh surface, at most `spacing` apart along each triangle (plus all vertices)."""
    pts = list(verts)
    n = len(verts)
    for a, b, c in tris:
        if a >= n or b >= n or c >= n:
            continue
        A, B, C = verts[a], verts[b], verts[c]
        e = max(math.dist(A, B), math.dist(B, C), math.dist(C, A))
        k = min(cap, int(e / spacing))
        if k < 2:
            continue
        for i in range(k + 1):
            for j in range(k + 1 - i):
                u, v = i / k, j / k
                w = 1 - u - v
                pts.append((A[0] * w + B[0] * u + C[0] * v, A[1] * w + B[1] * u + C[1] * v,
                            A[2] * w + B[2] * u + C[2] * v))
    return pts


# ---------------------------------------------------------------- copying meshes into a new .dae

def library_items(data, lib, item):
    """{id: (name, raw bytes)} of <item> elements inside <library_lib> (materials/effects/images)."""
    blk = _block(data, b'<library_' + lib.encode(), b'</library_' + lib.encode() + b'>')
    out = {}
    if blk is None:
        return out
    for m in re.finditer(rb'<' + item.encode() + rb'\b[^>]*\bid="([^"]+)"[^>]*>', blk):
        start = m.start()
        end = blk.find(b'</' + item.encode() + b'>', start)
        if end < 0:
            continue
        raw = blk[start:end + len(item) + 3]
        nm = re.search(rb'\bname="([^"]*)"', m.group(0))
        out[m.group(1).decode()] = ((nm.group(1).decode() if nm else m.group(1).decode()), raw)
    return out


def write_dae(meshes):
    """meshes: list of dicts {name, geometry_raw (bytes), matrix, materials [(symbol, target_id)],
    material_raw {id: bytes}, effect_raw {id: bytes}, image_raw {id: bytes}} -> bytes of a Collada file.
    All ids must already be unique (callers prefix them)."""
    mats, effs, imgs, geoms, nodes = {}, {}, {}, [], []
    for m in meshes:
        geoms.append(m['geometry_raw'])
        mats.update(m['material_raw'])
        effs.update(m['effect_raw'])
        imgs.update(m['image_raw'])
        mx = ' '.join(repr(float(v)) for row in m['matrix'] for v in row)
        bind = ''.join(f'<instance_material symbol="{s}" target="#{t}"/>' for s, t in m['materials'])
        nodes.append(f'<node id="{m["name"]}" name="{m["name"]}" type="NODE"><matrix sid="transform">{mx}</matrix>'
                     f'<instance_geometry url="#{m["geometry_id"]}"><bind_material><technique_common>{bind}'
                     f'</technique_common></bind_material></instance_geometry></node>')
    out = [b'<?xml version="1.0" encoding="utf-8"?>\n'
           b'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
           b'<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>']
    out.append(b'<library_images>' + b''.join(imgs.values()) + b'</library_images>')
    out.append(b'<library_effects>' + b''.join(effs.values()) + b'</library_effects>')
    out.append(b'<library_materials>' + b''.join(mats.values()) + b'</library_materials>')
    out.append(b'<library_geometries>' + b''.join(geoms) + b'</library_geometries>')
    out.append(b'<library_visual_scenes><visual_scene id="Scene" name="Scene">' + ''.join(nodes).encode()
               + b'</visual_scene></library_visual_scenes><scene><instance_visual_scene url="#Scene"/></scene></COLLADA>')
    return b''.join(out)


def rename_ids(raw, prefix):
    """Prefix every id="x" and every reference "#x" inside one element's bytes."""
    raw = re.sub(rb'\bid="([^"]+)"', lambda m: b'id="' + prefix + m.group(1) + b'"', raw)
    raw = re.sub(rb'"#([^"]+)"', lambda m: b'"#' + prefix + m.group(1) + b'"', raw)
    return raw
