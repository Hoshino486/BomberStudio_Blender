# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
import contextlib
import logging
import os
import time
import traceback
from . import core

LEGACY = bpy.app.version < (2, 80, 0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Preserve the explicitly configured local workspace on the development machine.
# Public installations fall back to Blender's writable per-user configuration.
_LOCAL_WORKSPACE = r'E:\mapper\Unpackaging tool\unityUnpackaging tool\blender插件'


def default_workspace():
    if os.name == 'nt' and os.path.isdir(_LOCAL_WORKSPACE):
        return _LOCAL_WORKSPACE
    config = bpy.utils.user_resource('CONFIG') or os.path.expanduser('~')
    return os.path.join(config, 'bomberstudio')


DEFAULT_TEMP = os.path.join(default_workspace(), '临时文件夹')
DEFAULT_OUTPUT = os.path.join(default_workspace(), '输出')



def settings(context=None):
    return (context or bpy.context).scene.bomberstudio


def temp_dir(context=None):
    path = bpy.path.abspath(settings(context).temp_dir)
    os.makedirs(path, exist_ok=True)
    return path


def log(message, context=None):
    print("[BomberStudio] " + message)
    try:
        with open(os.path.join(temp_dir(context), 'bomberstudio.log'), 'a', encoding='utf8') as stream:
            stream.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
    except (OSError, AttributeError):
        logging.getLogger(__name__).exception("日志写入失败")


def fail(operator, exc, context=None):
    log(traceback.format_exc(), context)
    operator.report({'ERROR'}, str(exc)[:900])
    return {'CANCELLED'}


def register(cls):
    # Blender 2.79 uses assignments; 2.80+ needs annotations.
    if not LEGACY:
        annotations = dict(getattr(cls, '__annotations__', {}))
        deferred = getattr(bpy.props, '_PropertyDeferred', ())
        for name, value in list(cls.__dict__.items()):
            if ((deferred and isinstance(value, deferred)) or
                    (isinstance(value, tuple) and len(value) == 2 and callable(value[0]) and isinstance(value[1], dict))):
                annotations[name] = value
                delattr(cls, name)
        cls.__annotations__ = annotations
    bpy.utils.register_class(cls)


def active(context=None):
    context = context or bpy.context
    return context.scene.objects.active if LEGACY else context.view_layer.objects.active


def set_active(obj, context=None):
    context = context or bpy.context
    if LEGACY:
        context.scene.objects.active = obj
    else:
        context.view_layer.objects.active = obj


def select(obj, value=True):
    if LEGACY:
        obj.select = value
    else:
        obj.select_set(value)


def select_only(objects, context=None):
    context = context or bpy.context
    mode_object(context)
    for obj in context.selected_objects:
        select(obj, False)
    for obj in objects:
        select(obj)
    if objects:
        set_active(objects[0], context)


def mode_object(context=None):
    context = context or bpy.context
    obj = active(context)
    if obj and obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def link(obj, context=None, collection=None):
    context = context or bpy.context
    if LEGACY:
        context.scene.objects.link(obj)
        if collection:
            collection.objects.link(obj)
    else:
        (collection or context.collection or context.scene.collection).objects.link(obj)


def collection(name, context=None):
    context = context or bpy.context
    if LEGACY:
        return bpy.data.groups.new(name)
    result = bpy.data.collections.new(name)
    context.scene.collection.children.link(result)
    return result


def update(context=None):
    context = context or bpy.context
    if LEGACY:
        context.scene.update()
    else:
        context.view_layer.update()


def hidden(obj, value):
    obj.hide_render = value
    if LEGACY:
        obj.hide = value
    else:
        obj.hide_set(value)


def meshes(context):
    return [o for o in context.selected_objects if o.type == 'MESH']


def ensure_meshes(context):
    result = meshes(context)
    if not result:
        raise ValueError("请先选中网格物体")
    return result


@contextlib.contextmanager
def selection_guard(context=None):
    context = context or bpy.context
    chosen = list(context.selected_objects)
    current = active(context)
    mode = current.mode if current else 'OBJECT'
    mode_object(context)
    try:
        yield
    finally:
        mode_object(context)
        surviving = [o for o in chosen if o.name in context.scene.objects]
        select_only(surviving, context)
        if current and current.name in context.scene.objects:
            set_active(current, context)
            if mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode=mode)
                except RuntimeError:
                    pass


def single_user(obj):
    if obj.data.users > 1:
        obj.data = obj.data.copy()


def copy_object(obj, context=None, target_collection=None):
    result = obj.copy()
    result.data = obj.data.copy()
    link(result, context, target_collection)
    return result


def evaluated_mesh(obj, context=None):
    context = context or bpy.context
    if LEGACY:
        mesh = obj.to_mesh(context.scene, True, 'PREVIEW')
        return mesh, lambda: bpy.data.meshes.remove(mesh)
    graph = context.evaluated_depsgraph_get()
    graph.update()
    ev = obj.evaluated_get(graph)
    mesh = ev.to_mesh()
    return mesh, ev.to_mesh_clear


def op_available(group, name):
    try:
        operator = getattr(getattr(bpy.ops, group), name)
        if LEGACY:
            operator.get_rna()
        else:
            operator.get_rna_type()
        return True
    except (AttributeError, RuntimeError):
        return False


def alpha_mode(mat, transparent):
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'DITHERED'
    elif hasattr(mat, 'blend_method'):
        mat.blend_method = 'HASHED' if transparent else 'OPAQUE'


def principled(mat):
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None) or nodes.new('ShaderNodeOutputMaterial')
        mat.node_tree.links.new(bsdf.outputs[0], out.inputs['Surface'])
    return bsdf


def input_socket(node, *names):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def color_layer(mesh, name):
    if hasattr(mesh, 'color_attributes'):
        return mesh.color_attributes.get(name) or mesh.color_attributes.new(name=name, type='FLOAT_COLOR', domain='CORNER')
    return mesh.vertex_colors.get(name) or mesh.vertex_colors.new(name=name)


def snapshot(context, label):
    """Write a copy, not the current working .blend, before explicitly requested edits."""
    folder = os.path.join(temp_dir(context), 'backups')
    os.makedirs(folder, exist_ok=True)
    leaf = time.strftime('%Y%m%d-%H%M%S-') + label + '-' + str(time.time_ns() if hasattr(time, 'time_ns') else int(time.time()*1e6)) + '.blend'
    path = os.path.join(folder, leaf)
    result = bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False)
    if 'FINISHED' not in result:
        raise ValueError("备份未完成")
    log("备份: " + path, context)
    return path


def new_uv(mesh, name):
    if LEGACY:
        mesh.uv_textures.new(name=name)
        return mesh.uv_layers[-1]
    return mesh.uv_layers.new(name=name)


def matmul(matrix, value):
    return matrix * value if LEGACY else matrix @ value


def bone_selected(rig, bone):
    return bone.select if hasattr(bone, 'select') else rig.pose.bones[bone.name].select


def set_bone_selected(rig, bone, value):
    if hasattr(bone, 'select'):
        bone.select = value
    else:
        rig.pose.bones[bone.name].select = value


def delete_geom(bm, geom, context):
    import bmesh
    if LEGACY:
        context = {'VERTS': 1, 'EDGES': 2, 'FACES_ONLY': 3, 'EDGES_FACES': 4, 'FACES': 5, 'ALL': 6, 'TAGGED_ONLY': 7}[context]
    return bmesh.ops.delete(bm, geom=geom, context=context)
