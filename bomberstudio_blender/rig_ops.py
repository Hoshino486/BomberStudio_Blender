# SPDX-License-Identifier: GPL-3.0-or-later
import csv
import io
import math
import os
import re
import bpy
from mathutils import Vector
from . import compat as C, core, model_ops

BONE_NAMES = {
    '全ての親': 'Root', 'センター': 'Root', '下半身': 'Hips', '腰': 'Hips',
    '上半身': 'Spine', '上半身2': 'Chest', '首': 'Neck', '頭': 'Head',
    '左目': 'LeftEye', '右目': 'RightEye', '左肩': 'LeftShoulder', '右肩': 'RightShoulder',
    '左腕': 'LeftUpperArm', '右腕': 'RightUpperArm', '左ひじ': 'LeftLowerArm', '右ひじ': 'RightLowerArm',
    '左手首': 'LeftHand', '右手首': 'RightHand', '左足': 'LeftUpperLeg', '右足': 'RightUpperLeg',
    '左ひざ': 'LeftLowerLeg', '右ひざ': 'RightLowerLeg', '左足首': 'LeftFoot', '右足首': 'RightFoot',
    '左つま先': 'LeftToes', '右つま先': 'RightToes', 'あ': 'AA', 'お': 'OH', 'ち': 'CH',
    'まばたき': 'Blink', 'ウィンク': 'BlinkLeft', 'ウィンク右': 'BlinkRight', '笑い': 'Smile',
    'Bip001 Pelvis': 'Hips', 'Bip001 Spine': 'Spine', 'Bip001 Spine1': 'Chest',
    'Bip001 Spine2': 'UpperChest', 'Bip001 Neck': 'Neck', 'Bip001 Head': 'Head',
    'Bip001 L Clavicle': 'LeftShoulder', 'Bip001 R Clavicle': 'RightShoulder',
    'Bip001 L UpperArm': 'LeftUpperArm', 'Bip001 R UpperArm': 'RightUpperArm',
    'Bip001 L Forearm': 'LeftLowerArm', 'Bip001 R Forearm': 'RightLowerArm',
    'Bip001 L Hand': 'LeftHand', 'Bip001 R Hand': 'RightHand',
    'Bip001 L Thigh': 'LeftUpperLeg', 'Bip001 R Thigh': 'RightUpperLeg',
    'Bip001 L Calf': 'LeftLowerLeg', 'Bip001 R Calf': 'RightLowerLeg',
    'Bip001 L Foot': 'LeftFoot', 'Bip001 R Foot': 'RightFoot',
    'Bip001 L Toe0': 'LeftToes', 'Bip001 R Toe0': 'RightToes',
    'Eye_L': 'LeftEye', 'Eye_R': 'RightEye', 'Root_M': 'Root', 'Hip': 'Hips',
    '身体': 'Body', '顔': 'Face', '髪': 'Hair', '瞳': 'Eyes',
}
VISEMES = (
    ('sil', (0, 0, 0)), ('PP', (0, 0, 0.08)), ('FF', (.15, 0, .45)),
    ('TH', (.35, 0, .50)), ('DD', (.30, 0, .65)), ('kk', (.55, 0, .35)),
    ('CH', (0, 0, 1)), ('SS', (0, .05, .75)), ('nn', (.20, 0, .50)),
    ('RR', (.35, .35, .25)), ('aa', (1, 0, 0)), ('E', (.50, 0, .45)),
    ('ih', (.25, 0, .65)), ('oh', (.15, .85, 0)), ('ou', (0, 1, 0)),
)
RIG_OPERATIONS = (
    ('BONES_FROM_GROUPS', '根据顶点组生成基础骨骼'),
    ('RENAME_GI', '重命名 GI / MMD 常用骨骼'),
    ('POSE', '开始 / 结束姿态模式'),
    ('MERGE_RIGS', '合并骨架'),
    ('ATTACH', '附加网格到骨架 / 骨骼'),
    ('MMD_FIX', 'MMD 基础整理'),
    ('REMOVE_PHYSICS', '移除刚体与关节'),
    ('TRANSLATE', '离线名称翻译'),
    ('EXPORT_CSV', '导出名称 CSV'),
    ('IMPORT_CSV', '导入名称 CSV 并翻译'),
    ('VISEMES', '创建 15 个 VRChat 口型'),
    ('PREVIEW_VISEME', '预览下一个口型'),
    ('STOP_VISEME', '停止口型预览'),
    ('PARENT_BONES', '设置选中骨骼的父骨骼'),
    ('SCALE', '按目标身高缩放模型'),
    ('EYES', '创建 / 定位眼球追踪骨骼'),
    ('TEST_EYES', '预览眼球旋转'),
    ('RESET_EYES', '重置眼球姿态'),
    ('OPTIMIZE_BONES', '移除无权重的末端骨骼'),
)


def find_armature(context):
    obj = C.active(context)
    if obj and obj.type == 'ARMATURE':
        return obj
    if obj and obj.type == 'MESH':
        rig = obj.find_armature()
        if rig:
            return rig
    return next((o for o in context.selected_objects if o.type == 'ARMATURE'), None)


def require_armature(context):
    result = find_armature(context)
    if result is None:
        raise ValueError("请选择骨架或绑定到骨架的网格")
    return result


def rig_meshes(rig, context):
    return [o for o in context.scene.objects if o.type == 'MESH' and
            (o.find_armature() == rig or o.parent == rig)]


def generate_bones(context):
    meshes = C.ensure_meshes(context)
    if not any(o.vertex_groups for o in meshes):
        raise ValueError("选中网格没有顶点组")
    centres = {}
    for obj in meshes:
        if obj.find_armature():
            raise ValueError(obj.name + " 已绑定骨架；先在副本解除旧绑定")
        for name, point in model_ops.group_centres(obj).items():
            centres.setdefault(name, []).append(point)
    data = bpy.data.armatures.new('BomberStudio_Rig')
    rig = bpy.data.objects.new('BomberStudio_Rig', data)
    C.link(rig, context)
    C.select_only([rig], context)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for name, points in sorted(centres.items(), key=lambda x: core.natural_key(x[0])):
            bone = data.edit_bones.new(name)
            bone.head = sum(points, Vector()) / len(points)
            bone.tail = bone.head + Vector((0, 0, C.settings(context).bone_length))
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in meshes:
        mod = obj.modifiers.new('BomberStudio_Armature', 'ARMATURE')
        mod.object = rig
    C.select_only([rig] + meshes, context)
    return rig


def rename_bones(rig, mapping, context):
    used = set(b.name for b in rig.data.bones)
    actual = {}
    for bone in rig.data.bones:
        name = mapping.get(bone.name)
        if name and name != bone.name and name not in used:
            actual[bone.name] = name
            used.add(name)
    meshes = rig_meshes(rig, context)
    for src, dst in actual.items():
        # Blender propagates normal bound-armature names; handle unparented modifier users too.
        rig.data.bones[src].name = dst
        for obj in meshes:
            group = obj.vertex_groups.get(src)
            if group and dst not in obj.vertex_groups:
                group.name = dst
    return len(actual)


def merge_rigs(context):
    target = require_armature(context)
    rigs = [o for o in context.selected_objects if o.type == 'ARMATURE']
    if len(rigs) < 2 or target not in rigs:
        raise ValueError("选择至少两个骨架，活动骨架作为目标")
    if any(o.animation_data and (o.animation_data.action or o.animation_data.nla_tracks) for o in rigs if o != target):
        raise ValueError("源骨架含动画；先保存并清除源骨架动画再合并，避免静默丢失 Action/NLA")
    used = set(b.name for b in target.data.bones)
    mesh_bindings = []
    for source in rigs:
        if source == target:
            continue
        objects = rig_meshes(source, context)
        renames = {}
        for bone in source.data.bones:
            original = bone.name
            new_name = original
            if new_name in used:
                new_name = source.name + '_' + original
                index = 1
                while new_name in used:
                    new_name = source.name + '_' + original + '_' + str(index)
                    index += 1
                renames[original] = new_name
            used.add(new_name)
        rename_bones(source, renames, context)
        for obj in objects:
            mesh_bindings.append((obj, source, obj.matrix_world.copy()))
    C.select_only([target] + [r for r in rigs if r != target], context)
    bpy.ops.object.join()
    for obj, source, matrix in mesh_bindings:
        # The source ID may already be invalid; joining normally remaps users.
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object is None:
                mod.object = target
        if obj.parent is None or obj.parent.type == 'ARMATURE':
            obj.parent = target
            obj.matrix_world = matrix
    return target


def attach_meshes(context):
    rig = require_armature(context)
    objects = C.ensure_meshes(context)
    cfg = C.settings(context)
    bone = cfg.parent_bone
    if bone and bone not in rig.data.bones:
        raise ValueError("父骨骼不存在: " + bone)
    for obj in objects:
        matrix = obj.matrix_world.copy()
        if bone:
            obj.parent, obj.parent_type, obj.parent_bone = rig, 'BONE', bone
        else:
            obj.parent = rig
            obj.parent_type = 'OBJECT'
            mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
            if mod is None:
                mod = obj.modifiers.new('BomberStudio_Armature', 'ARMATURE')
            mod.object = rig
        obj.matrix_world = matrix


def make_visemes(obj, aa, oh, ch, intensity=1.0):
    if obj is None or obj.type != 'MESH' or not obj.data.shape_keys:
        raise ValueError("活动网格需要 AA / OH / CH 源形态键")
    C.single_user(obj)
    keys = obj.data.shape_keys
    if not keys.use_relative:
        raise ValueError("口型生成要求相对形态键")
    if any(name not in keys.key_blocks for name in (aa, oh, ch)):
        raise ValueError("请选择有效的 AA、OH、CH 源形态键")
    targets = ['vrc.v_' + name for name, _ in VISEMES]
    if any(name in keys.key_blocks for name in targets):
        raise ValueError("已存在 vrc.v_* 口型；请先重命名或移除旧口型，保留其驱动/动画")
    source = [keys.key_blocks[n] for n in (aa, oh, ch)]
    basis = keys.reference_key
    deltas = [[v.co - key.relative_key.data[i].co for i, v in enumerate(key.data)] for key in source]
    for name, weights in VISEMES:
        new = obj.shape_key_add(name='vrc.v_' + name, from_mix=False)
        new.relative_key = basis
        for i, point in enumerate(new.data):
            point.co = basis.data[i].co + sum((deltas[k][i]*weights[k]*intensity for k in range(3)), Vector())
    return len(VISEMES)


def translate(context, dictionary, export_only=False):
    cfg = C.settings(context)
    records = []
    seen = set()
    for obj in list(context.selected_objects):
        items = []
        if cfg.translate_scope in ('ALL', 'OBJECTS'):
            items.append(('OBJECT', obj))
        if obj.type == 'MESH' and cfg.translate_scope in ('ALL', 'KEYS') and obj.data.shape_keys:
            items.extend(('SHAPEKEY', k) for k in obj.data.shape_keys.key_blocks[1:]
                         if not (cfg.skip_locked_keys and getattr(k, 'lock_shape', False)))
        if cfg.translate_scope in ('ALL', 'MATERIALS'):
            items.extend(('MATERIAL', s.material) for s in obj.material_slots if s.material)
        for kind, item in items:
            key = (kind, item.as_pointer())
            if key in seen:
                continue
            seen.add(key)
            before = item.name
            after = dictionary.get(before, before)
            records.append((kind, before, after))
            if not export_only:
                item.name = after
        if obj.type == 'ARMATURE' and cfg.translate_scope in ('ALL', 'BONES'):
            for bone in obj.data.bones:
                records.append(('BONE', bone.name, dictionary.get(bone.name, bone.name)))
            if not export_only:
                rename_bones(obj, dictionary, context)
    return records


def remove_physics(context):
    selected = list(context.selected_objects)
    roots = selected + [r for r in (find_armature(context),) if r]
    scoped = set(roots)
    def walk(obj):
        for child in obj.children:
            scoped.add(child)
            walk(child)
    for root in roots:
        walk(root)
    remove = [o for o in scoped if o.get('mmd_type') in ('RIGID_BODY', 'JOINT') or
              getattr(o, 'mmd_type', '') in ('RIGID_BODY', 'JOINT') or o.rigid_body or o.rigid_body_constraint]
    for obj in remove:
        bpy.data.objects.remove(obj, do_unlink=True)
    return len(remove)


def parent_bones(context):
    rig = require_armature(context)
    name = C.settings(context).parent_bone
    if name not in rig.data.bones:
        raise ValueError("请选择有效父骨骼")
    selected = ([b.name for b in rig.data.edit_bones if b.select and b.name != name] if rig.mode == 'EDIT' else
                [b.name for b in rig.data.bones if C.bone_selected(rig, b) and b.name != name])
    if not selected:
        raise ValueError("先在姿态 / 编辑模式选择待设置父级的骨骼")
    parent = rig.data.bones[name]
    ancestors = set()
    while parent:
        ancestors.add(parent.name)
        parent = parent.parent
    if ancestors.intersection(selected):
        raise ValueError("父子关系将产生循环")
    C.select_only([rig], context)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for child in selected:
            rig.data.edit_bones[child].use_connect = False
            rig.data.edit_bones[child].parent = rig.data.edit_bones[name]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def scale_model(context):
    cfg = C.settings(context)
    rig = find_armature(context)
    objects = list(context.selected_objects)
    if rig:
        objects = list(set(objects + rig_meshes(rig, context) + [rig]))
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        raise ValueError("请选择角色网格")
    zs = [C.matmul(o.matrix_world, Vector(c)).z for o in meshes for c in o.bound_box]
    height = max(zs)-min(zs)
    if height < 1e-9:
        raise ValueError("模型高度为零")
    scene_units = context.scene.unit_settings.scale_length or 1.
    factor = cfg.target_height / (height * scene_units)
    roots = [o for o in objects if o.parent not in objects]
    # Scale about the scene origin; hierarchy roots only prevent double transforms.
    for obj in roots:
        obj.location *= factor
        obj.scale *= factor
    C.update(context)
    return factor


def setup_eyes(context):
    cfg = C.settings(context)
    rig = require_armature(context)
    objects = rig_meshes(rig, context)
    positions = {}
    for side, group_name, bone_name in (('L', cfg.left_eye_group, cfg.left_eye_bone),
                                       ('R', cfg.right_eye_group, cfg.right_eye_bone)):
        entries = [model_ops.group_centres(o)[group_name] for o in objects
                   if group_name in model_ops.group_centres(o)]
        if not entries and bone_name in rig.data.bones:
            positions[bone_name] = rig.data.bones[bone_name].head_local.copy()
        elif entries:
            positions[bone_name] = C.matmul(rig.matrix_world.inverted(), (sum(entries, Vector()) / len(entries)))
        else:
            raise ValueError(side + " 眼球缺少权重组或已有眼骨；请指定名称")
    if cfg.head_bone and cfg.head_bone not in rig.data.bones:
        raise ValueError("头部父骨骼不存在: " + cfg.head_bone)
    if cfg.left_eye_bone == cfg.right_eye_bone:
        raise ValueError("左右眼骨骼名称需要不同")
    C.select_only([rig], context)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for bone_name, point in positions.items():
            existing = rig.data.edit_bones.get(bone_name)
            bone = existing or rig.data.edit_bones.new(bone_name)
            # Never relocate a bound existing rest bone (would deform the eye on import).
            if existing is None:
                bone.head = point
                bone.tail = point + Vector((0, -cfg.bone_length, 0))
                if cfg.head_bone:
                    bone.parent = rig.data.edit_bones[cfg.head_bone]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in objects:
        for group_name, bone_name in ((cfg.left_eye_group, cfg.left_eye_bone), (cfg.right_eye_group, cfg.right_eye_bone)):
            if group_name in obj.vertex_groups and group_name != bone_name:
                if bone_name in obj.vertex_groups:
                    raise ValueError("眼球目标顶点组已存在: " + bone_name)
                obj.vertex_groups[group_name].name = bone_name
    rig['BomberStudioEyeTracking'] = cfg.eye_mode + ': eye bone references; configure VRChat SDK in Unity separately'


class BOMBER_OT_rig(bpy.types.Operator):
    bl_idname = 'bomberstudio.rig'
    bl_label = '角色工具'
    bl_options = {'REGISTER', 'UNDO'}
    action = bpy.props.EnumProperty(items=[(key, label, label) for key, label in RIG_OPERATIONS])

    def execute(self, context):
        try:
            cfg = C.settings(context)
            action = self.action
            if cfg.backup_before_edit and action not in ('POSE', 'PREVIEW_VISEME', 'STOP_VISEME', 'TEST_EYES', 'RESET_EYES', 'EXPORT_CSV'):
                C.snapshot(context, action)
            if action == 'BONES_FROM_GROUPS':
                generate_bones(context)
            elif action == 'POSE':
                rig = require_armature(context)
                stop = rig.mode == 'POSE'
                C.select_only([rig], context)
                if not stop:
                    bpy.ops.object.mode_set(mode='POSE')
            elif action == 'RENAME_GI':
                rename_bones(require_armature(context), BONE_NAMES, context)
            elif action == 'MERGE_RIGS':
                merge_rigs(context)
            elif action == 'ATTACH':
                attach_meshes(context)
            elif action == 'MMD_FIX':
                objects = C.ensure_meshes(context)
                with C.selection_guard(context):
                    for obj in objects:
                        model_ops.remove_loose(obj, context)
                        model_ops.vertex_group_operation(obj, 'VG_REMOVE_EMPTY', context)
                        for p in obj.data.polygons:
                            p.use_smooth = True
                if cfg.remove_mmd_physics:
                    remove_physics(context)
            elif action == 'REMOVE_PHYSICS':
                remove_physics(context)
            elif action in ('TRANSLATE', 'EXPORT_CSV', 'IMPORT_CSV'):
                dictionary = dict(BONE_NAMES)
                if action == 'IMPORT_CSV':
                    with open(bpy.path.abspath(cfg.csv_path), 'r', encoding='utf-8-sig', newline='') as stream:
                        for row in csv.DictReader(stream):
                            if row.get('source') and row.get('target'):
                                dictionary[row['source']] = row['target']
                records = translate(context, dictionary, action == 'EXPORT_CSV')
                if action == 'EXPORT_CSV' or cfg.export_translation_csv:
                    buf = io.StringIO(newline='')
                    writer = csv.writer(buf)
                    writer.writerow(('kind', 'source', 'target'))
                    writer.writerows(records)
                    core.atomic_bytes(bpy.path.abspath(cfg.csv_path), ('\ufeff' + buf.getvalue()).encode('utf8'))
            elif action == 'VISEMES':
                make_visemes(C.active(context), cfg.viseme_aa, cfg.viseme_oh, cfg.viseme_ch, cfg.viseme_intensity)
            elif action in ('PREVIEW_VISEME', 'STOP_VISEME'):
                obj = C.active(context)
                if not obj or obj.type != 'MESH' or not obj.data.shape_keys:
                    raise ValueError("请选择带口型形态键的网格")
                keys = obj.data.shape_keys.key_blocks
                for name, _ in VISEMES:
                    if 'vrc.v_' + name in keys:
                        keys['vrc.v_' + name].value = 0
                if action == 'PREVIEW_VISEME':
                    cfg.viseme_preview = (cfg.viseme_preview + 1) % len(VISEMES)
                    name = 'vrc.v_' + VISEMES[cfg.viseme_preview][0]
                    if name not in keys:
                        raise ValueError("先创建口型")
                    keys[name].value = 1
                    self.report({'INFO'}, "当前口型: " + name)
            elif action == 'PARENT_BONES':
                parent_bones(context)
            elif action == 'SCALE':
                scale_model(context)
            elif action == 'EYES':
                setup_eyes(context)
            elif action in ('TEST_EYES', 'RESET_EYES'):
                rig = require_armature(context)
                for name in (cfg.left_eye_bone, cfg.right_eye_bone):
                    if name not in rig.pose.bones:
                        raise ValueError("眼骨不存在: " + name)
                for name in (cfg.left_eye_bone, cfg.right_eye_bone):
                    bone = rig.pose.bones[name]
                    bone.rotation_mode = 'XYZ'
                    bone.rotation_euler = (math.radians(cfg.eye_rotation), 0, 0) if action == 'TEST_EYES' else (0, 0, 0)
            elif action == 'OPTIMIZE_BONES':
                rig = require_armature(context)
                if rig.animation_data and (rig.animation_data.action or rig.animation_data.nla_tracks):
                    raise ValueError("骨架含动画；清理骨骼前先保存并清除动画")
                meshes = rig_meshes(rig, context)
                weighted = set(g.name for o in meshes for g in o.vertex_groups if model_ops.weights_by_group(o)[g.index])
                protected = set(cfg.protected_bones.split(','))
                selected = [b.name for b in rig.data.bones if not b.children and b.name not in weighted and b.name not in protected
                            and not rig.pose.bones[b.name].constraints]
                C.select_only([rig], context)
                bpy.ops.object.mode_set(mode='EDIT')
                try:
                    for name in selected:
                        rig.data.edit_bones.remove(rig.data.edit_bones[name])
                finally:
                    bpy.ops.object.mode_set(mode='OBJECT')
            C.log("角色工具完成: " + action, context)
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_rig,)
