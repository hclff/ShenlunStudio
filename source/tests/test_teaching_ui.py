"""Offline teaching UI contracts using real isolated Store and Qt widgets."""
import importlib
import importlib.util
import json
import sqlite3
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow


def ui():
    assert importlib.util.find_spec('shenlun.ui.teaching'), 'teaching manager UI required'
    return importlib.import_module('shenlun.ui.teaching')


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app, tmp_path, monkeypatch):
    value = MainWindow(Store(tmp_path / 'teaching-ui.db'))
    yield value
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    value.close()
    app.processEvents()


def method(identity='synthetic-ui', owner='limengyuan', scope='full_text'):
    return dict(id=identity, owner_id=owner, name='合成方法', purpose='合成检查',
                steps=['核对材料'], checks=['证据准确'], applicable_types=['归纳概括'],
                not_applicable='不得代替题干', sources=[dict(title='合成来源', url='https://example.org/source',
                    access_scope=scope, locator='第一段', excerpt='核对材料事实。', quote='材料事实')])


def enabled(host, identity='synthetic-ui', owner='limengyuan'):
    row = host.store.save_teaching_method(method(identity, owner), expected_version=0)
    return host.store.set_teaching_method_enabled(row['id'], True, reviewer='合成审核员',
                                                 note='仅合成测试', expected_version=1)


def panel(host):
    ui()
    return host.teaching_panel


def wait(app, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        app.processEvents(); QTest.qWait(10)
    assert predicate()


def test_default_catalog_separate_from_publication_api_and_owner_names_visible(host):
    p = panel(host)
    assert host.methods_tabs.count() == 2 and host.pages.count() == 6
    assert len(p.records) == 24 and all(not r['enabled'] for r in p.records)
    assert p.owner_filter.count() == 12
    from shenlun.teaching_catalog import teaching_owners
    selection = ui().TeachingSelectionDialog(host.store, host)
    for owner in teaching_owners():
        assert p.owner_filter.itemText(p.owner_filter.findData(owner['id'])) == owner['name']
        assert p.owner_edit.itemText(p.owner_edit.findData(owner['id'])) == owner['name']
        assert selection.owner_checks[owner['id']].text() == owner['name']
    selection.reject()
    assert '0' in p.summary.text() and host.selected_card() is None
    assert '社区整理' not in p.summary.text()
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    assert p.method_list.count() == 2 and p.owner_notice.text() == ''
    p.select_method('GH_BL01')
    assert p.name.text() == '白鹭·按任务约束审题'
    assert '/blob/354341' in p.source_fields['url'].text()
    assert '教师本人' not in p.boundary.toPlainText()
    assert p.new_button.isEnabled()  # No assessment form is necessary.


def test_teaching_dialogs_use_compact_guidance(host):
    mod = ui()
    review = mod.TeachingReviewDialog(host)
    review_text = ' '.join(label.text() for label in review.findChildren(mod.QLabel))
    assert '核对来源、引文和适用范围。' in review_text
    assert '官方认证' not in review_text and '教师本人' not in review_text
    selection = mod.TeachingSelectionDialog(host.store, host)
    selection_text = ' '.join(label.text() for label in selection.findChildren(mod.QLabel))
    assert '最多 6 项。' in selection_text
    assert '教师本人' not in selection_text and '平均打分' not in selection_text
    preview = mod.TeachingJsonPreviewDialog([method()], 'methods.json', host)
    preview_text = ' '.join(label.text() for label in preview.findChildren(mod.QLabel))
    assert '导入后为停用草稿。' in preview_text
    from shenlun.intake import ImportBatch, InputPage
    source = mod.TeachingSourcePreviewDialog(
        ImportBatch([InputPage('scan.png', 1, '材料文字', '本机 OCR')]), host)
    source_text = ' '.join(label.text() for label in source.findChildren(mod.QLabel))
    assert '请核对识别文字，最多 4000 字。' in source_text
    all_copy = ' '.join((review_text, selection_text, preview_text, source_text,
                         ' '.join(label.text() for label in panel(host).findChildren(mod.QLabel))))
    for redundant in ('GitHub 社区整理', '社区条目', '教师本人', '教师认证',
                      '官方认证', '仓库声称', '原始课程', '平均打分'):
        assert redundant not in all_copy
    review.reject(); selection.reject(); preview.reject(); source.reject()


def test_edit_save_revokes_enable_and_history_preserves_review(host):
    row = enabled(host); p = panel(host); p.refresh(); p.select_method(row['id'])
    p.name.setText('修改后的合成方法'); p.save_current()
    latest = host.store.get_teaching_method(row['id'])
    assert latest['version'] == 3 and latest['enabled'] is False
    assert p.history.toPlainText().count('合成审核员') >= 1
    assert p.current['version'] == 3 and not p.dirty


def test_failed_save_version_conflict_preserves_editor(host):
    row = host.store.save_teaching_method(method(), expected_version=0)
    p = panel(host); p.refresh(); p.select_method(row['id']); p.name.setText('未保存文字')
    host.store.save_teaching_method(method(), expected_version=1)
    p.save_current()
    assert p.name.text() == '未保存文字' and p.dirty and p.current['version'] == 1
    assert host.store.get_teaching_method(row['id'])['version'] == 2


def test_database_write_failure_keeps_editor_without_qt_slot_error(host, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.name.setText('待保存草稿')
    def failed(*a, **kw): raise sqlite3.OperationalError('合成写入失败')
    monkeypatch.setattr(host.store, 'save_teaching_method', failed)
    p.save_current()
    assert p.name.text() == '待保存草稿' and p.dirty and '合成写入失败' in p.message.text()


def test_dirty_switch_and_refresh_preserve_raw_source_editor(host, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.source_excerpt.setPlainText('未完成的原文片段')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    p.select_method('FB01'); host.refresh_methods()
    assert p.current['id'] == 'LM01' and p.source_excerpt.toPlainText() == '未完成的原文片段'
    assert p.dirty


def test_dirty_new_filter_and_close_default_no_preserve_all_fields(host, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.source_fields['locator'].setText('尚未校对的页码')
    calls = []
    def no(*a, **kw): calls.append(kw); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', no)
    p.new_method(); p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    host.show(); host.close()
    assert host.isVisible() and p.current['id'] == 'LM01' and p.owner_filter.currentData() is None
    assert p.source_fields['locator'].text() == '尚未校对的页码' and p.dirty
    assert len(calls) == 3 and all(c['defaultButton'] == QMessageBox.StandardButton.No for c in calls)


def test_local_review_dialog_requires_explicit_name_and_note(host):
    d = ui().TeachingReviewDialog(host)
    d.accept(); assert d.result() != QDialog.DialogCode.Accepted
    d.reviewer.setText('合成审核人'); d.accept(); assert d.result() != QDialog.DialogCode.Accepted
    d.note.setPlainText('本机逐项核对'); d.accept(); assert d.result() == QDialog.DialogCode.Accepted


def test_enable_has_named_review_and_default_no_confirmation(host, monkeypatch):
    p = panel(host); p.select_method('LM01'); mod = ui(); calls = []
    def reviewed(self):
        self.reviewer.setText('合成本地审核人'); self.note.setPlainText('已核对片段和边界')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(mod.TeachingReviewDialog, 'exec', reviewed)
    def reject(*a, **kw):
        calls.append((a, kw)); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', reject)
    p.enable_current()
    assert calls and (calls[0][1].get('defaultButton') == QMessageBox.StandardButton.No
                     or calls[0][0][-1] == QMessageBox.StandardButton.No)
    assert host.store.get_teaching_method('LM01')['version'] == 0
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    p.enable_current(); row = host.store.get_teaching_method('LM01')
    assert row['enabled'] and row['reviewer'] == '合成本地审核人'
    p.disable_current(); assert not host.store.get_teaching_method('LM01')['enabled']


def test_catalog_scope_cannot_enable(host, monkeypatch):
    row = host.store.save_teaching_method(method(scope='catalog'), expected_version=0)
    p = panel(host); p.refresh(); p.select_method(row['id'])
    def reviewed(self):
        self.reviewer.setText('合成人员'); self.note.setPlainText('目录不足')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui().TeachingReviewDialog, 'exec', reviewed)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    p.enable_current()
    assert host.store.get_teaching_method(row['id'])['version'] == 1
    assert not host.store.get_teaching_method(row['id'])['enabled']


def test_failed_import_and_preview_cancel_preserve_dirty_text(host, tmp_path, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.steps.setPlainText('未完成\n步骤')
    path = tmp_path / 'methods.json'; path.write_text('{broken', encoding='utf8')
    p.import_file(path)
    assert p.steps.toPlainText() == '未完成\n步骤' and p.dirty
    path.write_text(json.dumps([method()], ensure_ascii=False), encoding='utf8')
    monkeypatch.setattr(ui().TeachingJsonPreviewDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    p.import_file(path)
    assert host.store.get_teaching_method('synthetic-ui') is None and p.dirty
    assert p.steps.toPlainText() == '未完成\n步骤'


def test_import_collision_uses_returned_id_and_export_strips_trust(host, tmp_path, monkeypatch):
    original = enabled(host); p = panel(host); p.refresh()
    path = tmp_path / 'methods.json'; path.write_text(json.dumps([original], ensure_ascii=False), encoding='utf8')
    monkeypatch.setattr(ui().TeachingJsonPreviewDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    p.import_file(path)
    assert p.current['id'] != original['id'] and p.current['version'] == 1
    assert not p.current['enabled'] and host.store.get_teaching_method(original['id'])['enabled']
    assert p.current['id'] in p.message.text()
    target = tmp_path / 'export.json'; p.export_file(target)
    rows = json.loads(target.read_text(encoding='utf8'))
    assert all('reviewer' not in r and 'enabled' not in r and 'version' not in r for r in rows)


def test_import_keeps_unsaved_current_even_after_success(host, tmp_path, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.name.setText('保留草稿')
    path = tmp_path / 'methods.json'; path.write_text(json.dumps([method()], ensure_ascii=False), encoding='utf8')
    monkeypatch.setattr(ui().TeachingJsonPreviewDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    p.import_file(path)
    assert p.current['id'] == 'LM01' and p.name.text() == '保留草稿' and p.dirty


def test_invalid_batch_and_oversized_import_are_atomic_and_keep_editor(host, tmp_path):
    p = panel(host); p.select_method('LM01'); p.checks.setPlainText('未保存的检查项')
    path = tmp_path / 'methods.json'
    path.write_text(json.dumps([method(), dict(method('bad'), owner_id='unknown')]), encoding='utf8')
    p.import_file(path); assert host.store.get_teaching_method('synthetic-ui') is None
    path.write_bytes(b' ' * (2*1024*1024+1)); p.import_file(path)
    assert p.dirty and p.checks.toPlainText() == '未保存的检查项' and '2 MB' in p.message.text()


def test_local_source_intake_preview_cancel_then_apply(host, tmp_path, monkeypatch):
    p = panel(host); p.select_method('LM01'); old = p.source_excerpt.toPlainText()
    path = tmp_path / 'source.txt'; path.write_text('本机合成片段', encoding='utf8')
    # Execute the real local reader synchronously, keeping the remote boundary unreachable.
    monkeypatch.setattr(host, 'start_job', lambda title, op, done: done(op(lambda x: None, threading.Event())))
    monkeypatch.setattr(ui().TeachingSourcePreviewDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    p.read_source([str(path)]); assert p.source_excerpt.toPlainText() == old
    monkeypatch.setattr(ui().TeachingSourcePreviewDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    p.read_source([str(path)])
    assert p.source_excerpt.toPlainText() == '本机合成片段' and p.dirty


def test_selection_dialog_validates_compare_and_persists(host):
    mod = ui(); d = mod.TeachingSelectionDialog(host.store, host)
    d.mode.setCurrentIndex(d.mode.findData('compare')); d.accept()
    assert d.result() != QDialog.DialogCode.Accepted
    for identity in ('limengyuan', 'fenbi', 'zhanzhang', 'bailu'):
        d.owner_checks[identity].setChecked(True)
    d.accept(); assert d.result() != QDialog.DialogCode.Accepted
    d.owner_checks['bailu'].setChecked(False); d.accept()
    assert d.result() == QDialog.DialogCode.Accepted
    assert host.store.get_setting('teaching_selection')['owner_ids'] == ['limengyuan', 'fenbi', 'zhanzhang']


def test_source_selection_and_off_are_preserved_without_extra_model_calls(host, monkeypatch):
    enabled(host); mod = ui(); host.load_example()
    d = mod.TeachingSelectionDialog(host.store, host); d.mode.setCurrentIndex(d.mode.findData('source'))
    d.owner_checks['limengyuan'].setChecked(True); d.accept()
    reopened = mod.TeachingSelectionDialog(host.store, host)
    assert reopened.mode.currentData() == 'source' and reopened.owner_checks['limengyuan'].isChecked()
    host.refresh_teaching_summary(); assert '李梦圆' in host.teaching_summary.text()
    captured = []
    class Service:
        def assess(self, submission, **kw): captured.append(submission); return 'synthetic result'
    monkeypatch.setattr(host, 'get_service', lambda **kw: Service())
    monkeypatch.setattr(host, 'start_job', lambda title, operation, done, **kwargs: operation(None, threading.Event()))
    host.begin_assessment()
    assert len(captured) == 1 and captured[0]['answer'] == host.answer_edit.toPlainText()
    reopened.mode.setCurrentIndex(reopened.mode.findData('off')); reopened.accept(); host.refresh_teaching_summary()
    assert host.teaching_summary.text() == '方法辅助：关闭'


def test_preflight_explicit_missing_source_before_credentials(host, monkeypatch):
    panel(host); host.load_example()
    host.store.set_setting('teaching_selection', {'mode': 'source', 'owner_ids': ['bailu']})
    calls = []; monkeypatch.setattr(host, 'get_service', lambda **kw: calls.append('credentials'))
    host.begin_assessment()
    assert calls == [] and not host.workers and '白鹭' in host.status_label.text()


def test_preflight_uses_actual_question_type_and_combined_notice(host, monkeypatch):
    enabled(host); panel(host); host.load_example(); host.question_type.setCurrentText('文章写作')
    host.store.set_setting('teaching_selection', {'mode': 'source', 'owner_ids': ['limengyuan']})
    calls = []; monkeypatch.setattr(host, 'get_service', lambda **kw: calls.append('credentials'))
    host.begin_assessment(); assert calls == []
    host.store.set_setting('teaching_selection', {'mode': 'combined', 'owner_ids': []})
    host.refresh_teaching_summary(); assert '无可用' in host.teaching_summary.text()


def test_busy_guards_mutations_selection_and_assessment_before_key(host, tmp_path, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.name.setText('仍未保存')
    host.workers.add(object()); host.refresh_teaching_busy()
    before = host.store.list_teaching_versions('LM01')
    p.save_current(); p.enable_current(); p.disable_current(); p.new_method(); p.import_file(tmp_path / 'missing.json')
    calls = []; monkeypatch.setattr(host, 'get_service', lambda **kw: calls.append('credentials'))
    host.begin_assessment(); host.show_teaching_selection()
    assert not calls and host.store.list_teaching_versions('LM01') == before
    assert p.name.text() == '仍未保存' and not p.save_button.isEnabled()
    assert not host.teaching_select_button.isEnabled()
    host.workers.clear(); host.refresh_teaching_busy()
    assert p.save_button.isEnabled() and host.teaching_select_button.isEnabled()


def test_external_job_worker_locks_controls_and_refresh_preserves_dirty(host, app):
    from shenlun.ui.workers import Job
    p = panel(host); p.select_method('LM01'); p.source_quote.setPlainText('待核对引文')
    release = threading.Event()
    job = Job(lambda progress, cancel: release.wait(5), host)
    # Independent evaluation joins the same set without calling MainWindow.start_job.
    host.workers.add(job); job.start()
    try:
        wait(app, lambda: not p.save_button.isEnabled() and not host.teaching_select_button.isEnabled())
        assert p.source_quote.toPlainText() == '待核对引文'
    finally:
        release.set(); job.wait(2000); host.workers.discard(job); job.deleteLater()
    host.refresh_methods(); wait(app, lambda: p.save_button.isEnabled())
    assert p.dirty and p.source_quote.toPlainText() == '待核对引文'


def test_declining_dirty_close_after_worker_cancel_restores_save_and_job_results(host, app, monkeypatch):
    p = panel(host); p.select_method('LM01'); p.name.setText('取消关闭后保留并保存')
    host.show(); release = threading.Event(); prompts = []
    def decline(*a, **kw):
        prompts.append(kw); return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', decline)
    job = host.start_job('合成等待任务', lambda progress, cancel: release.wait(5), lambda result: None)
    try:
        host.close()
        assert host._closing and job.cancel_event.is_set() and host.workers
    finally:
        release.set()
    wait(app, lambda: not host.workers and bool(prompts))
    assert host.isVisible() and not host._closing and p.dirty
    assert prompts[-1]['defaultButton'] == QMessageBox.StandardButton.No
    p.save_current()
    saved = host.store.get_teaching_method('LM01')
    assert saved['name'] == '取消关闭后保留并保存' and saved['version'] == 1 and not p.dirty
    results = []
    host.start_job('后续合成任务', lambda progress, cancel: '后续结果', results.append)
    wait(app, lambda: not host.workers)
    assert results == ['后续结果'] and not host._closing


def test_minimum_layout_keeps_manager_and_controls_in_view(host, app, tmp_path):
    p = panel(host); host.resize(1050, 720); host.show(); host.navigation.setCurrentRow(2)
    host.methods_tabs.setCurrentWidget(p); p.select_method('LM01'); app.processEvents()
    assert host.size().width() == 1050 and host.size().height() == 720
    assert p.width() <= 832 and p.save_button.isVisible() and p.tabs.isVisible()
    assert host.rect().contains(p.save_button.mapTo(host, p.save_button.rect().bottomRight()))
    assert host.grab().save(str(tmp_path / 'teaching-1050x720.png'))
