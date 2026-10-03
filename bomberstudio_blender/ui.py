# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import compat as C, core, model_ops, rig_ops


def model_button(layout, action, text=None):
    op = layout.operator('bomberstudio.model', text=text or dict(model_ops.OPERATIONS)[action])
    op.action = action


def rig_button(layout, action, text=None):
    op = layout.operator('bomberstudio.rig', text=text or dict(rig_ops.RIG_OPERATIONS)[action])
    op.action = action


def manage_button(layout, action, text):
    layout.operator('bomberstudio.manage', text=text).action = action


def texture_button(layout, action, text):
    layout.operator('bomberstudio.textures', text=text).action = action


class BOMBER_UL_textures(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name, icon='IMAGE_DATA')
        row.label(text=item.lod)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        lod = C.settings(context).lod_filter
        return ([self.bitflag_filter_item if lod == 'ALL' or item.lod == lod else 0 for item in items], [])


class BOMBER_UL_materials(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.label(text=item.material.name if item.material else '材质已移除', icon='MATERIAL')


class PanelBase:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS' if C.LEGACY else 'UI'
    bl_category = 'BomberStudio'
    bl_options = {'DEFAULT_CLOSED'}


class BOMBER_PT_quick(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_quick'
    bl_label = 'BomberStudio · 快速访问'
    bl_options = set()

    def draw(self, context):
        layout = self.layout
        layout.operator('bomberstudio.import_model', icon='IMPORT')
        layout.operator('bomberstudio.import_cache', icon='IMPORT')
        row = layout.row(align=True)
        texture_button(row, 'COMBINE', '合并相同材质')
        model_button(row, 'JOIN')
        rig_button(layout, 'POSE')
        layout.operator('bomberstudio.export_fbx', icon='EXPORT')
        layout.label(text='材质旁文件自动修复 UV / 平铺 / 偏移')
        if C.settings(context).last_status:
            layout.label(text=C.settings(context).last_status[:72])


class BOMBER_PT_model(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_model'
    bl_label = '模型处理面板'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        for action, label in model_ops.OPERATIONS:
            if action in ('SPLIT_MATERIAL', 'SPLIT_LOOSE', 'JOIN'):
                continue
            if action in ('VG_REMOVE_ALL', 'VG_SORT', 'MODIFIERS', 'TANGENT', 'SPLIT_UV'):
                layout.separator()
            model_button(layout, action)
            if action == 'VG_PREFIX':
                rig_button(layout, 'BONES_FROM_GROUPS')
            if action == 'NORMAL_UV':
                rig_button(layout, 'RENAME_GI')
        layout.prop(cfg, 'modifier_name')
        layout.prop(cfg, 'match_tolerance')
        layout.prop(cfg, 'bone_length')
        layout.label(text='分割默认保留隐藏原模型；可在大纲视图恢复')


class BOMBER_PT_textures(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_textures'
    bl_label = '快速上预览贴图'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        layout.prop(cfg, 'texture_dir')
        row = layout.row(align=True)
        row.prop(cfg, 'lod_filter')
        texture_button(row, 'SCAN', '刷新')
        texture_button(layout, 'SCAN', '读取 DedupedTextures / BomberStudio 贴图')
        layout.template_list('BOMBER_UL_textures', '', cfg, 'textures', cfg, 'texture_index', rows=4)
        if cfg.preview_image:
            layout.template_ID_preview(cfg, 'preview_image', rows=2, cols=4)
        else:
            layout.label(text='未选中图片，请先选择文件夹')
        layout.prop(cfg, 'ignore_alpha')
        texture_button(layout, 'APPLY', '应用贴图到选中物体的活动材质')
        texture_button(layout, 'RELINK', '修复缺失贴图路径（精确文件名匹配）')


class BOMBER_PT_atlas(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_atlas'
    bl_label = '贴图合并 · 材质图集'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        layout.label(text='待合并的材质：')
        layout.template_list('BOMBER_UL_materials', '', cfg, 'atlas_materials', cfg, 'atlas_index', rows=6)
        texture_button(layout, 'LIST', '生成材质列表')
        row = layout.row(align=True)
        row.prop(cfg, 'atlas_uniform')
        row.prop(cfg, 'atlas_tile_size', text='')
        for prop in ('atlas_crop', 'atlas_nearest', 'atlas_pbr', 'atlas_solid_size', 'atlas_padding',
                     'atlas_shape', 'atlas_algorithm', 'atlas_format', 'atlas_max_size',
                     'atlas_keep_originals', 'output_dir'):
            layout.prop(cfg, prop)
        layout.operator('bomberstudio.atlas', icon='EXPORT')
        layout.label(text='默认保留原模型；UV 岛边缘自动扩展')
        layout.label(text='复杂程序节点、多 UV 通道请先烘焙')


class BOMBER_PT_optimization(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_optimization'
    bl_label = '优化 · 材质 / 网格 / 骨骼'

    def draw(self, context):
        layout = self.layout
        texture_button(layout, 'COMBINE', '合并完全相同的材质')
        model_button(layout, 'JOIN')
        model_button(layout, 'VG_REMOVE_EMPTY')
        layout.prop(C.settings(context), 'protected_bones')
        rig_button(layout, 'OPTIMIZE_BONES')
        layout.label(text='图集内置，无需 Material Combiner')


class BOMBER_PT_custom(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_custom'
    bl_label = '自定义模型创建'

    def draw(self, context):
        layout = self.layout
        layout.label(text='合并：选择多个骨架，活动骨架为目标')
        rig_button(layout, 'MERGE_RIGS')
        layout.prop(C.settings(context), 'parent_bone')
        rig_button(layout, 'ATTACH')
        layout.label(text='骨骼名留空：骨架绑定；填写：刚性骨骼父级')


class BOMBER_PT_mmd(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_mmd'
    bl_label = 'MMD 选项'

    def draw(self, context):
        layout = self.layout
        layout.label(text='面向新导入模型的基础清理，不重做既有 Unity 设置')
        layout.prop(C.settings(context), 'remove_mmd_physics')
        rig_button(layout, 'MMD_FIX')
        rig_button(layout, 'REMOVE_PHYSICS')
        layout.label(text='PMX / PMD 导入需要安装 mmd_tools')


class BOMBER_PT_other(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_other'
    bl_label = '其他选项 · 分离 / 翻译'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        row = layout.row(align=True)
        model_button(row, 'SPLIT_MATERIAL', '材质')
        model_button(row, 'SPLIT_LOOSE', '松散块')
        model_button(row, 'SPLIT_UV', 'UV 岛')
        model_button(layout, 'SPLIT_SHAPES', '按形态键生成静态快照')
        layout.prop(cfg, 'translate_scope')
        layout.prop(cfg, 'skip_locked_keys')
        rig_button(layout, 'TRANSLATE')
        layout.prop(cfg, 'csv_path')
        row = layout.row(align=True)
        rig_button(row, 'EXPORT_CSV')
        rig_button(row, 'IMPORT_CSV')
        layout.label(text='内置常用中/日/GI 名称表；未知名称保留')


class BOMBER_PT_visemes(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_visemes'
    bl_label = 'Visemes · 口型'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        obj = C.active(context)
        keys = obj.data.shape_keys if obj and obj.type == 'MESH' else None
        if keys:
            for prop in ('viseme_aa', 'viseme_oh', 'viseme_ch'):
                layout.prop_search(cfg, prop, keys, 'key_blocks')
        else:
            layout.label(text='请选择包含 AA/OH/CH 形态键的网格')
        layout.prop(cfg, 'viseme_intensity')
        rig_button(layout, 'VISEMES')
        row = layout.row(align=True)
        rig_button(row, 'PREVIEW_VISEME')
        rig_button(row, 'STOP_VISEME')


class BOMBER_PT_parent(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_parent'
    bl_label = 'Bone Parenting · 骨骼父级'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        rig = rig_ops.find_armature(context)
        if rig:
            layout.prop_search(cfg, 'parent_bone', rig.data, 'bones')
        else:
            layout.prop(cfg, 'parent_bone')
        layout.label(text='选中子骨骼后设置，保持头尾坐标')
        rig_button(layout, 'PARENT_BONES')


class BOMBER_PT_scale(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_scale'
    bl_label = 'Model Scaling · 模型缩放'

    def draw(self, context):
        layout = self.layout
        layout.prop(C.settings(context), 'target_height')
        rig_button(layout, 'SCALE')
        layout.label(text='按照 Z 轴世界高度；骨架与子网格仅缩放一次')


class BOMBER_PT_eyes(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_eyes'
    bl_label = 'Eye Tracking · 眼球追踪'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        layout.prop(cfg, 'eye_mode', expand=True)
        for prop in ('head_bone', 'left_eye_bone', 'right_eye_bone', 'left_eye_group', 'right_eye_group'):
            layout.prop(cfg, prop)
        rig_button(layout, 'EYES')
        layout.prop(cfg, 'eye_rotation')
        row = layout.row(align=True)
        rig_button(row, 'TEST_EYES')
        rig_button(row, 'RESET_EYES')
        layout.label(text='骨骼辅助与预览；VRChat SDK 绑定在 Unity 设置')


class BOMBER_PT_settings(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_settings'
    bl_label = '设置与诊断'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        for prop in ('embed_textures', 'export_animations', 'export_all_actions', 'export_nla_strips', 'export_translation_csv', 'backup_before_edit', 'temp_dir'):
            layout.prop(cfg, prop)
        manage_button(layout, 'BACKUP', '保存模型回滚副本')
        manage_button(layout, 'DIAGNOSTICS', '导出兼容性与模型诊断')
        manage_button(layout, 'OPEN_TEMP', '打开日志 / 临时文件夹')
        manage_button(layout, 'CLEAR_PREVIEW', '清空贴图列表与预览')
        layout.label(text='Blender: ' + bpy.app.version_string)


class BOMBER_PT_updates(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_updates'
    bl_label = '检查版本更新'

    def draw(self, context):
        layout, cfg = self.layout, C.settings(context)
        layout.label(text='当前版本: ' + '.'.join(str(v) for v in core.VERSION))
        layout.prop(cfg, 'update_repository')
        manage_button(layout, 'CHECK_UPDATE', '手动检查本插件更新（15 秒超时）')
        layout.label(text=cfg.update_status[:90])
        layout.operator('bomberstudio.install_update')
        manage_button(layout, 'RESTORE_UPDATE', '还原上一次插件备份')
        layout.label(text='更新不自动安装，安装 / 回滚后重启 Blender')


class BOMBER_PT_credits(PanelBase, bpy.types.Panel):
    bl_idname = 'BOMBER_PT_credits'
    bl_label = '贡献者名单 / 功能来源'

    def draw(self, context):
        layout = self.layout
        layout.label(text='BomberStudio Blender Bridge')
        layout.label(text='基于本机 BomberStudio 导出数据格式独立实现')
        layout.label(text='界面与工作流参考 StarBobis / MIMIBlender')
        layout.label(text='角色工具参考 Team Neoneko / Cats')
        layout.label(text='无需同时安装 MIMI / Cats；不复制其自动更新器')
        layout.label(text='详细功能与测试范围见安装包 README.md')


CLASSES = (BOMBER_UL_textures, BOMBER_UL_materials, BOMBER_PT_quick, BOMBER_PT_model, BOMBER_PT_textures,
           BOMBER_PT_atlas, BOMBER_PT_optimization, BOMBER_PT_custom, BOMBER_PT_mmd, BOMBER_PT_other,
           BOMBER_PT_visemes, BOMBER_PT_parent, BOMBER_PT_scale, BOMBER_PT_eyes, BOMBER_PT_settings,
           BOMBER_PT_updates, BOMBER_PT_credits)


def menu_import(self, context):
    self.layout.operator('bomberstudio.import_model', text='BomberStudio Model (.fbx/.obj/...)')
    self.layout.operator('bomberstudio.import_cache', text='BomberStudio Physics Cache (.json)')
