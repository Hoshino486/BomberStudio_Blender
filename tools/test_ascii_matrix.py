"""Isolated ASCII import regression across installed portable Blender versions."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from run_matrix import RUNTIMES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '临时文件夹' / 'ascii-fbx-fix-20261003'


def run(entry):
    version, exe = entry
    folder = OUT / ('blender-' + version)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(ROOT/'临时文件夹'), TMP=str(ROOT/'临时文件夹'),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    if version == '2.79b':
        env['OCIO'] = '2.79/datafiles/colormanagement/config.ocio'
    start = time.time()
    command = [str(exe), '--background', '--factory-startup', '--threads', '2',
               '--python-exit-code', '1', '--python', str(ROOT/'tests/blender_ascii.py'), '--', str(folder)]
    log = folder / 'console.log'
    print('RUN ASCII', version, flush=True)
    with log.open('w', encoding='utf8') as stream:
        try:
            p = subprocess.run(command, cwd=str(exe.parent), env=env, stdout=stream, stderr=subprocess.STDOUT,
                               timeout=240, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = 'TIMEOUT'
    report = {'version': version, 'returncode': code, 'seconds': round(time.time()-start,2), 'log': str(log)}
    result = folder/'results.json'
    if result.exists() and result.stat().st_mtime >= start:
        data = json.loads(result.read_text(encoding='utf8'))
        report.update(passed=data['passed'], failed=data['failed'])
        report['failures'] = [row for row in data['tests'] if row['status'] != 'PASS']
    print('DONE ASCII', json.dumps(report,ensure_ascii=False),flush=True)
    return report


if __name__ == '__main__':
    entries = [e for e in RUNTIMES if not sys.argv[1:] or e[0] in sys.argv[1:]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(run,entries))
    suffix = '-selected' if sys.argv[1:] else ''
    (OUT/('ascii-matrix-results'+suffix+'.json')).write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')
    sys.exit(1 if any(r.get('returncode') != 0 or r.get('failed',1) for r in reports) else 0)
