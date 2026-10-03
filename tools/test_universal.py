"""Install the exact same release ZIP in isolated Blender profiles (eight routes)."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_matrix import ROOT, TMP, RUNTIMES


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(entry, package, archive, output_root):
    version, exe, mode = entry
    out = output_root / ('install-' + version + '-' + mode.lower())
    out.mkdir(parents=True, exist_ok=True)
    for sub in ('scripts', 'config', 'datafiles', 'extensions'):
        (out / sub).mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(TMP), TMP=str(TMP),
               BLENDER_USER_SCRIPTS=str(out / 'scripts'), BLENDER_USER_CONFIG=str(out / 'config'),
               BLENDER_USER_DATAFILES=str(out / 'datafiles'), BLENDER_USER_EXTENSIONS=str(out / 'extensions'))
    if version == '2.79b':
        env['OCIO'] = '2.79/datafiles/colormanagement/config.ocio'
    command = [str(exe), '--background', '--factory-startup', '--threads', '2', '--python-exit-code', '1',
               '--python', str(ROOT / 'tests/universal_install.py'),
               '--', str(archive), mode, str(out)]
    row = {'version': version, 'mode': mode, 'archive_sha256': package['sha256']}
    started = time.time()
    log_path = out / 'install.log'
    try:
        if not exe.is_file():
            raise FileNotFoundError(str(exe))
        with log_path.open('w', encoding='utf8') as log:
            result = subprocess.run(command, cwd=str(exe.parent), env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=150,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        row['returncode'] = result.returncode
        path = out / 'installed.json'
        if path.is_file() and path.stat().st_mtime >= started:
            row['result'] = json.loads(path.read_text(encoding='utf8'))
        row['passed'] = result.returncode == 0 and row.get('result', {}).get('passed', False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        row.update(returncode=-1, passed=False, error=str(exc))
    row.update(seconds=round(time.time() - started, 3), log=str(log_path))
    if not row['passed'] and log_path.exists():
        row['error_tail'] = log_path.read_text(encoding='utf8', errors='replace')[-5000:]
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def main():
    release = json.loads((ROOT / 'release-manifest.json').read_text(encoding='utf8'))
    package = next(p for p in release['packages'] if p['kind'] == 'Universal')
    archive = ROOT / package['file']
    assert sha(archive) == package['sha256']
    output_root = TMP / ('universal-tests-' + release['version'])
    entries = [(v, exe, 'Legacy') for v, exe in RUNTIMES]
    entries += [(v, exe, 'Extension') for v, exe in RUNTIMES if v in ('4.5.5', '5.2.2')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda e: run(e, package, archive, output_root), entries))
    output = {'version': release['version'], 'archive': package['file'],
              'archive_sha256': package['sha256'], 'passed': sum(r['passed'] for r in rows),
              'failed': sum(not r['passed'] for r in rows), 'tests': rows}
    path = TMP / 'universal-install-results.json'
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf8')
    assert sha(archive) == package['sha256']
    return 0 if output['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
