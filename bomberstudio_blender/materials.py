# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import bpy
from . import compat as C, core


def load_image(path, data=False, ignore_alpha=False):
    image = bpy.data.images.load(path, check_existing=True)
    # Never change colour-space/alpha semantics of an image shared by other materials.
    tag = ('DATA' if data else 'COLOR') + ('_OPAQUE' if ignore_alpha else '')
    if image.get('BomberStudioUsage') != tag:
        existing = next((i for i in bpy.data.images if i.get('BomberStudioUsage') == tag and
                         os.path.normcase(bpy.path.abspath(i.filepath)) == os.path.normcase(path)), None)
        image = existing or image.copy()
        image['BomberStudioUsage'] = tag
    if data:
        image.colorspace_settings.name = 'Non-Color'
    if ignore_alpha:
        if C.LEGACY:
            image.use_alpha = False
            image.alpha_mode = 'STRAIGHT'
        else:
            image.alpha_mode = 'NONE'
    return image


def attach_texture(mat, path, slot='BASE', uv='UV0', scale=(1, 1), offset=(0, 0),
                   wrap=0, ignore_alpha=True):
    bsdf = C.principled(mat)
    tree = mat.node_tree
    tag = 'BomberStudio_' + slot
    for node in list(tree.nodes):
        if node.get('BomberStudioSlot') == tag:
            tree.nodes.remove(node)
    image = load_image(path, slot not in ('BASE', 'EMISSION'), ignore_alpha and slot == 'BASE')
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.name = tag
    tex.label = os.path.basename(path)
    tex.image = image
    tex.extension = 'EXTEND' if wrap == 1 else 'MIRROR' if wrap == 2 and bpy.app.version >= (4, 0, 0) else 'REPEAT'
    tex['BomberStudioSlot'] = tag
    tex['bomber_uv'] = uv
    tex['bomber_scale'] = list(scale)
    tex['bomber_offset'] = list(offset)
    uv_node = tree.nodes.new('ShaderNodeUVMap')
    uv_node.uv_map = uv
    uv_node['BomberStudioSlot'] = tag
    mapping = tree.nodes.new('ShaderNodeMapping')
    mapping['BomberStudioSlot'] = tag
    if hasattr(mapping, 'translation'):
        mapping.translation = (offset[0], offset[1], 0)
        mapping.scale = (scale[0], scale[1], 1)
    else:
        mapping.inputs['Location'].default_value = (offset[0], offset[1], 0)
        mapping.inputs['Scale'].default_value = (scale[0], scale[1], 1)
    tree.links.new(uv_node.outputs['UV'], mapping.inputs['Vector'])
    tree.links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
    tex.location = (-400, 200)
    uv_node.location = (-800, 200)
    mapping.location = (-600, 200)
    if slot == 'NORMAL':
        normal = tree.nodes.new('ShaderNodeNormalMap')
        normal['BomberStudioSlot'] = tag
        normal.uv_map = uv
        tree.links.new(tex.outputs['Color'], normal.inputs['Color'])
        tree.links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
    else:
        names = {'BASE': ('Base Color',), 'ROUGHNESS': ('Roughness',),
                 'METALLIC': ('Metallic',), 'EMISSION': ('Emission Color', 'Emission')}[slot]
        target = C.input_socket(bsdf, *names)
        if target is not None:
            tree.links.new(tex.outputs['Color'], target)
        if slot == 'EMISSION' and target is None:
            emission = tree.nodes.new('ShaderNodeEmission')
            add_shader = tree.nodes.new('ShaderNodeAddShader')
            for node in (emission, add_shader):
                node['BomberStudioSlot'] = tag
            tree.links.new(tex.outputs['Color'], emission.inputs['Color'])
            output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
            surface = output.inputs['Surface'].links[0].from_socket
            tree.links.new(surface, add_shader.inputs[0])
            tree.links.new(emission.outputs[0], add_shader.inputs[1])
            tree.links.new(add_shader.outputs[0], output.inputs['Surface'])
        if slot == 'EMISSION':
            strength = C.input_socket(bsdf, 'Emission Strength')
            if strength is not None:
                strength.default_value = 1
    if slot == 'BASE':
        alpha = C.input_socket(bsdf, 'Alpha')
        if alpha is not None:
            for old in list(alpha.links):
                tree.links.remove(old)
            alpha.default_value = 1
            if not ignore_alpha:
                tree.links.new(tex.outputs['Alpha'], alpha)
        elif not ignore_alpha:
            # 2.79 Principled has no Alpha input.
            output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
            transparent = tree.nodes.new('ShaderNodeBsdfTransparent')
            mix = tree.nodes.new('ShaderNodeMixShader')
            for node in (transparent, mix):
                node['BomberStudioSlot'] = tag
            tree.links.new(tex.outputs['Alpha'], mix.inputs[0])
            tree.links.new(transparent.outputs[0], mix.inputs[1])
            tree.links.new(bsdf.outputs[0], mix.inputs[2])
            tree.links.new(mix.outputs[0], output.inputs['Surface'])
        C.alpha_mode(mat, not ignore_alpha)
    return tex


def uv_name(objects, mat, index):
    names = set()
    for obj in objects:
        if obj.type == 'MESH' and any(s.material == mat for s in obj.material_slots):
            layers = obj.data.uv_layers
            if len(layers) <= index:
                raise ValueError("{0}: 材质 {1} 请求 UV{2}，实际只有 {3} 层".format(obj.name, mat.name, index, len(layers)))
            names.add(layers[index].name)
    if len(names) > 1:
        raise ValueError("同材质绑定的网格 UV 层名称不一致: " + mat.name)
    return next(iter(names), 'UV' + str(index))


def apply_sidecars(path, objects):
    rows = core.load_sidecars(path)
    if not rows:
        return {'sidecars': [], 'bound': 0, 'missing': []}
    root = os.path.dirname(path)
    index = core.texture_index(root)
    mats = set(s.material for o in objects if o.type == 'MESH' for s in o.material_slots if s.material)
    result = {'sidecars': [x[0] for x in rows], 'bound': 0, 'missing': []}
    for filename, report, descriptors in rows:
        for obj in objects:
            obj['BomberStudioSidecar'] = filename
            obj['BomberStudioAdapter'] = report.get('adapter', report.get('schema', ''))
        for desc in descriptors:
            matches = [m for m in mats if m.name == desc['name'] or re.sub(r'\.\d{3}$', '', m.name) == desc['name']]
            if len(matches) != 1:
                result['missing'].append("材质未唯一匹配: " + desc['name'])
                continue
            mat = matches[0]
            mat['BomberStudioSourceMaterial'] = json.dumps(desc['source'], ensure_ascii=False)
            mat['BomberStudioShader'] = desc['shader']
            # A null baseColor is deliberate for suppressed/data-only passes: do not guess.
            if desc['base'] is None:
                continue
            bindings = [(desc['base'], 'BASE')]
            for tex in desc['textures']:
                prop = tex['property'].lower()
                if tex is desc['base']:
                    continue
                if prop in ('_bumpmap', '_normalmap', '_normaltex'):
                    bindings.append((tex, 'NORMAL'))
                elif prop in ('_emissionmap', '_emissivemap'):
                    bindings.append((tex, 'EMISSION'))
                # Packed Unity mask/light maps are not interpreted as standard PBR channels.
            for tex, slot in bindings:
                try:
                    actual = core.resolve_texture(root, tex['file'], index)
                    uv = uv_name(objects, mat, tex['uv'])
                    attach_texture(mat, actual, slot, uv, tex['scale'], tex['offset'],
                                   tex['wrap'], tex['ignore_alpha'] if slot == 'BASE' else True)
                    result['bound'] += 1
                except (ValueError, RuntimeError) as exc:
                    result['missing'].append(str(exc))
        # Record original raw material JSON without replacing the richer sidecar.
    return result


def relink_images(objects, folder):
    index = None
    bound, missing = 0, []
    images = set(n.image for o in objects if o.type == 'MESH' for s in o.material_slots
                 if s.material and s.material.use_nodes for n in s.material.node_tree.nodes
                 if n.type == 'TEX_IMAGE' and n.image)
    for image in images:
        if image.packed_file or os.path.isfile(bpy.path.abspath(image.filepath)):
            continue
        try:
            if index is None:
                index = core.texture_index(folder)
            old = image.filepath.replace('\\', '/')
            path = core.resolve_texture(folder, os.path.basename(old) or image.name, index)
            image.filepath = path
            image.reload()
            bound += 1
        except (ValueError, RuntimeError) as exc:
            missing.append(str(exc))
    return bound, missing


def material_signature(mat):
    if not mat.use_nodes:
        return ('PLAIN', tuple(mat.diffuse_color))
    tree = mat.node_tree
    supported = {'BSDF_PRINCIPLED', 'TEX_IMAGE', 'UVMAP', 'MAPPING', 'OUTPUT_MATERIAL',
                 'NORMAL_MAP', 'VECT_MATH', 'BSDF_TRANSPARENT', 'MIX_SHADER', 'ADD_SHADER', 'EMISSION'}
    reachable = set()
    pending = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL' and getattr(n, 'is_active_output', True)]
    if not pending:
        return ('UNMERGEABLE', mat.as_pointer())
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(link.from_node for socket in node.inputs for link in socket.links)
    if mat.animation_data or tree.animation_data or any(n.type not in supported for n in reachable):
        return ('UNMERGEABLE', mat.as_pointer())
    # Comparing complete node/link state is conservative; never merge by material name alone.
    nodes = []
    for n in reachable:
        inputs = []
        for socket in n.inputs:
            if hasattr(socket, 'default_value'):
                value = socket.default_value
                try:
                    value = tuple(value)
                except TypeError:
                    pass
                inputs.append((socket.identifier, repr(value)))
        nodes.append((n.name, n.bl_idname, tuple(inputs),
                      n.image.as_pointer() if n.type == 'TEX_IMAGE' and n.image else 0,
                      getattr(n, 'uv_map', ''), getattr(n, 'operation', ''),
                      getattr(n, 'extension', ''), getattr(n, 'interpolation', ''),
                      getattr(n, 'space', ''),
                      tuple(getattr(n, 'translation', ())), tuple(getattr(n, 'rotation', ())),
                      tuple(getattr(n, 'scale', ())), getattr(n, 'distribution', ''),
                      getattr(n, 'vector_type', ''), getattr(n, 'node_tree', None).as_pointer()
                      if getattr(n, 'node_tree', None) else 0))
    links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links if l.from_node in reachable and l.to_node in reachable)
    return (tuple(sorted(nodes)), tuple(links), getattr(mat, 'blend_method', ''),
            getattr(mat, 'surface_render_method', ''), getattr(mat, 'use_backface_culling', False))


def combine_materials(objects):
    by_signature = {}
    count = 0
    for obj in objects:
        C.single_user(obj)
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            signature = material_signature(mat)
            if signature in by_signature and by_signature[signature] != mat:
                slot.material = by_signature[signature]
                count += 1
            else:
                by_signature[signature] = mat
        # Remap polygon indices before collapsing duplicate slots.
        originals = list(obj.data.materials)
        unique = []
        remap = {}
        for i, mat in enumerate(originals):
            if mat not in unique:
                unique.append(mat)
            remap[i] = unique.index(mat)
        indices = [remap.get(p.material_index, 0) for p in obj.data.polygons]
        obj.data.materials.clear()
        for mat in unique:
            obj.data.materials.append(mat)
        for polygon, index in zip(obj.data.polygons, indices):
            polygon.material_index = index
    return count


class BOMBER_OT_textures(bpy.types.Operator):
    bl_idname = 'bomberstudio.textures'
    bl_label = '贴图工具'
    bl_options = {'REGISTER', 'UNDO'}
    action = bpy.props.StringProperty(default='SCAN')

    def execute(self, context):
        try:
            cfg = C.settings(context)
            if self.action == 'SCAN':
                rows = core.texture_index(bpy.path.abspath(cfg.texture_dir))
                cfg.textures.clear()
                for row in rows:
                    item = cfg.textures.add()
                    item.name, item.path, item.lod = row['name'], row['path'], row['lod']
                cfg.texture_index = 0
                self.report({'INFO'}, "读取 {0} 张贴图".format(len(rows)))
            elif self.action == 'APPLY':
                objects = C.ensure_meshes(context)
                if not cfg.textures or not 0 <= cfg.texture_index < len(cfg.textures):
                    raise ValueError("请先读取贴图并选中一项")
                item = cfg.textures[cfg.texture_index]
                for obj in objects:
                    C.single_user(obj)
                    mat = obj.active_material
                    if mat is None:
                        mat = bpy.data.materials.new(obj.name + '_Material')
                        obj.data.materials.append(mat)
                    elif mat.users > 1:
                        mat = mat.copy()
                        obj.active_material = mat
                    uv = obj.data.uv_layers.active
                    if uv is None:
                        raise ValueError(obj.name + " 没有 UV；请先展开 UV")
                    attach_texture(mat, item.path, uv=uv.name, ignore_alpha=cfg.ignore_alpha)
                cfg.preview_image = bpy.data.images.get(os.path.basename(item.path)) or load_image(item.path)
                self.report({'INFO'}, "已应用到 {0} 个物体".format(len(objects)))
            elif self.action == 'RELINK':
                count, errors = relink_images(C.ensure_meshes(context), bpy.path.abspath(cfg.texture_dir))
                C.log(json.dumps({'relinked': count, 'missing': errors}, ensure_ascii=False), context)
                self.report({'WARNING'} if errors else {'INFO'}, "重新定位 {0} 张；缺失/歧义 {1} 张（详见日志）".format(count, len(errors)))
            elif self.action == 'COMBINE':
                count = combine_materials(C.ensure_meshes(context))
                self.report({'INFO'}, "合并 {0} 处相同材质绑定".format(count))
            elif self.action == 'LIST':
                cfg.atlas_materials.clear()
                mats = sorted(set(s.material for o in C.ensure_meshes(context) for s in o.material_slots if s.material),
                              key=lambda m: core.natural_key(m.name))
                for mat in mats:
                    row = cfg.atlas_materials.add()
                    row.material, row.name, row.enabled = mat, mat.name, True
                self.report({'INFO'}, "材质列表: {0}".format(len(mats)))
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_textures,)
