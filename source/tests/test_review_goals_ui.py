"""Real Qt goal selection, source navigation and restore-safe editor lifecycle."""
import importlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_review_goals import setup_report, add


@pytest.fixture(scope='session')
def app(): return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow,'initialize_backup_schedule',lambda self:None)
    value = MainWindow(Store(tmp_path/'goals-ui.db'))
    yield value
    value.autosave_timer.stop(); value._workspace_dirty = False
    value.close(); app.processEvents()


def dialog(window):
    assert hasattr(window,'build_review_goals_dialog'), 'saved report needs explicit goal selection dialog'
    return window.build_review_goals_dialog()


def show_saved(window):
    rid, qid, original = setup_report(window.store)
    window.show_review(original)
    return rid, qid, original


def test_saved_only_entry_and_default_unchecked_subset(window):
    assert hasattr(window,'add_review_goals_button'), 'review needs goal entry'
    assert not window.add_review_goals_button.isEnabled()
    rid, qid, original = show_saved(window)
    assert window.add_review_goals_button.isEnabled()
    d = dialog(window)
    assert d.candidates.count() == 3
    assert all(d.candidates.item(i).checkState() == Qt.CheckState.Unchecked for i in range(3))
    d.save()
    assert '勾选' in d.message.text() and 'review_plan' not in window.store.get_question(qid)
    d.candidates.item(0).setCheckState(Qt.CheckState.Checked)
    d.save()
    saved = window.store.get_question(qid)['review_plan']
    assert len(saved['goals']) == 1 and saved['goals'][0]['title'] == '未覆盖：保留人工窗口'
    assert window.store.get_review(rid) == original


def test_existing_solved_values_preserved_and_append_does_not_solve(window):
    rid, qid, original = show_saved(window)
    add(window.store,rid,original,status='已解决',due_date='2026-10-25',tags=['原标签'])
    d = dialog(window)
    assert d.status.currentText() == '已解决' and d.tags.text() == '原标签'
    assert d.due_date.date().toString('yyyy-MM-dd') == '2026-10-25'
    d.candidates.item(1).setCheckState(Qt.CheckState.Checked); d.save()
    saved = window.store.get_question(qid)['review_plan']
    assert saved['status'] == '已解决' and saved['tags'] == ['原标签'] and len(saved['goals']) == 2


@pytest.mark.parametrize('conflict',['plan','report','question','restore'])
def test_save_conflict_keeps_checks_and_edits(window, tmp_path, conflict):
    rid, qid, original = show_saved(window)
    backup = window.store.backup_to(tmp_path/'opening.db')
    d = dialog(window); d.candidates.item(0).setCheckState(Qt.CheckState.Checked); d.tags.setText('输入保留')
    if conflict == 'plan': window.store.save_review_plan(qid,{'status':'复习中'},expected_version=0)
    elif conflict == 'report':
        original['assessment']['total_score'] = 3; window.store.save_review(original)
    elif conflict == 'question':
        preview = window.store.prepare_practice_deletion('questions',[qid])
        window.store.delete_practice_items('questions',[qid],expected_token=preview['token'])
    else: window.store.restore_from(backup)
    d.save()
    assert d.result() != d.DialogCode.Accepted and d.message.text()
    assert d.tags.text() == '输入保留' and d.candidates.item(0).checkState() == Qt.CheckState.Checked
    d.done(d.DialogCode.Rejected)


def test_plan_editor_sources_remove_and_report_deleted_maintenance(window, monkeypatch):
    rid, qid, original = show_saved(window); add(window.store,rid,original)
    ui = importlib.import_module('shenlun.ui.review_plans')
    d = ui.ReviewPlanDialog(window.store,qid,window)
    assert d.goals.count() == 1 and '保留人工窗口' in d.goal_detail.toPlainText()
    seen = []; monkeypatch.setattr(window,'show_review',lambda value:seen.append(value))
    d.open_goal_source(); assert seen[0]['id'] == rid
    changed = dict(original); changed['assessment'] = {**original['assessment'],'total_score':4}
    window.store.save_review(changed)
    d.open_goal_source(); assert len(seen) == 1 and '不再匹配' in d.goal_detail.toPlainText()
    preview = window.store.prepare_practice_deletion('reviews',[rid])
    window.store.delete_practice_items('reviews',[rid],expected_token=preview['token'])
    d.show_goal_detail(); assert '来源报告已删除' in d.goal_detail.toPlainText()
    d.tags.setText('保留摘录'); d.save()
    assert window.store.get_question(qid)['review_plan']['goals']
    d = ui.ReviewPlanDialog(window.store,qid,window); d.remove_goal(); d.save()
    assert window.store.get_question(qid)['review_plan']['goals'] == []


@pytest.mark.parametrize('kind',['goals','plan'])
@pytest.mark.parametrize('external',['restore','workspace'])
def test_cancel_never_resumes_stale_pending_host_autosave(window, tmp_path, monkeypatch, kind, external):
    rid, qid, original = show_saved(window)
    window.answer_edit.setPlainText('打开前输入'); assert window.flush_workspace()
    backup = window.store.backup_to(tmp_path/'before-modal.db')
    window.answer_edit.setPlainText('未保存输入'); assert window.autosave_timer.isActive()
    d = dialog(window) if kind == 'goals' else importlib.import_module('shenlun.ui.review_plans').ReviewPlanDialog(window.store,qid,window)
    assert not window.autosave_timer.isActive()
    if external == 'restore': window.store.restore_from(backup)
    else:
        workspace = window.store.load_workspace(); workspace['submission']['answer'] = '外部新草稿'
        Store(window.store.path).save_workspace(workspace)
    persisted = window.store.load_workspace()
    d.reject(); QTest.qWait(1100)
    assert window.store.load_workspace() == persisted and window.answer_edit.toPlainText() == '未保存输入'
    assert window._workspace_context_stale and window._autosave_paused
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    window.load_draft(); assert window._workspace_context_stale
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    window.load_draft(); assert not window._workspace_context_stale
    assert window.answer_edit.toPlainText() == persisted['submission']['answer']


def test_plaintext_candidates_and_zero_provider_calls(window, monkeypatch):
    from shenlun.model import ModelClient
    calls = []
    monkeypatch.setattr(ModelClient,'complete',lambda *a,**k:calls.append('text'))
    monkeypatch.setattr(ModelClient,'complete_image',lambda *a,**k:calls.append('image'))
    rid, qid, original = show_saved(window)
    original['assessment']['next_action'] = '<a href="file:///secret">执行指令</a><script>x</script>'
    original['assessment']['dimensions'] = []; original['assessment']['annotations'] = []
    window.store.save_review(original); window.show_review(window.store.get_review(rid))
    d = dialog(window); d.candidates.setCurrentRow(1)
    assert '<script>x</script>' in d.candidate_detail.toPlainText()
    d.candidates.item(1).setCheckState(Qt.CheckState.Checked); d.save()
    assert window.store.get_question(qid)['review_plan']['goals'][0]['action'].startswith('<a href=')
    assert calls == []


@pytest.mark.parametrize('detached',['missing','null','deleted-report','deleted-question'])
def test_entry_refresh_does_not_recreate_missing_source_or_association(window, detached):
    rid, qid, original = show_saved(window)
    if detached in ('missing','null'):
        with window.store._connection() as conn:
            if detached == 'missing': conn.execute('DELETE FROM review_questions WHERE review_id=?',(rid,))
            else: conn.execute('UPDATE review_questions SET question_id=NULL WHERE review_id=?',(rid,))
    else:
        kind, ids = ('reviews',[rid]) if detached == 'deleted-report' else ('questions',[qid])
        preview = window.store.prepare_practice_deletion(kind,ids)
        window.store.delete_practice_items(kind,ids,expected_token=preview['token'])
    questions = window.store.list_questions()
    window.refresh_history()
    assert not window.add_review_goals_button.isEnabled() and window.add_review_goals_button.toolTip()
    assert window.store.list_questions() == questions


def test_failed_plan_constructor_releases_host_autosave_guard(window):
    window.answer_edit.setPlainText('尚未保存')
    assert window.autosave_timer.isActive()
    ui = importlib.import_module('shenlun.ui.review_plans')
    with pytest.raises(ValueError): ui.ReviewPlanDialog(window.store,'missing',window)
    assert window.autosave_timer.isActive() and not window.autosave_timer.signalsBlocked()


@pytest.mark.parametrize('kind',['goals','plan'])
def test_restore_after_opening_epoch_before_context_read_preserves_host_buffer(window,tmp_path,monkeypatch,kind):
    rid,qid,original = show_saved(window)
    window.answer_edit.setPlainText('已保存草稿'); assert window.flush_workspace()
    backup = window.store.backup_to(tmp_path/'epoch-race.db')
    window.answer_edit.setPlainText('恢复前未保存内容')
    original_generation = window.store.assessment_generation
    epoch = original_generation()
    def restore_once():
        monkeypatch.setattr(window.store,'assessment_generation',original_generation)
        window.store.restore_from(backup)
        return epoch
    monkeypatch.setattr(window.store,'assessment_generation',restore_once)
    with pytest.raises(ValueError,match='恢复'):
        if kind == 'goals': dialog(window)
        else: importlib.import_module('shenlun.ui.review_plans').ReviewPlanDialog(window.store,qid,window)
    QTest.qWait(1100)
    assert window.answer_edit.toPlainText() == '恢复前未保存内容'
    assert window.store.load_workspace()['submission']['answer'] == '已保存草稿'
    assert window._workspace_context_stale and window._autosave_paused


@pytest.mark.parametrize('kind',['goals','plan'])
def test_cancel_confirmation_restore_keeps_buffer_and_default_no(window,tmp_path,monkeypatch,kind):
    _,qid,_ = show_saved(window)
    window.answer_edit.setPlainText('基线'); assert window.flush_workspace()
    backup = window.store.backup_to(tmp_path/'cancel-race.db')
    window.answer_edit.setPlainText('未保存原答')
    d = dialog(window) if kind == 'goals' else importlib.import_module('shenlun.ui.review_plans').ReviewPlanDialog(window.store,qid,window)
    d.tags.setText('取消时输入')
    def confirm(*a,**k):
        assert k['defaultButton'] == QMessageBox.StandardButton.No
        window.store.restore_from(backup)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',confirm)
    d.reject(); QTest.qWait(1100)
    assert window.store.load_workspace()['submission']['answer'] == '基线'
    assert window.answer_edit.toPlainText() == '未保存原答' and window._workspace_context_stale


def test_ordinary_cancel_resumes_existing_pending_autosave(window):
    show_saved(window)
    window.answer_edit.setPlainText('普通取消后保存')
    d = dialog(window); d.reject()
    assert window.autosave_timer.isActive() and not window.autosave_timer.signalsBlocked()
    QTest.qWait(1100)
    assert window.store.load_workspace()['submission']['answer'] == '普通取消后保存'


def test_unsaved_and_no_summary_dialog_failure_keep_pending_autosave_normal(window):
    assert hasattr(window,'build_review_goals_dialog')
    window.answer_edit.setPlainText('已保存'); assert window.flush_workspace()
    window.answer_edit.setPlainText('打开失败仍可保存')
    with pytest.raises(ValueError): dialog(window)
    assert window.autosave_timer.isActive() and not window._autosave_paused
    rid = window.store.save_review({'submission':{'question':'题','materials':'材料','answer':'原答'},'assessment':{}})
    window.show_review(window.store.get_review(rid))
    assert not window.add_review_goals_button.isEnabled()
    with pytest.raises(ValueError,match='暂无'): dialog(window)
    assert window.autosave_timer.isActive() and not window._autosave_paused
