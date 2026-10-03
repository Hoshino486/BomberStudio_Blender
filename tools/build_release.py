"""Build one reproducible ZIP for legacy Add-on and native Extension installation."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'bomberstudio_blender'
TMP = ROOT / '临时文件夹'
spec = importlib.util.spec_from_file_location('build_core', str(ADDON / 'core.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
VERSION = '.'.join(str(v) for v in core.VERSION)


def publish(path, payload):
    TMP.mkdir(exist_ok=True)
    if path.exists():
        folder = TMP / ('release-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        folder.mkdir()
        shutil.copy2(path, folder / path.name)
    part = TMP / ('release-' + uuid.uuid4().hex + '.pending')
    with part.open('wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(part), str(path))


def members():
    files = {p.name: p for p in ADDON.glob('*.py')}
    files['blender_manifest.toml'] = ADDON / 'blender_manifest.toml'
    for name in ('README.md', 'CHANGELOG.md', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.md'):
        files[name] = ROOT / name
    # Verification reports stay outside the ZIP to avoid a self-hash cycle.
    for name in ('使用指南.md', 'ASCII_FBX.md'):
        files['docs/' + name] = ROOT / 'docs' / name
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(str(path))
        if name.endswith('.py'):
            ast.parse(path.read_text(encoding='utf8'), filename=name)
    return files


def build():
    TMP.mkdir(exist_ok=True)
    contents = members()
    leaf = 'BomberStudio_Blender_' + VERSION + '_Universal.zip'
    part = TMP / (leaf + '.' + uuid.uuid4().hex[:8] + '.pending')
    with zipfile.ZipFile(str(part), 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(contents.items()):
            entry = zipfile.ZipInfo('bomberstudio_blender/' + name, date_time=(2026, 10, 3, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    with zipfile.ZipFile(part) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(contents)
        assert not any('__pycache__' in n or n.endswith(('.pyc', '.zip')) for n in archive.namelist())
        for required in ('__init__.py', 'core.py', 'blender_manifest.toml'):
            assert 'bomberstudio_blender/' + required in archive.namelist()
    payload = part.read_bytes()
    package = {'file': leaf, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
               'kind': 'Universal', 'layout': 'bomberstudio_blender/'}
    publish(ROOT / leaf, payload)
    publish(ROOT / 'SHA256SUMS.txt', (package['sha256'] + '  ' + leaf + '\n').encode('utf8'))
    publish(ROOT / 'release-manifest.json', json.dumps({
        'addon': 'BomberStudio Blender Bridge', 'version': VERSION, 'date': '2026-10-03',
        'packages': [package],
        'install_modes': {'2.79-4.1': 'Legacy Add-on', '4.2+': 'Native Extension'},
        'compatibility_note': 'See docs/兼容验证.md for actual tested versions and platform.',
    }, ensure_ascii=False, indent=2).encode('utf8'))
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return package


if __name__ == '__main__':
    build()
