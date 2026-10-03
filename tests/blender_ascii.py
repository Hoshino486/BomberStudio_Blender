"""Real Blender ASCII import regression. Python 3.5-compatible for 2.79."""
import array
import base64
import hashlib
import json
import math
import os
import struct
import sys
import time
import traceback
import bpy
from mathutils import Vector
from io_scene_fbx import parse_fbx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bomberstudio_blender as addon
from bomberstudio_blender import ascii_fbx, bridge, compat as C, core

OUT = sys.argv[sys.argv.index('--')+1]
os.makedirs(OUT, exist_ok=True)
SOURCE = r'E:\Unity\Vergil\Assets\Art_Evanescia_00 (merge)1.fbx'
results = []
addon.register()
cfg = C.settings()
cfg.temp_dir = OUT


def case(name):
    def run(fn):
        start = time.time()
        print('START', name, flush=True)
        try:
            details = fn()
            results.append({'name': name, 'status': 'PASS', 'seconds': time.time()-start, 'details': details})
            print('PASS', name, flush=True)
        except Exception:
            results.append({'name': name, 'status': 'FAIL', 'seconds': time.time()-start, 'error': traceback.format_exc()})
            traceback.print_exc()
        core.atomic_json(os.path.join(OUT, 'checkpoint.json'), results)
        return fn
    return run


def reset():
    C.mode_object()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    # This process is --factory-startup and disposable; never run in the GUI session.
    for datablocks in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.actions, bpy.data.images):
        for data in list(datablocks):
            if data.users == 0:
                datablocks.remove(data)
    C.settings().temp_dir = OUT


def quote(value):
    if b'\0\1' in value:
        name, cls = value.split(b'\0\1', 1)
        value = cls + b'::' + name
    return '"' + value.decode('utf8').replace('"', '&quot;').replace('\r','&cr;').replace('\n','&lf;') + '"'


def binary_to_ascii(binary, output):
    """Test fixture generator, NOT part of the installed add-on."""
    root, version = parse_fbx.parse(binary)
    rows = ['; FBX {0} ASCII regression fixture'.format(version)]

    def emit(node, depth):
        if node.id == b'FileId':
            return  # Generated test file IDs are irrelevant to scene semantics.
        indent = '  '*depth
        name = node.id.decode('ascii')
        if len(node.props) == 1 and node.props_type[0] in b'ifdlbc':
            values = node.props[0]
            if node.id == b'KeyAttrDataFloat' and version >= 7200:
                values = struct.unpack('<{0}I'.format(len(values)), values.tobytes())
            rows.append(indent + name + ': *' + str(len(values)) + ' { a: ' +
                        ','.join(repr(v) for v in values) + ' }')
            return
        props = []
        for kind, value in zip(node.props_type, node.props):
            if kind == ord('S'):
                props.append(quote(value))
            elif kind == ord('R'):
                props.append(quote(base64.b64encode(value)))
            elif kind in b'CB':
                numeric = value[0] if isinstance(value, bytes) else int(value)
                props.append(chr(numeric) if kind == ord('C') and 65 <= numeric <= 90 else str(numeric))
            else:
                props.append(repr(value))
        rows.append(indent + name + ': ' + ','.join(props) + (' {' if node.elems else ''))
        if node.elems:
            for child in node.elems:
                emit(child, depth+1)
            rows.append(indent + '}')
    for node in root.elems:
        emit(node, 0)
    with open(output, 'w', encoding='utf8') as stream:
        stream.write('\n'.join(rows)+'\n')


def sample_world(objects):
    data = []
    for frame in (1, 2, 3):
        bpy.context.scene.frame_set(frame)
        C.update()
        row = []
        for obj in sorted((o for o in objects if o.type == 'MESH'), key=lambda o:o.name):
            mesh, release = C.evaluated_mesh(obj)
            try:
                row.extend(float(v) for vert in mesh.vertices for v in C.matmul(obj.matrix_world,vert.co))
            finally:
                release()
        data.append(row)
    return data


@case('ascii_binary_scene_animation_shape_uv_equivalence')
def _():
    reset()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 3
    data = bpy.data.armatures.new('FixtureRig')
    rig = bpy.data.objects.new('FixtureRig',data)
    C.link(rig)
    C.select_only([rig])
    bpy.ops.object.mode_set(mode='EDIT')
    root = data.edit_bones.new('Root')
    root.head,root.tail=(0,0,0),(0,0,1)
    bone = data.edit_bones.new('Tip')
    bone.head,bone.tail,bone.parent=(0,0,1),(0,0,2),root
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new('FixtureMesh')
    mesh.from_pydata([(0,0,1),(1,0,1),(0,1,1)],[],[(0,1,2)])
    mesh.update()
    obj = bpy.data.objects.new('FixtureMesh',mesh)
    C.link(obj)
    uv = C.new_uv(mesh,'UVSet0')
    for li,coord in enumerate(((0,0),(1,0),(0,1))):
        uv.data[li].uv=coord
    obj.vertex_groups.new(name='Tip').add([0,1,2],1.,'REPLACE')
    mod=obj.modifiers.new('FixtureArmature','ARMATURE')
    mod.object=rig
    obj.shape_key_add(name='Basis')
    key=obj.shape_key_add(name='Smile')
    key.data[0].co.z += .25
    mat=bpy.data.materials.new('FixtureMaterial')
    C.principled(mat)
    mesh.materials.append(mat)
    for frame,angle,value in ((1,0.,0.),(2,.4,1.),(3,-.2,.2)):
        rig.pose.bones['Tip'].rotation_mode='XYZ'
        rig.pose.bones['Tip'].rotation_euler=(angle,0,0)
        rig.pose.bones['Tip'].keyframe_insert('rotation_euler',frame=frame)
        key.value=value
        key.keyframe_insert('value',frame=frame)
    C.select_only([rig,obj])
    binary=os.path.join(OUT,'fixture_binary.fbx')
    ascii_path=os.path.join(OUT,'fixture_ascii.fbx')
    assert bpy.ops.export_scene.fbx(filepath=binary,use_selection=True,add_leaf_bones=False,
                                   bake_anim=True,bake_anim_use_all_actions=False,bake_anim_use_nla_strips=False)=={'FINISHED'}
    binary_to_ascii(binary,ascii_path)
    reset()
    expected,_=bridge.import_model(binary,bpy.context,animation_offset=0.)
    baseline=sample_world(expected)
    reset()
    actual,report=bridge.import_model(ascii_path,bpy.context,animation_offset=0.)
    sample=sample_world(actual)
    assert [len(row) for row in baseline]==[len(row) for row in sample]
    error=max(abs(a-b) for ar,br in zip(baseline,sample) for a,b in zip(ar,br))
    assert error<1e-5, error
    assert any(o.type=='ARMATURE' and len(o.data.bones)==2 for o in actual)
    obj=next(o for o in actual if o.type=='MESH')
    assert 'Smile' in obj.data.shape_keys.key_blocks and len(obj.data.uv_layers)==1
    assert report['fbx_conversion']['converted']
    return {'max_world_vertex_error':error,'frames':3,'vertices_per_frame':3}


@case('ascii_sidecars_original_path_and_relative_images')
def _():
    reset()
    ascii_path=os.path.join(OUT,'fixture_ascii.fbx')
    image=bpy.data.images.new('fixture_color',width=2,height=2,alpha=True)
    image.pixels[:]=[.2,.4,.8,1.]*4
    folder=os.path.join(OUT,'source_textures')
    os.makedirs(folder,exist_ok=True)
    image.filepath_raw=os.path.join(folder,'face.png')
    image.file_format='PNG'
    image.save()
    sidecar={'adapter':'REGRESSION','materials':[{'exportedName':'FixtureMaterial','baseColor':'_MainTex',
             'textureMappings':[{'Name':'source_textures/face.png','PropertyName':'_MainTex','Dest':0,'UVSet':0,
                                 'Scale':{'X':1,'Y':1},'Offset':{'X':0,'Y':0},'PreviewIgnoreAlpha':True}]}]}
    core.atomic_json(ascii_path+'.genshin.json',sidecar)
    objects,report=bridge.import_model(ascii_path,bpy.context,animation_offset=0.)
    assert report['bound']==1 and report['missing']==[], report
    assert report['model']==ascii_path
    assert all(o['BomberStudioSource']==ascii_path for o in objects)
    assert report['fbx_conversion']['cache_hit']
    return {'bound':report['bound'],'cache_hit':True,'original_model_path':report['model']}


@case('invalid_ascii_preflight_and_backend_failure_keep_existing_scene')
def _():
    reset()
    marker=bpy.data.objects.new('ExistingSceneMarker',None)
    C.link(marker)
    C.select_only([marker])
    path=os.path.join(OUT,'invalid_ascii.fbx')
    with open(path,'wb') as f:
        f.write(b'FBXHeaderExtension:{FBXVersion:7700}\nObjects:{}\nVertices:*3 {a:0,1}')
    before=set(bpy.data.objects)
    try:
        bridge.import_model(path,bpy.context)
    except ascii_fbx.AsciiFBXError:
        pass
    else:
        raise AssertionError('Malformed input accepted')
    assert set(bpy.data.objects)==before and C.active()==marker
    original=bridge.invoke_import
    try:
        def broken(*args,**kwargs):
            leaked=bpy.data.objects.new('BackendPartialObject',None)
            C.link(leaked)
            raise RuntimeError('test backend exception')
        bridge.invoke_import=broken
        try:
            bridge.import_model(os.path.join(OUT,'fixture_binary.fbx'),bpy.context)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Expected backend exception')
        assert set(bpy.data.objects)==before and C.active()==marker
    finally:
        bridge.invoke_import=original
    return {'preexisting_objects_preserved':True,'partial_backend_objects_removed':True}


@case('user_fbx_7700_full_operator_rig_shape_animation_import')
def _():
    reset()
    digest=core.sha256(SOURCE)
    start=time.time()
    assert bpy.ops.bomberstudio.import_model(filepath=SOURCE)=={'FINISHED'}
    objects=list(bpy.context.scene.objects)
    meshes=[o for o in objects if o.type=='MESH']
    rigs=[o for o in objects if o.type=='ARMATURE']
    assert len(meshes)==6 and sum(len(o.data.vertices) for o in meshes)==23975
    assert sum(len(o.data.polygons) for o in meshes)==44886
    assert len(rigs)==1 and len(rigs[0].data.bones)==348
    assert all(n in rigs[0].data.bones for n in ('Root_M','Neck_M','WeaponSec_00_JNT'))
    assert sum(len(o.data.shape_keys.key_blocks)-1 for o in meshes if o.data.shape_keys)==8
    assert sum(len(o.data.uv_layers) for o in meshes)==9
    assert all(o.find_armature()==rigs[0] for o in meshes)
    assert len(bpy.data.actions)>=6
    report=core.read_json(os.path.join(OUT,'last-import.json'))
    assert report['fbx_conversion']['object_counts']['AnimationCurve']==3186
    assert len(report['fbx_conversion']['skeleton_normalization'])==9
    assert report['model']==SOURCE and C.settings().source_model==SOURCE
    bone=rigs[0].pose.bones['Root_M']
    scene=bpy.context.scene
    matrices=[]
    bounds=[]
    for frame in (1,159,317):
        scene.frame_set(frame)
        C.update()
        matrices.append([float(x) for row in bone.matrix for x in row])
        positions=[]
        for obj in meshes:
            m,release=C.evaluated_mesh(obj)
            try:
                positions.extend(C.matmul(obj.matrix_world,v.co) for v in m.vertices)
            finally:
                release()
        assert all(math.isfinite(v) for point in positions for v in point)
        bounds.append([[min(point[i] for point in positions),max(point[i] for point in positions)] for i in range(3)])
    delta=max(abs(a-b) for a,b in zip(matrices[0],matrices[1]))
    assert delta>1e-6, 'Imported animation is static'
    assert digest==core.sha256(SOURCE)
    return {'source_sha256':digest,'seconds':time.time()-start,'meshes':6,'vertices':23975,'polygons':44886,
            'bones':348,'shape_keys_without_basis':8,'uv_layers':9,'actions':len(bpy.data.actions),
            'root_motion_matrix_delta':delta,'sampled_frames':[1,159,317],'world_bounds':bounds,
            'conversion':report['fbx_conversion']}


report={'blender':bpy.app.version_string,'passed':sum(r['status']=='PASS' for r in results),
        'failed':sum(r['status']=='FAIL' for r in results),'tests':results}
core.atomic_json(os.path.join(OUT,'results.json'),report)
addon.unregister()
print('ASCII_RESULT',json.dumps({'blender':report['blender'],'passed':report['passed'],'failed':report['failed']}),flush=True)
if report['failed']:
    raise RuntimeError('ASCII regression failed; see results.json')
