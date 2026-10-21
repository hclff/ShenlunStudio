"""Qt evaluation boundaries with isolated SQLite and synthetic source fixtures."""
import importlib
import importlib.util
import json
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from shenlun.storage import Store
from shenlun.ui import MainWindow


def ui(name):
    assert importlib.util.find_spec('shenlun.ui.' + name), 'new evaluation UI is required'
    return importlib.import_module('shenlun.ui.' + name)


def answer(text='保留人工窗口。'):
    return dict(question='概括服务站做法。', materials='服务站保留人工窗口。\n社区提供电话预约。',
                answer=text, question_type='归纳概括', max_score=20, word_limit=100)


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app, tmp_path, monkeypatch):
    value = MainWindow(Store(tmp_path / 'ui-eval.db'))
    value.config.update(base_url='https://fixture.invalid/v1', model='synthetic-fixture', session_token_budget=200000)
    yield value
    # Cleanup accepts discard only after the test's assertions and modal-decision probes.
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    value.close()
    app.processEvents()


def collection(host):
    dataset = host.store.create_eval_set('合成界面测试集')
    case = host.store.add_eval_case(dataset['id'], answer(), origin='public_candidate',
                                    source='合成来源夹具，不是真实答卷', answer_complete=True)
    return dataset, case


def wait(app, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        app.processEvents(); QTest.qWait(10)
    assert predicate()


def test_reference_draft_confirm_and_conflict_keep_editor(host, monkeypatch):
    mod = ui('reference_points'); qid = host.store.save_question(answer())
    first = mod.ReferencePointsDialog(host.store, qid, host)
    first.add_point(); first.point_text.setPlainText('保留人工窗口'); first.add_reference('M1', '保留人工窗口')
    first.save_points()
    assert host.store.get_active_reference_points(qid) is None
    second = mod.ReferencePointsDialog(host.store, qid, host)
    second.point_text.setPlainText('另一窗口文字')
    first.reviewer.setText('人工测试员')
    monkeypatch.setattr(mod.QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    first.save_points(confirm=True)
    assert host.store.get_active_reference_points(qid)['version'] == 2
    second.save_points()
    assert second.point_text.toPlainText() == '另一窗口文字'
    assert len(host.store.list_reference_versions(qid)) == 2
    first.point_text.setPlainText('新草稿'); first.save_points()
    assert host.store.get_active_reference_points(qid)['version'] == 2
    first.history_list.setCurrentRow(1)
    assert '人工测试员' in first.history_detail.toPlainText()
    first.reject(); second.reject()


def test_reference_decline_and_empty_reviewer_do_not_confirm(host, monkeypatch):
    mod = ui('reference_points'); qid = host.store.save_question(answer())
    dialog = mod.ReferencePointsDialog(host.store, qid, host)
    dialog.add_point(); dialog.point_text.setPlainText('窗口'); dialog.add_reference('M1', '人工窗口')
    dialog.save_points(confirm=True)
    assert not host.store.list_reference_versions(qid)
    dialog.reviewer.setText('人工测试员')
    calls = []
    def decline(*args, **kwargs):
        calls.append(kwargs['defaultButton']); return QMessageBox.StandardButton.No
    monkeypatch.setattr(mod.QMessageBox, 'question', decline)
    dialog.save_points(confirm=True)
    assert calls == [QMessageBox.StandardButton.No]
    assert not host.store.list_reference_versions(qid)
    assert 'M2' in dialog.materials_view.toPlainText()
    dialog.reject()


def test_declined_batch_never_reads_credentials_or_creates_run(host, monkeypatch):
    mod = ui('evaluation'); dataset, case = collection(host)
    dialog = mod.EvaluationDialog(host)
    def forbidden(*a, **kw): raise AssertionError('credential access before consent')
    monkeypatch.setattr(host, 'get_service', forbidden)
    choices = []
    def decline(*a, **kw): choices.append(kw['defaultButton']); return QMessageBox.StandardButton.No
    monkeypatch.setattr(mod.QMessageBox, 'question', decline)
    dialog.start_run()
    assert choices == [QMessageBox.StandardButton.No]
    assert host.store.list_eval_runs(dataset['id']) == []
    assert not dialog._jobs
    dialog.reject()


def test_incomplete_answer_is_labeled_as_excerpt_without_pending_prompt(host):
    mod = ui('evaluation')
    dataset = host.store.create_eval_set('摘录标签测试')
    host.store.add_eval_case(dataset['id'], answer(), origin='public_candidate',
                             source='合成来源', answer_complete=False)
    dialog = mod.EvaluationDialog(host)
    assert '摘录' in dialog.case_list.item(0).text()
    assert '待补' not in dialog.case_list.item(0).text()
    assert '待补' not in dialog.case_detail.toPlainText()
    dialog.reject()


def test_human_zero_blank_independence_history_and_stale_label(host,monkeypatch):
    mod = ui('evaluation'); _, case = collection(host)
    dialog = mod.EvaluationDialog(host)
    assert dialog.label_score.text() == '' and not dialog.label_independent.isChecked()
    dialog.label_reviewer.setText('测试老师'); dialog.label_reason.setPlainText('缺少要点')
    dialog.save_label()
    assert host.store.get_eval_label(case['id']) is None
    dialog.label_score.setText('0'); dialog.save_label()
    assert host.store.get_eval_label(case['id'])['score'] == 0
    assert not host.store.get_eval_label(case['id'])['independent']
    host.store.save_eval_label(case['id'], 5, reviewer='另一人', expected_version=1)
    dialog.label_score.setText('2'); dialog.save_label()
    assert dialog.label_score.text() == '2'
    assert host.store.get_eval_label(case['id'])['score'] == 5
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.reject()


def test_import_preview_cancel_bounds_and_atomic_failure(host, monkeypatch, tmp_path):
    mod = ui('evaluation'); dataset, case = collection(host)
    dialog = mod.EvaluationDialog(host)
    before = host.workspace_snapshot()
    path = tmp_path / 'candidate.json'
    path.write_text(json.dumps([dict(submission=answer('新答案'), source='合成来源', origin='gpt_control')]), encoding='utf8')
    monkeypatch.setattr(mod.JsonPreviewDialog, 'exec', lambda self: 0)
    dialog.import_file(str(path))
    assert len(host.store.list_eval_cases(dataset['id'])) == 1
    monkeypatch.setattr(mod.JsonPreviewDialog, 'exec', lambda self: 1)
    path.write_text(json.dumps([dict(submission=answer('新答案')), dict(submission=answer())]), encoding='utf8')
    dialog.import_file(str(path))
    assert len(host.store.list_eval_cases(dataset['id'])) == 1
    path.write_text(json.dumps([{}] * 101), encoding='utf8'); dialog.import_file(str(path))
    assert len(host.store.list_eval_cases(dataset['id'])) == 1
    assert host.workspace_snapshot() == before
    dialog.reject()


def test_blind_export_and_atomic_label_import(host, monkeypatch, tmp_path):
    mod = ui('evaluation'); dataset, case = collection(host)
    dialog = mod.EvaluationDialog(host); path = tmp_path / 'blind.json'
    dialog.export_blind(str(path))
    rows = json.loads(path.read_text(encoding='utf8'))
    assert rows[0]['human'] is None and 'origin' not in rows[0] and 'ai_score' not in rows[0]
    good = {**rows[0], 'human':0, 'reviewer':'盲评者', 'independent':True}
    path.write_text(json.dumps([good, {**good, 'case_id':'unknown'}]), encoding='utf8')
    monkeypatch.setattr(mod.JsonPreviewDialog, 'exec', lambda self: 1)
    dialog.import_file(str(path), labels=True)
    assert host.store.get_eval_label(case['id']) is None
    path.write_text(json.dumps([good]), encoding='utf8'); dialog.import_file(str(path), labels=True)
    assert host.store.get_eval_label(case['id'])['score'] == 0
    dialog.reject()


def test_cancel_retains_job_refreshes_saved_results_and_drops_late_result(host, app, monkeypatch):
    mod = ui('evaluation'); dataset, case = collection(host)
    dialog = mod.EvaluationDialog(host); entered = threading.Event(); release = threading.Event()
    class Service:
        config = host.config
        def evaluate_submission(self, submission, **kwargs):
            entered.set(); release.wait(3)
            return dict(submission=submission, assessment=dict(total_score=10, max_score=20), model='synthetic-fixture', prompt_version='fixture')
    monkeypatch.setattr(host, 'get_service', lambda **kw: Service())
    monkeypatch.setattr(mod.QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    dialog.start_run(); wait(app, entered.is_set)
    assert dialog._jobs and host.workers and not dialog.set_combo.isEnabled()
    dialog.reject(); assert dialog._close_pending and dialog._jobs
    release.set(); wait(app, lambda: not dialog._jobs)
    run = host.store.list_eval_runs(dataset['id'])[0]
    assert run['status'] == 'cancelled' and not host.workers
    assert not any(row['status'] == 'success' for row in host.store.list_eval_results(run['id']))
    assert not host.store.list_reviews()


def test_routes_and_minimum_layout_keep_six_workspaces(host, app):
    ui('evaluation'); ui('reference_points')
    host.resize(1050, 720); host.show(); host.navigation.setCurrentRow(5); app.processEvents()
    assert host.pages.count() == 6
    assert host.reference_points_button.isVisible() and host.evaluation_button.isVisible()
    assert host.width() == 1050 and host.height() == 720
    assert host.evaluation_button.geometry().right() <= host.evaluation_button.parentWidget().width()

def test_current_case_preserves_exam_and_custom_scoring_snapshot(host):
    mod = ui('evaluation')
    from shenlun.domain import prepare_submission
    value = answer(); value.update(exam='江苏省考', year='2025', category='A类')
    value['rubric'] = prepare_submission(value)['rubric']; value['rubric_selection']='generic'
    value['rubric'][0]['description'] = '合成自定义评分说明'
    dialog = mod.CaseDialog(value,host)
    record = dialog.record()
    assert record['submission']['exam']=='江苏省考'
    assert record['submission']['year']=='2025' and record['submission']['category']=='A类'
    assert record['submission']['rubric_selection']=='generic'
    assert record['submission']['rubric_template']['kind']=='custom'
    assert record['submission']['rubric']==value['rubric']
    dialog.reject()


def test_reference_multireference_withdraw_and_draft_remains_inactive(host, monkeypatch):
    mod=ui('reference_points'); qid=host.store.save_question(answer())
    dialog=mod.ReferencePointsDialog(host.store,qid,host)
    dialog.add_point(); dialog.point_text.setPlainText('人工服务和电话预约')
    dialog.add_reference('M1','人工窗口'); dialog.add_reference('M2','电话预约')
    dialog.reviewer.setText('核对人'); dialog.note.setText('人工核对两条材料')
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw: QMessageBox.StandardButton.Yes)
    dialog.save_points(confirm=True)
    assert len(host.store.get_active_reference_points(qid)['points'][0]['material_refs'])==2
    dialog.note.setText('引用条件待重新核实'); dialog.withdraw_points()
    assert host.store.get_active_reference_points(qid) is None
    dialog.save_points()
    assert host.store.get_active_reference_points(qid) is None
    assert [r['status'] for r in host.store.list_reference_versions(qid)]==['draft','withdrawn','confirmed']
    dialog.reject()


def test_successful_fixture_run_report_counts_spread_and_no_regular_attempt(host, app, monkeypatch, tmp_path):
    mod=ui('evaluation'); dataset,case=collection(host)
    from shenlun.domain import prepare_submission
    host.store.save_eval_label(case['id'],0,reviewer='合成独立标签',independent=True)
    class Service:
        config=host.config
        count=0
        def evaluate_submission(self,submission,**kwargs):
            self.count+=1; prepared=prepare_submission(submission)
            return dict(submission=prepared,assessment=dict(total_score=10+self.count,max_score=20,mode='full',rubric_fingerprint=prepared['rubric_fingerprint']),
                        model='synthetic-fixture',prompt_version='fixed',config_snapshot=dict(base_url='https://fixture.invalid/v1'),usage=dict(total_tokens=20))
    service=Service()
    monkeypatch.setattr(host,'get_service',lambda **kw:service)
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog=mod.EvaluationDialog(host);dialog.start_run();wait(app,lambda:not dialog._jobs)
    run=host.store.list_eval_runs(dataset['id'])[0]
    assert run['status']=='completed' and len(host.store.list_eval_results(run['id']))==3
    output=tmp_path/'report.json';dialog.export_report(str(output))
    report=json.loads(output.read_text(encoding='utf8'))
    assert report['summary']['requested_calls']==3 and report['summary']['human_pairs']==1
    assert report['summary']['repeat_groups'][0]['spread']==2
    assert not host.store.list_reviews() and not host.store.list_questions()
    assert '成功 3 / 计划 3 次' in dialog.run_detail.toPlainText()
    assert '小样本' in dialog.run_detail.toPlainText()
    assert '第 1 次：成功，11 / 20' in dialog.run_detail.toPlainText()
    assert '同条件重复 3 次，分差 2' in dialog.run_detail.toPlainText()
    assert dialog.set_combo.isEnabled()
    dialog.reject()


def test_parent_close_cancels_retains_completed_units_and_never_applies_late_result(host,app,monkeypatch):
    mod=ui('evaluation');dataset,case=collection(host)
    from shenlun.domain import prepare_submission
    entered=threading.Event(); release=threading.Event()
    class Service:
        config=host.config
        count=0
        def evaluate_submission(self,submission,**kwargs):
            self.count+=1
            if self.count==2:entered.set();release.wait(3)
            prepared=prepare_submission(submission)
            return dict(submission=prepared,assessment=dict(total_score=10,max_score=20,mode='full',rubric_fingerprint=prepared['rubric_fingerprint']),
                        model='synthetic-fixture',prompt_version='fixed')
    monkeypatch.setattr(host,'get_service',lambda **kw:Service())
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog=mod.EvaluationDialog(host);dialog.start_run();wait(app,entered.is_set)
    host.close();assert host._closing and host.workers
    release.set();wait(app,lambda:not host.workers)
    run=host.store.list_eval_runs(dataset['id'])[0];rows=host.store.list_eval_results(run['id'])
    assert run['status']=='cancelled'
    assert sum(row['status']=='success' for row in rows)==1
    assert dialog.result()==0 and not host.store.list_reviews()

def test_incomplete_source_never_runs_and_large_import_never_mutates(host,monkeypatch,tmp_path):
    mod=ui('evaluation');dataset,case=collection(host)
    incomplete=host.store.add_eval_case(dataset['id'],answer('只有短摘录'),origin='public_candidate',source='合成摘录夹具',answer_complete=False)
    dialog=mod.EvaluationDialog(host)
    # An incomplete item is not selected by default; even forced selection is rejected before consent/key access.
    for i in range(dialog.case_list.count()):
        item=dialog.case_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole)['id']==incomplete['id']:
            assert item.checkState()==Qt.CheckState.Unchecked
            item.setCheckState(Qt.CheckState.Checked)
    def forbidden(*a,**kw):raise AssertionError('incomplete source cannot request upload/credentials')
    monkeypatch.setattr(host,'get_service',forbidden)
    monkeypatch.setattr(mod.QMessageBox,'question',forbidden)
    dialog.start_run()
    assert not host.store.list_eval_runs(dataset['id'])
    large=tmp_path/'large.json'
    with large.open('wb') as stream:stream.seek(25*1024*1024);stream.write(b'0')
    dialog.import_file(str(large))
    assert len(host.store.list_eval_cases(dataset['id']))==2 and '25 MB' in dialog.message.text()
    longtext=tmp_path/'long.json';longtext.write_text(json.dumps([dict(submission=answer('a'*250001))]),encoding='utf8')
    dialog.import_file(str(longtext))
    assert len(host.store.list_eval_cases(dataset['id']))==2
    dialog.reject()


def test_whole_question_partition_and_readonly_inspection_do_not_touch_host_editor(host):
    mod=ui('evaluation');dataset,case=collection(host)
    other=host.store.add_eval_case(dataset['id'],answer('电话预约。'),origin='gpt_control',source='合成对照')
    host.question_edit.setPlainText('父窗口未提交文字');before=host.workspace_snapshot()
    dialog=mod.EvaluationDialog(host)
    assert dialog.case_detail.isReadOnly() and dialog.label_history.isReadOnly() and dialog.run_detail.isReadOnly()
    dialog.partition_combo.setCurrentIndex(dialog.partition_combo.findData('holdout'));dialog.set_partition()
    assert {row['partition'] for row in host.store.list_eval_cases(dataset['id'])}=={'holdout'}
    assert host.workspace_snapshot()==before
    dialog.reject()


def test_existing_collections_can_open_with_unfinished_current_rubric(host,monkeypatch):
    mod=ui('evaluation');collection(host)
    host.rubric_edit.setPlainText('[unfinished')
    seen=[]
    def inspect(dialog):seen.append(dialog.case_list.count());return 0
    monkeypatch.setattr(mod.EvaluationDialog,'exec',inspect)
    host.open_evaluation()
    assert seen==[1]
    assert host.rubric_edit.toPlainText()=='[unfinished'

def fill_label_draft(dialog, score='7', reviewer='未保存评阅人', reason='未保存的人工意见', independent=True):
    dialog.label_score.setText(score);dialog.label_reviewer.setText(reviewer)
    dialog.label_reason.setPlainText(reason);dialog.label_independent.setChecked(independent)


def label_form(dialog):
    return (dialog.label_score.text(),dialog.label_reviewer.text(),dialog.label_reason.toPlainText(),dialog.label_independent.isChecked())


def test_unsaved_label_survives_same_case_refresh_and_whole_question_partition(host,monkeypatch):
    mod=ui('evaluation');dataset,case=collection(host);dialog=mod.EvaluationDialog(host)
    fill_label_draft(dialog);before=label_form(dialog)
    dialog.refresh_cases()
    assert label_form(dialog)==before
    dialog.partition_combo.setCurrentIndex(dialog.partition_combo.findData('holdout'));dialog.set_partition()
    assert label_form(dialog)==before
    assert host.store.get_eval_label(case['id']) is None
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.reject()


def test_per_case_label_drafts_survive_case_and_set_switches_and_keep_conflict_version(host,monkeypatch):
    mod=ui('evaluation');dataset,case=collection(host)
    other=host.store.add_eval_case(dataset['id'],answer('合成第二原答'),source='合成测试')
    second=host.store.create_eval_set('第二合成评测集')
    host.store.add_eval_case(second['id'],answer('另一集合成原答'),source='合成测试')
    dialog=mod.EvaluationDialog(host);dialog.set_combo.setCurrentIndex(dialog.set_combo.findData(dataset['id']))
    fill_label_draft(dialog);first_form=label_form(dialog)
    dialog.case_list.setCurrentRow(1);fill_label_draft(dialog,'0','第二评阅人','另一条未保存意见',False);second_form=label_form(dialog)
    dialog.case_list.setCurrentRow(0)
    assert label_form(dialog)==first_form
    dialog.set_combo.setCurrentIndex(dialog.set_combo.findData(second['id']))
    dialog.set_combo.setCurrentIndex(dialog.set_combo.findData(dataset['id']))
    assert label_form(dialog)==first_form
    host.store.save_eval_label(case['id'],5,reviewer='外部新版本')
    dialog.refresh_cases();dialog.save_label()
    assert label_form(dialog)==first_form and host.store.get_eval_label(case['id'])['score']==5
    dialog.case_list.setCurrentRow(1)
    assert label_form(dialog)==second_form and host.store.get_eval_label(other['id']) is None
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.reject()


@pytest.mark.parametrize('close_action',['reject','window_close','parent_close'])
def test_dirty_label_close_requires_default_no_discard_and_never_autosaves(host,app,monkeypatch,close_action):
    mod=ui('evaluation');_,case=collection(host);dialog=mod.EvaluationDialog(host)
    host.show();dialog.show();app.processEvents();fill_label_draft(dialog);before=label_form(dialog)
    calls=[]
    def decline(*a,**kw):calls.append(kw['defaultButton']);return QMessageBox.StandardButton.No
    monkeypatch.setattr(mod.QMessageBox,'question',decline)
    action=dialog.reject if close_action=='reject' else dialog.close if close_action=='window_close' else host.close
    action();app.processEvents()
    assert calls==[QMessageBox.StandardButton.No]
    assert dialog.isVisible() and not dialog._close_pending and label_form(dialog)==before
    assert not host._closing and host.store.get_eval_label(case['id']) is None
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.reject();assert not dialog.isVisible() and host.store.get_eval_label(case['id']) is None


def test_explicit_label_reload_decline_keeps_draft_accept_discards_only_current(host,monkeypatch):
    mod=ui('evaluation');dataset,case=collection(host)
    other=host.store.add_eval_case(dataset['id'],answer('合成第二原答'),source='合成测试')
    dialog=mod.EvaluationDialog(host);fill_label_draft(dialog);first=label_form(dialog)
    dialog.case_list.setCurrentRow(1);fill_label_draft(dialog,'0','第二评阅人','另一条未保存意见');other_form=label_form(dialog)
    dialog.case_list.setCurrentRow(0)
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    dialog.reload_label();assert label_form(dialog)==first
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.reload_label();assert label_form(dialog)==('','','',False)
    dialog.case_list.setCurrentRow(1);assert label_form(dialog)==other_form
    assert not host.store.list_eval_labels(case['id']) and not host.store.list_eval_labels(other['id'])
    dialog.reject()

def test_parent_close_with_dirty_label_decline_keeps_worker_then_accept_cancels_without_autosave(host,app,monkeypatch):
    mod=ui('evaluation');dataset,case=collection(host)
    entered=threading.Event();release=threading.Event()
    class Service:
        config=host.config
        def evaluate_submission(self,submission,**kwargs):
            entered.set();release.wait(5)
            return dict(submission=submission,assessment=dict(total_score=10,max_score=20))
    dialog=mod.EvaluationDialog(host);fill_label_draft(dialog);before=label_form(dialog)
    monkeypatch.setattr(host,'get_service',lambda **kw:Service())
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.start_run();wait(app,entered.is_set)
    choices=[]
    def decline(*a,**kw):choices.append(kw['defaultButton']);return QMessageBox.StandardButton.No
    monkeypatch.setattr(mod.QMessageBox,'question',decline)
    host.close()
    assert choices==[QMessageBox.StandardButton.No] and not host._closing
    assert dialog._jobs and not any(job.cancel_event.is_set() for job in dialog._jobs)
    assert label_form(dialog)==before and host.store.get_eval_label(case['id']) is None
    monkeypatch.setattr(mod.QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    host.close();assert host._closing
    release.set();wait(app,lambda:not host.workers)
    assert host.store.list_eval_runs(dataset['id'])[0]['status']=='cancelled'
    assert host.store.get_eval_label(case['id']) is None
