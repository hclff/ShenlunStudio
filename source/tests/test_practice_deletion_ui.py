"""Deletion UI uses real Qt and isolated local records, never model services."""
import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    host = MainWindow(Store(tmp_path / 'deletion-ui.db'))
    yield host
    host.workers.clear()
    host._closing = False
    host.close()
    app.processEvents()


def submission(question='删除测试甲'):
    return {'question':question, 'materials':'服务站开通电话并及时回访。',
            'answer':'尚未完成的原答', 'question_type':'归纳概括', 'exam':'国考',
            'max_score':20, 'word_limit':150}


def saved_review(window, question='删除测试甲'):
    return window.store.save_review({'title':question, 'submission':submission(question),
        'assessment':{'total_score':15, 'max_score':20, 'annotations':[]},
        'revisions':{'minimal':'修改稿', 'model':'参考稿'}, 'sources':[]})


def select_id(listing, identifier):
    for index in range(listing.count()):
        if listing.item(index).data(Qt.ItemDataRole.UserRole) == identifier:
            listing.setCurrentRow(index)
            # Opening a report refreshes the list, invalidating its old Qt item.
            return listing.currentItem()
    raise AssertionError('fixture record not listed')


def accept(monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)


def test_lists_expose_separate_checkbox_and_detail_selection(window):
    rid = saved_review(window)
    window.refresh_history()
    item = select_id(window.review_list, rid)
    assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert item.checkState() == Qt.CheckState.Unchecked
    window.set_practice_checks('reviews', True)
    assert window.checked_practice_ids('reviews') == [rid]
    assert window.current_review['id'] == rid
    assert '1' in window.review_checked_label.text()
    assert window.review_bulk_delete_button.isEnabled()
    window.set_practice_checks('reviews', False)
    assert not window.review_bulk_delete_button.isEnabled()


def test_cancel_is_default_and_keeps_database_and_checks(window, monkeypatch):
    rid = saved_review(window)
    window.refresh_history()
    select_id(window.review_list, rid)
    window.set_practice_checks('reviews', True)
    calls = []
    def cancel(*args, **kwargs):
        calls.append((args, kwargs))
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    window.delete_practice_selection('reviews', checked=True)
    assert window.store.get_review(rid)
    assert window.checked_practice_ids('reviews') == [rid]
    assert calls[0][0][-1] == QMessageBox.StandardButton.No
    assert '无法撤销' in calls[0][0][2]


def test_bulk_review_deletion_retains_questions_clears_open_report(window, monkeypatch):
    ids = [saved_review(window, '删除测试甲'), saved_review(window, '删除测试乙')]
    qids = [window.store.question_for_review(rid) for rid in ids]
    window.store.save_human_score(ids[0], 14)
    window.refresh_history()
    select_id(window.review_list, ids[0])
    window.set_practice_checks('reviews', True)
    accept(monkeypatch)
    window.delete_practice_selection('reviews', checked=True)
    assert all(window.store.get_review(rid) is None for rid in ids)
    assert all(window.store.get_question(qid) for qid in qids)
    assert window.current_review is None
    assert not window.result_report.toPlainText()
    assert not window.result_original.toPlainText()
    assert not window.result_minimal.toPlainText()
    assert not window.result_model.toPlainText()
    assert not window.export_report_button.isEnabled()
    assert not window.export_pdf_button.isEnabled()
    assert not window.export_history_button.isEnabled()
    assert window.store.get_human_score(ids[0]) is None
    assert window.review_list.count() == 0
    assert '0' in window.review_checked_label.text()


def test_single_deletion_ignores_other_checked_record(window, monkeypatch):
    first = saved_review(window, '删除测试甲')
    second = saved_review(window, '删除测试乙')
    window.refresh_history()
    select_id(window.review_list, second).setCheckState(Qt.CheckState.Checked)
    select_id(window.review_list, first)
    accept(monkeypatch)
    window.delete_practice_selection('reviews')
    assert window.store.get_review(first) is None
    assert window.store.get_review(second)


def test_question_delete_keeps_report_editor_and_autosave_detached(window, monkeypatch):
    rid = saved_review(window)
    qid = window.store.question_for_review(rid)
    assert window.open_question(qid)
    window.answer_edit.setPlainText('删除题目时仍需保留的草稿')
    window.refresh_questions()
    select_id(window.question_list, qid)
    accept(monkeypatch)
    window.delete_practice_selection('questions')
    assert window.store.get_question(qid) is None
    assert window.store.get_review(rid)
    assert window.current_question_id is None
    assert window.answer_edit.toPlainText() == '删除题目时仍需保留的草稿'
    assert window.store.load_workspace()['question_id'] is None
    window.answer_edit.setPlainText('继续编辑，不自动重建题目')
    assert window.flush_workspace()
    assert window.store.get_question(qid) is None
    assert window.store.load_question_draft(qid) is None
    assert window.question_list.count() == 0
    assert not window.attempt_list.count()
    assert not window.comparison_browser.toPlainText()


def test_question_filter_clears_checks_same_refresh_keeps_checks(window):
    first = window.store.save_question(submission('独有甲'))
    window.store.save_question(submission('独有乙'))
    window.refresh_questions()
    window.set_practice_checks('questions', True)
    window.refresh_questions()
    assert len(window.checked_practice_ids('questions')) == 2
    window.question_search.setText('独有甲')
    assert window.question_list.count() == 1
    assert window.checked_practice_ids('questions') == []
    window.set_practice_checks('questions', True)
    assert window.checked_practice_ids('questions') == [first]
    window.question_search.clear()
    assert window.checked_practice_ids('questions') == []


def test_busy_closing_empty_and_post_confirmation_busy_do_not_delete(window, monkeypatch):
    rid = saved_review(window)
    window.refresh_history()
    select_id(window.review_list, rid)
    prompts = []
    def prompt(*args, **kwargs):
        prompts.append(args)
        window.workers.add(object())
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', prompt)
    window.delete_practice_selection('reviews', checked=True)
    assert not prompts
    window.workers.add(object())
    window.refresh_practice_delete_actions()
    assert not window.review_delete_button.isEnabled()
    window.delete_practice_selection('reviews')
    assert not prompts
    window.workers.clear()
    window._closing = True
    window.delete_practice_selection('reviews')
    assert not prompts
    window._closing = False
    window.delete_practice_selection('reviews')
    assert len(prompts) == 1
    assert window.store.get_review(rid)


def test_busy_poll_refreshes_only_when_worker_state_changes(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, 'refresh_teaching_busy', lambda: calls.append('teaching'))
    monkeypatch.setattr(window, 'refresh_practice_delete_actions', lambda: calls.append('practice'))
    window._last_busy_state = False
    window._sync_busy_state()
    assert calls == []
    window.workers.add(object())
    window._sync_busy_state()
    assert calls == ['teaching', 'practice']
    calls.clear()
    window._sync_busy_state()
    assert calls == []
    window.workers.clear()
    window._sync_busy_state()
    assert calls == ['teaching', 'practice']


def test_stale_data_conflict_is_reported_without_partial_delete(window, monkeypatch):
    rid = saved_review(window)
    window.refresh_history()
    select_id(window.review_list, rid)
    def mutate(*args, **kwargs):
        window.store.save_human_score(rid, 13)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', mutate)
    window.delete_practice_selection('reviews')
    assert window.store.get_review(rid)
    assert '删除' in window.status_label.text()
    assert any(word in window.status_label.text() for word in ('变化', '更改', '刷新', '失败'))


def test_deleted_history_never_falls_back_to_current_export(window, monkeypatch):
    rid = saved_review(window)
    window.show_review(window.store.get_review(rid))
    window.refresh_history()
    window.review_list.setCurrentRow(-1)
    calls = []
    monkeypatch.setattr(window, 'export_current', lambda: calls.append(True))
    window.export_history()
    assert calls == []


def test_external_deletion_during_confirmation_clears_stale_report(window, monkeypatch):
    rid = saved_review(window)
    window.refresh_history()
    select_id(window.review_list, rid)
    def remove_elsewhere(*args, **kwargs):
        other = Store(window.store.path)
        preview = other.prepare_practice_deletion('reviews', [rid])
        other.delete_practice_items('reviews', [rid], expected_token=preview['token'])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', remove_elsewhere)
    window.delete_practice_selection('reviews')
    assert window.current_review is None
    assert not window.export_report_button.isEnabled()
    assert not window.review_list.count()
    assert '删除' in window.status_label.text()
