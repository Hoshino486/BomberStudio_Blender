"""Pure Python converter tests; no bpy and no source asset mutation."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'bomberstudio_blender').exists():
    ROOT = Path(r'E:\mapper\Unpackaging tool\unityUnpackaging tool\blender插件')
TMP = ROOT / '临时文件夹' / 'ascii-fbx-unit'
TMP.mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location('ascii_converter', str(ROOT/'bomberstudio_blender/ascii_fbx.py'))
fbx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fbx)


def scene(body=b'', version=7700, extra=b''):
    return (b'; FBX ASCII test\nFBXHeaderExtension: {\n FBXVersion: ' + str(version).encode() +
            b'\n}\nObjects: {\n' + body + b'\n}\n' + extra)


def read_binary(payload):
    """Independent decoder used to verify node offsets, scalar types and array bits."""
    stream = io.BytesIO(payload)
    assert stream.read(23) == fbx.BINARY_MAGIC
    version, = struct.unpack('<I', stream.read(4))
    header = struct.Struct('<QQQB' if version >= 7500 else '<IIIB')

    def node():
        raw = stream.read(header.size)
        end, count, prop_bytes, name_len = header.unpack(raw)
        if not end:
            assert raw == b'\0' * header.size
            return None
        name = stream.read(name_len)
        start = stream.tell()
        props = []
        for _ in range(count):
            kind = stream.read(1)
            if kind in b'ILFD':
                fmt = {b'I': '<i', b'L': '<q', b'F': '<f', b'D': '<d'}[kind]
                value = struct.unpack(fmt, stream.read(struct.calcsize(fmt)))[0]
            elif kind == b'C':
                value = stream.read(1)
            elif kind in (b'S', b'R'):
                length, = struct.unpack('<I', stream.read(4))
                value = stream.read(length)
            elif kind in (b'i', b'l', b'f', b'd', b'b', b'c'):
                size, encoding, length = struct.unpack('<III', stream.read(12))
                data = stream.read(length)
                data = zlib.decompress(data) if encoding else data
                width = {b'i': 4, b'l': 8, b'f': 4, b'd': 8, b'b': 1, b'c': 1}[kind]
                assert len(data) == size * width
                value = (size, data)
            else:
                raise AssertionError(kind)
            props.append((kind.decode(), value))
        assert stream.tell() - start == prop_bytes
        children = []
        while stream.tell() < end:
            child = node()
            if child is None:
                break
            children.append(child)
        assert stream.tell() == end
        return {'name': name, 'props': props, 'children': children}
    nodes = []
    while True:
        n = node()
        if n is None:
            break
        nodes.append(n)
    return nodes


def convert(data, source_dir=None):
    parser = fbx.Parser(data)
    tree = parser.parse()
    out = io.BytesIO()
    fbx.write_binary(out, tree, parser.version, source_dir)
    return read_binary(out.getvalue())


def find(tree, name):
    for n in tree:
        if n['name'] == name:
            return n
        try:
            return find(n['children'], name)
        except KeyError:
            pass
    raise KeyError(name)


class AsciiFBXTests(unittest.TestCase):
    def test_binary_versions_offsets_empty_scopes(self):
        for version in (7100, 7400, 7500, 7700):
            tree = convert(scene('Geometry: 1,"Geometry::网格","Mesh" { Empty: {} }'.encode('utf8'), version))
            g = find(tree, b'Geometry')
            self.assertEqual(g['props'][0], ('L', 1))
            self.assertEqual(g['props'][1], ('S', '网格'.encode()+b'\0\1Geometry'))
            self.assertEqual(find(tree, b'Empty')['children'], [])

    def test_windows_paths_and_string_entities_not_python_escapes(self):
        tree = convert(scene(extra=b'Text: "E:\\new\\textures\\normal.png &quot;x&quot;&cr;&lf; &amp;"'))
        self.assertEqual(find(tree, b'Text')['props'][0][1], b'E:\\new\\textures\\normal.png "x"\r\n &amp;')

    def test_semantic_scalar_types_and_small_ids(self):
        body = b'''Model: 1,"Model::Root","LimbNode" {
Properties70: {
P: "Lcl Scaling","Lcl Scaling","","A",1,1,1
P: "UnitScaleFactor","double","Number","",1
P: "Enabled","bool","","",1
P: "Time","KTime","Time","",0
P: "Float","Float","","U",1
} }'''
        tree = convert(scene(body, extra=b'Connections: { C: "OO",1,0 }\nPoseNode: { Node: 1 }'))
        p = find(tree, b'Properties70')['children']
        self.assertEqual([t for t, _ in p[0]['props'][4:]], ['D']*3)
        self.assertEqual([row['props'][4][0] for row in p[1:]], ['D','I','L','F'])
        self.assertEqual([t for t, _ in find(tree, b'C')['props']], ['S','L','L'])
        self.assertEqual(find(tree, b'Node')['props'][0][0], 'L')

    def test_typed_arrays_and_multiline_comments(self):
        tree = convert(scene(b'''Geometry: 1,"Geometry::m","Mesh" {
Vertices: *6 { a: 0,0,0,
; comment with a fake closing }
1,2,3 }
PolygonVertexIndex: *3 { a: 0,1,-3 }
UV: *0 { a: }
}''', extra=b'KeyTime: *2 { a: 0,46186158000 }'))
        self.assertEqual(find(tree, b'Vertices')['props'][0], ('d',(6,struct.pack('<6d',0,0,0,1,2,3))))
        self.assertEqual(find(tree, b'PolygonVertexIndex')['props'][0][0], 'i')
        self.assertEqual(find(tree, b'UV')['props'][0], ('d',(0,b'')))
        self.assertEqual(find(tree, b'KeyTime')['props'][0][0], 'l')

    def test_animation_attribute_integer_bit_patterns(self):
        bits = [0, 1065353216, -1082130432, 4294967295, 1]
        tree = convert(scene(extra=b'KeyAttrDataFloat: *5 { a: '+','.join(map(str,bits)).encode()+b' }'))
        self.assertEqual(find(tree, b'KeyAttrDataFloat')['props'][0],
                         ('f',(5,struct.pack('<5I',*(v & 0xffffffff for v in bits)))))
        old = convert(scene(version=7100, extra=b'KeyAttrDataFloat: *2 { a: 1,-1 }'))
        self.assertEqual(find(old,b'KeyAttrDataFloat')['props'][0][1][1],struct.pack('<2f',1,-1))

    def test_array_compression_roundtrip(self):
        raw = b'0,'*999+b'0'
        tree = convert(scene(extra=b'Weights: *1000 { a: '+raw+b' }'))
        self.assertEqual(find(tree,b'Weights')['props'][0][1],(1000,b'\0'*8000))

    def test_embedded_image_content(self):
        body = b'Video: 1,"Video::t","Clip" { Content: , "'+base64.b64encode(b'PNG')+b'",\n "'+base64.b64encode(b'image')+b'" }'
        self.assertEqual(find(convert(scene(body)),b'Content')['props'],[('R',b'PNGimage')])
        with self.assertRaises(fbx.AsciiFBXError):
            convert(scene(b'Video: 1,"Video::t","Clip" { Content: "!!!" }'))

    def test_relative_texture_rebase_preserves_subdirectory(self):
        tree = convert(scene(b'Texture: 1,"Texture::t","" { RelativeFilename: "two\\face.png" }'), str(TMP))
        path = find(tree,b'RelativeFilename')['props'][0][1].decode()
        self.assertEqual(Path(path),TMP/'two/face.png')

    def test_invalid_array_counts_syntax_numbers_and_versions(self):
        for data in (scene(extra=b'Vertices: *3 { a: 1,2 }'),
                     scene(extra=b'Vertices: *1 { a: nan }'),
                     scene(extra=b'Vertices: *1 { a: X }'),
                     scene(extra=b'Thing: {'),
                     scene(extra=b'Thing: "not closed'),
                     scene(version=6100)):
            with self.assertRaises(fbx.AsciiFBXError):
                fbx.Parser(data).parse()

    def test_bom_and_node_newline(self):
        tree = convert(b'\xef\xbb\xbf'+scene(b'Model: 1,"Model::m","Null"\n{\n Empty:\n}\n'))
        self.assertEqual(find(tree,b'Empty')['props'],[])

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(fbx.AsciiFBXError,'ID'):
            fbx.Parser(scene(b'Model: 1,"Model::a","Null" {}\nModel: 1,"Model::b","Null" {}')).parse()

    def test_memory_and_nesting_limits(self):
        old = fbx.MAX_ARRAY_VALUES
        try:
            fbx.MAX_ARRAY_VALUES = 1
            with self.assertRaisesRegex(fbx.AsciiFBXError,'上限'):
                fbx.Parser(scene(extra=b'Weights: *2 { a: 0,0 }')).parse()
        finally:
            fbx.MAX_ARRAY_VALUES = old
        with self.assertRaises(fbx.AsciiFBXError):
            fbx.Parser(scene(extra=b'A: {'*(fbx.MAX_DEPTH+2)+b'}'*(fbx.MAX_DEPTH+2))).parse()

    def test_skin_roots_and_intermediates_not_outer_wrappers(self):
        body = b'''Model: 1,"Model::Outer","Null" {}
Model: 2,"Model::Root","Root" {}
Model: 3,"Model::Gap","Null" {}
Model: 4,"Model::Tip","LimbNode" {}
Deformer: 5,"SubDeformer::root","Cluster" {}'''
        extra = b'Connections: { C: "OO",2,1\n C: "OO",3,2\n C: "OO",4,3\n C: "OO",2,5 }'
        tree = fbx.Parser(scene(body,extra=extra)).parse()
        changes = fbx.normalize_skeleton_nodes(tree)
        self.assertEqual({c['id'] for c in changes},{2,3})
        self.assertEqual(tree[1].children[0].props[2],('S',b'Null'))
        self.assertEqual(fbx.normalize_skeleton_nodes(tree),[])

    def test_model_parent_cycle_rejected(self):
        data = scene(b'Model: 1,"Model::A","LimbNode" {}\nModel: 2,"Model::B","LimbNode" {}',
                     extra=b'Connections: { C: "OO",1,2\n C: "OO",2,1 }')
        with self.assertRaisesRegex(fbx.AsciiFBXError,'循环'):
            fbx.normalize_skeleton_nodes(fbx.Parser(data).parse())

    def test_cache_hit_corruption_invalidation_and_source_unchanged(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as directory:
            source = Path(directory)/'模型.fbx'
            source.write_bytes(scene())
            original = source.read_bytes()
            output, first = fbx.prepare_import(str(source),directory)
            self.assertFalse(first['cache_hit'])
            self.assertEqual(Path(output).read_bytes()[:23],fbx.BINARY_MAGIC)
            _, second = fbx.prepare_import(str(source),directory)
            self.assertTrue(second['cache_hit'])
            Path(output).write_bytes(b'corrupt')
            _, third = fbx.prepare_import(str(source),directory)
            self.assertFalse(third['cache_hit'])
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(third['source_sha256'],hashlib.sha256(original).hexdigest())
            source.write_bytes(original+b'\n; changed')
            different, _ = fbx.prepare_import(str(source),directory)
            self.assertNotEqual(output,different)

    def test_binary_passthrough_and_failure_no_output(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as directory:
            source = Path(directory)/'source.fbx'
            source.write_bytes(fbx.BINARY_MAGIC+struct.pack('<I',7400))
            path, receipt = fbx.prepare_import(str(source),directory)
            self.assertEqual(path,str(source))
            self.assertFalse(receipt['converted'])
            source.write_bytes(scene(extra=b'Broken: {'))
            with self.assertRaises(fbx.AsciiFBXError):
                fbx.prepare_import(str(source),directory)
            self.assertFalse(list((Path(directory)/'ascii-fbx').glob('*.fbx')))
            self.assertFalse(list((Path(directory)/'ascii-fbx').glob('*.pending')))


if __name__ == '__main__':
    unittest.main()
