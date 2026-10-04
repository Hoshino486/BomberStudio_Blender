# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread operators and transient UI state for explicitly requested online updates."""
import os
import time
import bpy
from . import compat as C, core, updates, update_install, update_jobs

CHECK_MAX_AGE = 30 * 60
_record = None
_active = None
_restart = False
_restart_message = ''


def is_busy():
    return _active is not None


def restart_required():
    return _restart


def progress_state():
    return _active._job.snapshot() if _active is not None else None


def record_for(repository):
    try:
        canonical = updates.normalize_repository(repository)
    except ValueError:
        return None
    return _record if _record and _record['repository'] == canonical else None


def online_access():
    if hasattr(bpy.app, 'online_access') and not bpy.app.online_access:
        raise ValueError('Blender 当前关闭了在线访问；请在首选项启用后手动重试')


def idle_required(allow_restart=False):
    if is_busy():
        raise ValueError('更新操作正在进行，请等待完成或先取消')
    if _restart and not allow_restart:
        raise ValueError('插件代码已替换，请先保存工作并重启 Blender')


def store_check(record, context):
    global _record
    cfg = C.settings(context)
    record = dict(record)
    record['checked'] = time.strftime('%Y-%m-%d %H:%M:%S')
    record['checked_epoch'] = time.time()
    record['repository_input'] = cfg.update_repository
    core.atomic_json(os.path.join(C.temp_dir(context), 'last-update-check.json'), record)
    _record = record
    if not cfg.update_repository.strip():
        cfg.update_repository = record['repository']
    cfg.update_status = record['status']
    cfg.update_last_check = record['checked']
    C.log('更新检查: ' + record['repository'] + ' / ' + record['tag'] + ' / ' + record['relation'], context)


def clear_check():
    global _record
    _record = None


def mark_restart(receipt, context, restored=False):
    global _restart, _restart_message, _record
    _restart = True
    _record = None
    version = receipt.get('before_version') if restored else receipt.get('installed_version')
    label = '.'.join(str(v) for v in version) if version else '旧版本'
    _restart_message = ('已还原 ' if restored else '已安装 ') + label + '；请保存工作并重启 Blender'
    C.settings(context).update_status = _restart_message
    C.log(_restart_message + '；备份: ' + receipt['backup'], context)


def install_record(context):
    idle_required()
    online_access()
    record = record_for(C.settings(context).update_repository)
    if not record or record.get('relation') != 'NEWER':
        raise ValueError('请先手动检查并找到比当前版本更新的正式 Release')
    if time.time() - record.get('checked_epoch', 0) > CHECK_MAX_AGE:
        raise ValueError('检查结果已超过 30 分钟，请重新检查')
    if not record.get('asset'):
        raise ValueError(record.get('asset_error') or '没有可安装的更新附件')
    if tuple(record['remote_version']) <= tuple(core.VERSION):
        raise ValueError('远端版本未高于当前版本，不执行在线降级或重复安装')
    return record


def can_install(context):
    try:
        install_record(context)
        return True
    except ValueError:
        return False


def backup_info(context):
    try:
        receipt = core.read_json(os.path.join(C.temp_dir(context), 'last-update.json'))
        target = os.path.realpath(os.path.dirname(__file__))
        previous = os.path.realpath(receipt['previous'])
        if (os.path.realpath(receipt['target']) == target
                and os.path.dirname(previous) == os.path.dirname(target)
                and os.path.basename(previous).startswith('.bomberstudio-previous-')
                and os.path.isfile(os.path.join(previous, '__init__.py'))
                and receipt.get('phase', 'installed') in ('installed', 'prepared')):
            return receipt
    except (OSError, ValueError, KeyError):
        pass
    return None


def _redraw(wm):
    for window in wm.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class BOMBER_OT_online_update(bpy.types.Operator):
    bl_idname = 'bomberstudio.online_update'
    bl_label = 'BomberStudio 在线更新'
    action = bpy.props.EnumProperty(items=[('CHECK', '检查更新', ''), ('INSTALL', '立即更新', '')])

    def invoke(self, context, event):
        try:
            idle_required()
            online_access()
            if self.action == 'INSTALL':
                self._confirmation = dict(install_record(context))
                return context.window_manager.invoke_props_dialog(self, width=460)
            return self.execute(context)
        except Exception as exc:
            return C.fail(self, exc, context)

    def draw(self, context):
        record = getattr(self, '_confirmation', None)
        if record is None:
            self.layout.label(text='手动检查 / 在线更新')
            return
        self.layout.label(text='下载并安装：' + record['tag'])
        self.layout.label(text='仓库：' + record['repository'])
        self.layout.label(text='附件：' + record['asset']['name'])
        self.layout.label(text='自动校验 SHA256 并保留旧版备份')
        self.layout.label(text='完成后请保存工作并重启；不会自动关闭 Blender')

    def execute(self, context):
        global _active
        try:
            idle_required()
            online_access()
            cfg = C.settings(context)
            record = install_record(context) if self.action == 'INSTALL' else None
            if record and getattr(self, '_confirmation', record) != record:
                raise ValueError('确认期间更新目标发生变化，请重新检查')
            self._repository = updates.normalize_repository(cfg.update_repository)
            self._temp = C.temp_dir(context)
            self._wm = context.window_manager
            self._timer = None
            self._job = update_jobs.UpdateJob(self.action, self._repository, core.VERSION, self._temp,
                                             bpy.app.version, __package__.startswith('bl_ext.'), record)
            self._record = record
            if self.action == 'CHECK':
                clear_check()
            _active = self
            cfg.update_status = '正在检查 GitHub Release…' if self.action == 'CHECK' else '正在下载并校验更新…'
            self._wm.progress_begin(0, 100)
            if bpy.app.background or context.window is None:
                self._job.run()
                return self._complete(context)
            self._timer = self._wm.event_timer_add(0.15, window=context.window)
            self._wm.modal_handler_add(self)
            self._job.start()
            _redraw(self._wm)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            if getattr(self, '_job', None) is not None:
                self._job.cancel()
                self._cleanup()
            return C.fail(self, exc, context)

    def modal(self, context, event):
        if event.type == 'ESC':
            self._job.cancel()
        if event.type == 'TIMER' and getattr(event, 'timer', self._timer) == self._timer:
            state = self._job.snapshot()
            self._wm.progress_update(state['progress'] * 100)
            _redraw(self._wm)
            if state['done']:
                return self._complete(context)
        return {'PASS_THROUGH'}

    def _cleanup(self):
        global _active
        if getattr(self, '_timer', None) is not None:
            self._wm.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_wm'):
            self._wm.progress_end()
            _redraw(self._wm)
        if _active is self:
            _active = None

    def _complete(self, context):
        state = self._job.snapshot()
        try:
            self._job.join()
            if state['cancelled'] or self._job.cancel_event.is_set():
                C.settings(context).update_status = '操作已取消；原插件未替换'
                C.log(C.settings(context).update_status, context)
                return {'CANCELLED'}
            if state['error']:
                raise ValueError(state['error'])
            online_access()
            if (updates.normalize_repository(C.settings(context).update_repository) != self._repository
                    or os.path.realpath(C.temp_dir(context)) != os.path.realpath(self._temp)):
                raise ValueError('操作期间仓库或临时目录已改变，请重新检查；原插件未替换')
            if self.action == 'CHECK':
                store_check(state['result'], context)
                self.report({'INFO'}, C.settings(context).update_status)
            else:
                data = state['result']['download']
                C.settings(context).update_status = '校验通过，正在备份并安装…'
                receipt = update_install.install_zip(
                    data['path'], os.path.dirname(__file__), self._temp, bpy.app.version,
                    expected_version=self._record['remote_version'], expected_sha256=data['sha256'],
                    extension=__package__.startswith('bl_ext.'), source=data)
                mark_restart(receipt, context)
                self.report({'INFO'}, C.settings(context).update_status)
            return {'FINISHED'}
        except Exception as exc:
            C.settings(context).update_status = ('检查失败: ' if self.action == 'CHECK' else '更新失败: ') + str(exc)[:200]
            return C.fail(self, exc, context)
        finally:
            self._cleanup()

    def cancel(self, context):
        job = getattr(self, '_job', None)
        if job is not None:
            job.cancel()
            job.join(updates.TIMEOUT + 2)
        self._cleanup()


class BOMBER_OT_cancel_update(bpy.types.Operator):
    bl_idname = 'bomberstudio.cancel_online_update'
    bl_label = '取消当前下载 / 检查'

    def execute(self, context):
        if _active is not None:
            _active._job.cancel()
            C.settings(context).update_status = '正在取消；等待当前网络读取结束…'
        return {'FINISHED'}


def shutdown():
    if _active is not None:
        _active.cancel(None)


CLASSES = (BOMBER_OT_online_update, BOMBER_OT_cancel_update)
