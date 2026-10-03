# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU atlas composer. Deliberately preflights unsupported shader graphs before scene edits."""
import math
import os
import time
import uuid
import bpy
import numpy as np
from . import compat as C, core, materials

CHANNELS = ('BASE', 'ROUGHNESS', 'METALLIC', 'NORMAL', 'EMISSION')


def vector_binding(node, depth=0):
    if depth > 12:
        raise ValueError("贴图向量节点链过深")
    if node.type == 'UVMAP':
        return node.uv_map, (1., 1.), (0., 0.)
    if node.type == 'TEX_COORD':
        return '', (1., 1.), (0., 0.)
    if node.type == 'VECT_MATH' and node.operation in ('MULTIPLY', 'ADD'):
        if len(node.inputs[0].links) != 1 or node.inputs[1].is_linked:
            raise ValueError("只支持 UV × 常量 + 常量的向量节点")
        uv, scale, offset = vector_binding(node.inputs[0].links[0].from_node, depth+1)
        value = node.inputs[1].default_value
        if node.operation == 'MULTIPLY':
            return uv, (scale[0]*value[0], scale[1]*value[1]), (offset[0]*value[0], offset[1]*value[1])
        return uv, scale, (offset[0]+value[0], offset[1]+value[1])
    if node.type == 'MAPPING':
        source = node.inputs.get('Vector')
        if source is None or not source.is_linked:
            raise ValueError("Mapping 节点缺少 UV 输入")
        uv, scale, offset = vector_binding(source.links[0].from_node, depth+1)
        if hasattr(node, 'translation'):
            location, rotation, stretch = node.translation, node.rotation, node.scale
        else:
            location, rotation, stretch = [node.inputs[n].default_value for n in ('Location', 'Rotation', 'Scale')]
        if any(abs(x) > 1e-8 for x in rotation) or getattr(node, 'vector_type', 'POINT') != 'POINT':
            raise ValueError("旋转/非 Point Mapping 请先烘焙为常规贴图")
        return uv, (scale[0]*stretch[0], scale[1]*stretch[1]), (offset[0]*stretch[0]+location[0], offset[1]*stretch[1]+location[1])
    raise ValueError("图集暂未支持向量节点: " + node.bl_idname)


def image_source(node, scalar=False):
    if node.type != 'TEX_IMAGE' or node.image is None:
        raise ValueError("图集要求直接图像贴图或常量；复杂程序节点请先烘焙")
    if node.image.source in ('TILED', 'SEQUENCE', 'MOVIE'):
        raise ValueError("UDIM/序列/视频贴图需要先烘焙为单张图片")
    if getattr(node, 'projection', 'FLAT') != 'FLAT':
        raise ValueError("图集只接受 Flat UV 贴图投影")
    uv, scale, offset = '', (1., 1.), (0., 0.)
    if node.inputs['Vector'].is_linked:
        link = node.inputs['Vector'].links[0]
        if link.from_node.type == 'TEX_COORD' and link.from_socket.name != 'UV':
            raise ValueError("图集需要 UV 坐标，不接受 Generated/Object 坐标")
        uv, scale, offset = vector_binding(link.from_node)
    return {'image': node.image, 'uv': uv, 'scale': scale, 'offset': offset,
            'wrap': node.extension, 'scalar': scalar, 'constant': None}


def constant(value, scalar=False):
    if isinstance(value, (float, int)):
        value = (value, value, value, 1)
    return {'image': None, 'uv': '', 'scale': (1, 1), 'offset': (0, 0),
            'wrap': 'REPEAT', 'scalar': scalar, 'constant': tuple(value)}


def describe(mat, pbr):
    if not mat.use_nodes:
        diffuse = tuple(mat.diffuse_color)
        base = constant(diffuse if len(diffuse) == 4 else diffuse + (1.,))
        return {'BASE': base}
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        raise ValueError(mat.name + " 没有 Principled BSDF；先转换或烘焙材质")
    output = next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and getattr(n, 'is_active_output', True)), None)
    if output is None or not output.inputs['Surface'].is_linked:
        raise ValueError(mat.name + " 没有有效表面输出")
    output_source = output.inputs['Surface'].links[0].from_node
    if output_source != bsdf and not output_source.get('BomberStudioSlot'):
        raise ValueError(mat.name + " 含自定义混合着色器；请先烘焙")
    result = {}
    for channel in CHANNELS if pbr else ('BASE',):
        names = {'BASE': ('Base Color',), 'ROUGHNESS': ('Roughness',), 'METALLIC': ('Metallic',),
                 'NORMAL': ('Normal',), 'EMISSION': ('Emission Color', 'Emission')}[channel]
        socket = C.input_socket(bsdf, *names)
        if socket is None:
            result[channel] = constant((.5, .5, 1, 1) if channel == 'NORMAL' else (0, 0, 0, 1))
        elif socket.is_linked:
            source = socket.links[0].from_node
            if channel == 'NORMAL':
                if source.type != 'NORMAL_MAP' or source.space != 'TANGENT':
                    raise ValueError("法线图集只接受切线空间 Normal Map")
                if source.inputs['Strength'].is_linked or abs(source.inputs['Strength'].default_value - 1.) > 1e-6:
                    raise ValueError("法线强度请先烘焙为 1.0")
                if not source.inputs['Color'].is_linked:
                    result[channel] = constant((.5, .5, 1, 1))
                    continue
                source = source.inputs['Color'].links[0].from_node
            result[channel] = image_source(source, channel in ('ROUGHNESS', 'METALLIC'))
        else:
            value = (.5, .5, 1, 1) if channel == 'NORMAL' else socket.default_value
            result[channel] = constant(value, channel in ('ROUGHNESS', 'METALLIC'))
    alpha = C.input_socket(bsdf, 'Alpha')
    base = result['BASE']
    base['alpha'] = float(alpha.default_value) if alpha is not None else 1.
    base['use_image_alpha'] = False
    if alpha is not None and alpha.is_linked:
        link = alpha.links[0]
        if link.from_node.type != 'TEX_IMAGE' or link.from_socket.name != 'Alpha' or link.from_node.image != base['image']:
            raise ValueError("分离 Alpha 节点需先烘焙进基础色贴图")
        base['use_image_alpha'] = True
    if alpha is None and output_source != bsdf:
        base['use_image_alpha'] = True
    strength = C.input_socket(bsdf, 'Emission Strength')
    if pbr and strength is not None and (strength.is_linked or abs(strength.default_value-1) > 1e-6):
        emission = result['EMISSION']
        if strength.is_linked:
            raise ValueError("发光强度节点请先烘焙")
        gain = float(strength.default_value)
        if gain == 0:
            result['EMISSION'] = constant((0, 0, 0, 1))
        elif emission['image'] is None:
            values = emission['constant']
            result['EMISSION'] = constant(tuple(v * gain for v in values[:3]) + (1,))
        else:
            raise ValueError("图像发光强度请先烘焙为 1.0")
    if pbr:
        for names in [('Coat Weight', 'Clearcoat'), ('Transmission Weight', 'Transmission'), ('Subsurface Weight', 'Subsurface')]:
            extra = C.input_socket(bsdf, *names)
            if extra is not None and (extra.is_linked or abs(extra.default_value) > 1e-8):
                raise ValueError("图集未包含涂层/透射/次表面通道；请先烘焙")
    return result


def pixels(image):
    width, height = image.size[:]
    if width < 1 or height < 1:
        raise ValueError("图像未加载: " + image.name)
    if width * height > 64 * 1024 * 1024:
        raise ValueError("单张源图超过 6400 万像素；请先降采样")
    out = np.empty(width*height*4, dtype=np.float32)
    if hasattr(image.pixels, 'foreach_get'):
        image.pixels.foreach_get(out)
    else:
        out[:] = image.pixels[:]
    return out.reshape(height, width, 4)


def srgb_encode(values):
    values = np.asarray(values, dtype=np.float32)
    return np.where(values <= .0031308, values*12.92, 1.055*np.maximum(values, 0)**(1/2.4)-.055)


def render_tile(desc, bounds, width, height, nearest, channel):
    """Sample texel centres in original UV domain, including tiling and mirrored tiling."""
    if desc['image'] is None:
        value = np.array(desc['constant'], dtype=np.float32)
        if channel in ('BASE', 'EMISSION'):
            value[:3] = srgb_encode(value[:3])
        result = np.empty((height, width, 4), dtype=np.float32)
        result[:] = value
    else:
        source = pixels(desc['image'])
        sh, sw = source.shape[:2]
        u0, v0, u1, v1 = bounds
        us = u0+(np.arange(width, dtype=np.float32)+.5)/width*(u1-u0)
        vs = v0+(np.arange(height, dtype=np.float32)+.5)/height*(v1-v0)
        us = us*desc['scale'][0]+desc['offset'][0]
        vs = vs*desc['scale'][1]+desc['offset'][1]
        wrap = desc['wrap']
        if wrap == 'REPEAT':
            us, vs = np.mod(us, 1), np.mod(vs, 1)
        elif wrap == 'MIRROR':
            us, vs = 1-np.abs(np.mod(us, 2)-1), 1-np.abs(np.mod(vs, 2)-1)
        outside_u, outside_v = (us < 0) | (us > 1), (vs < 0) | (vs > 1)
        x, y = us*sw-.5, vs*sh-.5
        def xi(value):
            return np.mod(value, sw) if wrap == 'REPEAT' else np.clip(value, 0, sw-1)
        def yi(value):
            return np.mod(value, sh) if wrap == 'REPEAT' else np.clip(value, 0, sh-1)
        if nearest:
            result = source[yi(np.floor(y+.5).astype(int))[:, None], xi(np.floor(x+.5).astype(int))[None, :]].copy()
        else:
            x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
            fx, fy = (x-x0)[None, :, None], (y-y0)[:, None, None]
            a = source[yi(y0)[:, None], xi(x0)[None, :]]
            b = source[yi(y0)[:, None], xi(x0+1)[None, :]]
            c = source[yi(y0+1)[:, None], xi(x0)[None, :]]
            d = source[yi(y0+1)[:, None], xi(x0+1)[None, :]]
            result = ((a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy).astype(np.float32)
        if wrap == 'CLIP':
            result[outside_v, :] = 0
            result[:, outside_u] = 0
    if channel == 'BASE':
        result[:, :, 3] = (result[:, :, 3] if desc.get('use_image_alpha') else 1) * desc.get('alpha', 1.)
    if desc['scalar']:
        # Blender's Color -> Value conversion uses luminance.
        gray = result[:, :, 0]*.2126 + result[:, :, 1]*.7152 + result[:, :, 2]*.0722
        result[:, :, :3] = gray[:, :, None]
    return result


def create_atlas(context):
    cfg = C.settings(context)
    objects = C.ensure_meshes(context)
    mats = [row.material for row in cfg.atlas_materials if row.enabled and row.material]
    unique = []
    for material in mats:
        if material not in unique:
            unique.append(material)
    mats = unique
    if not mats:
        raise ValueError("先生成材质列表并勾选待合并材质")
    descriptors = dict((m, describe(m, cfg.atlas_pbr)) for m in mats)
    coordinates = dict((m, []) for m in mats)
    uv_names = {}
    for obj in objects:
        if any(s.material in mats for s in obj.material_slots) and obj.data.uv_layers.active is None:
            raise ValueError(obj.name + " 没有 UV；请先展开")
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mat not in descriptors:
                continue
            sources = descriptors[mat]
            requested = set(d['uv'] or obj.data.uv_layers.active.name for d in sources.values() if d['image'])
            if len(requested) > 1:
                raise ValueError(mat.name + " 的 PBR 通道使用不同 UV；请先烘焙统一 UV")
            name = next(iter(requested), obj.data.uv_layers.active.name)
            if name not in obj.data.uv_layers:
                raise ValueError(obj.name + " 缺少 UV 层 " + name)
            uv_names[(obj, mat)] = name
            uv = obj.data.uv_layers[name]
            coordinates[mat].extend(tuple(uv.data[li].uv) for p in obj.data.polygons if p.material_index == i for li in p.loop_indices)
    mats = [m for m in mats if coordinates[m]]
    if not mats:
        raise ValueError("勾选的材质未用于所选网格面")
    bounds = {}
    sizes = []
    for mat in mats:
        coords = coordinates[mat]
        if not all(math.isfinite(v) for uv in coords for v in uv):
            raise ValueError(mat.name + " 存在无效 UV")
        raw = (min(uv[0] for uv in coords), min(uv[1] for uv in coords),
               max(uv[0] for uv in coords), max(uv[1] for uv in coords))
        # Cropping disabled still includes tiles outside 0..1; never clamp/wreck tiling.
        b = raw if cfg.atlas_crop else (min(0, raw[0]), min(0, raw[1]), max(1, raw[2]), max(1, raw[3]))
        b = (b[0], b[1], max(b[2], b[0]+1e-6), max(b[3], b[1]+1e-6))
        bounds[mat] = b
        image = descriptors[mat]['BASE']['image']
        w, h = image.size[:] if image else (cfg.atlas_solid_size, cfg.atlas_solid_size)
        if image:
            w = max(1, int(math.ceil(w*(b[2]-b[0]))))
            h = max(1, int(math.ceil(h*(b[3]-b[1]))))
        if cfg.atlas_uniform and image:
            ratio = cfg.atlas_tile_size / max(w, h)
            w, h = max(1, round(w*ratio)), max(1, round(h*ratio))
        sizes.append((w, h))
    width, height, placements = core.pack_rectangles(sizes, cfg.atlas_padding, cfg.atlas_shape == 'SQUARE',
                                                    cfg.atlas_algorithm, cfg.atlas_max_size)
    if width*height > 4096*4096:
        raise ValueError("本 CPU 图集单次内存保护限制为 1677 万像素；请降低尺寸或分批合并")
    folder = os.path.join(bpy.path.abspath(cfg.output_dir), 'Atlas_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    os.makedirs(folder, exist_ok=True)
    files = {}
    channels = CHANNELS if cfg.atlas_pbr else ('BASE',)
    for channel in channels:
        canvas = np.zeros((height, width, 4), dtype=np.float32)
        for mat, (x, y, w, h) in zip(mats, placements):
            tile = render_tile(descriptors[mat][channel], bounds[mat], w, h, cfg.atlas_nearest, channel)
            pad = cfg.atlas_padding
            if pad:
                canvas[y-pad:y+h+pad, x-pad:x+w+pad] = np.pad(tile, ((pad, pad), (pad, pad), (0, 0)), mode='edge')
            else:
                canvas[y:y+h, x:x+w] = tile
        image = bpy.data.images.new('BomberAtlas_' + channel, width=width, height=height, alpha=True)
        try:
            if channel not in ('BASE', 'EMISSION'):
                image.colorspace_settings.name = 'Non-Color'
            if hasattr(image.pixels, 'foreach_set'):
                image.pixels.foreach_set(canvas.ravel())
            else:
                image.pixels[:] = canvas.ravel().tolist()
            image.update()
            ext = '.tga' if cfg.atlas_format == 'TARGA' else '.png'
            filename = os.path.join(folder, 'BomberAtlas_' + channel + ext)
            stage = os.path.join(folder, '.pending_' + channel + ext)
            image.filepath_raw, image.file_format = stage, cfg.atlas_format
            image.save()
            os.replace(stage, filename)
            files[channel] = filename
        finally:
            bpy.data.images.remove(image)
        del canvas
    # Commit mesh edits only once all output files are complete.
    if cfg.backup_before_edit and not cfg.atlas_keep_originals:
        C.snapshot(context, 'Atlas')
    material = bpy.data.materials.new('BomberStudio_Atlas')
    alpha = any(descriptors[m]['BASE'].get('use_image_alpha') or descriptors[m]['BASE'].get('alpha', 1) < 1 for m in mats)
    for channel, filename in files.items():
        materials.attach_texture(material, filename, channel, uv='BomberAtlasUV', ignore_alpha=not alpha)
    created = []
    collection = C.collection('BomberStudio_Atlas', context) if cfg.atlas_keep_originals else None
    try:
        for source in objects:
            if not any(s.material in mats for s in source.material_slots):
                continue
            target = C.copy_object(source, context, collection) if cfg.atlas_keep_originals else source
            if cfg.atlas_keep_originals:
                target.name = source.name + '_Atlas'
            C.single_user(target)
            created.append(target)
            uv = target.data.uv_layers.get('BomberAtlasUV') or C.new_uv(target.data, 'BomberAtlasUV')
            slots = [s.material for s in source.material_slots]
            atlas_slot = len(target.data.materials)
            target.data.materials.append(material)
            for poly in target.data.polygons:
                mat = slots[poly.material_index] if poly.material_index < len(slots) else None
                if mat not in mats:
                    continue
                original_uv = target.data.uv_layers[uv_names[(source, mat)]]
                x, y, w, h = placements[mats.index(mat)]
                u0, v0, u1, v1 = bounds[mat]
                for li in poly.loop_indices:
                    u, v = original_uv.data[li].uv[:]
                    uv.data[li].uv = ((x+(u-u0)/(u1-u0)*w)/width, (y+(v-v0)/(v1-v0)*h)/height)
                poly.material_index = atlas_slot
            target['BomberStudioAtlasManifest'] = os.path.join(folder, 'atlas.json')
        # Remove unused atlas-source slots only after indices have been rewritten.
        for target in created:
            used = set(p.material_index for p in target.data.polygons)
            for i in reversed(range(len(target.data.materials))):
                if i not in used:
                    target.data.materials.pop(index=i)
    except Exception:
        if cfg.atlas_keep_originals:
            for obj in created:
                bpy.data.objects.remove(obj, do_unlink=True)
        raise
    if cfg.atlas_keep_originals:
        for obj in objects:
            if any(s.material in mats for s in obj.material_slots):
                C.hidden(obj, True)
    manifest = {'schema': 'BomberStudio.BlenderAtlas/1', 'size': [width, height], 'files': files,
                'channels': list(channels), 'originals_preserved': cfg.atlas_keep_originals,
                'materials': [{'name': m.name, 'rect': rect, 'uv_bounds': bounds[m]} for m, rect in zip(mats, placements)]}
    core.atomic_json(os.path.join(folder, 'atlas.json'), manifest)
    C.select_only(created, context)
    C.log("图集输出: " + folder, context)
    return manifest


class BOMBER_OT_atlas(bpy.types.Operator):
    bl_idname = 'bomberstudio.atlas'
    bl_label = '合并贴图'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            manifest = create_atlas(context)
            self.report({'INFO'}, "图集完成: {0} × {1}，{2} 个通道".format(manifest['size'][0], manifest['size'][1], len(manifest['files'])))
            return {'FINISHED'}
        except Exception as exc:
            return C.fail(self, exc, context)


CLASSES = (BOMBER_OT_atlas,)
