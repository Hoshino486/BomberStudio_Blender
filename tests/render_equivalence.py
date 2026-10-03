"""Compare actual Cycles renders before/after atlas creation (not only pixel buffers)."""
import bpy
import json
import os
import sys
import numpy as np
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bomberstudio_blender as addon
from bomberstudio_blender import compat as C, core, atlas, materials
addon.register()
OUT = os.path.join(ROOT, '临时文件夹', 'render-verification')
os.makedirs(OUT, exist_ok=True)
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
cfg = C.settings()
cfg.temp_dir, cfg.output_dir = OUT, OUT
cfg.atlas_pbr, cfg.atlas_uniform, cfg.atlas_keep_originals = True, True, True
cfg.atlas_tile_size, cfg.atlas_solid_size, cfg.atlas_padding = 64, 32, 4
mesh = bpy.data.meshes.new('ReferencePanels')
mesh.from_pydata([(-1,-1,0), (0,-1,0), (0,1,0), (-1,1,0), (0,-1,0), (1,-1,0), (1,1,0), (0,1,0)],
                [], [(0,1,2,3), (4,5,6,7)])
mesh.update()
obj = bpy.data.objects.new('ReferencePanels', mesh)
C.link(obj)
uv = C.new_uv(mesh, 'UV0')
for p in mesh.polygons:
    for i, li in enumerate(p.loop_indices):
        uv.data[li].uv = [(0,0),(1,0),(1,1),(0,1)][i]
for i, color in enumerate([(.18,.4,.7,1), (.6,.08,.2,1)]):
    mat = bpy.data.materials.new('LinearColor_' + str(i))
    bsdf = C.principled(mat)
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Roughness'].default_value = .75
    bsdf.inputs['Metallic'].default_value = .15
    if i == 1:
        image = bpy.data.images.new('SourceTexture', width=16, height=16, alpha=True)
        values = np.empty((16,16,4), np.float32)
        values[:] = (.7,.3,.4,1)
        values[8:,8:] = (.2,.8,.4,1)
        image.pixels.foreach_set(values.ravel())
        image.filepath_raw, image.file_format = os.path.join(OUT,'source.png'), 'PNG'
        image.save()
        materials.attach_texture(mat, image.filepath_raw, uv='UV0')
    mesh.materials.append(mat)
    mesh.polygons[i].material_index = i
    row = cfg.atlas_materials.add()
    row.material, row.enabled = mat, True
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.cycles.seed = 13
scene.render.resolution_x = 256
scene.render.resolution_y = 256
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
scene.world = bpy.data.worlds.new('World')
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.5,.5,.5,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value = .8
light_data = bpy.data.lights.new('Key', 'AREA')
light = bpy.data.objects.new('Key', light_data)
C.link(light)
light.location = (0,0,3)
light_data.energy, light_data.size = 50, 3
camera_data = bpy.data.cameras.new('Camera')
camera = bpy.data.objects.new('Camera', camera_data)
C.link(camera)
camera.location = (0,0,4)
camera.rotation_euler = (0,0,0)
camera_data.type, camera_data.ortho_scale = 'ORTHO', 2.4
scene.camera = camera


def render(leaf):
    path = os.path.join(OUT, leaf)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return atlas.pixels(bpy.data.images.load(path, check_existing=False))


before = render('before.png')
C.select_only([obj])
manifest = atlas.create_atlas(bpy.context)
after = render('after.png')
# Avoid silhouette antialiasing and the source texture's interpolated quadrant seam.
mask = np.zeros(before.shape[:2], bool)
mask[40:110, 40:110] = True
mask[40:100, 150:205] = True
error = np.abs(before[mask,:3]-after[mask,:3])
report = {'blender': bpy.app.version_string, 'mean_error': float(error.mean()),
          'max_error': float(error.max()), 'threshold': .015,
          'samples': int(mask.sum()), 'atlas': manifest}
report['passed'] = report['mean_error'] < .005 and report['max_error'] < .015
core.atomic_json(os.path.join(OUT,'render-comparison.json'), report)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT,'render-comparison.blend'))
print('RENDER_EQUIVALENCE', json.dumps(report), flush=True)
assert report['passed'], 'Atlas shading changed; see before.png / after.png'
