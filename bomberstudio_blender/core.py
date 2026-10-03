# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure Python export contracts, packing and file transactions (Python 3.5+)."""
import hashlib
import json
import math
import os
import re
import shutil
import struct
import time
import uuid

VERSION = (1, 1, 1)
IMAGE_EXTENSIONS = ('.png', '.tga', '.jpg', '.jpeg', '.bmp', '.dds', '.tif', '.tiff', '.exr')
SIDECARS = ('.genshin.json', '.starrail.json', '.hi3.json', '.zzz.json')


def natural_key(value):
    return tuple((1, int(x)) if x.isdigit() else (0, x.lower())
                 for x in re.split(r'(\d+)', value))


def clean_name(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', re.sub(r'\.\d{3}$', '', value).lower())


def local_path(root, relative):
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, relative))
    if os.path.commonpath([root, path]) != root:
        raise ValueError("文件引用越出导出目录: " + relative)
    return path


def read_json(path):
    with open(path, 'r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def atomic_bytes(path, content, backup=True):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    staging = os.path.join(folder, '.' + os.path.basename(path) + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(staging, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and os.path.exists(path):
            shutil.copy2(path, path + '.bak-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.remove(staging)
    return path


def atomic_json(path, content, backup=True):
    return atomic_bytes(path, json.dumps(content, ensure_ascii=False, indent=2).encode('utf8'), backup)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def xy(value, default):
    if isinstance(value, dict):
        return (float(value.get('X', value.get('x', default[0]))),
                float(value.get('Y', value.get('y', default[1]))))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return default


def normalize_material(row):
    textures = row.get('textureMappings', row.get('textures', [])) or []
    result = []
    for tex in textures:
        if not isinstance(tex, dict) or not tex.get('Name'):
            continue
        result.append({
            'file': tex['Name'], 'property': tex.get('PropertyName', ''),
            'dest': tex.get('Dest', -1), 'uv': max(0, int(tex.get('UVSet', 0))),
            'scale': xy(tex.get('Scale'), (1, 1)), 'offset': xy(tex.get('Offset'), (0, 0)),
            'wrap': int(tex.get('WrapMode', 0)), 'ignore_alpha': bool(tex.get('PreviewIgnoreAlpha', False)),
        })
    base = row.get('baseColor')
    candidates = [t for t in result if t['property'] == base] if base else []
    if not candidates and 'baseColor' not in row:
        candidates = [t for t in result if t['dest'] == 0]
    return {
        'name': row.get('exportedName', row.get('Name', row.get('m_Name', 'Material'))),
        'base': candidates[0] if candidates else None, 'textures': result,
        'source': row, 'shader': row.get('SourceShaderName', ''),
    }


def load_sidecars(model_path):
    reports = []
    for suffix in SIDECARS:
        path = model_path + suffix
        if os.path.isfile(path):
            data = read_json(path)
            reports.append((path, data, [normalize_material(x) for x in data.get('materials', [])]))
    return reports


def texture_index(root):
    rows = []
    if not os.path.isdir(root):
        raise ValueError("贴图目录不存在: " + root)
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '临时文件夹')
        for leaf in sorted(files, key=natural_key):
            if leaf.lower().endswith(IMAGE_EXTENSIONS):
                rel = os.path.relpath(os.path.join(folder, leaf), root)
                match = re.search(r'(?i)(?:^|[^a-z])lod[_ -]?(\d+)', rel)
                rows.append({'path': os.path.join(folder, leaf), 'name': leaf,
                             'lod': 'LOD' + match.group(1) if match else '未标记'})
    return rows


def resolve_texture(root, name, index=None):
    path = local_path(root, name.replace('\\', '/'))
    if os.path.isfile(path):
        return path
    wanted = os.path.basename(name.replace('\\', '/')).lower()
    matches = [r['path'] for r in (index if index is not None else texture_index(root))
               if os.path.basename(r['path']).lower() == wanted]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("贴图同名歧义，保留原绑定: " + name)
    raise ValueError("缺少贴图: " + name)


def validate_cache(path):
    data = read_json(path)
    root = os.path.dirname(os.path.abspath(path))
    if data.get('schema') != 'BomberStudio.NoWind.VertexCache/1' or data.get('completed') is not True:
        raise ValueError("物理缓存未完成或 schema 不匹配")
    frames, fps = int(data['frames']), int(data['fps'])
    if frames < 1 or fps < 1 or not data.get('meshes'):
        raise ValueError("缓存帧数/FPS/网格无效")
    for row in data['meshes']:
        filename = local_path(root, row['cache'])
        count = int(row['vertexCount'])
        with open(filename, 'rb') as stream:
            header = stream.read(32)
        if len(header) != 32:
            raise ValueError("PC2 文件头截断")
        magic, version, points, start, rate, sample_count = struct.unpack('<12siiffi', header)
        if (magic, version, points, start, rate, sample_count) != (b'POINTCACHE2\0', 1, count, 1., 1., frames):
            raise ValueError("PC2 文件头不匹配: " + filename)
        if count < 1 or os.path.getsize(filename) != 32 + count * frames * 12:
            raise ValueError("PC2 长度不匹配: " + filename)
        if sha256(filename) != row['sha256'].lower():
            raise ValueError("PC2 SHA256 不匹配: " + filename)
        for sub in row['submeshes']:
            for face in sub['triangles']:
                if len(face) != 3 or any(not isinstance(i, int) or i < 0 or i >= count for i in face):
                    raise ValueError("PC2 三角形索引越界")
        for coords in row.get('uvSets', {}).values():
            if len(coords) != count or any(len(v) != 2 for v in coords):
                raise ValueError("PC2 UV 数量不匹配")
    for desc in data.get('materials', {}).values():
        if desc.get('image') and not os.path.isfile(local_path(root, desc['image'])):
            raise ValueError("PC2 缺少材质贴图: " + desc['image'])
    return data


class UnionFind:
    def __init__(self, count):
        self.parents = list(range(count))

    def find(self, value):
        while self.parents[value] != value:
            self.parents[value] = self.parents[self.parents[value]]
            value = self.parents[value]
        return value

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parents[b] = a


def pack_rectangles(sizes, padding=2, square=True, algorithm='BINARY', max_size=8192):
    """Deterministic, unrotated guillotine or shelf packing. Coordinates include gutters."""
    if not sizes or padding < 0:
        raise ValueError("空图集或负间距")
    if any(w < 1 or h < 1 for w, h in sizes):
        raise ValueError("图块尺寸必须为正")
    ordered = sorted(range(len(sizes)), key=lambda i: (-max(sizes[i]), -sizes[i][0]*sizes[i][1], i))
    area = sum((w + 2*padding)*(h + 2*padding) for w, h in sizes)
    required = max(max(w, h) + 2*padding for w, h in sizes)
    side = 1
    while side < max(required, math.sqrt(area)):
        side *= 2
    while side <= max_size:
        free = [(0, 0, side, side)]
        placements = {}
        x = y = shelf_height = 0
        for i in ordered:
            w, h = sizes[i][0]+2*padding, sizes[i][1]+2*padding
            if algorithm == 'SHELF':
                if x + w > side:
                    x, y, shelf_height = 0, y+shelf_height, 0
                if y + h > side:
                    break
                placements[i] = (x+padding, y+padding, sizes[i][0], sizes[i][1])
                x += w
                shelf_height = max(shelf_height, h)
            else:
                choices = [(fw*fh-w*h, j) for j, (_, _, fw, fh) in enumerate(free) if fw >= w and fh >= h]
                if not choices:
                    break
                _, j = min(choices)
                fx, fy, fw, fh = free.pop(j)
                placements[i] = (fx+padding, fy+padding, sizes[i][0], sizes[i][1])
                if fw > w:
                    free.append((fx+w, fy, fw-w, h))
                if fh > h:
                    free.append((fx, fy+h, fw, fh-h))
        if len(placements) == len(sizes):
            height = side if square else max(y+h+padding for x, y, w, h in placements.values())
            width = side if square else max(x+w+padding for x, y, w, h in placements.values())
            return width, height, [placements[i] for i in range(len(sizes))]
        side *= 2
    raise ValueError("图集超过 {0}px；降低统一尺寸或分批合并".format(max_size))
