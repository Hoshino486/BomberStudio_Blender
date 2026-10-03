# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import json
import os
import re
import shutil
import time
import uuid
import zipfile
import bpy
from bpy_extras.io_utils import ImportHelper
from . import compat as C, core, updates


def validate_addon_zip(filename):
    """Reject archive traversal, links, oversized files and unrelated packages before extracting."""
    with zipfile.ZipFile(filename) as archive:
        files = [i for i in archive.infolist() if not i.filename.endswith('/')]
        names = [i.filename.replace('\\', '/') for i in files]
        root = 'bomberstudio_blender/' if 'bomberstudio_blender/__init__.py' in names else ''
        if root + '__init__.py' not in names or root + 'core.py' not in names:
            raise ValueError("ZIP 不是 BomberStudio Blender 插件安装包")
        if sum(i.file_size for i in files) > 64*1024*1024:
            raise ValueError("插件 ZIP 解压大小超过 64MiB")
        content = {}
        for info, name in zip(files, names):
            if root and not name.startswith(root):
                raise ValueError("ZIP 含无关顶层文件")
            rel = name[len(root):]
            if name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '..' in rel.split('/'):
                raise ValueError("ZIP 含越界路径")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP 含符号链接")
            if info.file_size > 8*1024*1024:
                raise ValueError("插件文件过大")
            if rel.endswith(('.exe', '.dll', '.pyd', '.bat', '.cmd', '.ps1')):
                raise ValueError("该插件升级包应为纯 Python，不接受可执行附件")
            content[rel] = archive.read(info)
        for rel, payload in content.items():
            if rel.endswith('.py'):
                source = payload.decode('utf-8-sig')
                # Optional 4.1+ provider is deliberately inert on older Python.
                # All zip path, size and symlink checks above still apply.
                deferred = rel.startswith('vendor/io_scene_valvesource/') and bpy.app.version < (4, 1, 0)
                if not deferred:
                    ast.parse(source, filename=rel)
        init = content['__init__.py'].decode('utf8')
        if 'BomberStudio' not in init:
            raise ValueError("插件标识不匹配")
        return content


def install_local_zip(filename, context):
    content = validate_addon_zip(filename)
    target = os.path.realpath(os.path.dirname(__file__))
    parent = os.path.dirname(target)
    nonce = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    stage = os.path.join(C.temp_dir(context), 'update-stage-' + nonce)
    os.makedirs(stage)
    for name, payload in content.items():
        core.atomic_bytes(core.local_path(stage, name), payload, backup=False)
    # Commit on the destination filesystem; copy fallback also works if temp is on another disk.
    incoming = os.path.join(parent, '.bomberstudio-incoming-' + nonce)
    backup = os.path.join(C.temp_dir(context), 'addon-backup-' + nonce)
    shutil.copytree(target, backup)
    shutil.copytree(stage, incoming)
    displaced = os.path.join(parent, '.bomberstudio-previous-' + nonce)
    os.replace(target, displaced)
    try:
        os.replace(incoming, target)
    except Exception:
        os.replace(displaced, target)
        raise
    receipt = {'target': target, 'backup': backup, 'previous': displaced, 'package': filename,
               'sha256': core.sha256(filename), 'restart_required': True}
    core.atomic_json(os.path.join(C.temp_dir(context), 'last-update.json'), receipt)
    return receipt


class BOMBER_OT_update_install(bpy.types.Operator, ImportHelper):
    bl_idname = 'bomberstudio.install_update'
    bl_label = '从本地 ZIP 安装更新（保留备份）'
    filename_ext = '.zip'
    filter_glob = bpy.props.StringProperty(default='*.zip', options={'HIDDEN'})

    def execute(self, context):
        try:
            install_local_zip(self.filepath, context)
            C.settings(context).update_status = '更新已写入；请重启 Blender。旧版备份保留在临时目录。'
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
                if hasattr(bpy.app, 'online_access') and not bpy.app.online_access:
                    raise ValueError('Blender 当前关闭了在线访问；请在首选项启用后手动重试')
                record = updates.check_latest(cfg.update_repository, core.VERSION)
                record['checked'] = time.strftime('%Y-%m-%d %H:%M:%S')
                record['repository_input'] = cfg.update_repository
                core.atomic_json(os.path.join(C.temp_dir(context), 'last-update-check.json'), record)
                # Old .blend files may explicitly store an empty setting. Preserve
                # nonempty custom repositories, including the full pasted URL.
                if not cfg.update_repository.strip():
                    cfg.update_repository = record['repository']
                cfg.update_status = record['status']
                C.log('更新检查: ' + record['repository'] + ' / ' + record['tag'] + ' / ' + record['relation'], context)
                self.report({'INFO'}, cfg.update_status)
            elif self.action == 'OPEN_RELEASE':
                bpy.ops.wm.url_open(url=updates.releases_url(cfg.update_repository))
            elif self.action == 'RESTORE_UPDATE':
                receipt = core.read_json(os.path.join(C.temp_dir(context), 'last-update.json'))
                target = os.path.realpath(os.path.dirname(__file__))
                if os.path.realpath(receipt['target']) != target:
                    raise ValueError("备份属于另一个插件安装目录")
                previous = os.path.realpath(receipt['previous'])
                if os.path.dirname(previous) != os.path.dirname(target) or not os.path.basename(previous).startswith('.bomberstudio-previous-'):
                    raise ValueError("回滚路径校验失败")
                if not os.path.isfile(os.path.join(previous, '__init__.py')):
                    raise ValueError("旧版目录不完整")
                displaced = os.path.join(os.path.dirname(target), '.bomberstudio-restored-from-' + uuid.uuid4().hex)
                os.replace(target, displaced)
                try:
                    os.replace(previous, target)
                except Exception:
                    os.replace(displaced, target)
                    raise
                cfg.update_status = '旧版已恢复，请重启 Blender；替换版也已保留'
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
