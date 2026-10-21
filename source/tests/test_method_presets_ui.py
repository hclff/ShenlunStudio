"""Offline, real-widget contracts for exact saved method combinations."""
import importlib
import importlib.util
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_method_library_crud import article_draft
from test_teaching_ui import enabled


def ui():
    assert importlib.util.find_spec('shenlun.ui.method_presets'), 'method combination manager required'
    return importlib.import_module('shenlun.ui.method_presets')


@pytest.fixture
def host(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    value = MainWindow(Store(tmp_path / 'presets-ui.db'))
    yield value
    value.workers.clear()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    value.close(); app.processEvents()


def dialog(host):
    return ui().MethodPresetDialog(host.store, host)


def test_combination_manager_uses_compact_guidance(host):
    d = dialog(host)
    text = '\n'.join(widget.text() for widget in d.findChildren(QLabel))
    assert '每类最多 6 项；使用前须全部可用。' in text
    assert '筛选不影响已选成员。' in text
    assert d.load_checks_button.toolTip() == '用当前勾选替换组合成员。'
    for redundant in ('组合只保存方法标识', '可收纳草稿', '题型匹配且来源可用',
                      '不可用成员也会保留'):
        assert redundant not in text
    d.reject()


def check(d, kind, identity):
    pool = d.pools[kind]
    item = next(pool.item(i) for i in range(pool.count())
                if pool.item(i).data(Qt.ItemDataRole.UserRole) == identity)
    item.setCheckState(Qt.CheckState.Checked)


def preset(host, name='合成组合', teaching_ids=None, article_ids=None):
    return host.store.save_method_preset(dict(name=name, question_type='归纳概括',
        teaching_ids=teaching_ids or ['LM01'], article_ids=article_ids or []), expected_version=0)


def test_combination_manager_is_available():
    assert ui().MethodPresetDialog


def test_save_draft_members_does_not_enable_methods(host):
    d = dialog(host); d.name.setText('本机待审组合'); check(d, 'teaching', 'LM01')
    before = host.store.get_teaching_method('LM01')
    d.save_current()
    saved = host.store.list_method_presets()[0]
    assert saved['name'] == '本机待审组合' and saved['teaching_ids'] == ['LM01']
    assert host.store.get_teaching_method('LM01') == before and not before['enabled']
    assert not d.dirty and '草稿' in d.members_list.item(0).text()
    d.reject()


def test_load_current_checks_combines_both_libraries_and_filters_preserve_members(host):
    article = host.store.save_manual_article_method(article_draft(host.store))
    host.refresh_methods()
    for index in range(host.teaching_panel.method_list.count()):
        item = host.teaching_panel.method_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == 'LM01': item.setCheckState(Qt.CheckState.Checked)
    for index in range(host.method_table.rowCount()):
        item = host.method_table.item(index, 0)
        if item.data(Qt.ItemDataRole.UserRole) == article['id']: item.setCheckState(Qt.CheckState.Checked)
    d = dialog(host); d.load_current_checks()
    assert d.member_ids('teaching') == ['LM01'] and d.member_ids('article') == [article['id']]
    d.searches['teaching'].setText('不存在的搜索词')
    assert d.pools['teaching'].count() == 0 and d.member_ids('teaching') == ['LM01']
    host.navigation.setCurrentRow(1); d.refresh()
    assert d.members_list.count() == 2 and d.member_ids('article') == [article['id']]
    d.reject()


def test_cas_failure_and_dirty_switch_keep_unsaved_fields(host, monkeypatch):
    first = preset(host); second = preset(host, '另一个组合')
    d = dialog(host); d.select_preset(first['id']); d.name.setText('未保存的名称')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    d.select_preset(second['id']); d.new_preset(); d.reject()
    assert d.current['id'] == first['id'] and d.name.text() == '未保存的名称' and d.dirty
    host.store.save_method_preset({**first, 'name': '并发修改'}, expected_version=first['version'])
    d.save_current()
    assert d.dirty and d.name.text() == '未保存的名称' and d.current['version'] == first['version']
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    d.reject()


def test_unavailable_saved_member_survives_rename_until_explicit_remove(host):
    saved = preset(host)
    host.store.delete_method('teaching', 'LM01', expected_version=0)
    d = dialog(host); d.select_preset(saved['id'])
    assert '不可用' in d.members_list.item(0).text()
    d.name.setText('保留失效引用'); d.save_current()
    assert host.store.get_method_preset(saved['id'])['teaching_ids'] == ['LM01']
    d.members_list.setCurrentRow(0); d.remove_selected_member()
    assert d.member_ids('teaching') == [] and d.dirty
    d.reject()


def test_delete_defaults_no_and_busy_guards_all_mutations(host, monkeypatch):
    saved = preset(host); d = dialog(host); d.select_preset(saved['id']); calls = []
    def no(*a, **kw): calls.append(kw); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', no)
    d.delete_current()
    assert calls[-1]['defaultButton'] == QMessageBox.StandardButton.No
    assert host.store.get_method_preset(saved['id'])
    d.name.setText('被锁住的修改'); host.workers.add(object()); host.refresh_method_preset_busy()
    d.save_current(); d.new_preset(); d.load_current_checks(); d.delete_current()
    assert d.dirty and d.name.text() == '被锁住的修改'
    assert host.store.get_method_preset(saved['id'])['name'] == saved['name']
    assert not d.save_button.isEnabled() and not host.method_choice.isEnabled()
    host.workers.clear(); host.refresh_method_preset_busy(); d.reject()


def test_training_choice_persists_and_deleted_active_preset_turns_off(host):
    ui(); saved = preset(host); host.refresh_method_presets()
    host.method_choice.setCurrentIndex(host.method_choice.findData(saved['id']))
    assert host.store.get_setting('method_choice') == {'mode': 'preset', 'preset_id': saved['id']}
    assert not host.teaching_select_button.isEnabled()
    host.store.delete_method_preset(saved['id'], expected_version=saved['version'])
    host.refresh_method_presets()
    assert host.method_choice.currentData() == 'off'
    assert '关闭' in host.teaching_summary.text()
    host.store.set_setting('method_choice', {'mode': 'preset', 'preset_id': saved['id']})
    host.refresh_method_presets()
    assert host.method_choice.currentData() == saved['id'] and '不可用' in host.method_choice.currentText()
    assert '不可用' in host.method_preset_summary.text()
    host.method_choice.setCurrentIndex(host.method_choice.findData('off'))
    assert host.method_preview()['article_methods'] == [] and not host.method_preview()['teaching_snapshot']
    host.method_choice.setCurrentIndex(host.method_choice.findData('auto'))
    assert host.teaching_select_button.isEnabled()


def test_member_limit_rejects_seventh_without_losing_first_six(host):
    d = dialog(host)
    identities = [r['id'] for r in host.store.list_teaching_methods()][:7]
    for identity in identities: check(d, 'teaching', identity)
    assert len(d.member_ids('teaching')) == 6 and identities[-1] not in d.member_ids('teaching')
    assert '6' in d.message.text()
    d.reject()


def test_manager_and_training_controls_fit_minimum_window(host, tmp_path):
    ui(); host.resize(1050, 720); host.show(); QApplication.processEvents()
    for control in (host.method_choice, host.method_preset_manage_button, host.method_preset_preview_button):
        assert control.isVisible() and host.rect().contains(control.mapTo(host, control.rect().bottomRight()))
    d = dialog(host); d.show(); QApplication.processEvents()
    assert d.width() <= 1000 and d.height() <= 680
    assert d.rect().contains(d.save_button.mapTo(d, d.save_button.rect().bottomRight()))
    assert d.grab().save(str(tmp_path / 'preset-manager-1050x720.png'))
    d.reject()


def test_preview_lists_exact_live_names_ids_versions_without_mutation(host):
    ui(); teaching = enabled(host, 'preset-preview-method')
    card = host.store.save_manual_article_method({**article_draft(host.store), 'applicable_types': ['归纳概括']})
    from shenlun.service import AppService
    AppService(host.store, {}, '').enable_card(card['id'], True)
    saved = preset(host, teaching_ids=[teaching['id']], article_ids=[card['id']])
    host.refresh_method_presets(); host.method_choice.setCurrentIndex(host.method_choice.findData(saved['id']))
    before = host.store.list_teaching_versions(teaching['id'])
    actual = host.method_preview()
    text = ui().preview_text(actual)
    assert [r['id'] for r in actual['teaching_snapshot']['methods']] == [teaching['id']]
    assert [r['id'] for r in actual['article_methods']] == [card['id']]
    assert teaching['name'] in text and teaching['id'] in text and f'版本 {teaching["version"]}' in text
    assert card['id'] in text and card['name'] in text
    assert host.store.list_teaching_versions(teaching['id']) == before


def test_malformed_choice_remains_visible_until_explicit_selection(host):
    ui(); value = {'mode': 'auto', 'unexpected': 'do not silently rewrite'}
    host.store.set_setting('method_choice', value); host.refresh_method_presets()
    assert '不可用' in host.method_choice.currentText() and '不可用' in host.method_preset_summary.text()
    assert host.store.get_setting('method_choice') == value
    host.method_choice.setCurrentIndex(host.method_choice.findData('auto'))
    assert host.store.get_setting('method_choice') == {'mode': 'auto'}


def test_database_error_keeps_edits_and_dialog_close_defaults_no(host, monkeypatch):
    d = dialog(host); d.name.setText('未提交内容'); check(d, 'teaching', 'LM01')
    def failed(*args, **kw): raise sqlite3.OperationalError('合成存储错误')
    monkeypatch.setattr(host.store, 'save_method_preset', failed)
    d.save_current()
    assert d.dirty and d.name.text() == '未提交内容' and '合成存储错误' in d.message.text()
    calls = []
    def no(*args, **kw): calls.append(kw); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', no)
    d.show(); d.close()
    assert d.isVisible() and d.dirty and calls[-1]['defaultButton'] == QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    d.reject()


def test_current_checkbox_limit_is_atomic_and_closing_guard_preserves_members(host):
    d = dialog(host); check(d, 'teaching', 'LM01')
    host.teaching_panel.check_methods(True)
    d.load_current_checks()
    assert d.member_ids('teaching') == ['LM01'] and '6' in d.message.text()
    host._closing = True
    d.name.setText('关闭中仍保留'); d.save_current(); d.new_preset(); d.remove_selected_member()
    assert d.name.text() == '关闭中仍保留' and d.member_ids('teaching') == ['LM01']
    host.refresh_method_preset_busy()
    assert not d.save_button.isEnabled() and not host.method_preset_manage_button.isEnabled()
    host._closing = False; d.reject()


def test_concurrent_combination_can_be_explicitly_reloaded_after_declining_discard(host, monkeypatch):
    saved = preset(host); d = dialog(host); d.select_preset(saved['id'])
    d.name.setText('仍在编辑的组合名')
    latest = host.store.save_method_preset({**saved, 'name': '另一窗口已保存'}, expected_version=saved['version'])
    calls = []
    def no(*args, **kw): calls.append(kw); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', no)
    assert hasattr(d, 'reload_current'), 'CAS errors need an explicit reload path'
    d.reload_current()
    assert d.name.text() == '仍在编辑的组合名' and d.dirty
    assert calls[-1]['defaultButton'] == QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    d.reload_current()
    assert d.current == latest and d.name.text() == '另一窗口已保存' and not d.dirty
    d.reject()


def test_failed_choice_setting_restores_last_saved_choice(host, monkeypatch):
    ui(); original = host.store.set_setting
    def failed(key, value):
        if key == 'method_choice': raise sqlite3.OperationalError('合成选择写入失败')
        return original(key, value)
    monkeypatch.setattr(host.store, 'set_setting', failed)
    host.method_choice.setCurrentIndex(host.method_choice.findData('off'))
    assert host.store.get_setting('method_choice') is None and host.method_choice.currentData() == 'auto'
    assert '合成选择写入失败' in host.status_label.text()


def test_busy_timer_keeps_legacy_selector_locked_for_preset_mode(host):
    ui(); saved = preset(host); host.refresh_method_presets()
    host.method_choice.setCurrentIndex(host.method_choice.findData(saved['id']))
    QTest.qWait(150); QApplication.processEvents()
    assert not host.teaching_select_button.isEnabled()
    host.workers.add(object()); QTest.qWait(150); QApplication.processEvents()
    assert not host.method_choice.isEnabled() and not host.method_preset_manage_button.isEnabled()
    host.workers.clear(); QTest.qWait(150); QApplication.processEvents()
    assert host.method_choice.isEnabled() and not host.teaching_select_button.isEnabled()


def test_legacy_auto_teaching_setting_refresh_updates_actual_method_summary(host):
    ui(); enabled(host, 'summary-preview-method')
    host.refresh_teaching_summary()
    assert '教学 1 项' in host.method_preset_summary.text()
    host.store.set_setting('teaching_selection', {'mode': 'off', 'owner_ids': []})
    host.refresh_teaching_summary()
    assert '教学 0 项' in host.method_preset_summary.text()
