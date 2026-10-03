# SPDX-License-Identifier: GPL-3.0-or-later
import math
import re
import bpy
import bmesh
from mathutils import Vector
from . import compat as C, core

OPERATIONS = (
    ('RESET_LOCATION', '重置模型 x/y/z 位置为 0'),
    ('RESET_ROTATION', '重置模型 x/y/z 旋转为 0'),
    ('DELETE_LOOSE', '删除模型中的松散点'),
    ('CLEAR_NORMALS', '清除自定义拆分法向'),
    ('VG_REMOVE_ALL', '移除所有顶点组'),
    ('VG_REMOVE_EMPTY', '移除未使用的空顶点组'),
    ('VG_REMOVE_NONNUMERIC', '移除非数字名称的顶点组'),
    ('VG_SORT', '根据顶点组名称排序'),
    ('VG_FILL', '填充数字顶点组间隙'),
    ('VG_MERGE', '合并相同数字前缀的顶点组'),
    ('VG_MAP', '按位置匹配活动物体的顶点组名称'),
    ('VG_PREFIX', '用模型名称作为顶点组前缀'),
    ('MODIFIERS', '在有形态键的模型上应用修改器'),
    ('NORMAL_UV', '平滑法线存入 UV（八面体编码）'),
    ('TANGENT', '向量和归一化重算 TANGENT'),
    ('COLOR', '算术平均归一化重算 COLOR'),
    ('SPLIT_UV', '根据 UV 岛分割模型'),
    ('BREAK_GROUP', '根据顶点组拆为松散块'),
    ('SPLIT_SHARED', '根据共享与孤立顶点组分割'),
    ('SPLIT_GROUP', '按顶点组分割模型'),
    ('SPLIT_CLUSTER', '按松散块分割并按顶点组聚类'),
    ('SPLIT_MATERIAL', '按材质分割模型'),
    ('SPLIT_LOOSE', '按几何松散块分割模型'),
    ('SPLIT_SHAPES', '按形态键生成静态快照'),
    ('JOIN', '合并选中网格'),
)


def weights_by_group(obj):
    result = dict((g.index, {}) for g in obj.vertex_groups)
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.weight > 1e-8 and assignment.group in result:
                result[assignment.group][vertex.index] = assignment.weight
    return result


def rename_groups(obj, mapping):
    # Stage names first to avoid Blender's automatic .001 collision suffix.
    names = set(g.name for g in obj.vertex_groups)
    targets = list(mapping.values())
    if len(set(targets)) != len(targets):
        raise ValueError("顶点组改名存在重复目标")
    if any(dst in names and dst not in mapping for dst in targets):
        raise ValueError("顶点组目标名称已存在")
    refs = [(obj.vertex_groups[src], dst) for src, dst in mapping.items() if src in obj.vertex_groups]
    for i, (group, _) in enumerate(refs):
        group.name = '__BomberRename_{0}_{1}'.format(i, obj.as_pointer())
    for group, target in refs:
        group.name = target


def group_centres(obj):
    weights = weights_by_group(obj)
    centres = {}
    for group in obj.vertex_groups:
        values = weights[group.index]
        total = sum(values.values())
        if total:
            point = sum((obj.data.vertices[i].co * w for i, w in values.items()), Vector()) / total
            centres[group.name] = C.matmul(obj.matrix_world, point)
    return centres


def edit_mesh(obj, callback, context):
    C.select_only([obj], context)
    C.single_user(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        callback(bm)
        bmesh.update_edit_mesh(obj.data)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def remove_loose(obj, context):
    def edit(bm):
        targets = [v for v in bm.verts if not v.link_faces and not v.link_edges]
        C.delete_geom(bm, geom=targets, context='VERTS')
    edit_mesh(obj, edit, context)


def vertex_group_operation(obj, action, context):
    groups = obj.vertex_groups
    if action == 'VG_REMOVE_ALL':
        groups.clear()
    elif action == 'VG_REMOVE_EMPTY':
        weights = weights_by_group(obj)
        empty_names = [g.name for g in groups if not weights[g.index]]
        for name in empty_names:
            groups.remove(groups[name])
    elif action == 'VG_REMOVE_NONNUMERIC':
        names = [g.name for g in groups if not g.name.isdigit()]
        for name in names:
            groups.remove(groups[name])
    elif action == 'VG_FILL':
        numbers = [int(g.name) for g in groups if g.name.isdigit()]
        if numbers:
            if max(numbers) > 10000:
                raise ValueError("数字组索引超过 10000，停止大规模填充")
            for i in range(max(numbers)+1):
                if str(i) not in groups:
                    groups.new(name=str(i))
    elif action == 'VG_SORT':
        C.select_only([obj], context)
        desired = sorted([g.name for g in groups], key=core.natural_key)
        for target, name in enumerate(desired):
            groups.active_index = groups[name].index
            while groups[name].index > target:
                bpy.ops.object.vertex_group_move(direction='UP')
    elif action == 'VG_PREFIX':
        rename_groups(obj, dict((g.name, obj.name + '_' + g.name) for g in groups))
    elif action == 'VG_MERGE':
        weights = weights_by_group(obj)
        buckets = {}
        for g in groups:
            match = re.match(r'^(\d+)(?:\D|$)', g.name)
            if match:
                buckets.setdefault(str(int(match.group(1))), []).append((g.name, weights[g.index]))
        for name, members in buckets.items():
            combined = {}
            for old_name, values in members:
                for vi, weight in values.items():
                    combined[vi] = min(1., combined.get(vi, 0.) + weight)
            for old_name, values in members:
                groups.remove(groups[old_name])
            group = groups.new(name=name)
            for vi, weight in combined.items():
                group.add([vi], weight, 'REPLACE')


def map_groups(objects, reference, tolerance):
    if reference not in objects or len(objects) < 2:
        raise ValueError("选择至少两个网格，活动网格作为命名参考")
    centres = group_centres(reference)
    plans = []
    for obj in objects:
        if obj == reference:
            continue
        mapping = {}
        for name, point in group_centres(obj).items():
            matches = [n for n, p in centres.items() if (p-point).length <= tolerance]
            if len(matches) == 1:
                mapping[name] = matches[0]
        if len(set(mapping.values())) != len(mapping):
            raise ValueError(obj.name + " 存在位置匹配歧义；缩小匹配距离")
        plans.append((obj, mapping))
    for obj, mapping in plans:
        rename_groups(obj, mapping)
    return sum(len(mapping) for obj, mapping in plans)


def apply_modifiers_preserve_keys(obj, context, name=''):
    selected = [m for m in obj.modifiers if m.name == name] if name else [m for m in obj.modifiers if m.show_viewport and m.type != 'ARMATURE']
    if not selected:
        raise ValueError(obj.name + " 没有待应用修改器（默认跳过骨架修改器）")
    selected_names = set(m.name for m in selected)
    retained_before = False
    for modifier in obj.modifiers:
        if modifier.name in selected_names and retained_before:
            raise ValueError('待应用修改器前存在保留的可见修改器；请先处理栈前段，保持计算顺序')
        if modifier.show_viewport and modifier.name not in selected_names:
            retained_before = True
    C.single_user(obj)
    keys = obj.data.shape_keys
    if not keys:
        C.select_only([obj], context)
        for mod in selected:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        return
    if any(m.type == 'ARMATURE' for m in selected):
        raise ValueError("形态键流程保留骨架修改器；请先使用姿态烘焙功能")
    if any(m.type not in {'SMOOTH', 'LAPLACIANSMOOTH', 'CORRECTIVE_SMOOTH', 'CAST',
                         'SIMPLE_DEFORM', 'DISPLACE', 'WAVE', 'LATTICE', 'CURVE',
                         'SHRINKWRAP', 'HOOK', 'MESH_DEFORM', 'SURFACE_DEFORM'} for m in selected):
        raise ValueError("形态键仅应用保序、保拓扑形变修改器；细分/焊接/布尔请在无形态键副本上处理")
    names = set(m.name for m in selected)
    # Keep original Key datablock (relative links, drivers, actions and masks stay intact).
    snapshot = [list(tuple(p.co) for p in block.data) for block in keys.key_blocks]
    topology = [tuple(p.vertices) for p in obj.data.polygons]
    evaluated = []
    probe = C.copy_object(obj, context)
    probe.name = '__BomberModifierProbe'
    probe_mesh = probe.data
    probe_key = probe_mesh.shape_keys
    try:
        if hasattr(probe, 'shape_key_clear'):
            probe.shape_key_clear()
        else:
            for key in reversed(list(probe.data.shape_keys.key_blocks)):
                probe.shape_key_remove(key)
        for mod in probe.modifiers:
            mod.show_viewport = mod.name in names
        for coords in snapshot:
            for vert, co in zip(probe.data.vertices, coords):
                vert.co = co
            probe.data.update()
            C.update(context)
            mesh, release = C.evaluated_mesh(probe, context)
            try:
                if len(mesh.vertices) != len(obj.data.vertices) or [tuple(p.vertices) for p in mesh.polygons] != topology:
                    raise ValueError("修改器改变拓扑；原网格和形态键保持不变")
                evaluated.append([tuple(v.co) for v in mesh.vertices])
            finally:
                release()
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
        if probe_mesh.users == 0:
            bpy.data.meshes.remove(probe_mesh)
        # Blender 4.x mesh.copy retains an extra user on the copied Key.
        # Only remove this probe-owned datablock, never purge user scene data.
        if probe_key is not None:
            try:
                if probe_key.name in bpy.data.shape_keys and probe_key.user is None:
                    if hasattr(bpy.data, 'batch_remove'):
                        bpy.data.batch_remove(ids=[probe_key])
                    else:
                        probe_key.user_clear()
            except ReferenceError:
                pass
    for block, coords in zip(keys.key_blocks, evaluated):
        for point, co in zip(block.data, coords):
            point.co = co
    for vert, co in zip(obj.data.vertices, evaluated[0]):
        vert.co = co
    for mod in selected:
        obj.modifiers.remove(mod)
    obj.data.update()


def smooth_vectors(mesh):
    mesh.update()
    sums = [Vector() for v in mesh.vertices]
    counts = [0] * len(mesh.vertices)
    for polygon in mesh.polygons:
        for vi in polygon.vertices:
            sums[vi] += polygon.normal
            counts[vi] += 1
    # Coincident vertices created at UV/material seams share the same smoothed vector.
    positions = {}
    for v in mesh.vertices:
        key = tuple(round(x, 6) for x in v.co)
        positions.setdefault(key, []).append(v.index)
    result = [Vector((0, 0, 1)) for v in mesh.vertices]
    for indices in positions.values():
        value = sum((sums[i] for i in indices), Vector())
        value.normalize()
        for i in indices:
            result[i] = value.copy()
    return result


def oct_encode(normal):
    n = normal / max(abs(normal.x)+abs(normal.y)+abs(normal.z), 1e-12)
    x, y = n.x, n.y
    if n.z < 0:
        x, y = (1-abs(y)) * (1 if x >= 0 else -1), (1-abs(x)) * (1 if y >= 0 else -1)
    return (x*.5+.5, y*.5+.5)


def normals_operation(obj, action):
    C.single_user(obj)
    mesh = obj.data
    normals = smooth_vectors(mesh)
    if action == 'NORMAL_UV':
        layer = mesh.uv_layers.get('Bomber_SmoothNormal_Oct') or C.new_uv(mesh, 'Bomber_SmoothNormal_Oct')
        for loop in mesh.loops:
            layer.data[loop.index].uv = oct_encode(normals[loop.vertex_index])
        obj['BomberStudioSmoothNormalEncoding'] = 'object-space octahedral UNORM2; uv*2-1, unfold lower hemisphere, normalize'
    else:
        layer = C.color_layer(mesh, 'TANGENT' if action == 'TANGENT' else 'COLOR')
        for loop in mesh.loops:
            normal = normals[loop.vertex_index]
            value = (normal.x*.5+.5, normal.y*.5+.5, normal.z*.5+.5, 1.)
            layer.data[loop.index].color = value if len(layer.data[loop.index].color) == 4 else value[:3]
        # Interop with MIMI: flags accompany real editable corner attributes.
        obj['3DMigoto:Recalculate' + action] = True
        obj['BomberStudio' + action + 'Encoding'] = 'smoothed object-space normal XYZ * 0.5 + 0.5; not Blender tangent handedness'


def face_clusters(obj, kind):
    mesh = obj.data
    count = len(mesh.polygons)
    uf = core.UnionFind(count)
    uv = mesh.uv_layers.active
    if kind == 'SPLIT_UV' and uv is None:
        raise ValueError(obj.name + " 没有 UV")
    assignments = [set(a.group for a in v.groups if a.weight > 1e-8) for v in mesh.vertices]
    faces = list(mesh.polygons)
    group_sets = [set.union(*(assignments[i] for i in p.vertices)) if p.vertices else set() for p in faces]
    if kind == 'SPLIT_MATERIAL':
        buckets = {}
        for p in faces:
            buckets.setdefault(p.material_index, []).append(p.index)
        return list(buckets.values())
    if kind == 'SPLIT_GROUP' or kind == 'BREAK_GROUP':
        buckets = {}
        for p in faces:
            totals = {}
            for vi in p.vertices:
                for a in mesh.vertices[vi].groups:
                    totals[a.group] = totals.get(a.group, 0) + a.weight
            best = max(totals, key=lambda i: (totals[i], -i)) if totals else -1
            buckets.setdefault(best, []).append(p.index)
        return list(buckets.values())
    edge_faces = {}
    for p in faces:
        loops = list(p.loop_indices)
        for pos, li in enumerate(loops):
            lj = loops[(pos+1) % len(loops)]
            a, b = mesh.loops[li].vertex_index, mesh.loops[lj].vertex_index
            key = tuple(sorted((a, b)))
            coords = {a: tuple(uv.data[li].uv), b: tuple(uv.data[lj].uv)} if uv else None
            for other, other_coords in edge_faces.get(key, []):
                if kind == 'SPLIT_UV' and any(abs(coords[i][j]-other_coords[i][j]) > 1e-6 for i in key for j in (0, 1)):
                    continue
                uf.union(p.index, other)
            edge_faces.setdefault(key, []).append((p.index, coords))
    # Unioning through a shared group is transitive, unlike single-pass set matching.
    if kind in ('SPLIT_SHARED', 'SPLIT_CLUSTER'):
        owners = {}
        for p, groups in zip(faces, group_sets):
            for group in groups:
                if group in owners:
                    uf.union(p.index, owners[group])
                owners[group] = p.index
    buckets = {}
    for p in faces:
        buckets.setdefault(uf.find(p.index), []).append(p.index)
    return list(buckets.values())


def split_model(obj, kind, context):
    clusters = face_clusters(obj, kind)
    if len(clusters) < 2:
        return []
    if len(clusters) > 512:
        raise ValueError("分组超过 512；请缩小选择范围后分批处理")
    if kind == 'BREAK_GROUP':
        memberships = dict((f, group) for group, cluster in enumerate(clusters) for f in cluster)
        def break_edges(bm):
            edges = [e for e in bm.edges if len(set(memberships.get(f.index) for f in e.link_faces)) > 1]
            bmesh.ops.split_edges(bm, edges=edges)
        edit_mesh(obj, break_edges, context)
        return [obj]
    collection = C.collection(obj.name + '_Splits', context)
    results = []
    for i, cluster in enumerate(clusters):
        result = C.copy_object(obj, context, collection)
        result.name = obj.name + '_part_{0:03d}'.format(i)
        keep = set(cluster)
        def trim(bm):
            remove = [f for f in bm.faces if f.index not in keep]
            C.delete_geom(bm, geom=remove, context='FACES')
            dead = [v for v in bm.verts if not v.link_faces]
            if dead:
                C.delete_geom(bm, geom=dead, context='VERTS')
        edit_mesh(result, trim, context)
        results.append(result)
    obj['BomberStudioSplitBackup'] = True
    C.hidden(obj, True)
    return results


def shape_snapshots(obj, context):
    if not obj.data.shape_keys or len(obj.data.shape_keys.key_blocks) < 2:
        raise ValueError(obj.name + ' 没有可生成快照的形态键')
    keys = list(obj.data.shape_keys.key_blocks)[1:]
    if len(keys) > 256:
        raise ValueError('形态键超过 256 个，请在副本中缩小处理范围')
    output = C.collection(obj.name + '_ShapeSnapshots', context)
    results = []
    for key in keys:
        coords = [tuple(p.co) for p in key.data]
        target = C.copy_object(obj, context, output)
        target.name = obj.name + '_' + key.name
        owner_key = target.data.shape_keys
        if hasattr(target, 'shape_key_clear'):
            target.shape_key_clear()
        else:
            for block in reversed(list(target.data.shape_keys.key_blocks)):
                target.shape_key_remove(block)
        if owner_key is not None:
            try:
                if owner_key.name in bpy.data.shape_keys:
                    if hasattr(bpy.data, 'batch_remove'):
                        bpy.data.batch_remove(ids=[owner_key])
                    else:
                        owner_key.user_clear()
            except ReferenceError:
                pass
        for vertex, co in zip(target.data.vertices, coords):
            vertex.co = co
        target.data.update()
        target['BomberStudioShapeSnapshot'] = key.name
        results.append(target)
    C.hidden(obj, True)
    return results


class BOMBER_OT_model(bpy.types.Operator):
    bl_idname = 'bomberstudio.model'
    bl_label = '模型处理'
    bl_options = {'REGISTER', 'UNDO'}
    action = bpy.props.EnumProperty(items=[(key, label, label) for key, label in OPERATIONS])

    def execute(self, context):
        try:
            cfg = C.settings(context)
            objects = C.ensure_meshes(context)
            reference = C.active(context)
            if cfg.backup_before_edit:
                C.snapshot(context, self.action)
            if self.action == 'VG_MAP':
                n = map_groups(objects, reference, cfg.match_tolerance)
                self.report({'INFO'}, "已匹配 {0} 个顶点组".format(n))
                return {'FINISHED'}
            if self.action == 'JOIN':
                C.select_only(objects, context)
                if reference in objects:
                    C.set_active(reference, context)
                if len(objects) < 2:
                    raise ValueError("至少选择两个网格")
                bpy.ops.object.join()
                return {'FINISHED'}
            created = []
            with C.selection_guard(context):
                for obj in objects:
                    C.single_user(obj)
                    action = self.action
                    if action == 'RESET_LOCATION':
                        obj.location = (0, 0, 0)
                    elif action == 'RESET_ROTATION':
                        obj.rotation_euler = (0, 0, 0)
                        obj.rotation_quaternion = (1, 0, 0, 0)
                        obj.rotation_axis_angle = (0, 0, 1, 0)
                    elif action == 'DELETE_LOOSE':
                        remove_loose(obj, context)
                    elif action == 'CLEAR_NORMALS':
                        C.select_only([obj], context)
                        if C.op_available('mesh', 'customdata_custom_splitnormals_clear'):
                            bpy.ops.mesh.customdata_custom_splitnormals_clear()
                        else:
                            obj.data.normals_split_custom_set([(0, 0, 0)] * len(obj.data.loops))
                    elif action.startswith('VG_'):
                        vertex_group_operation(obj, action, context)
                    elif action == 'MODIFIERS':
                        apply_modifiers_preserve_keys(obj, context, cfg.modifier_name)
                    elif action in ('NORMAL_UV', 'TANGENT', 'COLOR'):
                        normals_operation(obj, action)
                    elif action == 'SPLIT_SHAPES':
                        created.extend(shape_snapshots(obj, context))
                    elif action.startswith('SPLIT_') or action == 'BREAK_GROUP':
                        created.extend(split_model(obj, action, context))
            if created:
                C.select_only(created, context)
            self.report({'INFO'}, "{0}：处理 {1} 个网格".format(dict(OPERATIONS)[self.action], len(objects)))
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_model,)
