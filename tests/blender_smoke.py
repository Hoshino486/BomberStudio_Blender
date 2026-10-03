"""Real Blender regression. --python-exit-code 1 --python this.py -- OUTPUT_DIR"""
import bpy
import json
import math
import os
import shutil
import struct
import sys
import time
import traceback
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bomberstudio_blender as addon
from bomberstudio_blender import compat as C, core, bridge, materials, model_ops, rig_ops, atlas, ui

OUT = sys.argv[sys.argv.index('--')+1] if '--' in sys.argv else os.path.join(ROOT, '临时文件夹', 'smoke')
os.makedirs(OUT, exist_ok=True)
results = []
addon.register()


def case(name):
    def decorate(fn):
        print('START', name, flush=True)
        start = time.time()
        try:
            fn()
            results.append({'name': name, 'status': 'PASS', 'seconds': time.time()-start})
            print('PASS', name, flush=True)
        except Exception as exc:
            results.append({'name': name, 'status': 'FAIL', 'error': traceback.format_exc(), 'seconds': time.time()-start})
            traceback.print_exc()
        core.atomic_json(os.path.join(OUT, 'checkpoint.json'), results)
        return fn
    return decorate


def reset():
    C.mode_object()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    cfg = C.settings()
    cfg.temp_dir = OUT
    cfg.output_dir = OUT
    cfg.backup_before_edit = False
    cfg.atlas_materials.clear()
    return cfg


def mesh_object(name='Mesh', two_faces=False, keys=False):
    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    faces = [(0, 1, 2)]
    if two_faces:
        verts += [(2, 0, 0), (3, 0, 0), (2, 1, 0)]
        faces += [(3, 4, 5)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    C.link(obj)
    uv = C.new_uv(mesh, 'UV0')
    for p in mesh.polygons:
        for i, li in enumerate(p.loop_indices):
            uv.data[li].uv = [(0, 0), (1, 0), (0, 1)][i]
    C.select_only([obj])
    if keys:
        obj.shape_key_add(name='Basis')
        for name, delta in (('AA', (0, 0, 1)), ('OH', (0, .5, 0)), ('CH', (.5, 0, 0))):
            key = obj.shape_key_add(name=name)
            key.data[0].co += Vector(delta)
    return obj


def image_file(name, color):
    image = bpy.data.images.new(name, width=8, height=8, alpha=True)
    image.pixels[:] = list(color)*64
    path = os.path.join(OUT, name + '.png')
    image.filepath_raw, image.file_format = path, 'PNG'
    image.save()
    return path


@case('register_unregister_reregister')
def _():
    addon.unregister()
    addon.register()
    assert len(addon._registered) >= 29
    assert hasattr(bpy.types.Scene, 'bomberstudio')


@case('all_panels_draw_no_missing_properties')
def _():
    reset()
    class Layout:
        def row(self, *a, **k): return self
        column = box = split = row
        def operator(self, name, **kw):
            group, op = name.split('.')
            assert C.op_available(group, op), name
            return type('Button', (), {})()
        def label(self, *a, **k): pass
        def separator(self, *a, **k): pass
        def prop(self, data, name, **kw): assert hasattr(data, name), name
        def prop_search(self, data, name, *a, **kw): assert hasattr(data, name), name
        def template_list(self, *a, **kw): pass
        def template_ID_preview(self, *a, **kw): pass
    for cls in ui.CLASSES:
        if hasattr(cls, 'bl_space_type'):
            proxy = type('Panel', (), {'layout': Layout()})()
            cls.draw(proxy, bpy.context)


@case('vertex_group_weight_merge_sort_fill')
def _():
    reset()
    obj = mesh_object()
    for name, weight in [('2', .2), ('2.001', .3), ('4', 1.)]:
        group = obj.vertex_groups.new(name=name)
        group.add([0], weight, 'REPLACE')
    model_ops.vertex_group_operation(obj, 'VG_MERGE', bpy.context)
    assert abs(obj.vertex_groups['2'].weight(0)-.5) < 1e-6
    model_ops.vertex_group_operation(obj, 'VG_FILL', bpy.context)
    model_ops.vertex_group_operation(obj, 'VG_SORT', bpy.context)
    assert [g.name for g in obj.vertex_groups] == ['0', '1', '2', '3', '4']
    model_ops.vertex_group_operation(obj, 'VG_REMOVE_EMPTY', bpy.context)
    assert [g.name for g in obj.vertex_groups] == ['2', '4']


@case('normal_uv_color_tangent_and_clear')
def _():
    reset()
    obj = mesh_object()
    for operation in ('NORMAL_UV', 'TANGENT', 'COLOR'):
        model_ops.normals_operation(obj, operation)
    assert 'Bomber_SmoothNormal_Oct' in obj.data.uv_layers
    colors = obj.data.color_attributes if hasattr(obj.data, 'color_attributes') else obj.data.vertex_colors
    assert 'TANGENT' in colors and 'COLOR' in colors
    assert all(abs(a-b) < .004 for a,b in zip(tuple(colors['COLOR'].data[0].color)[:3], (.5,.5,1.)))
    assert bpy.ops.bomberstudio.model(action='CLEAR_NORMALS') == {'FINISHED'}


@case('split_preserves_shapes_weights_uv_and_backup')
def _():
    reset()
    obj = mesh_object(two_faces=True, keys=True)
    group = obj.vertex_groups.new(name='Root')
    group.add(list(range(6)), 1, 'REPLACE')
    parts = model_ops.split_model(obj, 'SPLIT_LOOSE', bpy.context)
    assert len(parts) == 2
    assert len(obj.data.vertices) == 6
    for part in parts:
        assert len(part.data.polygons) == 1
        assert len(part.data.vertices) == 3
        assert len(part.data.shape_keys.key_blocks) == 4
        assert len(part.data.uv_layers) == 1
        assert abs(part.vertex_groups['Root'].weight(0)-1) < 1e-6


@case('uv_island_split_sees_seam_on_connected_geometry')
def _():
    reset()
    obj = mesh_object()
    obj.data.clear_geometry() if hasattr(obj.data, 'clear_geometry') else None
    mesh = bpy.data.meshes.new('UVSeam')
    mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [], [(0,1,2),(0,2,3)])
    obj.data = mesh
    uv = C.new_uv(mesh, 'UV0')
    for i, item in enumerate(uv.data):
        item.uv = [(0,0),(1,0),(1,1),(2,0),(3,1),(2,1)][i]
    assert len(model_ops.face_clusters(obj, 'SPLIT_LOOSE')) == 1
    assert len(model_ops.face_clusters(obj, 'SPLIT_UV')) == 2


@case('shape_modifier_preserves_key_id_driver_and_topology')
def _():
    reset()
    obj = mesh_object(keys=True)
    key_id = obj.data.shape_keys.as_pointer()
    key = obj.data.shape_keys.key_blocks['AA']
    key.driver_add('value').driver.expression = '0.0'
    mod = obj.modifiers.new('TestDeform', 'SIMPLE_DEFORM')
    mod.deform_method, mod.factor = 'TAPER', .5
    model_ops.apply_modifiers_preserve_keys(obj, bpy.context)
    assert obj.data.shape_keys.as_pointer() == key_id
    assert len(obj.data.shape_keys.animation_data.drivers) == 1
    assert len(obj.modifiers) == 0
    assert (obj.data.shape_keys.key_blocks['AA'].data[0].co-obj.data.shape_keys.reference_key.data[0].co).length > .1
    obj.modifiers.new('RejectTopology', 'SUBSURF')
    try:
        model_ops.apply_modifiers_preserve_keys(obj, bpy.context)
    except ValueError:
        pass
    else:
        raise AssertionError('Topology-changing modifier should fail preflight')
    assert 'RejectTopology' in obj.modifiers


@case('visemes_numeric_and_duplicate_protection')
def _():
    reset()
    obj = mesh_object(keys=True)
    rig_ops.make_visemes(obj, 'AA', 'OH', 'CH', 1)
    keys = obj.data.shape_keys.key_blocks
    assert len(keys) == 19
    assert (keys['vrc.v_aa'].data[0].co-keys['AA'].data[0].co).length < 1e-6
    assert (keys['vrc.v_ou'].data[0].co-keys['OH'].data[0].co).length < 1e-6
    try:
        rig_ops.make_visemes(obj, 'AA', 'OH', 'CH', 1)
    except ValueError:
        pass
    else:
        raise AssertionError('Existing visemes must not be overwritten')


@case('bones_parent_scale_and_eyes')
def _():
    cfg = reset()
    obj = mesh_object(two_faces=True)
    for name, verts in [('Head', [1,2,4,5]), ('LeftEye', [0]), ('RightEye', [3])]:
        g = obj.vertex_groups.new(name=name)
        g.add(verts, 1, 'REPLACE')
    rig = rig_ops.generate_bones(bpy.context)
    assert len(rig.data.bones) == 3
    assert obj.find_armature() == rig
    C.select_only([rig])
    for b in rig.data.bones:
        C.set_bone_selected(rig, b, b.name in ('LeftEye', 'RightEye'))
    cfg.parent_bone = 'Head'
    rig_ops.parent_bones(bpy.context)
    assert rig.data.bones['LeftEye'].parent.name == 'Head'
    rig_ops.setup_eyes(bpy.context)
    assert 'BomberStudioEyeTracking' in rig
    # Give the model nonzero Z height before measuring the hierarchy.
    obj.data.vertices[2].co.z = 1
    obj.parent = rig
    C.update()
    cfg.target_height = 2
    C.select_only([rig, obj])
    rig_ops.scale_model(bpy.context)
    C.update()
    z = [C.matmul(obj.matrix_world, Vector(p)).z for p in obj.bound_box]
    assert abs(max(z)-min(z)-2) < 1e-5


@case('sidecar_source_contract_uv_scale_alpha')
def _():
    reset()
    obj = mesh_object()
    uv1 = C.new_uv(obj.data, 'UV1')
    mat = bpy.data.materials.new('FixtureMat')
    obj.data.materials.append(mat)
    image = image_file('SidecarImage', (.8,.2,.1,0))
    model = os.path.join(OUT, 'contract.fbx')
    record = {'exportedName': mat.name, 'baseColor': '_MainTex', 'textures': [
        {'Name': os.path.basename(image), 'PropertyName':'_MainTex','Dest':0,'UVSet':1,
         'Scale':{'X':2.,'Y':3.},'Offset':{'X':.1,'Y':.2},'WrapMode':0,'PreviewIgnoreAlpha':True}]}
    for suffix in core.SIDECARS:
        key = 'textureMappings' if suffix == '.genshin.json' else 'textures'
        desc = dict(record)
        desc[key] = desc.pop('textures') if key != 'textures' else desc['textures']
        core.atomic_json(model+suffix, {'adapter': 'SourceContractFixture', 'materials': [desc]})
    report = materials.apply_sidecars(model, [obj])
    assert report['bound'] == 4 and not report['missing'], report
    tex = mat.node_tree.nodes['BomberStudio_BASE']
    assert (not tex.image.use_alpha) if C.LEGACY else tex.image.alpha_mode == 'NONE'
    assert tex['bomber_uv'] == 'UV1'
    assert tuple(tex['bomber_scale']) == (2.,3.)
    assert 'BomberStudioSourceMaterial' in mat


@case('pc2_hash_preflight_actual_frame_evaluation')
def _():
    reset()
    path = os.path.join(OUT, 'cache')
    os.makedirs(path, exist_ok=True)
    pc = os.path.join(path, 'mesh.pc2')
    points = [(0,0,0),(1,0,0),(0,1,0), (0,0,1),(1,0,1),(0,1,1)]
    with open(pc, 'wb') as stream:
        stream.write(struct.pack('<12siiffi', b'POINTCACHE2\0', 1, 3, 1., 1., 2))
        for point in points:
            stream.write(struct.pack('<fff', *point))
    data = {'schema':'BomberStudio.NoWind.VertexCache/1','completed':True,'animation':'Fixture','fps':30,'frames':2,
            'materials':{'M':{'image':None,'uvSet':1,'scale':[2,3],'offset':[.1,.2],'ignoreAlpha':True}},
            'meshes':[{'path':'Root/Mesh','cache':'mesh.pc2','vertexCount':3,'sha256':core.sha256(pc),
                       'uvSets':{'1':[[0,0],[1,0],[0,1]]},'submeshes':[{'material':'M','triangles':[[0,1,2]]}]}]}
    manifest = os.path.join(path, 'physics-cache.json')
    core.atomic_json(manifest, data)
    objects = bridge.import_cache(manifest, bpy.context)
    assert abs(objects[0].data.uv_layers.active.data[1].uv.x-2.1) < 1e-5
    bpy.context.scene.frame_set(2)
    C.update()
    mesh, release = C.evaluated_mesh(objects[0])
    try:
        assert all(abs(v.co.z-1) < 1e-5 for v in mesh.vertices), [tuple(v.co) for v in mesh.vertices]
    finally:
        release()


@case('atlas_pbr_pixels_uv_shape_keys_originals')
def _():
    cfg = reset()
    obj = mesh_object(two_faces=True, keys=True)
    for name, color in [('AtlasRed',(1,0,0,1)), ('AtlasGreen',(0,1,0,1))]:
        mat = bpy.data.materials.new(name)
        image = image_file(name, color)
        materials.attach_texture(mat, image, uv='UV0')
        obj.data.materials.append(mat)
        row = cfg.atlas_materials.add()
        row.material, row.enabled = mat, True
    obj.data.polygons[1].material_index = 1
    cfg.atlas_tile_size, cfg.atlas_solid_size = 16, 8
    cfg.atlas_padding, cfg.atlas_pbr = 2, True
    cfg.atlas_keep_originals, cfg.atlas_uniform, cfg.atlas_crop = True, True, True
    manifest = atlas.create_atlas(bpy.context)
    assert len(manifest['files']) == 5
    assert len(obj.data.materials) == 2
    new = bpy.context.selected_objects[0]
    assert new != obj and len(new.data.shape_keys.key_blocks) == 4
    assert len(new.data.materials) == 1
    image = bpy.data.images.load(manifest['files']['BASE'], check_existing=False)
    data = atlas.pixels(image)
    for desc in manifest['materials']:
        x,y,w,h = desc['rect']
        color = data[y+h//2, x+w//2]
        expected = (1,0,0,1) if desc['name'].startswith('AtlasRed') else (0,1,0,1)
        assert all(abs(a-b)<.01 for a,b in zip(color,expected)), (desc,color)
    uv = new.data.uv_layers['BomberAtlasUV']
    assert all(0 <= v <= 1 for row in uv.data for v in row.uv)
    # Retain this scene for UI/render inspection.
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT,'verified-atlas.blend'), copy=True)


@case('atlas_tiling_crop_non_square_and_guard')
def _():
    cfg = reset()
    obj = mesh_object()
    for uv, coord in zip(obj.data.uv_layers.active.data, [(-1,-1),(2,-1),(-1,1)]):
        uv.uv = coord
    mat = bpy.data.materials.new('Tiled')
    materials.attach_texture(mat, image_file('Tile',(.25,.5,.75,1)), uv='UV0', scale=(2,3), offset=(.2,.1))
    obj.data.materials.append(mat)
    row = cfg.atlas_materials.add()
    row.material, row.enabled = mat, True
    cfg.atlas_pbr = False
    cfg.atlas_shape, cfg.atlas_algorithm, cfg.atlas_tile_size = 'RECT', 'SHELF', 24
    manifest = atlas.create_atlas(bpy.context)
    assert manifest['materials'][0]['uv_bounds'] == (-1.,-1.,2.,1.)
    assert manifest['size'][0] != manifest['size'][1]
    before = set(bpy.data.objects)
    cfg.atlas_materials.clear()
    row = cfg.atlas_materials.add()
    row.material, row.enabled = bpy.context.object.active_material, True
    target = C.principled(row.material)
    noise = row.material.node_tree.nodes.new('ShaderNodeTexNoise')
    row.material.node_tree.links.new(noise.outputs['Color'], target.inputs['Base Color'])
    try:
        atlas.create_atlas(bpy.context)
    except ValueError:
        pass
    else:
        raise AssertionError('Unsupported shader must fail preflight')
    assert set(bpy.data.objects) == before


@case('real_bomberstudio_fbx_binding_fixture_import')
def _():
    reset()
    source = r'E:\mapper\Unpackaging tool\unityUnpackaging tool\BomberStudio\临时文件\Endfield_20260929\genshin-regression\bindings.fbx'
    if not os.path.isfile(source):
        raise AssertionError('Real source fixture missing')
    objects, report = bridge.import_model(source, bpy.context)
    meshes = [o for o in objects if o.type == 'MESH']
    assert len(meshes) == 2
    assert all(len(o.data.uv_layers) == 2 for o in meshes)
    assert report['missing'] == [], report


@case('real_bomberstudio_rigged_fbx_static_import_export_roundtrip')
def _():
    cfg = reset()
    source = r'E:\mapper\Unpackaging tool\unityUnpackaging tool\BomberStudio\out\Merged_SK_actor_male__walk.fbx'
    objects, report = bridge.import_model(source, bpy.context, animations=False)
    meshes = [o for o in objects if o.type == 'MESH']
    rigs = [o for o in objects if o.type == 'ARMATURE']
    assert meshes and rigs, [o.type for o in objects]
    before = sum(len(o.data.vertices) for o in meshes)
    cfg.export_animations = False
    export = os.path.join(OUT, 'roundtrip.fbx')
    C.select_only(objects)
    result = bpy.ops.bomberstudio.export_fbx(filepath=export)
    assert result == {'FINISHED'} and os.path.getsize(export) > 1000
    reset()
    imported, _ = bridge.import_model(export, bpy.context)
    assert sum(len(o.data.vertices) for o in imported if o.type == 'MESH') == before
    assert any(o.type == 'ARMATURE' for o in imported)


sys.path.insert(0, os.path.dirname(__file__))
import extra_cases
extra_cases.run(globals())

report = {'blender': bpy.app.version_string, 'python': sys.version, 'passed': sum(r['status']=='PASS' for r in results),
          'failed': sum(r['status']=='FAIL' for r in results), 'tests': results}
core.atomic_json(os.path.join(OUT, 'results.json'), report)
addon.unregister()
print('BOMBER_RESULT', json.dumps({'blender': report['blender'], 'passed': report['passed'], 'failed': report['failed']}), flush=True)
if report['failed']:
    raise RuntimeError('{0} regression cases failed; see results.json'.format(report['failed']))
