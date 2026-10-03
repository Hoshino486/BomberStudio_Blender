# SPDX-License-Identifier: GPL-3.0-or-later
"""ASCII FBX 7.x -> binary FBX for Blender's existing importer.

No SDK, executable, external dependency, scene evaluation or source-file edits.
The parser keeps the FBX element tree, IDs, connections and typed arrays rather
than rebuilding meshes (which would discard rigs, shapes and animation).
Python 3.5 syntax is intentional for Blender 2.79.
"""
import array
import base64
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import uuid
import zlib


BINARY_MAGIC = b'Kaydara FBX Binary  \x00\x1a\x00'
CONVERTER_VERSION = 2
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_ARRAY_VALUES = 64 * 1024 * 1024
MAX_NODES = 1000000
MAX_DEPTH = 128
_NAME = re.compile(rb'[A-Za-z_][A-Za-z0-9_\-]*')
_ATOM = re.compile(rb'[^ \t\r\n,:{}*;"]+')
_SPACE = re.compile(rb'[ \t\r\n]+')
_VERSION = re.compile(rb'\bFBXVersion[ \t]*:[ \t]*(\d+)')
_ARRAY_END = re.compile(rb';[^\r\n]*|}')
_ARRAY_SEP = re.compile(rb'[,\s]+')
_INT = re.compile(rb'[+-]?\d+\Z')
_INT_ARRAYS = set((
    b'PolygonVertexIndex', b'Edges', b'Indexes', b'Index', b'NormalsIndex',
    b'BinormalsIndex', b'TangentsIndex', b'UVIndex', b'ColorIndex',
    b'Materials', b'Smoothing', b'TextureId', b'KeyAttrFlags',
    b'KeyAttrRefCount', b'PointsIndex', b'BlendModes', b'VertexIndexArray',
    b'EdgeIndexArray', b'PolygonIndexArray', b'Visibility',
))
_DOUBLE_ARRAYS = set((
    b'Vertices', b'Normals', b'NormalsW', b'Tangents', b'TangentsW',
    b'Binormals', b'BinormalsW', b'UV', b'Colors', b'Weights',
    b'FullWeights', b'Transform', b'TransformLink', b'TransformAssociateModel',
    b'Matrix', b'Points', b'KnotVector', b'KnotVectorU', b'KnotVectorV',
    b'Alphas', b'KeyValueDouble', b'BlendWeights', b'EdgeCrease', b'VertexCrease',
))
_FLOAT_PROPS = set((
    b'Number', b'double', b'Double', b'Color', b'ColorRGB', b'ColorRGBA',
    b'Vector', b'Vector2D', b'Vector3D', b'Vector4', b'Matrix', b'Matrix4x4',
    b'Lcl Translation', b'Lcl Rotation', b'Lcl Scaling', b'Visibility',
))
_LONG_PROPS = set((b'KTime', b'LongLong', b'ULongLong', b'longlong', b'ulonglong'))
_INT_PROPS = set((b'int', b'Integer', b'UInt', b'uint', b'Bool', b'bool',
                  b'enum', b'Enum', b'Short', b'UShort', b'Char', b'UChar',
                  b'Visibility Inheritance'))
_DOUBLE_SCALARS = set((b'Default', b'DeformPercent', b'Link_DeformAcuracy',
                       b'TextureAlpha', b'ModelUVTranslation',
                       b'ModelUVScaling'))
_LONG_SCALARS = set((b'Node', b'RootNode', b'LocalTime', b'ReferenceTime'))
_ENTITY = re.compile(rb'&(?:quot|cr|lf);')
_ENTITIES = {b'&quot;': b'"', b'&cr;': b'\r', b'&lf;': b'\n'}


class AsciiFBXError(ValueError):
    pass


class Node:
    __slots__ = ('name', 'props', 'children', 'block')

    def __init__(self, name, props=None, children=None, block=False):
        self.name = name
        self.props = props if props is not None else []
        self.children = children if children is not None else []
        self.block = block


class Parser:
    def __init__(self, data):
        self.data = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
        self.pos = 0
        self.nodes = 0
        self.array_values = 0
        self.inferred_arrays = set()
        match = _VERSION.search(self.data[:65536])
        if not match:
            raise AsciiFBXError('ASCII FBX 缺少 FBXVersion 文件头')
        self.version = int(match.group(1))
        if not 7100 <= self.version <= 7700:
            raise AsciiFBXError('ASCII FBX 版本 {0} 尚未适配；当前支持 7100–7700'.format(self.version))

    def error(self, message):
        line = self.data.count(b'\n', 0, self.pos) + 1
        raise AsciiFBXError('ASCII FBX 第 {0} 行：{1}'.format(line, message))

    def skip(self, newlines=True):
        data = self.data
        while self.pos < len(data):
            if newlines:
                match = _SPACE.match(data, self.pos)
                if match:
                    self.pos = match.end()
                    continue
            elif data[self.pos:self.pos+1] in (b' ', b'\t'):
                self.pos += 1
                continue
            if data[self.pos:self.pos+1] == b';':
                end = data.find(b'\n', self.pos)
                self.pos = len(data) if end < 0 else end
                if newlines:
                    continue
            break

    def expect(self, token):
        self.skip()
        if self.data[self.pos:self.pos+len(token)] != token:
            self.error('预期 ' + repr(token))
        self.pos += len(token)

    def string(self):
        self.pos += 1
        end = self.data.find(b'"', self.pos)
        if end < 0:
            self.error('字符串缺少结束引号')
        # FBX escapes quotes/newlines with entities, NOT Python/C backslashes.
        # In particular, preserve literal C:\\textures\\normal.png paths.
        value = self.data[self.pos:end]
        self.pos = end + 1
        value = _ENTITY.sub(lambda m: _ENTITIES[m.group()], value)
        try:
            value.decode('utf8')
        except UnicodeDecodeError:
            self.error('字符串不是 UTF-8；请从源软件重新导出 UTF-8 FBX')
        return value

    def scalar(self):
        if self.data[self.pos:self.pos+1] == b'"':
            return 'S', self.string()
        match = _ATOM.match(self.data, self.pos)
        if not match:
            self.error('无效属性')
        token = match.group()
        self.pos = match.end()
        if _INT.fullmatch(token):
            return 'N', int(token)
        try:
            value = float(token)
        except ValueError:
            # Legacy one-character FBX flags, e.g. Shading: Y / W.
            if len(token) == 1 and token.isalpha():
                return 'C', token
            if token.lower() in (b'true', b'false'):
                return 'N', int(token.lower() == b'true')
            self.error('无效数值 ' + repr(token[:64]))
        if not math.isfinite(value):
            self.error('非有限数值 ' + repr(token[:64]))
        return 'D', value

    def number_array(self, name):
        self.pos += 1  # '*'
        self.skip()
        match = re.match(rb'\d+', self.data[self.pos:self.pos+24])
        if not match:
            self.error('数组缺少长度')
        count = int(match.group())
        self.pos += len(match.group())
        if count > MAX_ARRAY_VALUES:
            self.error('数组长度超过内存保护上限')
        self.expect(b'{')
        self.expect(b'a')
        self.expect(b':')
        end = self.pos
        while True:
            match = _ARRAY_END.search(self.data, end)
            if match is None:
                self.error('数组缺少结束括号')
            if match.group() == b'}':
                end = match.start()
                break
            end = match.end()
        raw = self.data[self.pos:end]
        self.pos = end + 1
        if b';' in raw:
            raw = re.sub(rb';[^\r\n]*', b'', raw)
        tokens = _ARRAY_SEP.split(raw.strip(b' \t\r\n,'))
        if tokens == [b'']:
            tokens = []
        if len(tokens) != count:
            self.error('{0} 数组声明 {1} 项，实际 {2} 项'.format(name.decode(), count, len(tokens)))
        self.array_values += count
        bits = name == b'KeyAttrDataFloat' and self.version >= 7200
        if bits:
            code, typecode = 'f', 'I'
        elif name in _INT_ARRAYS:
            code, typecode = 'i', 'i'
        elif name in _DOUBLE_ARRAYS:
            code, typecode = 'd', 'd'
        elif name == b'KeyTime':
            code, typecode = 'l', 'q'
        elif name in (b'KeyValueFloat', b'KeyAttrDataFloat'):
            code, typecode = 'f', 'f'
        elif name == b'ImageData':
            code, typecode = 'c', 'B'
        else:
            self.inferred_arrays.add(name.decode())
            if all(_INT.fullmatch(t) for t in tokens):
                code, typecode = ('l', 'q') if any(abs(int(t)) > 2147483647 for t in tokens) else ('i', 'i')
            else:
                code, typecode = 'd', 'd'
        values = array.array(typecode)
        try:
            if bits:
                # In FBX >= 7200 ASCII this is INTEGER BIT PATTERNS, not floats.
                # Numeric int->float conversion corrupts animation tangents/flags.
                for token in tokens:
                    value = int(token)
                    if not -2147483648 <= value <= 4294967295:
                        self.error('KeyAttrDataFloat 位模式超出 32 位')
                    values.append(value & 0xffffffff)
            elif typecode in ('d', 'f'):
                for token in tokens:
                    value = float(token)
                    if not math.isfinite(value):
                        self.error(name.decode() + ' 包含非有限数值')
                    values.append(value)
            else:
                values.extend(int(token) for token in tokens)
        except (ValueError, OverflowError) as exc:
            if isinstance(exc, AsciiFBXError):
                raise
            self.error(name.decode() + ' 数组数值/类型异常: ' + str(exc))
        if sys.byteorder != 'little':
            values.byteswap()
        payload = values.tobytes()
        compressed = zlib.compress(payload, 6) if len(payload) >= 128 else payload
        encoded = len(payload) >= 128 and len(compressed) < len(payload)
        blob = compressed if encoded else payload
        return code, struct.pack('<III', count, int(encoded), len(blob)) + blob

    def node(self, depth):
        if depth > MAX_DEPTH:
            self.error('嵌套层数超过保护上限')
        self.skip()
        if self.data[self.pos:self.pos+1] == b'"':
            name = self.string()
        else:
            match = _NAME.match(self.data, self.pos)
            if not match:
                self.error('元素名称无效')
            name = match.group()
            self.pos = match.end()
        if len(name) > 255:
            self.error('元素名称超过 255 字节')
        self.expect(b':')
        self.nodes += 1
        if self.nodes > MAX_NODES:
            self.error('节点数超过保护上限')
        result = Node(name)
        self.skip(False)
        if self.data[self.pos:self.pos+1] == b'*':
            result.props.append(self.number_array(name))
            return result
        require_value = False
        while self.pos < len(self.data):
            self.skip(False)
            token = self.data[self.pos:self.pos+1]
            if token == b',':
                self.pos += 1
                self.skip()
                require_value = True
                continue
            if token == b'{':
                self.pos += 1
                result.block = True
                result.children = self.block(depth + 1, True)
                break
            if token in (b'\r', b'\n'):
                self.skip()
                if self.data[self.pos:self.pos+1] == b'{':
                    continue
                if not require_value:
                    break
            if token in (b'}', b'') or self.pos >= len(self.data):
                if require_value:
                    self.error('逗号后缺少属性')
                break
            # Allow adjacent nodes on one line, e.g. Foo: 1 Bar: 2.
            if result.props and not require_value:
                break
            result.props.append(self.scalar())
            require_value = False
        return result

    def block(self, depth=0, closing=False):
        nodes = []
        while True:
            self.skip()
            if self.pos >= len(self.data):
                if closing:
                    self.error('元素缺少结束括号')
                return nodes
            if self.data[self.pos:self.pos+1] == b'}':
                if not closing:
                    self.error('多余的结束括号')
                self.pos += 1
                return nodes
            nodes.append(self.node(depth))

    def parse(self):
        nodes = self.block()
        header = next((n for n in nodes if n.name == b'FBXHeaderExtension'), None)
        version = next((n for n in header.children if n.name == b'FBXVersion'), None) if header else None
        if version is None or version.props != [('N', self.version)]:
            self.error('FBXVersion 与实际文件头不一致')
        objects = next((n for n in nodes if n.name == b'Objects'), None)
        if objects is None:
            self.error('缺少 Objects 数据')
        ids = set()
        for obj in objects.children:
            if not obj.props or obj.props[0][0] != 'N':
                self.error('Objects 元素缺少整数 ID')
            key = obj.props[0][1]
            if key == 0 or key in ids:
                self.error('Objects 存在重复或保留 ID: ' + str(key))
            ids.add(key)
        return nodes


def _string_property(value):
    return b'S' + struct.pack('<I', len(value)) + value


def _numeric(code, value):
    if code == 'C':
        return b'C' + (value if isinstance(value, bytes) else bytes((int(bool(value)),)))
    formats = {'I': '<i', 'L': '<q', 'F': '<f', 'D': '<d'}
    if code == 'L' and value > 9223372036854775807:
        if value > 18446744073709551615:
            raise AsciiFBXError('整数超出 FBX 64 位范围')
        value -= 18446744073709551616
    try:
        return code.encode() + struct.pack(formats[code], value)
    except (struct.error, OverflowError) as exc:
        raise AsciiFBXError('FBX 属性 {0} 数值越界: {1}'.format(code, value)) from exc


def _property_bytes(node, parent, source_dir):
    props = node.props
    if node.name == b'Content' and parent in (b'Video', b'Texture'):
        if any(kind != 'S' for kind, _ in props):
            raise AsciiFBXError('嵌入贴图 Content 应为 Base64 字符串')
        try:
            # Each chunk may have its own Base64 padding.
            content = b''.join(base64.b64decode(re.sub(rb'\s+', b'', value), validate=True)
                               for _, value in props)
        except (ValueError, TypeError) as exc:
            raise AsciiFBXError('嵌入贴图 Base64 损坏') from exc
        return [b'R' + struct.pack('<I', len(content)) + content]
    result = []
    semantic = props[1][1] if node.name == b'P' and len(props) >= 4 else None
    for index, (kind, value) in enumerate(props):
        if kind in ('i', 'l', 'f', 'd', 'b', 'c'):
            result.append(kind.encode() + value)
            continue
        if kind == 'S':
            identity = (parent == b'Objects' and index == 1) or (node.name == b'SceneInfo' and index == 0)
            if identity and b'::' in value:
                cls, name = value.split(b'::', 1)
                value = name + b'\x00\x01' + cls
            if source_dir and parent in (b'Video', b'Texture') and node.name in (b'FileName', b'Filename', b'RelativeFilename') and value:
                filename = value.decode('utf8').replace('\\', os.sep).replace('/', os.sep)
                if not os.path.isabs(filename):
                    filename = os.path.abspath(os.path.join(source_dir, filename))
                value = filename.encode('utf8')
            result.append(_string_property(value))
            continue
        code = kind
        if kind == 'N':
            code = 'I' if -2147483648 <= value <= 2147483647 else 'L'
        if node.name == b'P' and index >= 4:
            if semantic in _FLOAT_PROPS:
                code, value = 'D', float(value)
            elif semantic in (b'Float', b'float'):
                code, value = 'F', float(value)
            elif semantic in _LONG_PROPS:
                code, value = 'L', int(value)
            elif semantic in _INT_PROPS:
                code, value = 'I', int(value)
        elif ((parent == b'Objects' and index == 0) or
              (node.name == b'C' and parent == b'Connections' and index in (1, 2)) or
              node.name in _LONG_SCALARS or (node.name == b'Document' and index == 0)):
            code, value = 'L', int(value)
        elif node.name in _DOUBLE_SCALARS:
            code, value = 'D', float(value)
        result.append(_numeric(code, value))
    return result


def write_binary(stream, nodes, version, source_dir=None):
    """Write offset-correct FBX 32/64-bit nodes, independent of Blender APIs."""
    header = struct.Struct('<QQQB' if version >= 7500 else '<IIIB')
    sentinel = b'\0' * header.size
    stream.write(BINARY_MAGIC + struct.pack('<I', version))

    def write_node(node, parent):
        start = stream.tell()
        stream.write(sentinel)
        stream.write(node.name)
        props = _property_bytes(node, parent, source_dir)
        for payload in props:
            stream.write(payload)
        prop_size = sum(len(p) for p in props)
        for child in node.children:
            write_node(child, node.name)
        if node.block or node.children or node.name in (b'AnimationStack', b'AnimationLayer'):
            stream.write(sentinel)
        end = stream.tell()
        if version < 7500 and end > 0xffffffff:
            raise AsciiFBXError('FBX 7.1–7.4 二进制文件超过 32 位偏移范围')
        stream.seek(start)
        stream.write(header.pack(end, len(props), prop_size, len(node.name)))
        stream.seek(end)

    for node in nodes:
        write_node(node, b'')
    stream.write(sentinel)
    # Standard FBX binary footer; scene/header timestamps are left untouched.
    stream.write(bytes.fromhex('fabcab09d0c8d466b176fb831cf7267e'))
    stream.write(b'\0' * 4)
    stream.write(b'\0' * (16 - stream.tell() % 16))
    stream.write(struct.pack('<I', version))
    stream.write(b'\0' * 120)
    stream.write(bytes.fromhex('f85a8c6adef5d97eece90ce3758f290b'))


def normalize_skeleton_nodes(nodes):
    """Keep mixed Unity skeleton chains inside one Blender armature.

    Blender discovers LimbNode chains before its "fake bone" pass. A Root/Null
    in the middle of such a chain otherwise leaves descendant armature_setup
    under a None key, causing KeyError at link_hierarchy. Root nodes used as
    actual Cluster bones would also lose their weights if treated as objects.

    Only retag provable skin bones and Null/Root intermediates BETWEEN bones.
    Never delete bones, rewrite IDs/connections/matrices or absorb outer wrappers.
    """
    objects = next((n for n in nodes if n.name == b'Objects'), None)
    connections = next((n for n in nodes if n.name == b'Connections'), None)
    if objects is None or connections is None:
        return []
    models = {n.props[0][1]: n for n in objects.children if n.name == b'Model' and len(n.props) >= 3}
    clusters = {n.props[0][1] for n in objects.children
                if n.name == b'Deformer' and len(n.props) >= 3 and n.props[2][1] == b'Cluster'}
    parents, bound = {}, set()
    for edge in connections.children:
        if edge.name != b'C' or len(edge.props) < 3 or edge.props[0][1] != b'OO':
            continue
        child, parent = edge.props[1][1], edge.props[2][1]
        if child in models and parent in models:
            if child in parents and parents[child] != parent:
                raise AsciiFBXError('骨架节点存在多个父级: ' + str(child))
            parents[child] = parent
        if child in models and parent in clusters:
            bound.add(child)
    # Validate the whole model graph, including all-bone cycles.
    finished = set()
    for key in models:
        path, seen = [], set()
        while key in models and key not in finished:
            if key in seen:
                raise AsciiFBXError('骨架父子连接存在循环')
            path.append(key)
            seen.add(key)
            key = parents.get(key)
        finished.update(path)
    bone_ids = {key for key, n in models.items() if n.props[2][1] in (b'LimbNode', b'Limb')}
    bone_ids.update(key for key in bound if models[key].props[2][1] in (b'Null', b'Root'))
    promoted = set(key for key in bone_ids if models[key].props[2][1] not in (b'LimbNode', b'Limb'))
    for key in bone_ids:
        pending, seen = [], {key}
        parent = parents.get(key)
        while parent in models:
            if parent in seen:
                raise AsciiFBXError('骨架父子连接存在循环')
            seen.add(parent)
            if parent in bone_ids:
                promoted.update(pending)
                break
            if models[parent].props[2][1] not in (b'Null', b'Root'):
                break
            pending.append(parent)
            parent = parents.get(parent)
    changes = []
    for key in sorted(promoted):
        model = models[key]
        changes.append({'id': key, 'name': model.props[1][1].decode('utf8'),
                        'from': model.props[2][1].decode('utf8'), 'to': 'LimbNode',
                        'reason': 'skin_cluster_bone' if key in bound else 'intermediate_skeleton_node'})
        model.props[2] = ('S', b'LimbNode')
    return changes


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_json(path, value):
    handle, staging = tempfile.mkstemp(prefix='.metadata-', suffix='.pending', dir=os.path.dirname(path))
    try:
        with os.fdopen(handle, 'w', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if os.path.isfile(staging):
            os.remove(staging)


def prepare_import(source, temp_root, logger=None):
    """Return (readable_fbx_path, evidence). Never overwrite the source file."""
    source = os.path.abspath(source)
    with open(source, 'rb') as stream:
        head = stream.read(65536)
        if head.startswith(BINARY_MAGIC):
            return source, {'format': 'BINARY', 'converted': False}
        if b'FBXHeaderExtension' not in head or not _VERSION.search(head):
            raise AsciiFBXError('该文件既不是标准二进制 FBX，也没有可识别的 ASCII FBX 文件头')
        stream.seek(0, 2)
        if stream.tell() > MAX_FILE_BYTES:
            raise AsciiFBXError('ASCII FBX 超过 512 MiB 内存保护上限；请在源软件分批导出')
        stream.seek(0)
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise AsciiFBXError('ASCII FBX 读取过程中超过大小上限')
    digest = hashlib.sha256(data).hexdigest()
    key = hashlib.sha256((source + '\0' + digest + '\0' + str(CONVERTER_VERSION)).encode('utf8')).hexdigest()
    cache = os.path.join(os.path.abspath(temp_root), 'ascii-fbx')
    os.makedirs(cache, exist_ok=True)
    basename = re.sub(r'[^A-Za-z0-9_.\-\u4e00-\u9fff]', '_', os.path.splitext(os.path.basename(source))[0])[:72]
    output = os.path.join(cache, basename + '-' + key[:20] + '.fbx')
    metadata = output + '.json'
    if os.path.isfile(output) and os.path.isfile(metadata):
        try:
            with open(metadata, encoding='utf8') as stream:
                receipt = json.load(stream)
            valid = (isinstance(receipt, dict) and receipt.get('source_sha256') == digest and
                     receipt.get('converter') == CONVERTER_VERSION and
                     receipt.get('source') == source and
                     receipt.get('binary_sha256') == _sha256_file(output))
            if valid:
                receipt['cache_hit'] = True
                if logger:
                    logger('ASCII FBX 已命中校验缓存: ' + output)
                return output, receipt
        except (ValueError, OSError, TypeError):
            pass
    if logger:
        logger('ASCII FBX 开始转换（原文件只读）: ' + source)
    start = time.time()
    parser = Parser(data)
    nodes = parser.parse()
    skeleton_changes = normalize_skeleton_nodes(nodes)
    object_node = next(n for n in nodes if n.name == b'Objects')
    object_counts = {}
    for node in object_node.children:
        key_name = node.name.decode()
        object_counts[key_name] = object_counts.get(key_name, 0) + 1
    handle, staging = tempfile.mkstemp(prefix='.conversion-', suffix='.pending', dir=cache)
    try:
        with os.fdopen(handle, 'w+b') as stream:
            write_binary(stream, nodes, parser.version, os.path.dirname(source))
            stream.flush()
            os.fsync(stream.fileno())
        receipt = {'format': 'ASCII', 'converted': True, 'converter': CONVERTER_VERSION,
                   'source': source, 'source_sha256': digest, 'source_bytes': len(data),
                   'binary': output, 'binary_sha256': _sha256_file(staging),
                   'binary_bytes': os.path.getsize(staging), 'fbx_version': parser.version,
                   'nodes': parser.nodes, 'array_values': parser.array_values,
                   'object_counts': object_counts, 'inferred_arrays': sorted(parser.inferred_arrays),
                   'skeleton_normalization': skeleton_changes,
                   'cache_hit': False, 'seconds': round(time.time()-start, 3)}
        if os.path.isfile(output):
            shutil.copy2(output, output + '.invalid-' + uuid.uuid4().hex[:8])
        os.replace(staging, output)
        _publish_json(metadata, receipt)
    finally:
        if os.path.isfile(staging):
            os.remove(staging)
    if logger:
        logger('ASCII FBX 转换完成: ' + json.dumps(receipt, ensure_ascii=False))
    return output, receipt
