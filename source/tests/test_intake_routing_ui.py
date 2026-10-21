"""Real Qt routing adoption; fixtures use synthetic local databases only."""
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from shenlun.storage import Store
from shenlun.ui.intake import IntakePreviewDialog


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from shenlun.ui import MainWindow
    # Daily backup shares the non-reentrant restore/task lock; unrelated to intake.
    monkeypatch.setattr(MainWindow, 'initialize_backup_schedule', lambda self: None)
    w = MainWindow(Store(tmp_path / 'routing.db'))
    yield w
    w.autosave_timer.stop(); w._workspace_dirty = False
    w.close(); app.processEvents()


def preview(window, text='题干一\n\n材料一', fields=None):
    page = SimpleNamespace(source='合成.txt', number=2, text=text, method='文本',
                           preview_png=b'', warnings=[], fields=fields or {})
    return IntakePreviewDialog(SimpleNamespace(pages=[page], errors=[]), window)


def router(window, **kwargs):
    p = preview(window, **kwargs)
    assert hasattr(p, 'create_routing_dialog'), 'explicit paragraph routing dialog is required'
    return p, p.create_routing_dialog()


def assign(dialog, row, target):
    combo = dialog.target_combos[row]
    combo.setCurrentIndex(combo.findData(target))


def test_open_cancel_default_skip_and_previews_make_no_writes(window):
    window.answer_edit.setPlainText('未保存原答')
    p, d = router(window)
    assert window.store.load_workspace() is None
    assert all(combo.currentData() == '' for combo in d.target_combos)
    assert d.previews['answer'].toPlainText() == '未保存原答'
    d.reject()
    assert window.store.load_workspace() is None
    assert window.answer_edit.toPlainText() == '未保存原答'
    p.reject()


def test_dirty_editor_and_persisted_baselines_commit_once(window):
    window.materials_edit.setPlainText('磁盘材料'); window.flush_workspace()
    window.answer_edit.setPlainText('最新未保存原答')
    p, d = router(window)
    assign(d, 0, 'question'); assign(d, 1, 'materials')
    assert d.previews['materials'].toPlainText() == '磁盘材料\n\n材料一'
    d.accept()
    assert d.result() == QDialog.DialogCode.Accepted
    assert window.question_edit.toPlainText() == '题干一'
    assert window.store.load_workspace()['submission']['answer'] == '最新未保存原答'
    assert window.store.list_workspace_history()[0]['submission']['answer'] == '最新未保存原答'
    assert not window._workspace_dirty
    assert not window.autosave_timer.isActive()
    assert p.selection()['mode'] == 'routing'
    # The outer intake dispatcher must never run the old two-write import.
    saved = window.store.load_workspace()
    window.apply_intake_selection(p.selection())
    assert window.store.load_workspace() == saved


def test_real_second_write_failure_keeps_buffer_and_host(window, monkeypatch):
    window.answer_edit.setPlainText('原答'); window.flush_workspace()
    opening = window.store.load_workspace(); p, d = router(window)
    assign(d, 0, 'answer')
    writer = window.store._save_workspace; calls = []
    def fail(conn, snapshot, archive_previous):
        saved = writer(conn, snapshot, archive_previous); calls.append(1)
        if len(calls) == 2: raise sqlite3.OperationalError('disk failure')
        return saved
    monkeypatch.setattr(window.store, '_save_workspace', fail)
    d.accept()
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.target_combos[0].currentData() == 'answer'
    assert 'disk failure' in d.status.text()
    assert window.answer_edit.toPlainText() == '原答'
    assert window.store.load_workspace() == opening
    assert window.store.list_workspace_history() == []


@pytest.mark.parametrize('change', ['source', 'check', 'order', 'remove', 'editor'])
def test_changed_source_or_editor_rejects_and_cannot_fall_back(window, change):
    p, d = router(window); assign(d, 0, 'materials')
    if change == 'source': p.text_edit.setPlainText('改了来源')
    elif change == 'check': p.page_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    elif change == 'order':
        # A second source is required for an actual order change.
        item = p.page_list.item(0).clone(); p.page_list.addItem(item); p.move_page(1)
    elif change == 'remove': p.remove_page()
    else: window.answer_edit.setPlainText('独立新输入')
    d.accept(); p.accept()
    assert d.result() != QDialog.DialogCode.Accepted
    assert p.result() != QDialog.DialogCode.Accepted
    assert window.store.load_workspace() is None
    assert p.selection()['mode'] == 'routing'


@pytest.mark.parametrize('change', ['other-window', 'restore', 'editor'])
def test_stale_during_default_no_overwrite_confirmation_keeps_buffer(window, monkeypatch, tmp_path, change):
    window.answer_edit.setPlainText('旧原答'); window.flush_workspace()
    generation = window.store.assessment_generation()
    backup = window.store.backup_to(tmp_path / 'backup.db', register=False)
    p, d = router(window); assign(d, 0, 'answer'); d.replace_checks['answer'].setChecked(True)
    defaults = []
    def confirm(parent, title, text, buttons, default):
        defaults.append(default)
        assert '我的原答' in text and '给定材料' not in text
        if change == 'other-window':
            other = Store(window.store.path)
            value = deepcopy(other.load_workspace()); value['submission']['answer'] = '其他窗口'
            other.save_workspace(value)
        elif change == 'restore': window.store.restore_from(backup)
        else: window.answer_edit.setPlainText('确认期间新输入')
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    d.accept()
    assert defaults == [QMessageBox.StandardButton.No]
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.target_combos[0].currentData() == 'answer'
    assert window.answer_edit.toPlainText() == ('确认期间新输入' if change == 'editor' else '旧原答')
    if change == 'restore': assert generation != window.store.assessment_generation()


def test_generation_is_captured_before_context_read(window, monkeypatch, tmp_path):
    window.flush_workspace()
    backup = window.store.backup_to(tmp_path / 'backup.db', register=False)
    load = window.store.load_workspace; once = []
    def restore_then_read():
        if not once: once.append(1); window.store.restore_from(backup)
        return load()
    monkeypatch.setattr(window.store, 'load_workspace', restore_then_read)
    p, d = router(window); assign(d, 0, 'answer'); d.accept()
    assert d.result() != QDialog.DialogCode.Accepted
    assert window.answer_edit.toPlainText() == ''


def test_busy_and_whole_question_modes_disable_routing(window):
    p = preview(window, fields={'question': '结构题', 'materials': '结构材料'})
    assert hasattr(p, 'routing_button'), 'routing entry is required'
    p.set_recognition_busy(True)
    assert not p.routing_button.isEnabled()
    assert p.create_routing_dialog() is None
    p.set_recognition_busy(False); p.exercise_check.setChecked(True)
    assert not p.routing_button.isEnabled()
    assert p.create_routing_dialog() is None


def test_actual_modal_pipeline_batch_assignment_and_zero_models(window, monkeypatch):
    from shenlun.model import ModelClient
    calls = []
    def forbid(*args, **kwargs):
        calls.append(1); raise AssertionError('routing must remain local')
    monkeypatch.setattr(ModelClient, 'complete', forbid)
    p = preview(window)
    def fill_modal():
        d = QApplication.activeModalWidget()
        for row in range(2): d.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        d.batch_combo.setCurrentIndex(d.batch_combo.findData('answer'))
        d.assign_checked(); d.accept()
    QTimer.singleShot(0, fill_modal)
    p.open_routing()
    assert p.result() == QDialog.DialogCode.Accepted
    assert window.answer_edit.toPlainText() == '题干一\n\n材料一'
    assert calls == []


def test_real_timer_cannot_autosave_own_opening_baseline(window, app):
    window.answer_edit.setPlainText('尚未保存原答')
    p, d = router(window)
    QTest.qWait(1000)
    assert window.store.load_workspace() is None
    assign(d, 0, 'materials'); d.accept()
    assert d.result() == QDialog.DialogCode.Accepted
    assert window.store.load_workspace()['submission']['answer'] == '尚未保存原答'


def test_no_overwrite_confirmation_preserves_buffer_and_empty_replacement_target(window, monkeypatch):
    window.answer_edit.setPlainText('旧原答')
    p, d = router(window); assign(d, 0, 'answer')
    d.replace_checks['answer'].setChecked(True)
    d.replace_checks['materials'].setChecked(True)
    confirmations = []
    def decline(*args):
        confirmations.append(args[2]); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', decline)
    d.accept()
    assert len(confirmations) == 1
    assert window.answer_edit.toPlainText() == '旧原答'
    assert window.store.load_workspace() is None
    assert d.target_combos[0].currentData() == 'answer'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    d.accept()
    assert window.answer_edit.toPlainText() == '题干一'
    assert window.materials_edit.toPlainText() == ''


def test_stale_cancel_timer_and_close_cannot_overwrite_other_window(window, app, monkeypatch):
    window.answer_edit.setPlainText('尚未保存原答')
    p, d = router(window); assign(d, 0, 'answer')
    other = Store(window.store.path)
    current = other.save_workspace({'submission': {'answer': '其他窗口的新原答'}})
    d.accept(); d.reject(); p.reject()
    QTest.qWait(1000)
    assert other.load_workspace() == current
    assert window.answer_edit.toPlainText() == '尚未保存原答'
    assert window._autosave_paused
    window.show(); app.processEvents()
    assert window.close() is False
    assert other.load_workspace() == current
    prompts = []
    def decline(*args):
        prompts.append(args[-1]); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', decline)
    window.load_draft()
    assert prompts == [QMessageBox.StandardButton.No]
    assert window.answer_edit.toPlainText() == '尚未保存原答'
    assert window._autosave_paused
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window.load_draft()
    assert window.answer_edit.toPlainText() == '其他窗口的新原答'
    assert not window._autosave_paused and not window._workspace_dirty
    assert other.load_workspace() == current  # Reload is read-only.
    assert window.close() is True


def test_cancel_without_commit_attempt_also_protects_stale_dirty_draft(window):
    window.answer_edit.setPlainText('保留输入')
    p, d = router(window)
    other = Store(window.store.path)
    saved = other.save_workspace({'submission': {'answer': '新草稿'}})
    d.reject(); p.reject()
    assert window._autosave_paused
    assert window.answer_edit.toPlainText() == '保留输入'
    assert window.store.load_workspace() == saved


def test_reload_confirmation_cannot_authorize_subsequent_restore(window, monkeypatch, tmp_path):
    window.answer_edit.setPlainText('保留输入')
    p, d = router(window)
    other = Store(window.store.path); other.save_workspace({'submission': {'answer': '新草稿'}})
    backup = other.backup_to(tmp_path / 'reload.db', register=False)
    d.reject(); p.reject()
    def restore_during_confirmation(*args):
        other.restore_from(backup)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', restore_during_confirmation)
    window.load_draft()
    assert window.answer_edit.toPlainText() == '保留输入'
    assert window._autosave_paused


def test_restore_between_cancel_reads_preserves_restored_row_until_confirmed_reload(window, app, monkeypatch, tmp_path):
    window.answer_edit.setPlainText('持久化草稿 P'); window.flush_workspace()
    opening = window.store.load_workspace()
    backup = window.store.backup_to(tmp_path / 'identical-workspace.db', register=False)
    window.answer_edit.setPlainText('尚未保存的原输入 D')
    p, d = router(window)
    generation = d.context['generation']
    read_generation = window.store.assessment_generation
    restore_guard = window.store.assessment_restore_guard
    pending_restore = [True]

    def deliver_pending_restore():
        if pending_restore[0]:
            pending_restore[0] = False
            # All real restore work runs before any guard is held by this hook.
            window.store.restore_from(backup)

    def generation_then_restore():
        value = read_generation()
        # Reproduce F1 on the old two-read path: return G after restoring exact P.
        deliver_pending_restore()
        return value

    @contextmanager
    def guard_after_pending_restore():
        # A pending restore must finish before the fixed atomic checker acquires
        # the real guard. Never invoke restore inside its non-reentrant lock.
        deliver_pending_restore()
        with restore_guard():
            yield

    monkeypatch.setattr(window.store, 'assessment_generation', generation_then_restore)
    monkeypatch.setattr(window.store, 'assessment_restore_guard', guard_after_pending_restore)
    d.reject(); p.reject()
    assert not pending_restore[0]
    assert read_generation() != generation
    QTest.qWait(1000)
    assert window.store.load_workspace() == opening
    assert window.answer_edit.toPlainText() == '尚未保存的原输入 D'
    assert window._workspace_context_stale and window._autosave_paused
    window.show(); app.processEvents()
    assert window.close() is False
    assert window.store.load_workspace() == opening
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    window.load_draft()
    assert window.answer_edit.toPlainText() == '尚未保存的原输入 D'
    assert window._autosave_paused
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window.load_draft()
    assert window.answer_edit.toPlainText() == '持久化草稿 P'
    assert not window._workspace_context_stale and not window._autosave_paused
    assert window.store.load_workspace() == opening
    assert window.close() is True
