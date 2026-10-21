"""Actual Qt backup worker lifetime and real backup/restore operations."""
from datetime import date
from pathlib import Path
import threading
import os
import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from shenlun.storage import Store
from test_task_recovery import app, window, drain


def test_background_daily_preserves_failed_task_and_midnight(window, app):
    retry = lambda: None
    window.show_task_failure('批改', '保留原失败', retry)
    before = window.failure_summary.text()
    window._backup_today = lambda: date(2026, 10, 19)
    window.check_auto_backup(force=True)
    drain(app, lambda: window._backup_job is None)
    assert len(window.backup_manager.records()) == 1
    assert window._retry_task is retry and not window.failure_panel.isHidden()
    assert window.failure_summary.text() == before
    window.check_auto_backup()
    assert window._backup_job is None
    window._backup_today = lambda: date(2026, 10, 20)
    window.check_auto_backup()
    drain(app, lambda: window._backup_job is None)
    assert len(window.backup_manager.records()) == 2


def test_failure_throttles_until_manual_retry_without_raw_error(window, app, monkeypatch):
    original = window.store.backup_to
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise OSError('SECRET token raw exception')
    monkeypatch.setattr(window.store, 'backup_to', fail)
    window.check_auto_backup(force=True)
    drain(app, lambda: window._backup_job is None)
    assert '失败' in window.backup_status.text() and 'SECRET' not in window.backup_status.text()
    window.check_auto_backup(); app.processEvents()
    assert len(calls) == 1 and window.backup_manager.records() == []
    monkeypatch.setattr(window.store, 'backup_to', original)
    window.check_auto_backup(force=True)
    drain(app, lambda: window._backup_job is None)
    assert len(window.backup_manager.records()) == 1


def test_manager_lists_registered_only_and_forget_keeps_file(window, app, tmp_path, monkeypatch):
    window.backup_timer.stop()
    saved = window.store.backup_to(tmp_path/'manual.db')
    (tmp_path/'unregistered.db').write_bytes(b'foreign')
    dialog = window.build_backup_manager_dialog()
    assert dialog.backup_list.count() == 1
    assert '手动' in dialog.backup_list.item(0).text()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.backup_list.setCurrentRow(0)
    dialog.forget_button.click()
    assert dialog.backup_list.count() == 0 and saved.exists()
    dialog.close()


def test_restore_manager_path_resets_ui_and_registers_safety(window, app, tmp_path, monkeypatch):
    source = Store(tmp_path/'source.db')
    source.set_setting('model_config', {'base_url':'http://localhost/v1','model':'恢复模型'})
    source.save_workspace({'submission': {'answer':'备份答案'}, 'question_id':None})
    backup = source.backup_to(tmp_path/'snapshot.db')
    window.answer_edit.setPlainText('恢复之前')
    window.show_task_failure('旧任务', '旧失败', lambda: None)
    window._declared_limit = ('stale connection', 1234)
    window._compatibility_tested_config = {'model':'old model'}
    window.auto_backup_enabled.setChecked(False)
    window.backup_retention.setValue(29)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert window.restore_backup_path(backup)
    assert window.answer_edit.toPlainText() == '备份答案'
    assert window.model_name.text() == '恢复模型'
    assert window.failure_panel.isHidden() and window._retry_task is None
    assert window._declared_limit is None and window._compatibility_tested_config is None
    assert window.auto_backup_enabled.isChecked() and window.backup_retention.value() == 7
    assert any(record['kind'] == 'safety' for record in window.backup_manager.records())


def test_restore_decline_and_other_window_task_leave_database_unchanged(window, app, tmp_path, monkeypatch):
    saved = window.store.backup_to(tmp_path/'saved.db')
    window.store.set_setting('value', 'current')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    assert not window.restore_backup_path(saved)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    other = Store(window.store.path)
    with other.assessment_restore_guard():
        assert not window.restore_backup_path(saved)
    assert window.store.get_setting('value') == 'current'


def test_backup_starting_during_restore_confirmation_does_not_pause_workspace(window, app, tmp_path, monkeypatch):
    saved = window.store.backup_to(tmp_path/'saved.db')
    def confirm(*args, **kwargs):
        window.check_auto_backup(force=True)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    assert not window.restore_backup_path(saved)
    assert not window._autosave_paused
    drain(app, lambda: window._backup_job is None)


def test_snapshot_runs_off_gui_thread_and_close_waits_safely(window, app, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original = window.store.backup_to
    threads = []
    def delayed(*args, **kwargs):
        threads.append(threading.get_ident()); started.set(); release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(window.store, 'backup_to', delayed)
    window.check_auto_backup(force=True)
    drain(app, started.is_set)
    assert threads[0] != threading.get_ident()
    timer = threading.Timer(.1, release.set)
    timer.start()
    window.close()
    timer.join()
    assert window._backup_job is None or not window._backup_job.isRunning()
    assert not window.isVisible()


def test_enabling_backup_in_settings_rechecks_same_day(window, app):
    window.auto_backup_enabled.setChecked(False)
    assert window.save_preferences()
    window.check_auto_backup(force=True)
    drain(app, lambda: window._backup_job is None)
    assert window.backup_manager.records() == []
    window.auto_backup_enabled.setChecked(True)
    assert window.save_preferences()
    window.check_auto_backup()
    drain(app, lambda: window._backup_job is None)
    assert len(window.backup_manager.records()) == 1


def test_app_quit_waits_for_real_backup_thread(tmp_path):
    script = '''
import sys, time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from shenlun.storage import Store
from shenlun.ui import MainWindow
app = QApplication([])
store = Store(Path(sys.argv[1])/'quit.db')
original = store.backup_to
def slow(*args, **kwargs):
    time.sleep(.25)
    return original(*args, **kwargs)
store.backup_to = slow
window = MainWindow(store)
window.show()
QTimer.singleShot(50, app.quit)
app.exec()
assert window._backup_job is None or not window._backup_job.isRunning()
assert len(window.backup_manager.records()) == 1
print('SAFE QUIT')
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
        capture_output=True, text=True, timeout=15, env={**os.environ, 'QT_QPA_PLATFORM':'offscreen'})
    assert result.returncode == 0, result.stderr
    assert 'SAFE QUIT' in result.stdout
