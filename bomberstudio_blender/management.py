# SPDX-License-Identifier: GPL-3.0-or-later
import os
import bpy
from bpy_extras.io_utils import ImportHelper
from . import compat as C, core, updates, update_install, online_update


def validate_addon_zip(filename):
    return update_install.validate_addon_zip(filename, bpy.app.version)


def install_local_zip(filename, context):
    return update_install.install_zip(filename, os.path.dirname(__file__), C.temp_dir(context),
                                      bpy.app.version, extension=__package__.startswith('bl_ext.'))


class BOMBER_OT_update_install(bpy.types.Operator, ImportHelper):
    bl_idname = 'bomberstudio.install_update'
    bl_label = '从本地 ZIP 安装更新（保留备份）'
    filename_ext = '.zip'
    filter_glob = bpy.props.StringProperty(default='*.zip', options={'HIDDEN'})

    def execute(self, context):
        try:
            online_update.idle_required()
            receipt = install_local_zip(self.filepath, context)
            online_update.mark_restart(receipt, context)
            self.report({'INFO'}, C.settings(context).update_status)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


class BOMBER_OT_manage(bpy.types.Operator):
    bl_idname = 'bomberstudio.manage'
    bl_label = '插件管理'
    action = bpy.props.StringProperty(default='DIAGNOSTICS')

    def execute(self, context):
        try:
            cfg = C.settings(context)
            if self.action == 'CHECK_UPDATE':
                online_update.idle_required()
                online_update.online_access()
                if not bpy.app.background:
                    return bpy.ops.bomberstudio.online_update('INVOKE_DEFAULT', action='CHECK')
                online_update.clear_check()
                record = updates.check_latest(cfg.update_repository, core.VERSION)
                online_update.store_check(record, context)
                self.report({'INFO'}, cfg.update_status)
            elif self.action == 'OPEN_RELEASE':
                bpy.ops.wm.url_open(url=updates.releases_url(cfg.update_repository))
            elif self.action == 'RESTORE_UPDATE':
                online_update.idle_required(allow_restart=True)
                receipt = update_install.restore(os.path.dirname(__file__), C.temp_dir(context))
                online_update.mark_restart(receipt, context, restored=True)
            elif self.action == 'DIAGNOSTICS':
                source = bpy.path.abspath(cfg.source_model) if cfg.source_model else ''
                report = {
                    'addon': list(core.VERSION), 'blender': bpy.app.version_string,
                    'python': __import__('sys').version, 'source_model': source,
                    'source_tools': dict(zip(('state', 'message'), __import__(__package__ + '.source_tools', fromlist=['status']).status())),
                    'sidecars': [source+s for s in core.SIDECARS if source and os.path.isfile(source+s)],
                    'importers': dict((group+'.'+name, C.op_available(group, name)) for group, name in
                                      [('import_scene', 'fbx'), ('wm', 'obj_import'), ('import_scene', 'obj'),
                                       ('import_scene', 'gltf'), ('mmd_tools', 'import_model'), ('import_scene', 'vrm')]),
                    'selected_meshes': [{'name': o.name, 'vertices': len(o.data.vertices),
                                         'polygons': len(o.data.polygons), 'uv_layers': [u.name for u in o.data.uv_layers],
                                         'shape_keys': len(o.data.shape_keys.key_blocks) if o.data.shape_keys else 0}
                                        for o in C.meshes(context)],
                    'missing_images': [i.filepath for i in bpy.data.images if i.source == 'FILE' and not i.packed_file
                                       and not os.path.isfile(bpy.path.abspath(i.filepath))],
                }
                path = core.atomic_json(os.path.join(C.temp_dir(context), 'diagnostics.json'), report)
                cfg.last_status = '诊断报告: ' + path
                self.report({'INFO'}, cfg.last_status)
            elif self.action == 'BACKUP':
                cfg.last_status = C.snapshot(context, 'manual')
            elif self.action == 'OPEN_TEMP':
                bpy.ops.wm.path_open(filepath=C.temp_dir(context))
            elif self.action == 'CLEAR_PREVIEW':
                cfg.preview_image = None
                cfg.textures.clear()
                cfg.texture_index = 0
            C.log("管理操作: " + self.action, context)
            return {'FINISHED'}
        except Exception as exc:
            if self.action == 'CHECK_UPDATE':
                C.settings(context).update_status = '检查失败: ' + str(exc)[:200]
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_update_install, BOMBER_OT_manage)
