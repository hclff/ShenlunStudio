"""Practice loop with real Qt and isolated SQLite; never use the vault or model."""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(Store(tmp_path / 'practice-ui.db'))
    yield w
    w.close()
    app.processEvents()


def wait(app, predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents(); QTest.qWait(20)
    assert predicate()


def exercise(question='概括便民做法', answer='开通电话报修。'):
    return {'question':question, 'materials':'服务站开通电话报修，派单后回访。', 'answer':answer,
            'question_type':'归纳概括', 'exam':'江苏省考', 'max_score':20, 'word_limit':150}


def review(answer='开通电话报修。', model='fixture'):
    return {'submission':exercise(answer=answer), 'assessment':{'total_score':16, 'max_score':20,
            'mode':'full', 'rubric_fingerprint':'r1', 'annotations':[]}, 'model':model,
            'prompt_version':'p1', 'revisions':{}, 'title':'便民服务'}


def test_question_bank_route_keeps_old_routes_and_offline_empty_state(window):
    window.navigation.setCurrentRow(5)
    assert window.pages.currentIndex() == 5
    assert window.PAGE_NAMES[:5] == ['开始批改','文章资料库','写作方法库','训练记录','设置']
    assert window.question_list.count() == 0
    assert window.store.list_reviews() == []
    assert window.store.load_workspace() is None


def test_autosave_preserves_invalid_rubric_and_recovers_on_restart(window, app):
    window.question_edit.setPlainText('尚未完成题干')
    window.answer_edit.setPlainText('  尚未整理的原答\n')
    window.rubric_edit.setPlainText('[{"id":')
    wait(app, lambda: window.store.load_workspace() is not None)
    assert window.store.load_workspace()['submission']['rubric_text'] == '[{"id":'
    another = MainWindow(window.store)
    try:
        assert another.answer_edit.toPlainText() == '  尚未整理的原答\n'
        assert another.rubric_edit.toPlainText() == '[{"id":'
        assert not another.workers
    finally:
        another.close()


def test_open_and_new_attempt_retain_per_question_draft(window):
    first = window.store.save_question(exercise())
    second = window.store.save_question(exercise(question='分析治理成效'))
    assert window.open_question(first)
    window.answer_edit.setPlainText('第一题未完成的原答')
    assert window.open_question(second)
    assert window.store.load_question_draft(first)['submission']['answer'] == '第一题未完成的原答'
    assert window.open_question(first)
    assert window.answer_edit.toPlainText() == '第一题未完成的原答'
    window.new_question_attempt(first)
    assert window.answer_edit.toPlainText() == ''
    assert any(x['submission']['answer'] == '第一题未完成的原答' for x in window.store.list_workspace_history())


def test_changed_question_does_not_overwrite_original_question_draft(window):
    qid = window.store.save_question(exercise())
    window.open_question(qid)
    window.answer_edit.setPlainText('原题草稿')
    assert window.flush_workspace()
    window.question_edit.setPlainText('另一道题')
    window.answer_edit.setPlainText('另一道答案')
    assert window.flush_workspace()
    assert window.store.load_question_draft(qid)['submission']['answer'] == '原题草稿'
    assert window.current_question_id is None


def test_load_and_close_cannot_discard_after_save_failure(window, monkeypatch):
    window.load_submission(exercise())
    window.answer_edit.setPlainText('不能丢失')
    def failed(*args, **kwargs): raise OSError('磁盘已满')
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    assert window.load_submission(exercise(question='新题')) is False
    assert window.answer_edit.toPlainText() == '不能丢失'
    assert '保存失败' in window.autosave_label.text()
    window.show(); window.close()
    assert window.isVisible()
    window.autosave_timer.stop()


def test_import_replace_guard_keeps_editor_on_storage_failure(window, monkeypatch):
    window.answer_edit.setPlainText('保留旧答案')
    def failed(*args, **kwargs): raise OSError('写入失败')
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    window.apply_intake_selection({'fields':None,'target':'answer','text':'导入答案','replace':True})
    assert window.answer_edit.toPlainText() == '保留旧答案'
    window.autosave_timer.stop()


def test_question_search_metadata_and_attempts_use_durable_records(window, app):
    qid = window.store.save_question(exercise(), title='热线服务', tags=['治理'], source='自编')
    rid = window.store.save_review(review())
    window.refresh_questions()
    window.question_search.setText('治理'); window.refresh_questions()
    assert window.question_list.count() == 1
    window.question_list.setCurrentRow(0); app.processEvents()
    assert window.attempt_list.count() == 1
    assert '热线服务' in window.question_title.text()
    assert window.attempt_list.item(0).data(Qt.ItemDataRole.UserRole) == rid
    window.store.mark_question_retry(qid, True)
    window.question_status.setCurrentText('待重练'); window.refresh_questions()
    assert window.question_list.count() == 1


def test_compare_shows_two_real_answers_and_condition_mismatch(window):
    original = review(); original['assessment']['material_points'] = [{'point':'便利老人', 'coverage':'covered', 'answer_quote':'电话报修'}]
    window.store.save_review(original)
    window.store.save_review(review(answer='派单、回访。', model='different'))
    window.refresh_questions(); window.question_list.setCurrentRow(0)
    window.attempt_list.item(0).setSelected(True)
    window.attempt_list.item(1).setSelected(True)
    window.compare_attempts()
    text = window.comparison_browser.toPlainText()
    assert '开通电话报修' in text and '派单、回访' in text
    assert '评分条件不同' in text
    assert 'different' in text and 'fixture' in text
    assert '便利老人' in text and '已覆盖' in text


def test_human_score_dialog_binds_selected_review_zero_and_default_not_independent(window, monkeypatch):
    from shenlun.ui.practice import HumanScoreDialog
    rid = window.store.save_review(review())
    window.refresh_history(); window.review_list.setCurrentRow(0)
    def accept(dialog):
        assert dialog.score.maximum() == 20
        assert not dialog.independent.isChecked()
        dialog.score.setValue(0); dialog.reason.setText('教师复核')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(HumanScoreDialog, 'exec', accept)
    window.record_human_score()
    value = window.store.get_human_score(rid)
    assert value['score'] == 0
    assert not value['independent']
    assert '1' in window.calibration_label.toPlainText()


def test_restore_discards_pending_autosave_and_reloads_restored_settings(window, tmp_path, monkeypatch, app):
    backup = tmp_path / 'restored.db'
    source = Store(tmp_path / 'source.db')
    source.save_workspace({'submission':exercise(answer='备份中的草稿'), 'question_id':None})
    source.set_setting('model_config', {'model':'恢复后的模型', 'base_url':'http://localhost:1234/v1'})
    source.backup_to(backup)
    window.answer_edit.setPlainText('恢复前的待保存答案')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(backup), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.restore_database()
    assert window.answer_edit.toPlainText() == '备份中的草稿'
    assert window.model_name.text() == '恢复后的模型'
    QTest.qWait(1100); app.processEvents()
    assert window.store.load_workspace()['submission']['answer'] == '备份中的草稿'


def test_backup_rejects_workers_before_opening_file_picker(window, monkeypatch):
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: pytest.fail('worker active'))
    window.workers.add(object())
    try:
        window.backup_database()
        assert '任务' in window.status_label.text()
    finally:
        window.workers.clear()


def test_loading_clean_same_question_archives_answer_before_replacing(window):
    window.load_submission(exercise(answer='应保留的已保存草稿'))
    assert window.flush_workspace()
    window.load_submission(exercise(answer='历史原答'))
    assert any(x['submission']['answer'] == '应保留的已保存草稿' for x in window.store.list_workspace_history())


def test_example_failed_load_keeps_failure_feedback(window, monkeypatch):
    window.answer_edit.setPlainText('草稿仍在')
    def failed(*args, **kwargs): raise OSError('磁盘错误')
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    window.load_example()
    assert window.answer_edit.toPlainText() == '草稿仍在'
    assert '失败' in window.status_label.text()
    window.autosave_timer.stop()


def test_recovery_picker_restores_selected_archived_draft(window, monkeypatch):
    window.load_submission(exercise(answer='需要找回的草稿'))
    window.load_submission(exercise(question='另一题', answer='当前草稿'))
    from PySide6.QtWidgets import QListWidget
    def choose(dialog):
        assert dialog.windowTitle() == '恢复最近草稿'
        listing = dialog.findChild(QListWidget)
        listing.setCurrentRow(0)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', choose)
    window.recover_workspace()
    assert window.answer_edit.toPlainText() == '需要找回的草稿'
    assert window.store.load_workspace()['submission']['answer'] == '需要找回的草稿'


def test_restore_cancel_leaves_current_data_and_pending_editor(window, tmp_path, monkeypatch):
    backup = tmp_path / 'backup.db'
    other = Store(tmp_path / 'other.db'); other.save_question(exercise(question='备份题')); other.backup_to(backup)
    window.answer_edit.setPlainText('当前未完成答案')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(backup), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window.restore_database()
    assert window.answer_edit.toPlainText() == '当前未完成答案'
    assert window.store.list_questions() == []
    assert window.autosave_timer.isActive()


def test_human_diagnostic_cannot_open_scoring_dialog(window, monkeypatch):
    from shenlun.ui.practice import HumanScoreDialog
    value = review(); value['assessment']['total_score'] = None
    window.store.save_review(value); window.refresh_history(); window.review_list.setCurrentRow(0)
    monkeypatch.setattr(HumanScoreDialog, 'exec', lambda *args: pytest.fail('diagnostic score'))
    window.record_human_score()
    assert window.store.human_calibration_pairs() == []
    assert '不能' in window.status_label.text()


def test_manual_save_preserves_invalid_rubric_raw_and_can_retry_after_failure(window, monkeypatch):
    window.rubric_edit.setPlainText('[unfinished')
    original = window.store.save_workspace
    def failed(*args, **kwargs): raise OSError('磁盘暂不可用')
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    assert not window.save_draft()
    monkeypatch.setattr(window.store, 'save_workspace', original)
    assert window.save_draft()
    assert window.store.load_workspace()['submission']['rubric_text'] == '[unfinished'
    assert window.store.get_setting('draft')['rubric_text'] == '[unfinished'


def test_stale_human_observation_is_labeled_for_rereview(window):
    from shenlun.ui.practice import HumanScoreDialog
    value = review(); rid = window.store.save_review(value)
    window.store.save_human_score(rid, 12)
    value['id'] = rid; value['submission']['answer'] = '改成另一份原答'
    window.store.save_review(value)
    dialog = HumanScoreDialog(window.store.get_review(rid), window.store.get_human_score(rid), window)
    assert '需要重新评阅' in ' '.join(label.text() for label in dialog.findChildren(type(window.status_label)))


def test_startup_recovery_read_error_can_close_without_overwriting_store(app, tmp_path, monkeypatch):
    store = Store(tmp_path / 'read-error.db')
    store.save_workspace({'submission':exercise(answer='数据库原有内容'), 'question_id':None})
    def failed(): raise OSError('暂不可读取')
    monkeypatch.setattr(store, 'load_workspace', failed)
    w = MainWindow(store); w.show(); w.close()
    assert not w.isVisible()


def test_settings_database_location_is_absolute_for_relative_store(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w = MainWindow(Store('relative.db'))
    try:
        labels = [label.text() for label in w.pages.widget(4).findChildren(type(w.status_label))]
        assert '本机数据库：' + str(tmp_path / 'relative.db') in labels
    finally:
        w.close()


def test_human_score_requires_history_selection_even_when_last_report_exists(window, monkeypatch):
    from shenlun.ui.practice import HumanScoreDialog
    rid = window.store.save_review(review()); window.show_review(window.store.get_review(rid))
    window.review_list.setCurrentRow(-1)
    monkeypatch.setattr(HumanScoreDialog, 'exec', lambda *args: pytest.fail('no selected history record'))
    window.record_human_score()
    assert window.store.get_human_score(rid) is None


@pytest.mark.parametrize('operation', ['new_attempt', 'load_submission', 'intake_replace'])
def test_immediate_replace_archives_latest_dirty_answer_and_invalid_rubric(window, operation):
    qid = window.store.save_question(exercise())
    window.open_question(qid)
    window.answer_edit.setPlainText('上一份已保存的原答')
    assert window.flush_workspace()
    window.answer_edit.setPlainText('定时器尚未触发的最新原答\n必须恢复这一版')
    window.rubric_edit.setPlainText('[{"id":"尚未写完')
    assert window.autosave_timer.isActive()
    if operation == 'new_attempt':
        assert window.new_question_attempt(qid)
    elif operation == 'load_submission':
        assert window.load_submission(exercise(answer='载入另一份同题原答'), question_id=qid)
    else:
        window.apply_intake_selection({'fields':None, 'target':'answer', 'text':'替换后的答案', 'replace':True})
    saved = [item for item in window.store.list_workspace_history()
             if item['submission']['answer'] == '定时器尚未触发的最新原答\n必须恢复这一版']
    assert saved
    assert saved[0]['submission']['rubric_text'] == '[{"id":"尚未写完'
    assert window.apply_workspace(saved[0])
    assert window.answer_edit.toPlainText() == '定时器尚未触发的最新原答\n必须恢复这一版'
    assert window.rubric_edit.toPlainText() == '[{"id":"尚未写完'


def test_archive_failure_after_latest_flush_blocks_replacement_and_failed_close(window, monkeypatch):
    qid = window.store.save_question(exercise())
    window.open_question(qid)
    window.answer_edit.setPlainText('最新未完成原答')
    window.rubric_edit.setPlainText('[unfinished')
    original = window.store.save_workspace
    def archive_failed(snapshot, archive_previous=False):
        if archive_previous: raise OSError('归档写入失败')
        return original(snapshot, archive_previous=archive_previous)
    monkeypatch.setattr(window.store, 'save_workspace', archive_failed)
    assert not window.new_question_attempt(qid)
    assert window.answer_edit.toPlainText() == '最新未完成原答'
    assert window.rubric_edit.toPlainText() == '[unfinished'
    assert window.store.load_workspace()['submission']['answer'] == '最新未完成原答'
    def all_failed(*args, **kwargs): raise OSError('磁盘暂不可写')
    monkeypatch.setattr(window.store, 'save_workspace', all_failed)
    window.show(); window.close()
    assert window.isVisible()
    assert '保存失败' in window.autosave_label.text()
    window.autosave_timer.stop()


def test_switch_question_clears_comparison_from_previous_question(window):
    window.store.save_review(review(answer='A题第一份原答'))
    window.store.save_review(review(answer='A题第二份原答'))
    other = window.store.save_question(exercise(question='从未作答的B题'))
    window.refresh_questions()
    for i in range(window.question_list.count()):
        if window.question_list.item(i).data(Qt.ItemDataRole.UserRole) != other:
            window.question_list.setCurrentRow(i); break
    window.attempt_list.item(0).setSelected(True); window.attempt_list.item(1).setSelected(True)
    window.compare_attempts()
    assert 'A题第一份原答' in window.comparison_browser.toPlainText()
    for i in range(window.question_list.count()):
        if window.question_list.item(i).data(Qt.ItemDataRole.UserRole) == other:
            window.question_list.setCurrentRow(i); break
    assert window.attempt_list.count() == 0
    assert window.comparison_browser.toPlainText() == ''
    assert window.question_tabs.currentIndex() == 0


def test_restore_clears_previous_database_comparison(window, tmp_path, monkeypatch):
    window.store.save_review(review(answer='恢复前第一份原答'))
    window.store.save_review(review(answer='恢复前第二份原答'))
    window.refresh_questions(); window.question_list.setCurrentRow(0)
    window.attempt_list.item(0).setSelected(True); window.attempt_list.item(1).setSelected(True)
    window.compare_attempts()
    assert '恢复前第一份原答' in window.comparison_browser.toPlainText()
    source = Store(tmp_path / 'empty-source.db')
    backup = source.backup_to(tmp_path / 'empty-backup.db')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(backup), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.restore_database()
    assert window.question_list.count() == 0
    assert window.comparison_browser.toPlainText() == ''
    assert window.question_tabs.currentIndex() == 0


def test_new_attempt_from_other_question_archives_target_draft_after_history_eviction(window):
    target = window.store.save_question(exercise(question='长期未完成的B题'))
    current = window.store.save_question(exercise(question='当前A题'))
    window.open_question(target)
    window.answer_edit.setPlainText('B题长期专属草稿，不能丢失')
    window.rubric_edit.setPlainText('[B题未完成量表')
    assert window.flush_workspace()
    window.open_question(current)
    for index in range(22):
        window.answer_edit.setPlainText(f'A题草稿第{index}次')
        assert window.flush_workspace(archive_previous=True)
    assert not any(item['submission']['answer'] == 'B题长期专属草稿，不能丢失'
                   for item in window.store.list_workspace_history())
    assert window.store.load_question_draft(target)['submission']['answer'] == 'B题长期专属草稿，不能丢失'
    assert window.new_question_attempt(target)
    assert window.current_question_id == target
    assert window.answer_edit.toPlainText() == ''
    saved = [item for item in window.store.list_workspace_history()
             if item['submission']['answer'] == 'B题长期专属草稿，不能丢失']
    assert saved
    assert saved[0]['submission']['rubric_text'] == '[B题未完成量表'


def test_new_attempt_other_question_keeps_target_draft_when_archive_fails(window, monkeypatch):
    target = window.store.save_question(exercise(question='B题'))
    current = window.store.save_question(exercise(question='A题'))
    window.open_question(target); window.answer_edit.setPlainText('B题应保留草稿'); window.flush_workspace()
    window.open_question(current)
    original = window.store.save_workspace
    def failed(snapshot, archive_previous=False):
        if archive_previous and snapshot.get('question_id') == target:
            raise OSError('B题归档失败')
        return original(snapshot, archive_previous=archive_previous)
    monkeypatch.setattr(window.store, 'save_workspace', failed)
    assert not window.new_question_attempt(target)
    assert window.store.load_question_draft(target)['submission']['answer'] == 'B题应保留草稿'
    assert window.answer_edit.toPlainText() == 'B题应保留草稿'
