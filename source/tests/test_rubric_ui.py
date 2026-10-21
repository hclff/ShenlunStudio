"""Offline rubric controls against real Qt, domain resolver and isolated SQLite."""
import json
import copy
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTextBrowser

from shenlun.storage import Store
from shenlun.domain import prepare_submission
from shenlun.ui import MainWindow


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    value = MainWindow(Store(tmp_path / 'rubric-ui.db'))
    yield value
    value.close()
    app.processEvents()


def select(window, key):
    index = window.rubric_profile.findData(key)
    assert index >= 0
    window.rubric_profile.setCurrentIndex(index)


def custom_text():
    return '[{"id":"mine","name":"<自定义维度>","weight":100,"description":"检查 <原文> & 对象"}]'


def test_auto_preview_changes_actual_weights_with_category_and_question_type(window):
    assert window.rubric_profile.currentData() == 'auto'
    window.exam.setCurrentText('江苏省考')
    window.year.setText('2026')
    window.category.setText('A类')
    first = window.rubric_preview()
    assert first['metadata']['profile'] == 'jiangsu_a'
    assert first['rubric'][1]['weight'] == 35
    window.category.setText('C类')
    second = window.rubric_preview()
    assert second['metadata']['profile'] == 'jiangsu_c'
    assert second['rubric'][1]['weight'] == 40
    window.question_type.setCurrentText('文章写作')
    third = window.rubric_preview()
    assert len(third['rubric']) == 6
    assert third['rubric'][2]['weight'] == 20
    assert '文章写作' in window.rubric_summary.text()
    assert 'C类' in window.rubric_summary.text()
    window.answer_edit.setPlainText('我的原答')
    actual = window.submission()
    assert actual['rubric_template']['profile'] == 'jiangsu_c'
    assert actual['rubric'][2]['weight'] == 20


def test_custom_json_survives_switches_and_is_actual_submission_and_preview(window):
    text = custom_text()
    window.rubric_edit.setPlainText(text)
    window.answer_edit.setPlainText('我保留的原答')
    window.category.setText('C类')
    window.question_type.setCurrentText('应用写作')
    select(window, 'national_law')
    assert window.rubric_edit.toPlainText() == text
    assert window.rubric_preview()['rubric'][0]['id'] == 'mine'
    assert '用户' in window.rubric_preview()['metadata']['notice']
    assert '维度、权重及分档由软件设计' not in window.rubric_preview()['metadata']['notice']
    assert '自定义' in window.rubric_summary.text()
    submission = window.submission()
    assert submission['rubric_template']['kind'] == 'custom'
    assert submission['rubric'][0]['weight'] == 100
    assert submission['rubric_selection'] == 'national_law'
    assert submission['answer'] == '我保留的原答'
    dialog = window.build_rubric_preview_dialog()
    browser = dialog.findChild(QTextBrowser, 'rubric_preview_browser')
    assert '<自定义维度>' in browser.toPlainText()
    assert '检查 <原文> & 对象' in browser.toPlainText()
    assert '100%' in browser.toPlainText()
    assert '<自定义维度>' not in browser.toHtml()
    dialog.deleteLater()


def test_selection_autosaves_and_survives_restart_bank_and_new_attempt(window, app):
    window.question_edit.setPlainText('概括服务做法')
    window.materials_edit.setPlainText('开通电话报修。')
    window.answer_edit.setPlainText('原来的草稿')
    select(window, 'jiangsu_c')
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        saved = window.store.load_workspace()
        if saved and saved['submission'].get('rubric_selection') == 'jiangsu_c':
            break
        QTest.qWait(20)
    assert saved['submission']['rubric_selection'] == 'jiangsu_c'
    restarted = MainWindow(window.store)
    try:
        assert restarted.rubric_profile.currentData() == 'jiangsu_c'
        assert restarted.answer_edit.toPlainText() == '原来的草稿'
    finally:
        restarted.close()
    window.save_current_question()
    qid = window.current_question_id
    assert qid
    assert window.store.get_question(qid)['submission']['rubric_selection'] == 'jiangsu_c'
    select(window, 'generic')
    assert window.open_question(qid)
    assert window.rubric_profile.currentData() == 'generic'
    assert window.new_question_attempt(qid)
    assert window.rubric_profile.currentData() == 'jiangsu_c'
    assert window.answer_edit.toPlainText() == ''
    assert any(item['submission']['answer'] == '原来的草稿'
               for item in window.store.list_workspace_history())


def test_invalid_json_and_selection_survive_raw_autosave_restart(window, app):
    text = '  [{"id":\n'
    window.rubric_edit.setPlainText(text)
    select(window, 'national_local')
    assert '未完成' in window.rubric_summary.text()
    assert window.flush_workspace()
    saved = window.store.load_workspace()['submission']
    assert saved['rubric_text'] == text
    assert saved['rubric_selection'] == 'national_local'
    another = MainWindow(window.store)
    try:
        assert another.rubric_edit.toPlainText() == text
        assert another.rubric_profile.currentData() == 'national_local'
        assert '未完成' in another.rubric_summary.text()
        dialog = another.build_rubric_preview_dialog()
        preview = dialog.findChild(QTextBrowser, 'rubric_preview_browser').toPlainText()
        assert '没有可用于批改的实际量表' in preview
        assert '合计 100%' not in preview
        dialog.deleteLater()
        with pytest.raises(ValueError):
            another.raw_submission()
    finally:
        another.close()


def test_summary_and_preview_explain_missing_category_and_annual_boundary(window):
    window.exam.setCurrentText('江苏省考')
    window.category.clear()
    assert '通用' in window.rubric_summary.text()
    assert '类别' in window.rubric_summary.text()
    window.category.setText('B类')
    window.year.setText('2027')
    assert '2026' in window.rubric_summary.text()
    assert '2027' in window.rubric_summary.text()
    assert '未核验' in window.rubric_summary.text()
    dialog = window.build_rubric_preview_dialog()
    browser = dialog.findChild(QTextBrowser, 'rubric_preview_browser')
    text = browser.toPlainText()
    for expected in ('2026', '2027', '软件', '权重', 'jsxf.gov.cn', '依法办事'):
        assert expected in text
    assert not browser.openExternalLinks()
    assert not browser.openLinks()
    dialog.deleteLater()


def test_explicit_copy_requires_overwrite_consent_and_does_not_clear_other_fields(window, monkeypatch):
    text = custom_text()
    window.rubric_edit.setPlainText(text)
    window.answer_edit.setPlainText('不可覆盖的原答')
    select(window, 'jiangsu_a')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    window.copy_rubric_template()
    assert window.rubric_edit.toPlainText() == text
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.copy_rubric_template()
    rows = json.loads(window.rubric_edit.toPlainText())
    assert rows[1]['weight'] == 35
    assert window.answer_edit.toPlainText() == '不可覆盖的原答'
    assert '自定义' in window.rubric_summary.text()


def test_restore_actual_provided_rubric_and_default_legacy_selection(window):
    value = {'question':'题目', 'answer':'原答', 'rubric':json.loads(custom_text())}
    select(window, 'national_law')
    assert window.load_submission(value)
    assert window.rubric_profile.currentData() == 'auto'
    assert json.loads(window.rubric_edit.toPlainText()) == value['rubric']
    assert '自定义' in window.rubric_summary.text()


def test_minimum_size_keeps_controls_and_preview_readable(window, app, tmp_path):
    window.resize(1050, 720)
    window.show()
    window.assessment_tabs.setCurrentIndex(1)
    window.category.setText('C类')
    window.year.setText('2027')
    app.processEvents()
    assert window.width() == 1050 and window.height() == 720
    assert not window.rubric_summary.isVisible()
    assert not any(label.isVisible() and '参考答案用于训练对照' in label.text()
                   for label in window.findChildren(QLabel))
    for control in (window.rubric_profile, window.rubric_preview_button,
                    window.rubric_copy_button, window.rubric_table, window.reference_edit):
        assert control.isVisible()
        assert control.width() > 80 and control.height() >= 25
        assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
    assert not window.rubric_edit.isVisible()
    assert window.rubric_table.height() >= 100
    assert window.reference_edit.height() >= 120
    dialog = window.build_rubric_preview_dialog()
    dialog.show()
    app.processEvents()
    browser = dialog.findChild(QTextBrowser, 'rubric_preview_browser')
    assert dialog.width() <= 1000 and dialog.height() <= 680
    assert browser.height() >= 300
    assert browser.verticalScrollBar().maximum() > 0
    assert '2026' in browser.toPlainText()
    assert window.grab().save(str(tmp_path / 'advanced-min.png'))
    assert dialog.grab().save(str(tmp_path / 'preview-min.png'))
    dialog.close()


def test_paused_workspace_disables_picker_and_copy(window):
    window.set_workspace_paused(True)
    assert not window.rubric_profile.isEnabled()
    assert not window.rubric_copy_button.isEnabled()
    window.set_workspace_paused(False)
    assert window.rubric_profile.isEnabled()
    assert window.rubric_copy_button.isEnabled()


def test_selection_change_cannot_bypass_failed_save_before_replacing_draft(window, monkeypatch):
    window.answer_edit.setPlainText('不能丢失的原答')
    window.rubric_edit.setPlainText(custom_text())
    select(window, 'jiangsu_b')
    def failed(*args, **kwargs):
        raise OSError('磁盘写入失败')
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    assert not window.load_submission({'question':'另一题', 'answer':''})
    assert window.answer_edit.toPlainText() == '不能丢失的原答'
    assert window.rubric_edit.toPlainText() == custom_text()
    assert window.rubric_profile.currentData() == 'jiangsu_b'
    assert window._workspace_dirty
    assert '保存失败' in window.autosave_label.text()


def builtin_submission():
    return prepare_submission({'question':'概括服务做法', 'materials':'服务站开通电话报修。',
                               'answer':'开通报修。', 'question_type':'归纳概括', 'exam':'江苏省考',
                               'year':'2026', 'category':'C类', 'max_score':20})


def test_review_created_bank_question_reopens_builtin_without_relabeling_custom(window):
    saved = builtin_submission()
    window.store.save_review({'title':'离线训练', 'submission':saved,
                              'assessment':{'total_score':15, 'max_score':20}})
    question = window.store.list_questions()[0]
    assert window.new_question_attempt(question['id'])
    assert window.rubric_edit.toPlainText() == ''
    window.answer_edit.setPlainText('新一轮原答')
    actual = window.submission()
    assert actual['rubric_template']['kind'] == 'builtin'
    assert actual['rubric_template']['profile'] == 'jiangsu_c'
    assert actual['rubric'][1]['weight'] == 40
    assert window.flush_workspace()
    assert window.open_question(question['id'])
    assert window.submission()['rubric_template']['kind'] == 'builtin'


@pytest.mark.parametrize('mutation', ['old_version', 'edited_description', 'edited_weight'])
def test_changed_builtin_provenance_or_actual_rubric_is_kept_as_custom_json(window, mutation):
    saved = copy.deepcopy(builtin_submission())
    if mutation == 'old_version':
        saved['rubric_template']['version'] = 'old-template-0'
    elif mutation == 'edited_description':
        saved['rubric'][0]['description'] = '以前版本实际使用的说明'
    else:
        saved['rubric'][0]['weight'] += 1
        saved['rubric'][1]['weight'] -= 1
    assert window.apply_workspace({'submission':{**saved, 'rubric_text':''}, 'question_id':None})
    assert json.loads(window.rubric_edit.toPlainText()) == saved['rubric']
    actual = window.submission()
    assert actual['rubric_template']['kind'] == 'custom'
    assert actual['rubric'] == saved['rubric']


def test_unfinished_raw_text_wins_over_previously_saved_builtin_rubric(window):
    saved = builtin_submission()
    saved['rubric_text'] = '  [{"id":\n'
    assert window.apply_workspace({'submission':saved, 'question_id':None})
    assert window.rubric_edit.toPlainText() == '  [{"id":\n'
    assert '未完成' in window.rubric_summary.text()


def test_unsupported_saved_selection_is_retained_until_user_repairs_it(window):
    value = {'question':'草稿', 'answer':'原答', 'rubric_selection':'future-profile'}
    assert window.apply_workspace({'submission':value, 'question_id':None})
    assert window.rubric_profile.currentData() == 'future-profile'
    assert '不支持' in window.rubric_summary.text()
    assert window.store.load_workspace()['submission']['rubric_selection'] == 'future-profile'
    with pytest.raises(ValueError):
        window.submission()
    select(window, 'auto')
    assert window.submission()['rubric_template']['kind'] == 'builtin'
