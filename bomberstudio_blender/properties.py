# SPDX-License-Identifier: GPL-3.0-or-later
import os
import bpy
from . import compat as C, materials, updates

OUTPUT_ROOT = os.path.dirname(C.DEFAULT_TEMP)


def preview_changed(self, context):
    if 0 <= self.texture_index < len(self.textures):
        path = self.textures[self.texture_index].path
        if os.path.isfile(path):
            try:
                self.preview_image = materials.load_image(path)
            except RuntimeError as exc:
                C.log("预览失败: " + str(exc), context)


class BOMBER_PG_texture(bpy.types.PropertyGroup):
    path = bpy.props.StringProperty()
    lod = bpy.props.StringProperty()


class BOMBER_PG_material(bpy.types.PropertyGroup):
    material = bpy.props.PointerProperty(type=bpy.types.Material)
    enabled = bpy.props.BoolProperty(default=True)


class BOMBER_PG_settings(bpy.types.PropertyGroup):
    source_model = bpy.props.StringProperty(name='模型源文件', subtype='FILE_PATH')
    texture_dir = bpy.props.StringProperty(name='贴图 / DedupedTextures 目录', subtype='DIR_PATH')
    temp_dir = bpy.props.StringProperty(name='临时 / 日志 / 备份目录', subtype='DIR_PATH', default=C.DEFAULT_TEMP)
    output_dir = bpy.props.StringProperty(name='图集输出目录', subtype='DIR_PATH', default=C.DEFAULT_OUTPUT)
    textures = bpy.props.CollectionProperty(type=BOMBER_PG_texture)
    texture_index = bpy.props.IntProperty(default=0, update=preview_changed)
    lod_filter = bpy.props.EnumProperty(name='LOD', items=[('ALL', '全部', '')] + [('LOD'+str(i), 'LOD'+str(i), '') for i in range(9)] + [('未标记', '未标记', '')])
    preview_image = bpy.props.PointerProperty(type=bpy.types.Image)
    ignore_alpha = bpy.props.BoolProperty(name='忽略基础色 Alpha（防止数据通道变透明）', default=True)
    atlas_materials = bpy.props.CollectionProperty(type=BOMBER_PG_material)
    atlas_index = bpy.props.IntProperty()
    atlas_uniform = bpy.props.BoolProperty(name='统一贴图尺寸', default=True)
    atlas_tile_size = bpy.props.IntProperty(name='最长边', default=1024, min=16, max=4096)
    atlas_crop = bpy.props.BoolProperty(name='根据 UV 边界裁剪', default=True)
    atlas_nearest = bpy.props.BoolProperty(name='禁用抗锯齿缩放（最近邻）', default=False)
    atlas_pbr = bpy.props.BoolProperty(name='图集 PBR 贴图', default=False,
                                     description='额外输出 Roughness / Metallic / Normal / Emission；仅支持已烘焙的直接贴图/常量')
    atlas_solid_size = bpy.props.IntProperty(name='纯色纹理尺寸', default=32, min=4, max=1024)
    atlas_padding = bpy.props.IntProperty(name='纹理间距 / 边缘扩展', default=4, min=0, max=64)
    atlas_shape = bpy.props.EnumProperty(name='图集尺寸', items=[('SQUARE', '正方形（2 的幂）', ''), ('RECT', '紧凑矩形', '')])
    atlas_algorithm = bpy.props.EnumProperty(name='打包算法', items=[('BINARY', '二叉树', ''), ('SHELF', '行架', '')])
    atlas_format = bpy.props.EnumProperty(name='输出格式', items=[('PNG', 'PNG', ''), ('TARGA', 'TGA', '')])
    atlas_max_size = bpy.props.IntProperty(name='图集最大边长', default=4096, min=256, max=8192)
    atlas_keep_originals = bpy.props.BoolProperty(name='创建副本并保留原模型', default=True)
    backup_before_edit = bpy.props.BoolProperty(name='修改前保存 .blend 回滚副本', default=False,
                                               description='较大的模型会增加磁盘占用；所有备份写入指定临时目录')
    match_tolerance = bpy.props.FloatProperty(name='位置匹配容差', default=0.0001, min=0.000001, precision=6)
    bone_length = bpy.props.FloatProperty(name='新骨骼长度', default=0.03, min=0.0001)
    modifier_name = bpy.props.StringProperty(name='修改器名称', description='空白 = 可见的非骨架修改器；形态键仅支持保序形变修改器')
    viseme_aa = bpy.props.StringProperty(name='Viseme AA', default='AA')
    viseme_oh = bpy.props.StringProperty(name='Viseme OH', default='OH')
    viseme_ch = bpy.props.StringProperty(name='Viseme CH', default='CH')
    viseme_intensity = bpy.props.FloatProperty(name='形态键混合强度', default=1., min=0., max=2.)
    viseme_preview = bpy.props.IntProperty(default=-1)
    parent_bone = bpy.props.StringProperty(name='父骨骼', default='')
    target_height = bpy.props.FloatProperty(name='目标身高（米）', default=1.7, min=.01, max=100.)
    eye_mode = bpy.props.EnumProperty(name='眼球模式', items=[('SDK3', 'VRChat SDK3', ''), ('LEGACY', 'Legacy', '')])
    head_bone = bpy.props.StringProperty(name='头部骨骼', default='Head')
    left_eye_bone = bpy.props.StringProperty(name='左眼骨骼', default='LeftEye')
    right_eye_bone = bpy.props.StringProperty(name='右眼骨骼', default='RightEye')
    left_eye_group = bpy.props.StringProperty(name='左眼权重组', default='LeftEye')
    right_eye_group = bpy.props.StringProperty(name='右眼权重组', default='RightEye')
    eye_rotation = bpy.props.FloatProperty(name='预览旋转角度', default=10, min=-45, max=45)
    protected_bones = bpy.props.StringProperty(name='保留末端骨骼（逗号分隔）', default='Head,LeftEye,RightEye,Root')
    remove_mmd_physics = bpy.props.BoolProperty(name='MMD 整理时移除刚体与关节', default=True)
    skip_locked_keys = bpy.props.BoolProperty(name='跳过锁定的形态键', default=True)
    translate_scope = bpy.props.EnumProperty(name='翻译范围', items=[('ALL', '全部', ''), ('KEYS', '形态键', ''),
                                                                           ('BONES', '骨骼', ''), ('OBJECTS', '物体', ''), ('MATERIALS', '材质', '')])
    csv_path = bpy.props.StringProperty(name='名称映射 CSV', subtype='FILE_PATH', default=os.path.join(OUTPUT_ROOT, '名称映射.csv'))
    export_translation_csv = bpy.props.BoolProperty(name='翻译时导出 CSV', default=False)
    embed_textures = bpy.props.BoolProperty(name='导出 FBX 时嵌入贴图', default=False)
    export_animations = bpy.props.BoolProperty(name='导出 FBX 动画', default=True)
    export_all_actions = bpy.props.BoolProperty(name='导出全部兼容 Action（可能较慢）', default=False)
    export_nla_strips = bpy.props.BoolProperty(name='分别导出 NLA 片段', default=False)
    update_repository = bpy.props.StringProperty(
        name='本插件发布仓库', default=updates.DEFAULT_REPOSITORY,
        description='支持 owner/repo 或完整 GitHub 仓库地址；留空使用默认仓库，可自定义，不自动下载安装')
    update_status = bpy.props.StringProperty(default='仅手动检查；支持 owner/repo 或完整 GitHub 地址')
    last_status = bpy.props.StringProperty(default='')


CLASSES = (BOMBER_PG_texture, BOMBER_PG_material, BOMBER_PG_settings)
