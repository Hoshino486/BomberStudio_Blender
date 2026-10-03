"""Run isolated portable Blender processes; never modify global add-on preferences."""
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = ROOT / '临时文件夹'
RUNTIMES = [
    ('2.79b', TMP / 'blender-runtimes/blender-2.79b-windows64/blender-2.79b-windows64/blender.exe'),
    ('3.2.2', pathlib.Path('E:/blender-3.2.2-windows-x64/blender.exe')),
    ('4.0.2', pathlib.Path('E:/blender-4.0.2-windows-x64/blender.exe')),
    ('4.1.1', pathlib.Path('E:/blender-4.1.1-windows-x64/blender.exe')),
    ('4.5.5', pathlib.Path('E:/blender-4.5.5-windows-x64/blender.exe')),
    ('5.2.2', TMP / 'blender-runtimes/blender-5.2.2-windows-x64/blender-5.2.2-windows-x64/blender.exe'),
]


def run(entry):
    version, exe = entry
    out = TMP / ('test-' + version)
    out.mkdir(exist_ok=True)
    log = TMP / ('test-' + version + '-matrix.log')
    if not exe.exists():
        return {'version': version, 'status': 'MISSING_RUNTIME'}
    env = dict(os.environ, TEMP=str(TMP), TMP=str(TMP), PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    if version == '2.79b':
        env['OCIO'] = '2.79/datafiles/colormanagement/config.ocio'
    command = [str(exe), '--background', '--factory-startup', '--threads', '2', '--python-exit-code', '1',
               '--python', str(ROOT/'tests/blender_smoke.py'), '--', str(out)]
    start = time.time()
    print('RUN', version, flush=True)
    with log.open('w', encoding='utf8') as stream:
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=str(exe.parent),
                                    timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 'TIMEOUT'
    row = {'version': version, 'returncode': code, 'seconds': round(time.time()-start, 2), 'log': str(log)}
    path = out/'results.json'
    if path.exists() and path.stat().st_mtime >= start:
        data = json.loads(path.read_text(encoding='utf8'))
        row.update(passed=data['passed'], failed=data['failed'])
        row['failures'] = [{'name': r['name'], 'error': r.get('error')} for r in data['tests'] if r['status'] != 'PASS']
    print('DONE', json.dumps(row, ensure_ascii=False), flush=True)
    return row


if __name__ == '__main__':
    entries = [e for e in RUNTIMES if not sys.argv[1:] or e[0] in sys.argv[1:]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, entries))
    (TMP/'matrix-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
    sys.exit(1 if any(r.get('returncode') != 0 for r in results) else 0)
