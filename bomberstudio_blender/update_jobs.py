# SPDX-License-Identifier: GPL-3.0-or-later
"""Network worker: only stdlib and pure helpers; Blender RNA stays on the main thread."""
import json
import threading
from . import updates, update_install


class UpdateJob:
    def __init__(self, action, repository, current_version, temp_dir,
                 blender_version, extension=False, record=None):
        self.action = action
        self.repository = repository
        self.current_version = tuple(current_version)
        self.temp_dir = temp_dir
        self.blender_version = tuple(blender_version)
        self.extension = extension
        self.record = json.loads(json.dumps(record)) if record else None
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self.thread = None
        self._state = {'phase': 'CHECKING' if action == 'CHECK' else 'DOWNLOADING',
                       'progress': 0.0, 'received': 0, 'total': 0, 'done': False,
                       'result': None, 'error': '', 'cancelled': False}

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _progress(self, received, total):
        self._set(progress=received / float(total) * 0.9, received=received, total=total)

    def run(self):
        try:
            updates._cancelled(self.cancel_event)
            if self.action == 'CHECK':
                result = updates.check_latest(self.repository, self.current_version)
            else:
                downloaded = updates.download_release(self.record, self.temp_dir, self.current_version,
                                                       progress=self._progress, cancel=self.cancel_event)
                self._set(phase='VALIDATING', progress=0.95)
                content = update_install.validate_addon_zip(
                    downloaded['path'], self.blender_version,
                    expected_version=self.record['remote_version'], extension=self.extension)
                result = {'download': downloaded, 'metadata': update_install.package_metadata(
                    content, self.blender_version, self.record['remote_version'], self.extension)}
            updates._cancelled(self.cancel_event)
            self._set(done=True, result=result, progress=1.0)
        except updates.UpdateCancelled as exc:
            self._set(done=True, cancelled=True, error=str(exc))
        except Exception as exc:
            self._set(done=True, error=str(exc) or type(exc).__name__)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='BomberStudioUpdateIO')
        self.thread.daemon = True
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def join(self, timeout=None):
        if self.thread is not None:
            self.thread.join(timeout)
