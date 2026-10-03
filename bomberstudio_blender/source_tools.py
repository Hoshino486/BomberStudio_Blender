# SPDX-License-Identifier: GPL-3.0-or-later
"""Version-gated integration; this module retains Python 3.5 syntax.

The bundled upstream package requires Blender 4.1. Never import it on older
Blender versions. An already registered standalone provider is reused, not owned.
"""
import importlib
import os
import sys
import bpy
from bpy_extras.io_utils import ImportHelper
from . import compat as C, core

MIN_VERSION = (4, 1, 0)
SOURCE_VERSION = (3, 4, 3)
FORMATS = ('.smd', '.vta', '.dmx', '.qc', '.qci')
_backend = None
_owned = False
_last_error = ''


def backend_ready():
    return (hasattr(bpy.types.Scene, 'vs') and
            C.op_available('import_scene', 'smd') and
            C.op_available('export_scene', 'smd'))


def active_backend():
    if not backend_ready():
        return None
    if _owned:
        return _backend
    # Operator RNA reveals its actual Python provider, including bl_ext packages.
    try:
        ident = bpy.ops.import_scene.smd.get_rna_type().identifier
        cls = bpy.types.Operator.bl_rna_get_subclass_py(ident)
        return sys.modules.get(cls.__module__.rsplit('.', 1)[0])
    except (AttributeError, RuntimeError, KeyError):
        return None


def status():
    if backend_ready():
        return ('BUNDLED', '内置 Source Tools 3.4.3 已启用') if _owned else (
            'EXTERNAL', '复用已启用的独立 Source Tools')
    orphaned_maps = any(C.op_available('smd', 'vertex_map_' + action + '_' + name)
                        for name in ('valvesource_vertex_paint', 'valvesource_vertex_blend', 'valvesource_vertex_blend1')
                        for action in ('select', 'create', 'remove'))
    if orphaned_maps or hasattr(bpy.types.Scene, 'vs') or C.op_available('import_scene', 'smd') or C.op_available('export_scene', 'smd'):
        return 'CONFLICT', '检测到不完整或冲突的 Source Tools 注册'
    if bpy.app.version < MIN_VERSION:
        return 'UNSUPPORTED', '内置 Source Tools 需要 Blender 4.1 或更新版本'
    if _last_error:
        return 'ERROR', 'Source Tools 启用失败，详见日志'
    return 'AVAILABLE', '内置 Source Tools 尚未启用'


def external_requested(context=None):
    context = context or bpy.context
    preferences = getattr(context, 'preferences', None) or getattr(context, 'user_preferences', None)
    if preferences is None:
        return False
    return any(name.rsplit('.', 1)[-1] in ('io_scene_valvesource', 'io_smd_tools')
               for name in preferences.addons.keys())


def ensure_backend():
    global _backend, _owned, _last_error
    if backend_ready():
        return active_backend()
    state, message = status()
    if state in ('CONFLICT', 'UNSUPPORTED'):
        raise ValueError(message)
    try:
        _backend = importlib.import_module('.vendor.io_scene_valvesource', __package__)
        _backend.register()
        _owned = True
        # Blender's real installer runs register() in _RestrictContext.
        # Data access waits for depsgraph/load callbacks or the first operation.
        if getattr(bpy.context, 'scene', None) is not None:
            _backend.State.update_scene()
        _last_error = ''
        return _backend
    except Exception as exc:
        if _backend is not None:
            _backend.unregister()
        _owned = False
        _last_error = str(exc)
        raise


def register_backend():
    """Do not break the core add-on if an optional provider fails or loads later."""
    if bpy.app.version < MIN_VERSION or external_requested():
        return
    try:
        ensure_backend()
    except Exception as exc:
        C.log('Source Tools 自动启用失败: ' + str(exc))


def unregister_backend():
    global _owned
    if _owned and _backend is not None:
        _backend.unregister()
    _owned = False


def refresh_backend(context):
    backend = ensure_backend()
    state = getattr(backend, 'State', None)
    if state is not None:
        state.update_scene(context.scene)
    return backend


class BOMBER_OT_source_tools(bpy.types.Operator):
    bl_idname = 'bomberstudio.source_tools'
    bl_label = '启用 / 刷新 Source Tools'
    bl_description = '启用内置 Source Tools；已有独立版时复用它，不重复注册'
    action = bpy.props.EnumProperty(items=[
        ('ENABLE', '启用 / 刷新', ''), ('DISABLE', '停用内置副本', '')])

    def execute(self, context):
        try:
            if self.action == 'DISABLE':
                if not _owned:
                    raise ValueError('当前提供者是独立插件，请在首选项中管理它')
                unregister_backend()
                C.log('已停用内置 Source Tools；可重新启用。', context)
            else:
                refresh_backend(context)
                C.log(status()[1], context)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


class BOMBER_OT_import_source(bpy.types.Operator, ImportHelper):
    bl_idname = 'bomberstudio.import_source'
    bl_label = '导入 Source 模型 / 动画'
    bl_description = 'Source Tools：SMD / VTA / DMX / QC / QCI；VTA 和动画可更新已有对象'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ''
    filter_glob = bpy.props.StringProperty(default='*.smd;*.vta;*.dmx;*.qc;*.qci', options={'HIDDEN'})
    files = bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN'})
    directory = bpy.props.StringProperty(subtype='DIR_PATH')
    animations = bpy.props.BoolProperty(name='导入动画', default=True)
    create_collections = bpy.props.BoolProperty(name='为导入模型创建集合', default=True)
    make_camera = bpy.props.BoolProperty(name='根据 QC 创建相机', default=False)
    bone_mode = bpy.props.EnumProperty(name='骨架处理', items=[
        ('APPEND', '合并到现有骨架', ''), ('VALIDATE', '验证现有骨架', ''),
        ('NEW_ARMATURE', '新建骨架', '')], default='APPEND')
    up_axis = bpy.props.EnumProperty(name='向上轴', items=[
        ('X', 'X', ''), ('Y', 'Y', ''), ('Z', 'Z', '')], default='Z')

    def execute(self, context):
        try:
            paths = ([os.path.join(self.directory, item.name) for item in self.files]
                     if self.files else [self.filepath])
            paths = [os.path.abspath(bpy.path.abspath(path)) for path in paths]
            for path in paths:
                if not os.path.isfile(path):
                    raise ValueError('Source 文件不存在: ' + path)
                if os.path.splitext(path)[1].lower() not in FORMATS:
                    raise ValueError('Source 格式应为 SMD / VTA / DMX / QC / QCI')
            ensure_backend()
            # VTA and animation imports may edit existing meshes/rigs and create
            # no objects. Keep their native semantics instead of FBX's contract.
            if (C.settings(context).backup_before_edit and
                    (any(p.lower().endswith('.vta') for p in paths) or
                     (self.bone_mode != 'NEW_ARMATURE' and
                      any(o.type == 'ARMATURE' for o in context.scene.objects)))):
                C.snapshot(context, 'before-source-import')
            C.mode_object(context)
            before = set(bpy.data.objects)
            for path in paths:
                result = bpy.ops.import_scene.smd(
                    'EXEC_DEFAULT', filepath=path, doAnim=self.animations,
                    createCollections=self.create_collections, makeCamera=self.make_camera,
                    append=self.bone_mode, upAxis=self.up_axis)
                if 'FINISHED' not in result:
                    raise ValueError('Source 导入器未完成: ' + path)
            created = [obj for obj in bpy.data.objects if obj not in before]
            for obj in created:
                obj['BomberStudioSourceTools'] = True
            receipt = {'files': paths, 'new_objects': len(created), 'provider': status()[0]}
            core.atomic_json(os.path.join(C.temp_dir(context), 'last-source-import.json'), receipt)
            C.log('Source 导入完成：{0} 个文件，新增 {1} 个对象'.format(len(paths), len(created)), context)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


class BOMBER_OT_export_source(bpy.types.Operator):
    bl_idname = 'bomberstudio.export_source'
    bl_label = '导出 Source 模型 / 动画'
    bl_description = '按照场景 Source Tools 设置导出 SMD / VTA 或 DMX'
    export_scene = bpy.props.BoolProperty(name='导出整个场景', default=False)

    def execute(self, context):
        try:
            refresh_backend(context)
            cfg = context.scene.vs
            if not cfg.export_path:
                raise ValueError('请先设置 Source 导出目录')
            if cfg.export_path.startswith('//') and not bpy.data.filepath:
                raise ValueError('相对导出路径需要先保存 .blend，或改用绝对目录')
            if not self.export_scene and not context.selected_objects:
                raise ValueError('请先选中需要导出的网格或骨架')
            if not bpy.ops.export_scene.smd.poll():
                raise ValueError('场景中没有可导出的 Source 对象')
            # Native exporter owns its undo transaction; never retain Scene/RNA
            # references across this call.
            backend = active_backend()
            groups = []
            export_scene = self.export_scene
            has_single = True
            if not export_scene and backend is not None and hasattr(backend, 'getSelectedExportables'):
                units = list(backend.getSelectedExportables())
                groups = list(dict.fromkeys(unit.item.name for unit in units if unit.ob_type == 'COLLECTION'))
                has_single = any(unit.ob_type != 'COLLECTION' for unit in units)
                if not groups and not has_single:
                    raise ValueError('选中对象未列入 Source 导出列表；检查集合禁用和导出勾选')
            jobs = ([''] if export_scene or has_single else []) + groups
            for group in jobs:
                result = bpy.ops.export_scene.smd('EXEC_DEFAULT', export_scene=export_scene, collection=group)
                if 'FINISHED' not in result:
                    raise ValueError('Source 导出未完成，请查看 Blender 信息日志')
            C.log('Source 导出完成；文件名与子目录由 Source Tools 导出列表决定。', bpy.context)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, bpy.context)


CLASSES = (BOMBER_OT_source_tools, BOMBER_OT_import_source, BOMBER_OT_export_source)
