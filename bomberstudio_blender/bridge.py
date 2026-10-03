# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import struct
import shutil
import time
import uuid
import bpy
from bpy_extras.io_utils import ImportHelper, ExportHelper
from . import compat as C, core, materials, ascii_fbx


def invoke_import(path, scale=1., animations=True, automatic_bones=False, animation_offset=1.):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.fbx':
        if not C.op_available('import_scene', 'fbx'):
            raise ValueError("当前 Blender 未启用 FBX 导入器；启用随 Blender 提供的 FBX 插件后重试")
        return bpy.ops.import_scene.fbx(filepath=path, global_scale=scale, use_anim=animations,
                                        automatic_bone_orientation=automatic_bones, use_image_search=True, anim_offset=animation_offset)
    if ext == '.obj':
        if C.op_available('wm', 'obj_import'):
            return bpy.ops.wm.obj_import(filepath=path, global_scale=scale)
        if C.op_available('import_scene', 'obj'):
            return bpy.ops.import_scene.obj(filepath=path, global_clamp_size=0)
    if ext in ('.gltf', '.glb') and C.op_available('import_scene', 'gltf'):
        return bpy.ops.import_scene.gltf(filepath=path)
    if ext in ('.pmx', '.pmd') and C.op_available('mmd_tools', 'import_model'):
        return bpy.ops.mmd_tools.import_model(filepath=path, scale=scale)
    if ext == '.vrm' and C.op_available('import_scene', 'vrm'):
        return bpy.ops.import_scene.vrm(filepath=path)
    raise ValueError("该格式导入器未安装或未启用: " + ext + "；PMX/PMD 使用 mmd_tools，VRM 使用 VRM Add-on")


def import_model(path, context, scale=1., animations=True, automatic_bones=False, sidecars=True, animation_offset=1.):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError("模型文件不存在: " + path)
    import_path = path
    conversion = None
    if os.path.splitext(path)[1].lower() == '.fbx':
        # Preflight happens before scene changes. Keep the ORIGINAL path for
        # sidecars, image relinking, source custom properties and repeat imports.
        import_path, conversion = ascii_fbx.prepare_import(
            path, C.temp_dir(context), lambda message: C.log(message, context))
    C.mode_object(context)
    before = set(bpy.data.objects)
    original_selection = list(context.selected_objects)
    original_active = C.active(context)
    try:
        result = invoke_import(import_path, scale, animations, automatic_bones, animation_offset)
        if 'FINISHED' not in result:
            raise ValueError("导入器没有完成: " + path)
    except Exception as exc:
        # A backend may fail after partially creating an object hierarchy.
        # Remove only objects created by this attempt, never pre-existing scene objects.
        C.mode_object(context)
        for obj in list(bpy.data.objects):
            if obj not in before:
                bpy.data.objects.remove(obj, do_unlink=True)
        C.select_only(original_selection, context)
        if original_active is not None:
            C.set_active(original_active, context)
        if conversion and conversion.get('converted'):
            C.log('ASCII FBX 后端导入失败；转换记录: ' + json.dumps(conversion, ensure_ascii=False), context)
            raise RuntimeError('ASCII FBX 已转换，但 Blender 后端导入报错: {0}；详见 bomberstudio.log'.format(exc)) from exc
        raise
    created = [o for o in bpy.data.objects if o not in before]
    if not created:
        raise ValueError("导入器未创建物体")
    ext = os.path.splitext(path)[1].lower()
    importer_scales = ext in ('.fbx', '.pmx', '.pmd') or (ext == '.obj' and C.op_available('wm', 'obj_import'))
    if scale != 1 and not importer_scales:
        for obj in created:
            if obj.parent not in created:
                obj.location *= scale
                obj.scale *= scale
        C.update(context)
    for obj in created:
        obj['BomberStudioSource'] = path
    report = materials.apply_sidecars(path, created) if sidecars else {'bound': 0, 'missing': []}
    recovered, missing = materials.relink_images(created, os.path.dirname(path))
    report.update({'model': path, 'objects': len(created), 'relinked': recovered})
    if conversion is not None:
        report['fbx_conversion'] = conversion
    report['missing'].extend(missing)
    # Retain raw exported Materials metadata by reference; no execution of bundled scripts.
    raw_material_dir = os.path.join(os.path.dirname(path), 'Materials')
    if os.path.isdir(raw_material_dir):
        for obj in created:
            obj['BomberStudioMaterialsDirectory'] = raw_material_dir
    C.select_only(created, context)
    core.atomic_json(os.path.join(C.temp_dir(context), 'last-import.json'), report)
    C.log(json.dumps(report, ensure_ascii=False), context)
    return created, report


def import_cache(path, context):
    data = core.validate_cache(path)  # all binary files checked before any scene mutation
    root = os.path.dirname(path)
    collection = C.collection('BomberStudio_NoWind_' + data.get('animation', 'Cache'), context)
    mats = {}
    created = []
    for name, desc in data.get('materials', {}).items():
        mat = bpy.data.materials.new(name + '_NoWind')
        C.principled(mat)
        if desc.get('image'):
            materials.attach_texture(mat, core.local_path(root, desc['image']), uv='BaseColorUV',
                                     ignore_alpha=bool(desc.get('ignoreAlpha')))
        mats[name] = mat
    for row in data['meshes']:
        filename = core.local_path(root, row['cache'])
        with open(filename, 'rb') as stream:
            stream.seek(32)
            vertices = list(struct.iter_unpack('<fff', stream.read(row['vertexCount'] * 12)))
        mesh = bpy.data.meshes.new(row['path'] + '_Cache')
        mesh.from_pydata(vertices, [], [face for sub in row['submeshes'] for face in sub['triangles']])
        mesh.update()
        obj = bpy.data.objects.new(row['path'].split('/')[-1] + '_NoWind', mesh)
        C.link(obj, context, collection)
        obj['BomberStudioSourcePath'] = row['path']
        obj['BomberStudioCacheManifest'] = path
        uv = C.new_uv(mesh, 'BaseColorUV')
        poly_index = 0
        for sub in row['submeshes']:
            name = sub['material']
            desc = data.get('materials', {}).get(name, {})
            mat = mats.get(name)
            if mat is None:
                mat = bpy.data.materials.new(name or 'NoWind_Default')
                mats[name] = mat
            mesh.materials.append(mat)
            coords = row.get('uvSets', {}).get(str(desc.get('uvSet', 0)), [])
            scale, offset = desc.get('scale', [1, 1]), desc.get('offset', [0, 0])
            for face in sub['triangles']:
                polygon = mesh.polygons[poly_index]
                polygon.material_index = len(mesh.materials) - 1
                polygon.use_smooth = True
                for li in polygon.loop_indices:
                    vi = mesh.loops[li].vertex_index
                    u, v = coords[vi] if vi < len(coords) else (0, 0)
                    uv.data[li].uv = (u*scale[0]+offset[0], v*scale[1]+offset[1])
                poly_index += 1
        mod = obj.modifiers.new('BomberStudio_PC2', 'MESH_CACHE')
        mod.cache_format, mod.filepath, mod.deform_mode = 'PC2', filename, 'OVERWRITE'
        mod.time_mode, mod.frame_start = 'FRAME', 1
        created.append(obj)
    scene = context.scene
    scene.render.fps, scene.frame_start, scene.frame_end = data['fps'], 1, data['frames']
    scene.frame_set(1)
    C.select_only(created, context)
    return created


class BOMBER_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = 'bomberstudio.import_model'
    bl_label = '导入 BomberStudio 模型'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.fbx'
    filter_glob = bpy.props.StringProperty(default='*.fbx;*.obj;*.glb;*.gltf;*.pmx;*.pmd;*.vrm', options={'HIDDEN'})
    files = bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory = bpy.props.StringProperty(subtype='DIR_PATH')
    scale = bpy.props.FloatProperty(name='导入比例', default=1., min=0.00001, max=100000)
    animations = bpy.props.BoolProperty(name='导入动画', default=True)
    sidecars = bpy.props.BoolProperty(name='读取 BomberStudio 材质旁文件', default=True)
    animation_offset = bpy.props.FloatProperty(name='动画帧偏移', default=1., description='BomberStudio 零秒动画一般用 1；Blender 自身导出的逐帧往返用 0')
    automatic_bones = bpy.props.BoolProperty(name='自动骨骼方向（可能改变绑定，默认关闭）', default=False)

    def execute(self, context):
        try:
            files = [os.path.join(self.directory, f.name) for f in self.files] if self.files else [self.filepath]
            total, warnings, converted = 0, 0, 0
            for path in files:
                objects, report = import_model(path, context, self.scale, self.animations, self.automatic_bones, self.sidecars, self.animation_offset)
                total += len(objects)
                warnings += len(report['missing'])
                converted += bool(report.get('fbx_conversion', {}).get('converted'))
            cfg = C.settings(context)
            cfg.texture_dir = os.path.dirname(files[-1])
            cfg.source_model = files[-1]
            cfg.last_status = "已导入 {0} 个物体；ASCII FBX 自动转换 {1} 个；材质待检查 {2} 项".format(total, converted, warnings)
            self.report({'WARNING'} if warnings else {'INFO'}, cfg.last_status + "（临时目录 last-import.json）")
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


class BOMBER_OT_cache(bpy.types.Operator, ImportHelper):
    bl_idname = 'bomberstudio.import_cache'
    bl_label = '导入 BomberStudio PC2 物理缓存'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob = bpy.props.StringProperty(default='physics-cache.json;*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            objects = import_cache(self.filepath, context)
            self.report({'INFO'}, "PC2 校验通过，已导入 {0} 个网格".format(len(objects)))
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


class BOMBER_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = 'bomberstudio.export_fbx'
    bl_label = '导出 FBX'
    filename_ext = '.fbx'
    filter_glob = bpy.props.StringProperty(default='*.fbx', options={'HIDDEN'})

    def execute(self, context):
        try:
            if not context.selected_objects:
                raise ValueError("先选择待导出的网格和骨架")
            cfg = C.settings(context)
            export_path = os.path.abspath(self.filepath)
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            stage_dir = os.path.join(C.temp_dir(context), 'fbx-export-' + uuid.uuid4().hex)
            os.makedirs(stage_dir)
            staged = os.path.join(stage_dir, os.path.basename(export_path))
            result = bpy.ops.export_scene.fbx(filepath=staged, use_selection=True,
                                             add_leaf_bones=False, bake_anim=cfg.export_animations,
                                             bake_anim_use_all_actions=cfg.export_all_actions,
                                             bake_anim_use_nla_strips=cfg.export_nla_strips,
                                             path_mode='COPY' if cfg.embed_textures else 'ABSOLUTE',
                                             embed_textures=cfg.embed_textures)
            if 'FINISHED' not in result or not os.path.isfile(staged) or os.path.getsize(staged) < 64:
                raise ValueError('FBX 导出未完成；原文件保持不变')
            if os.path.exists(export_path):
                shutil.copy2(export_path, export_path + '.bak-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
            try:
                os.replace(staged, export_path)
            except OSError as exc:
                import errno
                if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17:
                    raise
                # Cross-volume publishing requires one transient same-directory file.
                incoming = export_path + '.' + uuid.uuid4().hex + '.pending'
                shutil.copy2(staged, incoming)
                os.replace(incoming, export_path)
            self.report({'INFO'}, "FBX 已写入: " + export_path)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_import, BOMBER_OT_cache, BOMBER_OT_export)
