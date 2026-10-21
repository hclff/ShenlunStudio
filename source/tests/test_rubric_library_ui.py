"""Explicit library operations against real Qt, Store and workspace timers."""
import json

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QLabel

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_rubric_library import RAW


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, 'initialize_backup_schedule', lambda self: None)
    value = MainWindow(Store(tmp_path / 'library-ui.db'))
    yield value
    value.autosave_timer.stop(); value._workspace_dirty = False
    value.close(); app.processEvents()


def manager(window):
    assert hasattr(window, 'build_rubric_library_dialog'), 'explicit rubric library manager is required'
    return window.build_rubric_library_dialog()


def record(window, name='量表甲', raw=RAW):
    return window.store.save_rubric_preset({'name':name, 'question_type':'文章写作', 'raw_json':raw},
        expected_generation=window.store.assessment_generation())


def test_builtin_save_does_not_modify_draft_or_builtin_identity(window):
    dialog = manager(window)
    before = window.workspace_snapshot()
    dialog.name_edit.setText('内置快照')
    dialog.save_current()
    rows = window.store.list_rubric_presets()
    assert len(rows) == 1
    assert json.loads(rows[0]['raw_json']) == window.selected_rubric_template()['rubric']
    assert window.workspace_snapshot() == before
    assert window.rubric_edit.toPlainText() == ''
    dialog.reject()


def test_load_exact_raw_only_keeps_settings_answer_and_uses_ordinary_autosave(window):
    row = record(window)
    window.answer_edit.setPlainText('原答不得改变')
    window.exam.setCurrentText('国考')
    before = window.workspace_snapshot()
    dialog = manager(window)
    assert dialog.selected['id'] == row['id']
    dialog.load_selected()
    after = window.workspace_snapshot()
    expected = json.loads(json.dumps(before, ensure_ascii=False))
    expected['submission']['rubric_text'] = RAW
    assert after == expected
    assert window._workspace_dirty
    assert window.flush_workspace()
    assert Store(window.store.path).load_workspace()['submission']['rubric_text'] == RAW
    dialog.reject()


def test_management_and_filters_never_change_loaded_copy(window, monkeypatch):
    first = record(window)
    window.rubric_edit.setPlainText(RAW)
    dialog = manager(window)
    dialog.name_edit.setText('改名后')
    dialog.rename_selected()
    assert window.store.get_rubric_preset(first['id'])['name'] == '改名后'
    dialog.name_edit.setText('独立副本')
    dialog.copy_selected()
    assert len(window.store.list_rubric_presets()) == 2
    assert all(row['raw_json'] == RAW for row in window.store.list_rubric_presets())
    dialog.search_edit.setText('不存在')
    assert dialog.list_widget.count() == 0
    dialog.search_edit.clear()
    dialog.filter_type.setCurrentText('归纳概括')
    assert dialog.list_widget.count() == 0
    dialog.filter_type.setCurrentIndex(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.delete_selected()
    assert len(window.store.list_rubric_presets()) == 1
    assert window.rubric_edit.toPlainText() == RAW
    dialog.reject()


def test_invalid_custom_save_keeps_raw_name_and_selection(window):
    record(window)
    window.rubric_edit.setPlainText('  [{"id":\n')
    dialog = manager(window)
    selected = dialog.selected
    dialog.name_edit.setText('待修正的名称')
    dialog.save_current()
    assert len(window.store.list_rubric_presets()) == 1
    assert dialog.name_edit.text() == '待修正的名称'
    assert dialog.selected == selected
    assert window.rubric_edit.toPlainText() == '  [{"id":\n'
    assert dialog.status.text()
    dialog.reject()


def test_duplicate_name_keeps_typed_input(window):
    record(window)
    dialog = manager(window)
    dialog.name_edit.setText('  量表甲 ')
    dialog.save_current()
    assert dialog.name_edit.text() == '  量表甲 '
    assert len(window.store.list_rubric_presets()) == 1
    assert dialog.status.text()
    dialog.reject()


@pytest.mark.parametrize('action', ['load_selected', 'delete_selected', 'update_selected'])
def test_cancel_is_default_and_preserves_exact_raw_and_row(window, monkeypatch, action):
    row = record(window)
    window.rubric_edit.setPlainText('  [{"unfinished":\n')
    dialog = manager(window)
    def cancel(*args):
        assert args[-1] == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    getattr(dialog, action)()
    assert window.rubric_edit.toPlainText() == '  [{"unfinished":\n'
    assert window.store.get_rubric_preset(row['id']) == row
    dialog.reject()


@pytest.mark.parametrize('action', ['load_selected', 'rename_selected', 'copy_selected', 'update_selected', 'delete_selected'])
def test_concurrent_edit_rejects_stale_selection_and_retains_input(window, monkeypatch, action):
    row = record(window)
    dialog = manager(window)
    other = Store(window.store.path)
    updated = other.save_rubric_preset({'name':'其他窗口', 'question_type':'文章写作', 'raw_json':RAW},
        preset_id=row['id'], expected_version=1, expected_generation=other.assessment_generation())
    dialog.name_edit.setText('保留此输入')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    getattr(dialog, action)()
    assert dialog.status.text()
    assert dialog.name_edit.text() == '保留此输入'
    assert window.store.list_rubric_presets() == [updated]
    assert window.rubric_edit.toPlainText() == ''
    dialog.reject()


@pytest.mark.parametrize('change', ['edit', 'delete', 'restore', 'editor'])
def test_nested_load_confirmation_rechecks_epoch_row_and_editor(window, tmp_path, monkeypatch, change):
    row = record(window)
    window.rubric_edit.setPlainText('  [{"keep":\n')
    backup = tmp_path / 'before.db'; window.store.backup_to(backup)
    dialog = manager(window)
    def confirm(*args):
        other = Store(window.store.path)
        if change == 'restore': other.restore_from(backup)
        elif change == 'delete': other.delete_rubric_preset(row['id'], expected_version=1, expected_generation=other.assessment_generation())
        elif change == 'edit': other.save_rubric_preset({'name':'外部改动', 'question_type':'文章写作', 'raw_json':RAW},
            preset_id=row['id'], expected_version=1, expected_generation=other.assessment_generation())
        else: window.answer_edit.setPlainText('确认期间的新输入')
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    dialog.load_selected()
    assert window.rubric_edit.toPlainText() == '  [{"keep":\n'
    assert dialog.status.text()
    dialog.reject()


def test_update_is_explicit_and_does_not_mutate_loaded_or_history(window, monkeypatch):
    row = record(window)
    original = {'title':'独立报告', 'assessment':{'total_score':10}}
    review_id = window.store.save_review(original)
    history = window.store.get_review(review_id)
    dialog = manager(window)
    replacement = '[{"id":"new","name":"新维度","weight":100}]'
    window.rubric_edit.setPlainText(replacement)
    assert window.store.get_rubric_preset(row['id'])['raw_json'] == RAW
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    dialog.update_selected()
    assert window.store.get_rubric_preset(row['id'])['raw_json'] == replacement
    assert window.rubric_edit.toPlainText() == replacement
    assert window.store.get_review(review_id) == history
    dialog.reject()


def test_old_manager_after_restore_keeps_name_and_never_recreates_deleted_library(window, tmp_path):
    backup = tmp_path / 'empty.db'; window.store.backup_to(backup)
    row = record(window)
    dialog = manager(window)
    window.store.restore_from(backup)
    dialog.name_edit.setText('恢复前窗口的新名字')
    dialog.save_current(); dialog.rename_selected(); dialog.copy_selected()
    assert window.store.list_rubric_presets() == []
    assert dialog.name_edit.text() == '恢复前窗口的新名字'
    assert dialog.selected['id'] == row['id']
    assert dialog.status.text()
    dialog.reject()


def test_minimum_window_entry_and_manager_controls_fit(window, app):
    window.resize(1050,720); window.show(); window.assessment_tabs.setCurrentIndex(1)
    app.processEvents()
    assert hasattr(window, 'rubric_library_button'), 'template area library entry is required'
    assert window.rubric_library_button.isVisible()
    for widget in (window.rubric_library_button, window.rubric_table, window.rubric_editor.advanced_button):
        assert window.rect().contains(widget.mapTo(window, widget.rect().bottomRight()))
    assert not window.rubric_summary.isVisible()
    dialog = manager(window); dialog.resize(800,560); dialog.show(); app.processEvents()
    for widget in (dialog.name_edit, dialog.type_edit, *dialog.action_buttons):
        assert widget.isVisible() and dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    dialog.reject()


@pytest.mark.parametrize('action', ['load_selected', 'update_selected', 'delete_selected'])
def test_confirmation_cannot_switch_to_another_selected_record(window, monkeypatch, action):
    first = record(window, 'A量表')
    second = record(window, 'B量表', '[{"id":"b","name":"另一个模板","weight":100}]')
    window.rubric_edit.setPlainText(RAW)
    dialog = manager(window)
    def switch_selection(*args):
        dialog.list_widget.setCurrentRow(1)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', switch_selection)
    getattr(dialog, action)()
    assert window.store.list_rubric_presets() == [first, second]
    assert dialog.status.text()
    assert window.rubric_edit.toPlainText() == RAW
    dialog.reject()


@pytest.mark.parametrize('action', ['copy_rubric_template', 'restore_rubric_template'])
def test_existing_template_confirmation_rejects_restore_and_retains_editor(window, tmp_path, monkeypatch, action):
    window.answer_edit.setPlainText('持久化草稿'); window.flush_workspace()
    opening = window.store.load_workspace()
    backup = tmp_path / 'template.db'; window.store.backup_to(backup)
    window.rubric_edit.setPlainText(RAW)
    def restore(*args):
        window.store.restore_from(backup)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', restore)
    getattr(window, action)()
    QTest.qWait(1000)
    assert window.rubric_edit.toPlainText() == RAW
    assert window.store.load_workspace() == opening
    assert window._autosave_paused


@pytest.mark.parametrize('change', ['restore', 'external'])
def test_cancel_resumes_pending_autosave_but_restore_requires_confirmed_reload(window, tmp_path, monkeypatch, change):
    window.answer_edit.setPlainText('正常待存输入')
    dialog = manager(window)
    assert not window.autosave_timer.isActive()
    dialog.reject()
    assert window.autosave_timer.isActive()
    QTest.qWait(1000)
    assert window.store.load_workspace()['submission']['answer'] == '正常待存输入'
    backup = tmp_path / 'workspace.db'; window.store.backup_to(backup)
    opening = window.store.load_workspace()
    window.answer_edit.setPlainText('必须保留的未存输入')
    dialog = manager(window)
    if change == 'restore':
        window.store.restore_from(backup)
    else:
        opening = Store(window.store.path).save_workspace({'submission':{'answer':'其他窗口草稿'}})
    dialog.reject()
    QTest.qWait(1000)
    assert window.store.load_workspace() == opening
    assert window.answer_edit.toPlainText() == '必须保留的未存输入'
    assert window._workspace_context_stale and window._autosave_paused
    assert window.close() is False
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    window.load_draft()
    assert window.answer_edit.toPlainText() == '必须保留的未存输入'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window.load_draft()
    assert window.answer_edit.toPlainText() == ('正常待存输入' if change == 'restore' else '其他窗口草稿')
    assert not window._autosave_paused


def test_all_library_operations_are_local_without_provider_calls(window, monkeypatch):
    from shenlun.model import ModelClient
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('library must stay local')
    monkeypatch.setattr(ModelClient, 'complete', forbidden)
    monkeypatch.setattr(ModelClient, 'complete_image', forbidden)
    dialog = manager(window)
    dialog.name_edit.setText('本机模板'); dialog.save_current()
    assert len(window.store.list_rubric_presets()) == 1
    dialog.name_edit.setText('副本'); dialog.copy_selected()
    dialog.name_edit.setText('重命名'); dialog.rename_selected()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    dialog.update_selected(); dialog.delete_selected(); dialog.load_selected()
    assert window.rubric_edit.toPlainText()
    assert calls == []


@pytest.mark.parametrize('action', ['copy_rubric_template', 'restore_rubric_template'])
@pytest.mark.parametrize('change', ['profile', 'answer'])
def test_existing_template_confirmation_rechecks_host_and_template_selection(window, monkeypatch, action, change):
    window.rubric_edit.setPlainText(RAW)
    def mutate(*args):
        if change == 'profile':
            window.rubric_profile.setCurrentIndex(window.rubric_profile.findData('national_law'))
        else:
            window.answer_edit.setPlainText('确认期间更新的原答')
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', mutate)
    getattr(window, action)()
    assert window.rubric_edit.toPlainText() == RAW


@pytest.mark.parametrize('action', ['delete_selected', 'update_selected'])
def test_restore_inside_management_confirmation_rejects_old_write(window, tmp_path, monkeypatch, action):
    row = record(window)
    backup = tmp_path / 'before-confirm.db'; window.store.backup_to(backup)
    dialog = manager(window)
    dialog.name_edit.setText('不能丢失的名称')
    def restore(*args):
        window.store.restore_from(backup)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', restore)
    getattr(dialog, action)()
    assert window.store.get_rubric_preset(row['id']) == row
    assert dialog.name_edit.text() == '不能丢失的名称'
    assert dialog.status.text()
    dialog.reject()
