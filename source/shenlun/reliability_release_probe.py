"""Synthetic, provider-free 0.9.4 acceptance shared by source and frozen smoke."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import os
import tempfile
import time


class _Interrupted(BaseException):
    """Simulate process exit after the real score transaction has committed."""


class _OfflineClient:
    def __init__(self, *, interrupt=False, revision_only=False):
        self.interrupt = interrupt
        self.revision_only = revision_only
        self.systems = []
        self.spent_tokens = 0

    def complete(self, system, payload, cancel=None):
        from .service import ASSESSMENT_SYSTEM, REVISION_SYSTEM
        self.systems.append(system)
        if system == ASSESSMENT_SYSTEM and not self.revision_only:
            data = {'summary':'合成离线验收，不验证评分效果', 'task_analysis':'概括便民做法',
                'dimensions':[{'id':row['id'], 'score':80, 'reason':'合成固定值'}
                              for row in payload['submission']['rubric']],
                'annotations':[], 'material_points':[{'point':'开通电话预约',
                    'material_refs':[{'id':'M1', 'quote':'开通电话预约'}],
                    'coverage':'covered', 'answer_quote':'开通电话预约'}], 'strengths':[],
                'next_action':'核对材料', 'uncertainties':[]}
        elif system == REVISION_SYSTEM:
            if self.interrupt:
                raise _Interrupted()
            data = {'minimal':'开通电话预约。', 'model':'开通电话预约，保留人工窗口。'}
        else:
            raise RuntimeError('离线验收收到非预期请求，禁止重新评分。')
        return {'data':data, 'usage':{'input_tokens':10, 'output_tokens':10, 'total_tokens':20},
                'model':'offline-reliability-fixture'}


@contextmanager
def _provider_guard():
    """Fail closed and count any accidental attempt to use the real provider."""
    from .model import ModelClient
    attempts = []
    original = ModelClient.complete
    def blocked(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('离线发布验收禁止外部模型请求。')
    ModelClient.complete = blocked
    try:
        yield attempts
    finally:
        ModelClient.complete = original


def _drain(app, predicate, timeout=10):
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()
    if not predicate():
        raise RuntimeError('离线验收等待后台备份超时。')


def _answer_modal(app, action, title, answer):
    """Exercise the real default-cancel confirmation, without replacing its API."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    observed = []
    invalid = []
    timer = QTimer()
    timer.setInterval(10)
    def respond():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            observed.append(dialog.windowTitle())
            timer.stop()
            if dialog.defaultButton() is not dialog.button(QMessageBox.StandardButton.No):
                invalid.append('confirmation must default to cancel')
                dialog.reject()
            elif dialog.windowTitle() == title:
                dialog.button(answer).click()
            else:
                dialog.reject()
    timer.timeout.connect(respond)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    def timeout():
        invalid.append('confirmation timed out')
        modal = app.activeModalWidget()
        if modal is not None:
            modal.reject()
    watchdog.timeout.connect(timeout)
    watchdog.start(10000)
    timer.start()
    try:
        result = action()
    finally:
        timer.stop()
        watchdog.stop()
    if observed != [title] or invalid:
        raise RuntimeError('验收未经过预期确认窗口：'+str(observed))
    return result


def probe_reliability(directory, screenshot=None):
    """Use only a fresh child of the explicit smoke parent and tiny synthetic data."""
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox
    from .auto_backup import AutoBackupManager
    from .evaluation import canonical_hash
    from .service import AppService, ASSESSMENT_SYSTEM, PROMPT_VERSION, REVISION_SYSTEM
    from .storage import Store
    from .ui import MainWindow

    parent = Path(directory).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='reliability-probe-', dir=parent))
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('离线发布验收需要实际 QApplication。')
    windows = []
    dialogs = []
    checks = {}
    def capture(target, suffix):
        if screenshot is None:
            return
        path = Path(screenshot).with_name(Path(screenshot).stem+'-reliability-'+suffix+'.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        app.processEvents()
        if not target.grab().save(str(path)):
            raise OSError('无法保存可靠性验收截图。')
    def open_window(store):
        window = MainWindow(store)
        windows.append(window)
        window.show()
        app.processEvents()
        _drain(app, lambda: window._backup_job is None)
        window.backup_timer.stop()
        return window
    def close_window(window):
        window.wait_for_backup()
        window.close()
        app.processEvents()
    def dialog_screenshots(window, dialog, name, normal, minimum):
        dialogs.append(dialog)
        for suffix, size, dialog_size in (('normal',(1360,900),normal), ('minimum',(1050,720),minimum)):
            window.resize(*size)
            dialog.resize(*dialog_size)
            dialog.show()
            capture(dialog, name+'-'+suffix)
        dialog.reject()

    with _provider_guard() as external_attempts:
        try:
            store = Store(root/'synthetic.db')
            manager = AutoBackupManager(store)
            # Disable scheduled work until the daily-copy exercise below. No
            # ordinary user database or seed catalog is opened by this probe.
            manager.configure(enabled=False, retention=1)
            window = open_window(store)
            window.question_edit.setPlainText('【合成验收】概括便民做法')
            window.materials_edit.setPlainText('社区开通电话预约。\n服务站保留人工窗口。')
            window.answer_edit.setPlainText('开通电话预约。')
            window.assessment_tabs.setCurrentIndex(1)
            template = window.submission()
            checks['builtin_identity'] = (template['rubric_template']['kind'] == 'builtin'
                and window.rubric_edit.toPlainText() == '' and window.rubric_table.rowCount() > 0)
            for name, size in (('normal',(1360,900)), ('minimum',(1050,720))):
                window.resize(*size)
                capture(window, 'rubric-'+name)
            window.copy_rubric_template()
            row_id = window.submission()['rubric'][0]['id']
            window.rubric_table.item(0,0).setText('合成自定义维度')
            custom = window.submission()
            checks['table_affects_submission'] = (custom['rubric_template']['kind'] == 'custom'
                and custom['rubric'][0]['name'] == '合成自定义维度' and custom['rubric'][0]['id'] == row_id)
            raw = window.rubric_edit.toPlainText()
            advanced = window.build_rubric_json_dialog()
            dialogs.append(advanced)
            advanced.json_edit.setPlainText('unfinished synthetic JSON')
            advanced.reject()
            checks['advanced_cancel_preserved'] = window.rubric_edit.toPlainText() == raw
            invalid = '  [{"id":\n'
            window.rubric_edit.setPlainText(invalid)
            if not window.flush_workspace():
                raise RuntimeError('无法保存合成量表草稿。')
            close_window(window)

            store = Store(root/'synthetic.db')
            window = open_window(store)
            checks['invalid_draft_restart'] = (window.rubric_edit.toPlainText() == invalid
                and window.rubric_table.rowCount() == 0)
            window.rubric_edit.setPlainText(raw)
            svc = AppService(store, {'base_url':'http://127.0.0.1:1/v1', 'model':'offline-reliability-fixture'}, '')
            submission, payload, sources, methods, points = svc.prepare_grade(window.submission())
            context = {'payload':payload, 'sources':sources, 'method_snapshot':methods,
                'reference_points_snapshot':points, 'prompt_version':PROMPT_VERSION,
                'system_hash':canonical_hash(ASSESSMENT_SYSTEM), 'method_choice_snapshot':svc._last_method_choice}
            task_id = store.create_assessment_task(submission, context)
            first = _OfflineClient(interrupt=True)
            svc.client = first
            try:
                svc.run_assessment_task(task_id)
            except _Interrupted:
                pass
            task = store.get_assessment_task(task_id)
            before = store.get_review(task['review_id']) if task['review_id'] else None
            if before is None:
                raise RuntimeError('评分事务未在中断前保存。')
            assessment_hash = canonical_hash(before['assessment'])
            question_id = store.question_for_review(before['id'])
            plan = store.save_review_plan(question_id, {'status':'复习中', 'due_date':'2026-10-21',
                'tags':['合成验收']}, expected_version=0)
            close_window(window)

            store = Store(root/'synthetic.db')
            window = open_window(store)
            task = store.get_assessment_task(task_id)
            checks['score_persisted_restart'] = (task['review_id'] == before['id']
                and task['state'] == 'revising' and task['recoverable'] and not task['active'])
            recovery = window.build_assessment_recovery_dialog()
            checks['recovery_lists_task'] = recovery.task_list.count() == 1
            dialog_screenshots(window, recovery, 'recovery', (790,580), (650,480))
            declined = _answer_modal(app, lambda: window.resume_assessment_task(task_id),
                '继续批改任务', QMessageBox.StandardButton.No)
            checks['resume_cancel_no_work'] = (declined is False and not window.workers
                and store.get_assessment_task(task_id)['state'] == 'revising')
            resumed = AppService(store, svc.config, '')
            second = _OfflineClient(revision_only=True)
            resumed.client = second
            review = resumed.run_assessment_task(task_id)
            calls_before_reopen = len(second.systems)
            reopened = resumed.run_assessment_task(task_id)
            checks['revisions_only'] = (first.systems == [ASSESSMENT_SYSTEM, REVISION_SYSTEM]
                and second.systems == [REVISION_SYSTEM]
                and store.get_assessment_task(task_id)['state'] == 'completed'
                and review['revisions']['minimal'] == '开通电话预约。')
            checks['original_grade_unchanged'] = (canonical_hash(review['assessment']) == assessment_hash
                and review['submission'] == before['submission'] and len(store.list_reviews()) == 1)
            checks['completed_reopens_without_call'] = (reopened == review and len(second.systems) == calls_before_reopen)

            manager = window.backup_manager
            manager.configure(enabled=True, retention=1)
            backup_dir = Path(manager.settings()['directory'])
            backup_dir.mkdir(parents=True, exist_ok=True)
            foreign = backup_dir/'unregistered.db'
            foreign.write_bytes(b'synthetic foreign file')
            untrusted = root/'untrusted-from-database'
            store.set_setting('auto_backup_directory', str(untrusted))
            manual = store.backup_to(backup_dir/'manual.db')
            safety = store.backup_to(backup_dir/'safety.db', backup_kind='safety')
            first_daily = manager.run_daily(date(2026,10,19))
            old_daily = [row for row in manager.records() if row['kind'] == 'daily']
            repeated = manager.run_daily(date(2026,10,19))
            checks['daily_dedupe'] = (first_daily == 'created' and repeated == 'current'
                and len([row for row in manager.records() if row['kind'] == 'daily']) == 1)
            window._backup_today = lambda: date(2026,10,20)
            window.check_auto_backup(force=True)
            _drain(app, lambda: window._backup_job is None)
            records = manager.records()
            daily = [row for row in records if row['kind'] == 'daily']
            checks['registered_retention'] = (len(daily) == (1 if os.name == 'nt' else 2)
                and any(row['date'] == '2026-10-20' for row in daily)
                and (not Path(old_daily[0]['path']).exists() if os.name == 'nt' else Path(old_daily[0]['path']).exists())
                and manual.is_file() and safety.is_file() and foreign.read_bytes() == b'synthetic foreign file'
                and {row['kind'] for row in records} == {'daily','manual','safety'})
            manager_dialog = window.build_backup_manager_dialog()
            close = manager_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close)
            checks['manager_localized_registered_only'] = (close.text() == '关闭'
                and manager_dialog.backup_list.count() == len(records))
            dialog_screenshots(window, manager_dialog, 'backup', (780,450), (650,380))

            # Change live state after the snapshot. The actual threaded UI restore
            # must replace it, preserve task/plan data, and register a safety copy.
            store.set_setting('reliability_sentinel', 'after-backup')
            chosen = root/'chosen-after-backup'
            manager.configure(enabled=False, retention=2, directory=chosen)
            result = _answer_modal(app, lambda: window.restore_backup_path(manual),
                '确认替换本机数据', QMessageBox.StandardButton.Yes)
            _drain(app, lambda: window._backup_job is None)
            restored = Store(root/'synthetic.db')
            checks['backup_restore_roundtrip'] = (result is True
                and restored.get_setting('reliability_sentinel') is None
                and restored.get_review(before['id']) == review
                and restored.get_question(question_id)['review_plan'] == plan
                and restored.get_assessment_task(task_id)['state'] == 'completed'
                and not restored.assessment_tasks_active()
                and sum(row['kind'] == 'safety' for row in manager.records()) == 2)
            checks['registry_machine_local'] = (AutoBackupManager(Store(root/'other.db')).records() == []
                and manager.settings()['retention'] == 2 and manager.settings()['enabled'] is False
                and manager.settings()['directory'] == str(chosen)
                and restored.get_setting('auto_backup_directory') == str(untrusted)
                and not untrusted.exists())
            checks['zero_external_model_calls'] = len(external_attempts) == 0
            checks['workers_finished'] = all(not w.workers and w._backup_job is None for w in windows)
            result = {'ok':all(checks.values()), 'checks':checks, 'data_dir':str(root),
                'external_model_calls':len(external_attempts),
                'scoring_calls':sum(system == ASSESSMENT_SYSTEM for system in first.systems+second.systems),
                'resumed_revision_calls':sum(system == REVISION_SYSTEM for system in second.systems)}
            if not result['ok']:
                raise RuntimeError('0.9.4 可靠性发布验收失败：'+str(result))
            return result
        finally:
            for window in windows:
                window.wait_for_backup()
            for dialog in dialogs:
                dialog.reject()
            for window in windows:
                close_window(window)
