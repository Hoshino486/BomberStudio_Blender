# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual GitHub Release checks, without bpy or automatic downloads (Python 3.5+)."""
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REPOSITORY = 'Hoshino486/BomberStudio_Blender'
TIMEOUT = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


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
    return {
        'repository': repo, 'tag': tag, 'current_version': list(current),
        'remote_version': list(remote) if remote is not None else None,
        'relation': relation, 'status': status, 'release_url': releases_url(repo),
    }
