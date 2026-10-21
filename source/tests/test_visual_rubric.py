"""Visual rubric edits use real Qt widgets, submission resolution and SQLite drafts."""
import json
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QDialog, QLineEdit, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_rubric_ui import app, window, select


def rows(window):
    return json.loads(window.rubric_edit.toPlainText())


def custom(window):
    window.rubric_edit.setPlainText(json.dumps([
        {'id':'fixed-a', 'name':'甲', 'weight':33.333333333, 'description':'说明甲', 'extra':{'keep':[1,2]}},
        {'id':'fixed-b', 'name':'乙', 'weight':66.666666667, 'description':'说明乙'}], ensure_ascii=False))


def test_template_render_keeps_builtin_identity_and_raw_empty(window):
    table = window.rubric_table
    assert table.rowCount() == 5
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    window.category.setText('C类')
    assert table.item(1, 1).text() == '40'
    assert window.rubric_edit.toPlainText() == ''
    window.answer_edit.setPlainText('原答')
    assert window.submission()['rubric_template']['kind'] == 'builtin'
    window.copy_rubric_template()
    assert window.submission()['rubric_template']['kind'] == 'custom'
    assert table.editTriggers() != QAbstractItemView.EditTrigger.NoEditTriggers


def test_advanced_template_cancel_keeps_builtin_and_keyboard_edit_survives_restart(window, app):
    dialog = window.build_rubric_json_dialog()
    dialog.json_edit.setPlainText('unfinished')
    dialog.reject()
    assert window.rubric_edit.toPlainText() == ''
    window.copy_rubric_template()
    window.show()
    window.assessment_tabs.setCurrentIndex(1)
    table = window.rubric_table
    stable_id = rows(window)[0]['id']
    table.setCurrentCell(0, 0)
    table.editItem(table.item(0, 0))
    app.processEvents()
    input_edit = table.findChild(QLineEdit)
    assert input_edit is not None
    input_edit.setText('键盘改名')
    QTest.keyClick(input_edit, Qt.Key.Key_Return)
    app.processEvents()
    assert rows(window)[0]['name'] == '键盘改名'
    assert window.flush_workspace()
    restarted = MainWindow(Store(window.store.path))
    try:
        assert restarted.rubric_table.item(0, 0).text() == '键盘改名'
        assert rows(restarted)[0]['id'] == stable_id
    finally:
        restarted.close()
        app.processEvents()


def test_add_limit_and_domain_name_id_weight_checks(window):
    window.copy_rubric_template()
    for _ in range(15):
        window.rubric_editor.add_button.click()
    assert len(rows(window)) == 20
    assert not window.rubric_editor.add_button.isEnabled()
    assert len({row['id'] for row in rows(window)}) == 20
    window.rubric_editor.add_row()
    assert len(rows(window)) == 20
    window.answer_edit.setPlainText('原答')
    assert len(window.submission()['rubric']) == 20
    window.rubric_table.item(0, 0).setText('  ')
    with pytest.raises(ValueError, match='名称'):
        window.submission()
    window.rubric_table.item(0, 0).setText('恢复名称')
    window.rubric_table.item(0, 1).setText('-1')
    with pytest.raises(ValueError, match='范围'):
        window.submission()
    invalid = rows(window)
    invalid[0]['weight'] = 15
    invalid[1]['id'] = invalid[0]['id']
    window.rubric_edit.setPlainText(json.dumps(invalid))
    with pytest.raises(ValueError, match='重复'):
        window.submission()


def test_edit_add_delete_preserves_ids_unknown_fields_and_submission(window):
    custom(window)
    table = window.rubric_table
    table.item(0, 0).setText('重命名')
    table.item(0, 2).setText('新的说明')
    assert rows(window)[0] == {'id':'fixed-a', 'name':'重命名', 'weight':33.333333333,
                               'description':'新的说明', 'extra':{'keep':[1,2]}}
    window.rubric_editor.add_button.click()
    added_id = rows(window)[2]['id']
    assert added_id not in ('fixed-a', 'fixed-b')
    table.item(2, 0).setText('新增维度')
    assert rows(window)[2]['id'] == added_id
    table.selectRow(2)
    window.rubric_editor.delete_button.click()
    window.answer_edit.setPlainText('原答')
    assert window.submission()['rubric'] == rows(window)
    assert window.rubric_preview()['rubric'] == rows(window)


def test_decimal_cells_preserve_precision_and_use_domain_validation(window):
    custom(window)
    assert window.rubric_table.item(0, 1).text() == '33.333333333'
    assert '100%' in window.rubric_editor.total_label.text()
    window.rubric_table.item(0, 1).setText('33.123456789')
    assert rows(window)[0]['weight'] == 33.123456789
    window.answer_edit.setPlainText('原答')
    with pytest.raises(ValueError):
        window.submission()
    assert '未完成' in window.rubric_editor.total_label.text()
    assert window.flush_workspace()
    assert json.loads(window.store.load_workspace()['submission']['rubric_text'])[0]['weight'] == 33.123456789
    window.rubric_table.item(1, 1).setText('66.876543211')
    assert window.submission()['rubric'][1]['weight'] == 66.876543211


def precise_custom(window):
    window.rubric_edit.setPlainText('''[
        {"id":"exact-a","name":"甲","weight":33.333333333333333333,
         "description":"原说明","extra":{"number":0.123456789012345678901,
         "nested":[-9.876543210987654321e-200,-0,1.2300,true,null,
                    "0.123456789012345678901",{"text":"\\\"quoted\\\"\\nline"}]}},
        {"id":"exact-b","name":"乙","weight":66.666666666666666667}
    ]''')


def test_high_precision_weights_render_without_binary_float_rounding(window):
    precise_custom(window)
    assert window.rubric_table.item(0, 1).text() == '33.333333333333333333'
    assert window.rubric_table.item(1, 1).text() == '66.666666666666666667'
    assert window.rubric_editor.total_label.text() == '合计 100%'


@pytest.mark.parametrize('operation', ['rename', 'description', 'add', 'delete'])
def test_unrelated_table_changes_preserve_all_numeric_tokens_and_unknown_fields(window, operation):
    precise_custom(window)
    before = json.loads(window.rubric_edit.toPlainText(), parse_float=Decimal)
    if operation == 'rename':
        window.rubric_table.item(0, 0).setText('新名称')
    elif operation == 'description':
        window.rubric_table.item(0, 2).setText('新说明')
    elif operation == 'add':
        window.rubric_editor.add_button.click()
    else:
        window.rubric_table.selectRow(1)
        window.rubric_editor.delete_button.click()
    raw = window.rubric_edit.toPlainText()
    after = json.loads(raw, parse_float=Decimal)
    assert after[0]['weight'] == Decimal('33.333333333333333333')
    assert after[0]['extra'] == before[0]['extra']
    # Numeric lexemes stay numbers and retain exponent, signed zero and trailing zeros.
    for token in ('0.123456789012345678901', '-9.876543210987654321e-200', '-0', '1.2300'):
        assert token in raw
    assert after[0]['id'] == 'exact-a'
    assert window.flush_workspace()
    assert window.store.load_workspace()['submission']['rubric_text'] == raw


def test_edited_high_precision_weights_survive_add_delete_autosave_restart(window, app):
    precise_custom(window)
    window.rubric_table.item(0, 1).setText('33.123456789012345678901')
    window.rubric_table.item(1, 1).setText('66.876543210987654321099')
    window.rubric_editor.add_button.click()
    window.rubric_table.selectRow(2)
    window.rubric_editor.delete_button.click()
    raw = window.rubric_edit.toPlainText()
    actual = json.loads(raw, parse_float=Decimal)
    assert actual[0]['weight'] == Decimal('33.123456789012345678901')
    assert actual[1]['weight'] == Decimal('66.876543210987654321099')
    assert actual[0]['extra']['number'] == Decimal('0.123456789012345678901')
    window.answer_edit.setPlainText('原答')
    # The existing submission contract still uses normal JSON numbers and domain validation.
    assert window.submission()['rubric'][0]['weight'] == float('33.123456789012345678901')
    assert window.rubric_editor.total_label.text() == '合计 100%'
    assert window.flush_workspace()
    restarted = MainWindow(Store(window.store.path))
    try:
        assert restarted.rubric_edit.toPlainText() == raw
        assert restarted.rubric_table.item(0, 1).text() == '33.123456789012345678901'
        assert restarted.rubric_table.item(1, 1).text() == '66.876543210987654321099'
    finally:
        restarted.close()
        app.processEvents()


@pytest.mark.parametrize('text', [
    '1e1000000', 'NaN', 'Infinity', '-Infinity',
    '1, "injected": 2', 'true', '{"injected":1}', '[1]'])
def test_invalid_weight_tokens_remain_quoted_or_numeric_drafts_without_large_status(window, app, text):
    precise_custom(window)
    window.rubric_table.item(0, 1).setText(text)
    raw = window.rubric_edit.toPlainText()
    parsed = json.loads(raw, parse_float=Decimal)
    if text in ('1, "injected": 2', 'true', '{"injected":1}', '[1]'):
        assert parsed[0]['weight'] == text
        assert 'injected' not in parsed[0]
    elif text == '1e1000000':
        assert parsed[0]['weight'] == Decimal('1e1000000')
    window.answer_edit.setPlainText('原答')
    with pytest.raises(ValueError):
        window.submission()
    assert '未完成' in window.rubric_editor.total_label.text()
    assert len(window.rubric_editor.total_label.text()) < 30
    window.resize(1050, 720)
    window.assessment_tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    control = window.rubric_editor.advanced_button
    assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
    assert window.flush_workspace()
    assert window.store.load_workspace()['submission']['rubric_text'] == raw
    window.rubric_table.item(0, 1).setText('33.333333333333333333')
    assert window.submission()['rubric'][0]['id'] == 'exact-a'


@pytest.mark.parametrize('text', ['  [{"id":\n', '{"not":"rows"}', '[1]', 'null'])
def test_invalid_raw_survives_switch_restart_and_advanced_cancel(window, app, text):
    window.rubric_edit.setPlainText(text)
    select(window, 'national_law')
    assert window.rubric_table.rowCount() == 0
    assert not window.rubric_editor.add_button.isEnabled()
    dialog = window.build_rubric_json_dialog()
    assert dialog.json_edit.toPlainText() == text
    dialog.json_edit.setPlainText('[]')
    dialog.reject()
    assert window.rubric_edit.toPlainText() == text
    assert window.flush_workspace()
    restarted = MainWindow(Store(window.store.path))
    try:
        assert restarted.rubric_edit.toPlainText() == text
        assert restarted.rubric_table.rowCount() == 0
        assert restarted.rubric_editor.advanced_button.isEnabled()
        assert restarted.build_rubric_json_dialog().json_edit.toPlainText() == text
    finally:
        restarted.close()
        app.processEvents()


def test_advanced_json_confirmation_validates_and_syncs_without_cancel_changes(window):
    custom(window)
    original = window.rubric_edit.toPlainText()
    dialog = window.build_rubric_json_dialog()
    dialog.json_edit.setPlainText('[{"id":"x","name":"修复","weight":99}]')
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error_label.text()
    assert window.rubric_edit.toPlainText() == original
    repaired = ' [{"id":"x","name":"修复","weight":100,"unknown":[7]}] '
    dialog.json_edit.setPlainText(repaired)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert window.rubric_edit.toPlainText() == repaired
    assert window.rubric_table.item(0, 0).text() == '修复'
    assert rows(window)[0]['unknown'] == [7]


def test_restore_template_requires_consent_and_restores_builtin(window, monkeypatch):
    custom(window)
    original = window.rubric_edit.toPlainText()
    select(window, 'jiangsu_b')
    assert window.rubric_edit.toPlainText() == original
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    window.restore_rubric_template()
    assert window.rubric_edit.toPlainText() == original
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.restore_rubric_template()
    window.answer_edit.setPlainText('原答')
    assert window.rubric_edit.toPlainText() == ''
    assert window.submission()['rubric_template']['kind'] == 'builtin'
    assert window.submission()['rubric_template']['profile'] == 'jiangsu_b'


def test_invalid_table_draft_and_zero_rows_can_be_repaired(window):
    custom(window)
    window.rubric_table.item(0, 1).setText('还没填好')
    assert rows(window)[0]['weight'] == '还没填好'
    assert window.flush_workspace()
    window.rubric_table.item(0, 1).setText('33.333333333')
    window.rubric_table.selectAll()
    window.rubric_editor.delete_button.click()
    assert rows(window) == []
    window.rubric_editor.add_button.click()
    window.rubric_table.item(0, 1).setText('100')
    window.answer_edit.setPlainText('原答')
    assert window.submission()['rubric'][0]['weight'] == 100


def test_paused_workspace_disables_visual_mutation(window):
    window.set_workspace_paused(True)
    assert not window.rubric_editor.isEnabled()
    window.set_workspace_paused(False)
    assert window.rubric_editor.isEnabled()


def test_extreme_invalid_weight_keeps_minimum_window_reachable(window, app):
    raw = '[{"id":"x","name":"草稿","weight":1e200}]'
    window.rubric_edit.setPlainText(raw)
    window.resize(1050, 720)
    window.assessment_tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    assert window.width() == 1050
    assert window.rubric_edit.toPlainText() == raw
    assert '未完成' in window.rubric_editor.total_label.text()
    control = window.rubric_editor.advanced_button
    assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))


@pytest.mark.parametrize('size', [(1360,900), (1050,720)])
def test_normal_and_min_window_keep_table_controls_reachable(window, app, tmp_path, size):
    window.resize(*size)
    window.assessment_tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    assert not window.rubric_edit.isVisible()
    assert not window.rubric_summary.isVisible()
    assert window.rubric_table.height() >= 100
    assert window.rubric_table.viewport().height() >= 2 * window.rubric_table.rowHeight(0)
    for control in (window.rubric_table, window.rubric_profile, window.rubric_preview_button,
                    window.rubric_copy_button, window.rubric_editor.advanced_button,
                    window.rubric_editor.restore_button, window.rubric_editor.add_button,
                    window.rubric_editor.delete_button, window.reference_edit):
        assert control.isVisible()
        assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
        assert control.width() >= 45 and control.height() >= 25
    assert window.grab().save(str(tmp_path / f'rubric-{size[0]}x{size[1]}.png'))
