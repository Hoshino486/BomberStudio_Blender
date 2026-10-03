"""Run: py -3.13 -m unittest discover -s tests -p test_core.py -v"""
import importlib.util
import json
import os
import pathlib
import random
import struct
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = ROOT / '临时文件夹' / 'unit-tests'
TMP.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('bomber_core', str(ROOT / 'bomberstudio_blender' / 'core.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class CoreTests(unittest.TestCase):
    def test_normalize_four_sidecars(self):
        for field in ('textures', 'textureMappings'):
            result = core.normalize_material({'exportedName': 'Face', 'baseColor': '_MainTex', field: [
                {'Name': 'face.png', 'PropertyName': '_MainTex', 'Dest': 0, 'UVSet': 1,
                 'Scale': {'X': 2, 'Y': 3}, 'Offset': {'X': .1, 'Y': .2}, 'PreviewIgnoreAlpha': True}]})
            self.assertEqual(result['base']['uv'], 1)
            self.assertEqual(result['base']['scale'], (2, 3))
            self.assertTrue(result['base']['ignore_alpha'])

    def test_explicit_null_basecolor_is_not_guessed(self):
        row = {'exportedName': 'SuppressedPass', 'baseColor': None,
               'textures': [{'Name': 'data.png', 'Dest': 0, 'PropertyName': '_Mask'}]}
        self.assertIsNone(core.normalize_material(row)['base'])

    def test_atomic_backup(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as folder:
            p = os.path.join(folder, 'settings.json')
            core.atomic_json(p, {'v': 1})
            core.atomic_json(p, {'v': 2})
            self.assertEqual(core.read_json(p), {'v': 2})
            backups = list(pathlib.Path(folder).glob('*.bak-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(core.read_json(str(backups[0])), {'v': 1})

    def test_path_guard(self):
        with self.assertRaises(ValueError):
            core.local_path(str(TMP), '../outside.png')

    def test_resolve_collision(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as folder:
            for sub in ('a', 'b'):
                p = pathlib.Path(folder)/sub
                p.mkdir()
                (p/'shared.png').write_bytes(b'image')
            with self.assertRaises(ValueError):
                core.resolve_texture(folder, 'shared.png')

    def test_texture_lod(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as folder:
            pathlib.Path(folder, 'Body_LOD2_color.png').touch()
            self.assertEqual(core.texture_index(folder)[0]['lod'], 'LOD2')

    def test_packing_property(self):
        rng = random.Random(19)
        for algorithm in ('BINARY', 'SHELF'):
            for _ in range(40):
                sizes = [(rng.randint(1, 160), rng.randint(1, 160)) for i in range(15)]
                w, h, rects = core.pack_rectangles(sizes, 3, False, algorithm, 4096)
                for i, (x, y, rw, rh) in enumerate(rects):
                    self.assertGreaterEqual(x, 3)
                    self.assertGreaterEqual(y, 3)
                    self.assertLessEqual(x+rw+3, w)
                    self.assertLessEqual(y+rh+3, h)
                    for ox, oy, ow, oh in rects[:i]:
                        self.assertTrue(x+rw+6 <= ox or ox+ow+6 <= x or y+rh+6 <= oy or oy+oh+6 <= y)

    def test_packing_limit(self):
        with self.assertRaises(ValueError):
            core.pack_rectangles([(10000, 10)], max_size=4096)

    def test_union_transitive(self):
        uf = core.UnionFind(5)
        uf.union(0, 1)
        uf.union(2, 3)
        uf.union(1, 3)
        self.assertEqual(len(set(uf.find(i) for i in range(4))), 1)

    def test_natural_sort(self):
        self.assertEqual(sorted(['12', '2', '1', 'Root'], key=core.natural_key), ['1', '2', '12', 'Root'])

    def test_cache_corruption(self):
        with tempfile.TemporaryDirectory(dir=str(TMP)) as folder:
            p = os.path.join(folder, 'mesh.pc2')
            with open(p, 'wb') as f:
                f.write(struct.pack('<12siiffi', b'POINTCACHE2\0', 1, 3, 1., 1., 2))
                f.write(struct.pack('<18f', *([0.]*18)))
            manifest = {'schema': 'BomberStudio.NoWind.VertexCache/1', 'completed': True,
                        'fps': 30, 'frames': 2, 'materials': {},
                        'meshes': [{'cache': 'mesh.pc2', 'vertexCount': 3, 'sha256': core.sha256(p),
                                    'submeshes': [{'triangles': [[0, 1, 2]], 'material': 'M'}],
                                    'uvSets': {'0': [[0, 0], [1, 0], [0, 1]]}}]}
            m = os.path.join(folder, 'physics-cache.json')
            core.atomic_json(m, manifest)
            self.assertEqual(core.validate_cache(m)['frames'], 2)
            with open(p, 'r+b') as f:
                f.seek(40)
                f.write(b'\xff')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                core.validate_cache(m)


if __name__ == '__main__':
    unittest.main()
