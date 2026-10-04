# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated, rollback-capable file transactions. Never imports or executes a ZIP."""
import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from . import core


def _version(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3 or any(type(v) is not int or v < 0 for v in value):
        raise ValueError('插件版本应为三个非负整数')
    return tuple(value)


def _literal(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('安装包缺少版本标识：' + name)


def _toml_string(source, key):
    rows = re.findall(r'(?m)^' + re.escape(key) + r'\s*=\s*"([^"]+)"\s*(?:#.*)?$', source)
    if len(rows) != 1:
        raise ValueError('扩展清单字段缺失或重复：' + key)
    return rows[0]


def package_metadata(content, blender_version, expected_version=None, extension=False):
    info = _literal(content['__init__.py'].decode('utf-8-sig'), 'bl_info')
    if not isinstance(info, dict) or 'BomberStudio' not in info.get('name', ''):
        raise ValueError('安装包不是 BomberStudio Blender 插件')
    version = _version(info.get('version'))
    if version != _version(_literal(content['core.py'].decode('utf-8-sig'), 'VERSION')):
        raise ValueError('安装包的 __init__.py 与 core.py 版本不一致')
    if tuple(blender_version) < _version(info.get('blender', (2, 79, 0))):
        raise ValueError('更新包要求更高版本的 Blender')
    manifest = content.get('blender_manifest.toml')
    if manifest:
        manifest = manifest.decode('utf-8-sig')
        if _toml_string(manifest, 'id') != 'bomberstudio_blender':
            raise ValueError('扩展清单插件 ID 不匹配')
        manifest_version = tuple(int(v) for v in _toml_string(manifest, 'version').split('.'))
        if version != _version(manifest_version):
            raise ValueError('安装包的扩展清单版本与源码不一致')
        if extension:
            minimum = tuple(int(v) for v in _toml_string(manifest, 'blender_version_min').split('.'))
            if tuple(blender_version) < _version(minimum):
                raise ValueError('更新包要求更高版本的 Blender 扩展机制')
            if re.search(r'(?m)^blender_version_max\s*=', manifest):
                maximum = tuple(int(v) for v in _toml_string(manifest, 'blender_version_max').split('.'))
                if tuple(blender_version) >= _version(maximum):
                    raise ValueError('更新包尚未声明适配当前 Blender 版本')
    elif extension or expected_version is not None:
        raise ValueError('在线更新包缺少 blender_manifest.toml，请使用统一安装包')
    if expected_version is not None and version != _version(expected_version):
        raise ValueError('Release 标签与 ZIP 内部版本不一致；请重新打包，不要只重命名 ZIP')
    return {'version': list(version), 'name': info['name']}


def validate_addon_zip(filename, blender_version, expected_version=None, extension=False):
    with zipfile.ZipFile(filename) as archive:
        infos = archive.infolist()
        if len(infos) > 4096 or sum(i.file_size for i in infos) > 64 * 1024 * 1024:
            raise ValueError('插件 ZIP 文件数或解压大小超过上限')
        names = [i.filename.replace('\\', '/') for i in infos]
        root = 'bomberstudio_blender/' if 'bomberstudio_blender/__init__.py' in names else ''
        if root + '__init__.py' not in names or root + 'core.py' not in names:
            raise ValueError('ZIP 不是 BomberStudio 插件安装包；不要选择 GitHub Source code ZIP')
        content, seen = {}, set()
        for info, name in zip(infos, names):
            if name.startswith('/') or re.match(r'^[A-Za-z]:', name):
                raise ValueError('ZIP 含绝对路径')
            parts = name.rstrip('/').split('/')
            if any(p in ('', '.', '..') or p.endswith((' ', '.')) or ':' in p for p in parts):
                raise ValueError('ZIP 含越界、空白或不兼容路径')
            if any(re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p) for p in parts):
                raise ValueError('ZIP 含 Windows 保留文件名')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP 含符号链接')
            if info.flag_bits & 1:
                raise ValueError('不接受加密 ZIP')
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('插件单文件超过 8 MiB')
            if root and not name.startswith(root):
                raise ValueError('ZIP 含无关顶层文件')
            if name.endswith('/'):
                continue
            rel = name[len(root):]
            folded = rel.lower()
            if folded in seen:
                raise ValueError('ZIP 存在重复或大小写冲突的文件名')
            seen.add(folded)
            if folded.endswith(('.exe', '.dll', '.pyd', '.bat', '.cmd', '.ps1')):
                raise ValueError('更新包应为纯 Python，不接受可执行附件')
            if '__pycache__' in rel.split('/') or folded.endswith(('.pyc', '.pyo')):
                continue  # Never install stale or foreign-runtime bytecode.
            payload = archive.read(info)
            if rel.endswith('.py'):
                deferred = rel.startswith('vendor/io_scene_valvesource/') and tuple(blender_version) < (4, 1, 0)
                if not deferred:
                    ast.parse(payload.decode('utf-8-sig'), filename=rel)
            content[rel] = payload
        for name in seen:
            prefixes = name.split('/')[:-1]
            if any('/'.join(prefixes[:i]) in seen for i in range(1, len(prefixes) + 1)):
                raise ValueError('ZIP 文件与目录路径冲突')
        package_metadata(content, blender_version, expected_version, extension)
        return content


def _paths(target, temp_dir):
    target, temp_dir = os.path.realpath(target), os.path.realpath(temp_dir)
    try:
        nested = os.path.commonpath([target, temp_dir]) == target
    except ValueError:  # A user-selected temporary directory may be on another drive.
        nested = False
    if nested:
        raise ValueError('临时 / 备份目录应位于插件安装目录之外')
    if not os.path.isfile(os.path.join(target, '__init__.py')):
        raise ValueError('目标插件安装目录不完整')
    os.makedirs(temp_dir, exist_ok=True)
    return target, temp_dir


@contextlib.contextmanager
def _lock(target):
    name = '.bomberstudio-update-' + hashlib.sha256(target.encode('utf8')).hexdigest()[:16] + '.lock'
    path = os.path.join(os.path.dirname(target), name)
    token = uuid.uuid4().hex
    try:
        with open(path, 'x', encoding='utf8') as stream:
            stream.write(token)
    except FileExistsError:
        raise ValueError('另一更新事务正在运行；若上次进程异常退出，请先检查锁文件：' + path)
    try:
        yield
    finally:
        with open(path, encoding='utf8') as stream:
            owned = stream.read() == token
        if owned:
            os.remove(path)


def _old_receipt(path):
    if os.path.isfile(path):
        with open(path, 'rb') as stream:
            return stream.read()
    return None


def _restore_receipt(path, payload):
    if payload is None:
        if os.path.isfile(path):
            os.remove(path)
    else:
        core.atomic_bytes(path, payload, backup=False)


def install_zip(filename, target, temp_dir, blender_version, expected_version=None,
                expected_sha256=None, extension=False, source=None):
    content = validate_addon_zip(filename, blender_version, expected_version, extension)
    digest = core.sha256(filename)
    if expected_sha256 and digest != expected_sha256.lower():
        raise ValueError('下载包在安装前发生变化，SHA256 校验失败')
    metadata = package_metadata(content, blender_version, expected_version, extension)
    target, temp_dir = _paths(target, temp_dir)
    parent = os.path.dirname(target)
    with _lock(target):
        nonce = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
        stage = os.path.join(temp_dir, 'update-stage-' + nonce)
        incoming = os.path.join(parent, '.bomberstudio-incoming-' + nonce)
        previous = os.path.join(parent, '.bomberstudio-previous-' + nonce)
        backup = os.path.join(temp_dir, 'addon-backup-' + nonce)
        failed = os.path.join(parent, '.bomberstudio-failed-' + nonce)
        receipt_path = os.path.join(temp_dir, 'last-update.json')
        old_receipt = _old_receipt(receipt_path)
        os.makedirs(stage)
        for name, payload in content.items():
            core.atomic_bytes(core.local_path(stage, name), payload, backup=False)
        shutil.copytree(target, backup, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        shutil.copytree(stage, incoming)
        before_version = None
        try:
            with open(os.path.join(target, 'core.py'), encoding='utf-8-sig') as stream:
                before_version = list(_version(_literal(stream.read(), 'VERSION')))
        except (OSError, ValueError, SyntaxError):
            pass
        receipt = {'target': target, 'backup': backup, 'previous': previous, 'package': filename,
                   'sha256': digest, 'restart_required': True, 'phase': 'prepared',
                   'before_version': before_version, 'installed_version': metadata['version'],
                   'installed_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': source}
        core.atomic_json(os.path.join(temp_dir, 'update-journal-' + nonce + '.json'), receipt, backup=False)
        moved_old = False
        moved_new = False
        try:
            # Persist recovery paths before replacing any installed file.
            core.atomic_json(receipt_path, receipt, backup=False)
            os.replace(target, previous)
            moved_old = True
            os.replace(incoming, target)
            moved_new = True
            receipt['phase'] = 'installed'
            core.atomic_json(receipt_path, receipt, backup=False)
        except Exception:
            if moved_new:
                os.replace(target, failed)
            if moved_old:
                os.replace(previous, target)
            _restore_receipt(receipt_path, old_receipt)
            raise
        return receipt


def restore(target, temp_dir):
    target, temp_dir = _paths(target, temp_dir)
    with _lock(target):
        path = os.path.join(temp_dir, 'last-update.json')
        receipt = core.read_json(path)
        if os.path.realpath(receipt['target']) != target:
            raise ValueError('备份属于另一个插件安装目录')
        if receipt.get('phase', 'installed') not in ('installed', 'prepared'):
            raise ValueError('该备份已执行回滚，请先重启 Blender')
        previous = os.path.realpath(receipt['previous'])
        if (os.path.dirname(previous) != os.path.dirname(target)
                or not os.path.basename(previous).startswith('.bomberstudio-previous-')
                or not os.path.isfile(os.path.join(previous, '__init__.py'))):
            raise ValueError('旧版目录或回滚路径校验失败')
        displaced = os.path.join(os.path.dirname(target), '.bomberstudio-restored-from-' + uuid.uuid4().hex)
        original = _old_receipt(path)
        moved = False
        os.replace(target, displaced)
        try:
            os.replace(previous, target)
            moved = True
            receipt['phase'] = 'rolled_back'
            receipt['rollback_displaced'] = displaced
            receipt['restart_required'] = True
            core.atomic_json(path, receipt, backup=False)
        except Exception:
            if moved:
                os.replace(target, previous)
            os.replace(displaced, target)
            _restore_receipt(path, original)
            raise
        return receipt
