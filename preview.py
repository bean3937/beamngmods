"""Blender script (run by Blender, not imported): preview pictures of generated adapter / pipe meshes.

blender -b --factory-startup --python preview.py -- <job.json>
job.json: {"items": [{"dir": folder with engine.obj / gearbox.obj / adapter.obj (any of them; extra *.obj are
           context), "title": text, "ax": 0|1, "out": png path}], "size": [w, h]}
Each picture is a 2 x 2 grid: side, top, below (orthographic, framed on the adapter) and a 3/4 view."""
import bpy, json, math, os, sys
import numpy as np
from mathutils import Vector

job = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
W, H = job.get('size', [800, 560])
COLOURS = {'engine': (0.32, 0.42, 0.55, 1), 'gearbox': (0.72, 0.6, 0.38, 1), 'adapter': (0.85, 0.2, 0.15, 1),
           'pipe': (0.85, 0.2, 0.15, 1), 'body': (0.6, 0.6, 0.6, 1), 'exhaust': (0.72, 0.6, 0.38, 1),
           'header': (0.32, 0.42, 0.55, 1)}


def material(name, col):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = col
    return m


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sh = sc.display.shading
    sh.light, sh.color_type = 'STUDIO', 'MATERIAL'
    sh.show_cavity, sh.cavity_type = True, 'BOTH'
    sh.show_object_outline = True
    sh.show_specular_highlight = True
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.film_transparent = False
    sc.world = bpy.data.worlds.new('w')
    sc.world.color = (0.9, 0.9, 0.92)
    sc.render.use_stamp = True
    sc.render.use_stamp_date = sc.render.use_stamp_time = sc.render.use_stamp_render_time = False
    sc.render.use_stamp_frame = sc.render.use_stamp_scene = sc.render.use_stamp_camera = False
    sc.render.use_stamp_filename = sc.render.use_stamp_memory = sc.render.use_stamp_hostname = False
    sc.render.use_stamp_lens = sc.render.use_stamp_marker = sc.render.use_stamp_frame_range = False
    sc.render.use_stamp_note = True
    sh.xray_alpha = 0.55
    sc.render.stamp_font_size = 15
    return sc


def load(d):
    objs = {}
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.obj'):
            continue
        bpy.ops.wm.obj_import(filepath=os.path.join(d, fn), forward_axis='Y', up_axis='Z')
        o = bpy.context.selected_objects[0]
        kind = fn[:-4].split('_')[0]
        o.data.materials.clear()
        o.data.materials.append(material(kind, COLOURS.get(kind, (0.5, 0.5, 0.5, 1))))
        objs[fn[:-4]] = o
    return objs


def bounds(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def camera(sc, loc, look, ortho=None):
    cam = bpy.data.cameras.new('c')
    if ortho:
        cam.type, cam.ortho_scale = 'ORTHO', ortho
    else:
        cam.lens = 40
    cam.clip_end = 100
    o = bpy.data.objects.new('c', cam)
    sc.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(look) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.camera = o
    return o


def render(sc, path):
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
    bpy.data.images.remove(img)
    return px


for it in job['items']:
    sc = reset()
    sc.display.shading.show_xray = bool(it.get('xray'))      # pipes: see them through the body around them
    objs = load(it['dir'])
    focus = [o for k, o in objs.items() if k.startswith(('adapter', 'pipe'))] or list(objs.values())
    lo, hi = bounds(focus)
    c = (lo + hi) / 2
    size = max((hi - lo).length, 0.25)
    ax = it.get('ax', 1)
    lat = Vector((1, 0, 0)) if ax == 1 else Vector((0, 1, 0))
    along = Vector((0, 1, 0)) if ax == 1 else Vector((1, 0, 0))
    d = 5.0
    views = [('side', c + lat * d, size * 1.6, 'Z'), ('top', c + Vector((0, 0, d)), size * 1.6, None),
             ('below', c - Vector((0, 0, d)), size * 1.6, None),
             ('3/4', c + (lat * 0.9 + along * 0.7 + Vector((0, 0, 0.6))).normalized() * size * 2.6, None, None)]
    tiles = []
    tmp = it['out'][:-4] + '_tmp.png'
    for name, loc, ortho, _up in views:
        cam = camera(sc, loc, c, ortho)
        if name == 'top':
            cam.rotation_euler = (0, 0, 0 if ax == 1 else math.pi / 2)
        if name == 'below':
            cam.rotation_euler = (math.pi, 0, 0 if ax == 1 else math.pi / 2)
        sc.render.stamp_note_text = f"{it['title']}  [{name}]"
        tiles.append(render(sc, tmp))
    grid = np.concatenate([np.concatenate([tiles[2], tiles[3]], axis=1), np.concatenate([tiles[0], tiles[1]], axis=1)],
                          axis=0)
    out = bpy.data.images.new('grid', 2 * W, 2 * H)
    out.pixels = grid.ravel()
    out.filepath_raw, out.file_format = it['out'], 'PNG'
    out.save()
    os.remove(tmp)
    print('PREVIEW', it['out'])
