"""Qt recovery keeps snapshot persistence and the real service path intact."""
import copy

import pytest
from PySide6.QtWidgets import QMessageBox

from shenlun.model import ModelError
from shenlun.service import AppService
from shenlun.storage import Store
from test_task_recovery import app, window, drain
from test_durable_assessment import task
from test_service import ai_assessment, raw


def offline_factory(window, replies, payloads):
    class Client:
        def complete(self, system, payload, cancel=None):
            payloads.append(copy.deepcopy(payload))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            data = reply(payload) if callable(reply) else reply
            return {'data':data, 'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}, 'model':'fixture'}
    def factory(config, key):
        service = AppService(window.store, config, key)
        service.client = Client()
        return service
    return factory


def test_startup_lists_pending_task_without_calls_and_declined_confirmation_is_noop(window, monkeypatch):
    tid = task(window.store)
    payloads = []
    window.service_factory = offline_factory(window, [], payloads)
    dialog = window.build_assessment_recovery_dialog()
    assert dialog.task_list.count() == 1
    assert '概括成效' in dialog.task_list.item(0).text()
    before = Store(window.store.path).get_assessment_task(tid)
    messages = []
    def decline(*args, **kwargs):
        messages.append(args[2])
        assert kwargs['defaultButton'] == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', decline)
    window.resume_assessment_task(tid)
    assert payloads == [] and not window.workers
    assert window.store.get_assessment_task(tid) == before
    assert all(word in messages[0] for word in ['快照', '当前已保存', '费用', '未知'])
    dialog.close()


def test_window_restart_resumes_revisions_and_completed_resume_opens_report(window, app, monkeypatch):
    payloads = []
    window.service_factory = offline_factory(window, [ai_assessment, ModelError('timeout')], payloads)
    window._set_submission_fields(raw())
    window.begin_assessment()
    drain(app, lambda: not window.workers)
    saved = Store(window.store.path).list_assessment_tasks()[0]
    assert saved['state'] == 'failed' and saved['review_id']
    before = window.store.get_review(saved['review_id'])
    window.answer_edit.setPlainText('界面后来修改')
    window.service_factory = offline_factory(window, [{'minimal':'续写', 'model':'示范'}], payloads)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.resume_assessment_task(saved['id'])
    drain(app, lambda: not window.workers)
    assert len(payloads) == 3 and 'assessment' in payloads[-1]
    assert window.current_review['assessment'] == before['assessment']
    assert window.answer_edit.toPlainText() == '界面后来修改'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: pytest.fail('completed task must not ask to call model'))
    window.resume_assessment_task(saved['id'])
    assert len(payloads) == 3 and not window.workers
    assert window.current_review['id'] == saved['review_id']


def test_corrupt_task_ui_blocks_service_construction(window, monkeypatch):
    tid = task(window.store)
    with window.store._connection() as conn:
        conn.execute("UPDATE assessment_tasks SET data='{}' WHERE id=?", (tid,))
    window.service_factory = lambda *a: pytest.fail('must not construct provider service')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: pytest.fail('invalid snapshot cannot be confirmed'))
    window.resume_assessment_task(tid)
    assert '无法继续' in window.status_label.text()


def test_new_window_does_not_resume_cancelled_task_until_confirmed(window, app, monkeypatch):
    from shenlun.ui import MainWindow
    from shenlun.model import CancelledError
    from test_service import service
    window.wait_for_backup()
    window.backup_manager.configure(enabled=False)
    tid = task(window.store)
    with pytest.raises(CancelledError):
        service(window.store, [ai_assessment, CancelledError('cancelled')]).run_assessment_task(tid)
    restarted = MainWindow(Store(window.store.path))
    payloads = []
    try:
        restarted.service_factory = offline_factory(restarted, [{'minimal':'续写', 'model':'示范'}], payloads)
        app.processEvents()
        assert not restarted.workers and payloads == []
        dialog = restarted.build_assessment_recovery_dialog()
        assert '已取消' in dialog.task_list.item(0).text()
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        assert restarted.resume_assessment_task(tid), restarted.status_label.text()
        drain(app, lambda: not restarted.workers)
        assert len(payloads) == 1 and 'assessment' in payloads[0]
        dialog.close()
    finally:
        restarted.close()
        app.processEvents()


def test_queued_result_callback_after_restore_is_discarded(window, monkeypatch, tmp_path):
    tid = task(window.store)
    window.service_factory = offline_factory(window, [ai_assessment, {'minimal':'修改', 'model':'示范'}], [])
    queued = []
    def collect(title, operation, callback, **kwargs):
        queued.append((callback, operation(None, None)))
    monkeypatch.setattr(window, 'start_job', collect)
    window.launch_assessment_task(tid)
    backup = window.store.backup_to(tmp_path/'completed.db')
    window.store.restore_from(backup)
    callback, result = queued[0]
    callback(result)
    assert window.current_review is None
    assert '失效' in window.status_label.text()


def test_restore_before_queued_worker_claim_prevents_calls_and_task_mutation(window, monkeypatch, tmp_path):
    tid = task(window.store)
    backup = window.store.backup_to(tmp_path/'pending.db')
    payloads, queued = [], []
    window.service_factory = offline_factory(window, [ai_assessment, {'minimal':'修改', 'model':'示范'}], payloads)
    monkeypatch.setattr(window, 'start_job', lambda title, operation, callback, **kwargs: queued.append(operation))
    window.launch_assessment_task(tid)
    other = Store(window.store.path)
    other.restore_from(backup)
    before = other.get_assessment_task(tid)
    with pytest.raises(ValueError, match='恢复|失效|改变'):
        queued[0](None, None)
    assert payloads == [] and other.list_reviews() == []
    assert other.get_assessment_task(tid) == before


def test_restore_during_service_construction_also_invalidates_launch_generation(window, monkeypatch, tmp_path):
    tid = task(window.store)
    backup = window.store.backup_to(tmp_path/'before-service.db')
    payloads, queued = [], []
    factory = offline_factory(window, [ai_assessment, {'minimal':'修改', 'model':'示范'}], payloads)
    def restore_after_config_loaded(config, key):
        service = factory(config, key)
        Store(window.store.path).restore_from(backup)
        return service
    window.service_factory = restore_after_config_loaded
    monkeypatch.setattr(window, 'start_job', lambda title, operation, callback, **kwargs: queued.append(operation))
    window.launch_assessment_task(tid)
    before = window.store.get_assessment_task(tid)
    with pytest.raises(ValueError, match='恢复|失效|改变'):
        queued[0](None, None)
    assert payloads == [] and window.store.list_reviews() == []
    assert window.store.get_assessment_task(tid) == before


@pytest.mark.parametrize('restored_stage', ['pending', 'scored'])
def test_restore_during_recovery_confirmation_requires_new_consent(window, monkeypatch, tmp_path, restored_stage):
    from test_service import service
    window.wait_for_backup()
    window.backup_manager.configure(enabled=False)
    tid = task(window.store)
    pending_backup = window.store.backup_to(tmp_path/'pending.db')
    initial = service(window.store, [ai_assessment, ModelError('synthetic revision interruption')]).run_assessment_task(tid)
    backup = pending_backup if restored_stage == 'pending' else window.store.backup_to(tmp_path/'scored.db')
    assert window.store.get_assessment_task(tid)['review_id'] == initial['id']
    payloads, confirmations, restored = [], [], []
    replies = ([ai_assessment] if restored_stage == 'pending' else []) + [{'minimal':'new revision', 'model':'new model'}]
    window.service_factory = offline_factory(window, replies, payloads)
    def run(title, operation, callback, **kwargs):
        callback(operation(None, None))
    monkeypatch.setattr(window, 'start_job', run)
    def answer(*args, **kwargs):
        confirmations.append(args[2])
        assert kwargs['defaultButton'] == QMessageBox.StandardButton.No
        if len(confirmations) == 1:
            assert '只继续生成修改稿' in args[2]
            other = Store(window.store.path)
            other.restore_from(backup)
            restored.append((other.get_assessment_task(tid), other.list_reviews()))
            return QMessageBox.StandardButton.Yes
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', answer)

    accepted = window.resume_assessment_task(tid)

    assert payloads == [], 'pre-restore consent must never authorize a model request in the restored database'
    assert accepted is False
    assert '确认' in window.status_label.text() and '失效' in window.status_label.text()
    assert window.store.get_assessment_task(tid) == restored[0][0]
    assert window.store.list_reviews() == restored[0][1]
    assert len(restored[0][1]) == (0 if restored_stage == 'pending' else 1)
    assert window.current_review is None
    # Reopening continuation must ask again using the restored stage; declining
    # that fresh question also preserves the restored task and report exactly.
    assert window.resume_assessment_task(tid) is False
    assert len(confirmations) == 2
    assert ('从评分阶段重试' in confirmations[1]) == (restored_stage == 'pending')
    assert payloads == [] and window.store.get_assessment_task(tid) == restored[0][0]
