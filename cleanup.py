"""Blender script (run by Blender, not imported): clean up generated adapter / pipe meshes.

blender -b --factory-startup --python cleanup.py -- <job.json>
job.json: {"items": [{"in": obj path, "out": obj path, "tri_max": int}]}

Per mesh: merge doubles, outward normals, bevel the sharp edges (rounded cast-metal look), weighted normals (smooth
shading that keeps flat faces flat), triangulate, decimate to tri_max if needed, export OBJ (positions + normals,
Z up, same axes as the input). Prints one 'DONE <out> <tris>' line per mesh."""
import bpy, bmesh, json, math, sys

job = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))


def tris_of(o):
    return sum(len(p.vertices) - 2 for p in o.data.polygons)


for it in job['items']:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=it['in'], forward_axis='Y', up_axis='Z')
    o = bpy.context.selected_objects[0]
    bpy.context.view_layer.objects.active = o
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0003)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0002)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(o.data)
    bm.free()
    bev = o.modifiers.new('bevel', 'BEVEL')
    bev.width, bev.segments, bev.limit_method = 0.0015, 1, 'ANGLE'
    bev.angle_limit = math.radians(40)
    bev.harden_normals = False
    tri = o.modifiers.new('tri', 'TRIANGULATE')
    bpy.ops.object.modifier_apply(modifier='bevel')
    bpy.ops.object.modifier_apply(modifier='tri')
    n = tris_of(o)
    if n > it['tri_max']:
        dec = o.modifiers.new('dec', 'DECIMATE')
        dec.ratio = it['tri_max'] / n * 0.98
        bpy.ops.object.modifier_apply(modifier='dec')
        tri = o.modifiers.new('tri', 'TRIANGULATE')
        bpy.ops.object.modifier_apply(modifier='tri')
    wn = o.modifiers.new('wn', 'WEIGHTED_NORMAL')
    wn.keep_sharp, wn.weight = True, 50
    bpy.ops.object.modifier_apply(modifier='wn')
    bpy.ops.wm.obj_export(filepath=it['out'], forward_axis='Y', up_axis='Z', export_selected_objects=False,
                          export_uv=False, export_normals=True, export_materials=False,
                          export_triangulated_mesh=True, apply_modifiers=True)
    print('DONE', it['out'], tris_of(o))
