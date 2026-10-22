"""The evidence tab and the exported basis section read the same rows."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from shenlun.reports import export_review, review_html
from shenlun.storage import Store
from shenlun.ui import MainWindow


@pytest.fixture(scope='session')
def app(): return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(Store(tmp_path/'trace-ui.db'))
    w.show()
    yield w
    w.close(); app.processEvents()


def trace_review():
    return {'title': '便民服务', 'submission': {
                'question': '概括做法', 'materials': '社区增设电话报修，老人能顺利报修。',
                'answer': '社区增设电话报修。\n服务便民。'},
            'assessment': {'total_score': 16, 'max_score': 20, 'summary': '措施需要具体化',
                'next_action': '核对服务对象', 'uncertainties': ['是否包含报修渠道信息待确认'],
                'dimensions': [
                    {'id': 'coverage', 'name': '材料覆盖', 'score': 60, 'weight': 40, 'reason': '材料要点覆盖不全'},
                    {'id': 'expression', 'name': '组织表达', 'score': 80, 'weight': 20, 'reason': '表述基本准确'}],
                'annotations': [
                    {'start': 0, 'end': 8, 'quote': '社区增设电话报修', 'dimension': 'coverage',
                     'issue_type': '要点遗漏', 'issue': '未写清报修渠道带来的可及性', 'suggestion': '补出渠道与可及性',
                     'material_refs': []},
                    {'start': 9, 'end': 13, 'quote': '服务便民', 'dimension': 'expression',
                     'issue_type': '表述空泛', 'issue': '结论空泛', 'suggestion': '写出具体对象',
                     'material_refs': [{'id': 'M1', 'quote': '老人能顺利报修'}]}]},
            'revisions': {'minimal': '', 'model': '', 'status': '仅精批完成，未生成修改稿'}}


def test_trace_tab_groups_evidence_and_marks_pending(window):
    window.show_review(trace_review())
    tree = window.result_trace
    assert window.result_tabs.tabText(2) == '评分依据'
    assert tree.topLevelItemCount() == 2
    first = tree.topLevelItem(0)
    assert '材料覆盖' in first.text(0) and '得分60/100·权重40%' in first.text(0)
    assert '为什么是这个分' in first.text(1)
    assert first.text(2) == '待核实'
    assert first.childCount() == 1
    child = first.child(0)
    assert child.text(0).startswith('1. ') and '社区增设电话报修' in child.text(0)
    assert '要点遗漏' in child.text(1) and '建议：' in child.text(1)
    assert child.text(2).startswith('待核实')
    second = tree.topLevelItem(1)
    assert second.text(2) == ''
    assert second.child(0).text(2) == '已附材料引用'


def test_trace_rows_expand_and_keep_the_advice_column_readable(window):
    value = trace_review()
    value['assessment']['annotations'][0]['issue'] = '未写清渠道与适用条件。' * 12
    value['assessment']['annotations'][0]['suggestion'] = '补出渠道、对象和使用条件。' * 12
    window.show_review(value)
    tree = window.result_trace
    assert not tree.header().stretchLastSection()
    assert tree.columnWidth(1) > tree.columnWidth(2)
    child = tree.topLevelItem(0).child(0)
    assert child.sizeHint(0).height() > tree.fontMetrics().height() * 4


def test_double_clicking_evidence_jumps_back_to_the_quote(window):
    window.show_review(trace_review())
    child = window.result_trace.topLevelItem(0).child(0)
    window.open_trace_item(child)
    assert window.result_tabs.currentWidget() is window.annotated_result
    assert window.result_original.textCursor().selectedText() == '社区增设电话报修'


def test_unassigned_annotations_still_appear_with_a_clear_title(window):
    value = trace_review()
    value['assessment']['annotations'][0].pop('dimension')
    value['assessment']['annotations'][0]['issue_type'] = '其他'
    window.show_review(value)
    tree = window.result_trace
    assert tree.topLevelItemCount() == 3
    last = tree.topLevelItem(2)
    assert '未归属维度' in last.text(0) and '批注未对应本次量表维度' in last.text(0)
    assert last.childCount() == 1


def test_legacy_or_empty_report_renders_an_empty_tree(window):
    window.show_review({'submission': {'answer': '旧原答'}, 'assessment': {}, 'revisions': {}})
    assert window.result_trace.topLevelItemCount() == 0


def test_report_basis_section_sits_before_annotations():
    html = review_html(trace_review())
    assert 'id="score-basis"' in html
    assert html.index('评分依据') < html.index('逐处批注')
    assert '待核实' in html and '要点遗漏' in html


def test_export_carries_recheck_results_into_html_and_json(tmp_path):
    rechecks = {0: {'decision': 'uphold', 'reason': '材料引用成立', 'created_at': '2026-10-22 10:00'}}
    page, data = tmp_path/'report.html', tmp_path/'report.json'
    export_review(trace_review(), page, rechecks)
    export_review(trace_review(), data, rechecks)
    assert '维持原判断' in page.read_text(encoding='utf8')
    payload = json.loads(data.read_text(encoding='utf8'))
    assert payload['recheck_results']['0']['decision'] == 'uphold'


def test_annotations_are_checkable_and_gate_the_recheck_button(window):
    window.show_review(trace_review())
    item = window.annotation_list.item(0)
    assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert item.checkState() == Qt.CheckState.Unchecked
    assert not window.recheck_button.isEnabled()
    item.setCheckState(Qt.CheckState.Checked)
    assert not window.recheck_button.isEnabled()  # an unsaved review has no id
    assert window.checked_annotations() == [0]
    item.setCheckState(Qt.CheckState.Unchecked)
    assert window.checked_annotations() == []


def test_saved_verdicts_show_as_badges_and_trace_column(window):
    review = trace_review()
    review['id'] = window.store.save_review(review)
    window.store.save_review_recheck(review['id'], {'verdicts': {
        '0': {'index': 0, 'decision': 'revise', 'reason': '建议换用材料原词',
              'suggested_suggestion': '写出报修渠道'}}})
    current = window.store.get_review(review['id'])
    window.show_review(current)
    item = window.annotation_list.item(0)
    assert '〔复核：建议修正〕' in item.text()
    child = window.result_trace.topLevelItem(0).child(0)
    assert child.text(2).startswith('建议修正')
    assert '建议换用材料原词' in child.text(2)
    item.setCheckState(Qt.CheckState.Checked)
    assert window.recheck_button.isEnabled()
    assert '复核所选批注（1）' in window.recheck_button.text()


def test_result_controls_fit_the_minimum_window(window, app, tmp_path):
    window.resize(1050, 720)
    window.show()
    window.show_review(trace_review(), navigate=False)
    window.assessment_tabs.setCurrentIndex(2)
    window.result_tabs.setCurrentWidget(window.result_trace)
    app.processEvents()
    assert window.result_trace.isVisible()
    for control in (window.recheck_button, window.generate_revision_button,
                    window.export_report_button, window.export_pdf_button,
                    window.add_review_goals_button):
        assert control.isVisibleTo(window)
        assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
    assert window.annotation_list.width() <= 310
    assert window.grab().save(str(tmp_path / 'result-trace-1050x720.png'))


def test_declining_the_recheck_confirmation_starts_nothing(window, monkeypatch):
    review = trace_review()
    review['id'] = window.store.save_review(review)
    window.show_review(window.store.get_review(review['id']))
    window.annotation_list.item(0).setCheckState(Qt.CheckState.Checked)
    seen = {}

    def refuse(parent, title, text):
        seen['title'], seen['text'] = title, text
        return False

    monkeypatch.setattr('shenlun.ui.common.confirm_billable', refuse)
    window.recheck_selected()
    assert seen['title'] == '确认复核所选批注'
    assert '1 条批注' in seen['text'] and '不改动' in seen['text']
    assert not window.workers
