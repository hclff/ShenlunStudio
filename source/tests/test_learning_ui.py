"""Actual Qt learning views and local plan editors, without remote services."""
import importlib

import pytest
from PySide6.QtCore import QDate, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_review_learning import reviewed
from test_practice_store import fields


@pytest.fixture(scope='session')
def app(): return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(Store(tmp_path/'learning-ui.db'))
    w.show()
    yield w
    w.close(); app.processEvents()


def test_report_summary_opens_exact_existing_annotation_and_diff_switches_revision(window, app):
    value = reviewed(); window.show_review(value)
    assert window.result_tabs.currentWidget() is window.result_summary
    assert '16' in window.result_summary.toPlainText() and '优先改进' in window.result_summary.toPlainText()
    assert '优先改进' in window.result_report.toPlainText()
    window.open_result_link(QUrl('#learning-annotation-0'))
    assert window.result_tabs.currentWidget() is window.annotated_result
    assert window.result_original.textCursor().selectedText() == '开通电话。'
    assert window.revision_diff_list.count() == 2
    window.revision_diff_list.setCurrentRow(0)
    text = window.revision_diff_browser.toPlainText()
    assert '电话预约' in text and '补充预约用途' in text
    window.revision_diff_kind.setCurrentIndex(1)
    assert '保留人工窗口' in window.revision_diff_browser.toPlainText()
    assert window.current_review == value


def test_missing_revision_and_legacy_report_have_no_fabricated_diff(window):
    window.show_review({'submission':{'answer':'旧原答'},'assessment':{},'revisions':{}})
    assert window.revision_diff_list.count() == 0
    assert '尚未生成' in window.revision_diff_browser.toPlainText()
    assert '暂无' in window.result_report.toPlainText()


def test_actual_rich_text_diff_renders_additions_green_and_deletions_red(window):
    value = reviewed(); value['revisions']['minimal'] = '开通预约。\n服务便民。'
    window.show_review(value); window.revision_diff_list.setCurrentRow(0)
    document = window.revision_diff_browser.document()
    added = document.find('预约').charFormat()
    removed = document.find('电话').charFormat()
    assert added.background().color() == QColor('#e4f5ee')
    assert removed.background().color() == QColor('#fff0ee')


def test_plan_editor_save_updates_list_and_preserves_existing_grade(window, app):
    ui = importlib.import_module('shenlun.ui.review_plans')
    qid = window.store.save_question(fields())
    dialog = ui.ReviewPlanDialog(window.store, qid, window)
    dialog.status.setCurrentText('复习中')
    dialog.scheduled.setChecked(True); dialog.due_date.setDate(QDate(2026,10,22))
    dialog.tags.setText('遗漏要点，逻辑混乱')
    dialog.save()
    assert dialog.result() == dialog.DialogCode.Accepted
    saved = window.store.get_question(qid)['review_plan']
    assert saved['due_date'] == '2026-10-22' and saved['tags'] == ['遗漏要点','逻辑混乱']
    listing = ui.ReviewListDialog(window)
    listing.status_filter.setCurrentText('复习中')
    assert listing.items.count() == 1
    listing.search.setText('找不到的标签')
    assert listing.items.count() == 0
    listing.reject()


def test_plan_editor_conflict_preserves_unsaved_changes_and_cancel_is_guarded(window, monkeypatch):
    ui = importlib.import_module('shenlun.ui.review_plans')
    qid = window.store.save_question(fields())
    dialog = ui.ReviewPlanDialog(window.store, qid, window)
    dialog.tags.setText('尚未保存')
    window.store.save_review_plan(qid, {'status':'待复习','due_date':'','tags':['另一个窗口']}, expected_version=0)
    dialog.save()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert dialog.tags.text() == '尚未保存' and '更新' in dialog.message.text()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    dialog.reject()
    assert dialog.tags.text() == '尚未保存'
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    dialog.reject()


def test_summary_export_links_are_local_anchors_not_unregistered_app_urls():
    from shenlun.reports import review_html
    html = review_html(reviewed())
    assert 'href="#learning-annotation-0"' in html
    assert 'id="learning-annotation-0"' in html
    assert 'learning://' not in html
