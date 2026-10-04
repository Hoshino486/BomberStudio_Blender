# SPDX-License-Identifier: GPL-3.0-or-later
"""User-initiated GitHub checks and bounded downloads; no bpy (Python 3.5+)."""
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_REPOSITORY = 'Hoshino486/BomberStudio_Blender'
TIMEOUT = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 180


class UpdateCancelled(RuntimeError):
    pass


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled('操作已取消；原插件未替换')


def _asset_url(url, repository, tag, name):
    parsed = urllib.parse.urlsplit(url)
    prefix = '/' + repository + '/releases/download/'
    path = urllib.parse.unquote(parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com'
            or parsed.query or parsed.fragment
            or path[:len(prefix)].lower() != prefix.lower()
            or path[len(prefix):] != tag + '/' + name):
        raise ValueError('Release 附件地址与配置的仓库、标签或文件名不一致')
    return url


def _trusted_download_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc.lower() in (
        'github.com', 'release-assets.githubusercontent.com',
        'objects.githubusercontent.com', 'github-releases.githubusercontent.com'))


class _GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_download_url(newurl):
            raise ValueError('更新下载跳转到了非 GitHub HTTPS 地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def select_asset(repository, tag, version, assets):
    """Prefer the exact Universal asset, never a source archive or arbitrary ZIP."""
    if version is None:
        raise ValueError('版本标签未识别，请在发布页核对')
    version_text = '.'.join(str(v) for v in version)
    names = ['BomberStudio_Blender_' + version_text + '_Universal.zip',
             'BomberStudio_Blender_' + version_text + '.zip']
    rows = assets if isinstance(assets, list) else []
    candidate = None
    for name in names:
        matched = [row for row in rows if isinstance(row, dict)
                   and str(row.get('name', '')).lower() == name.lower()]
        if len(matched) > 1:
            raise ValueError('存在多个同名更新附件，请维护者保留一个明确的安装包')
        if matched:
            candidate = matched[0]
            break
    if candidate is None:
        raise ValueError('Release 未附加对应版本的 BomberStudio ZIP；请查看发布页')
    size = candidate.get('size')
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise ValueError('Release ZIP 大小无效或超过 64 MiB')
    name = candidate['name']
    url = _asset_url(candidate.get('browser_download_url', ''), repository, tag, name)
    digest = candidate.get('digest') or ''
    sha = digest[7:].lower() if re.match(r'^sha256:[0-9a-fA-F]{64}$', digest) else ''
    checksum = None
    if not sha:
        checksums = [row for row in rows if isinstance(row, dict) and row.get('name') == 'SHA256SUMS.txt']
        if len(checksums) != 1:
            raise ValueError('附件缺少 GitHub SHA256；请在同一 Release 附加 SHA256SUMS.txt')
        item = checksums[0]
        checksum = {'name': item['name'], 'url': _asset_url(
            item.get('browser_download_url', ''), repository, tag, item['name'])}
    return {'id': candidate.get('id'), 'name': name, 'url': url, 'size': size,
            'sha256': sha, 'checksum': checksum}


def _download_open(url, current_version, opener):
    if not _trusted_download_url(url):
        raise ValueError('下载地址必须为 GitHub HTTPS')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'BomberStudio-Blender/' + '.'.join(str(v) for v in current_version),
        'Accept': 'application/octet-stream',
    })
    response = opener(request, timeout=TIMEOUT)
    if not _trusted_download_url(response.geturl()):
        response.close()
        raise ValueError('更新下载响应来自非 GitHub HTTPS 地址')
    return response


def download_release(record, temp_dir, current_version, progress=None, cancel=None, opener=None):
    """Download only after a click; write a UUID file and verify before publishing it."""
    _cancelled(cancel)
    repo = normalize_repository(record.get('repository', ''))
    tag = record['tag']
    asset = record.get('asset')
    if not asset:
        raise ValueError(record.get('asset_error') or '本次检查没有可安装的更新附件')
    url = _asset_url(asset['url'], repo, tag, asset['name'])
    if not 0 < asset['size'] <= MAX_DOWNLOAD_BYTES:
        raise ValueError('Release ZIP 大小无效或超过 64 MiB')
    opener = opener or urllib.request.build_opener(_GitHubRedirect()).open
    expected = asset.get('sha256', '')
    verified_by = 'github_asset_digest'
    if not expected:
        checksum = asset.get('checksum')
        if not checksum:
            raise ValueError('缺少可验证的 SHA256')
        checksum_url = _asset_url(checksum['url'], repo, tag, 'SHA256SUMS.txt')
        with _download_open(checksum_url, current_version, opener) as response:
            payload = response.read(96 * 1024 + 1)
        _cancelled(cancel)
        if len(payload) > 96 * 1024:
            raise ValueError('SHA256SUMS.txt 超过大小上限')
        matches = []
        for line in payload.decode('utf-8-sig').splitlines():
            found = re.match(r'^([0-9a-fA-F]{64})[ \t]+\*?(.+)$', line)
            if found and found.group(2).strip() == asset['name']:
                matches.append(found.group(1).lower())
        if len(matches) != 1:
            raise ValueError('SHA256SUMS.txt 中缺少唯一匹配的安装包校验值')
        expected = matches[0]
        verified_by = 'release_SHA256SUMS'
    if not re.match(r'^[0-9a-fA-F]{64}$', expected):
        raise ValueError('SHA256 格式无效')
    folder = os.path.join(os.path.realpath(temp_dir), 'update-downloads')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'release-' + uuid.uuid4().hex + '.zip')
    partial = target + '.part'
    started = time.monotonic()
    digest = hashlib.sha256()
    received = 0
    try:
        with _download_open(url, current_version, opener) as response, open(partial, 'xb') as stream:
            _cancelled(cancel)
            length = response.headers.get('Content-Length')
            if length and int(length) != asset['size']:
                raise ValueError('服务器 Content-Length 与 Release 附件大小不一致')
            while True:
                _cancelled(cancel)
                if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                    raise ValueError('更新下载超过 180 秒总时限，请重试')
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > asset['size'] or received > MAX_DOWNLOAD_BYTES:
                    raise ValueError('下载内容超过声明的安装包大小')
                stream.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(received, asset['size'])
            stream.flush()
            os.fsync(stream.fileno())
        _cancelled(cancel)
        if received != asset['size']:
            raise ValueError('下载中断或附件不完整：接收大小与 Release 不一致')
        actual = digest.hexdigest()
        if actual != expected.lower():
            raise ValueError('SHA256 校验失败；下载文件未用于替换插件')
        os.replace(partial, target)
        return {'path': target, 'sha256': actual, 'bytes': received, 'asset_name': asset['name'],
                'tag': tag, 'repository': repo, 'verified_by': verified_by}
    finally:
        # Only remove the unique file created by this call, never a directory.
        if os.path.isfile(partial):
            os.remove(partial)


def normalize_repository(value):
    """Accept owner/repo or a GitHub repository/subpage URL; empty uses the default."""
    text = (value or '').strip()
    if not text:
        return DEFAULT_REPOSITORY
    if text.lower().startswith(('github.com/', 'www.github.com/')):
        text = 'https://' + text
    if '://' in text:
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme.lower() not in ('http', 'https') or parsed.netloc.lower() not in ('github.com', 'www.github.com'):
            raise ValueError('请填写 github.com 仓库地址，或 owner/repo；不接受其他主机、端口或带账号的地址')
        parts = parsed.path.strip('/').split('/')
        if len(parts) < 2 or any(p in ('.', '..', '') for p in parts):
            raise ValueError('GitHub 地址需包含仓库所有者和仓库名，例如 ' + DEFAULT_REPOSITORY)
        text = '/'.join(parts[:2])
    else:
        text = text.rstrip('/')
    if text.endswith('.git'):
        text = text[:-4]
    parts = text.split('/')
    if (len(parts) != 2 or any(p in ('', '.', '..') for p in parts)
            or not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$', text)):
        raise ValueError('仓库格式不正确；填写 owner/repo 或完整 GitHub 仓库地址')
    return text


def releases_url(repository):
    return 'https://github.com/' + normalize_repository(repository) + '/releases/latest'


def tag_version(tag):
    # Includes v1.2.3 and the project's existing BomberStudio_Blender_1.2.3 tags.
    found = re.search(r'(?<![0-9.])([0-9]+)\.([0-9]+)\.([0-9]+)$', tag.strip())
    return tuple(int(v) for v in found.groups()) if found else None


def check_latest(repository, current_version, opener=None):
    """Read bounded metadata from GitHub. The optional opener supports offline tests."""
    repo = normalize_repository(repository)
    current = tuple(current_version)
    current_text = '.'.join(str(v) for v in current)
    url = 'https://api.github.com/repos/' + repo + '/releases/latest'
    request = urllib.request.Request(url, headers={
        'User-Agent': 'BomberStudio-Blender/' + current_text,
        'Accept': 'application/vnd.github+json',
    })
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            message = '仓库或正式 Release 未找到（HTTP 404）：' + repo + '；检查仓库名称，并发布非草稿、非预发布的 Release'
        elif exc.code == 429 or (exc.code == 403 and exc.headers and exc.headers.get('X-RateLimit-Remaining') == '0'):
            message = 'GitHub 查询频率受限（HTTP ' + str(exc.code) + '）；请稍后重试'
        elif exc.code == 403:
            message = 'GitHub 拒绝访问（HTTP 403）；检查仓库可见性或网络访问限制'
        else:
            message = 'GitHub 返回 HTTP ' + str(exc.code) + '；请稍后重试'
        raise ValueError(message) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ValueError('查询 GitHub 超时（15 秒）；检查网络后手动重试') from exc
    except urllib.error.URLError as exc:
        raise ValueError('连接 GitHub 失败：' + str(exc.reason)[:160]) from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('更新元数据超过 2 MiB 大小上限')
    try:
        data = json.loads(payload.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('GitHub 更新响应不是有效的 UTF-8 JSON') from exc
    if not isinstance(data, dict) or not isinstance(data.get('tag_name'), str) or not data['tag_name'].strip():
        raise ValueError('更新响应缺少版本标签 tag_name')
    tag = data['tag_name']
    remote = tag_version(tag)
    if remote is None:
        relation = 'UNKNOWN'
        status = '远端标签：' + tag[:80] + '（版本格式未识别，请查看发布页）'
    else:
        remote_text = '.'.join(str(v) for v in remote)
        if remote > current:
            relation = 'NEWER'
            status = '发现新版本：' + remote_text + '（当前 ' + current_text + '）'
        elif remote == current:
            relation = 'CURRENT'
            status = '已是最新正式版：' + current_text
        else:
            relation = 'OLDER'
            status = '本地 ' + current_text + ' 高于已发布 ' + remote_text + '，无需降级'
    try:
        asset = select_asset(repo, tag, remote, data.get('assets', []))
        asset_error = ''
    except (ValueError, TypeError) as exc:
        asset, asset_error = None, str(exc)
    return {
        'repository': repo, 'tag': tag, 'current_version': list(current),
        'remote_version': list(remote) if remote is not None else None,
        'relation': relation, 'status': status, 'release_url': releases_url(repo),
        'asset': asset, 'asset_error': asset_error,
    }
