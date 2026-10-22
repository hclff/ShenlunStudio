"""Actual window/thread/service boundaries for deliberate retry of a frozen submission."""
import copy
import threading
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.model import ModelError
from shenlun.settings import load_config, save_config
from shenlun.storage import Store


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from shenlun.ui import MainWindow
    store = Store(tmp_path/'ui.db')
    save_config(store, {'base_url':'http://localhost:1234/v1','model':'fixture'})
    w = MainWindow(store)
    w.show()
    yield w
    w.close(); app.processEvents()


def drain(app, predicate):
    until = time.monotonic()+5
    while not predicate() and time.monotonic()<until:
        app.processEvents(); QTest.qWait(10)
    assert predicate()


def test_failed_assessment_retry_uses_original_input_and_new_saved_settings(window, app, monkeypatch):
    from test_service import ai_assessment
    from shenlun.service import AppService
    calls = []
    class Client:
        def complete(self, system, payload, cancel=None):
            calls.append(copy.deepcopy(payload))
            if len(calls) == 1: raise ModelError('模型响应超时')
            data = ai_assessment(payload) if 'submission' in payload and 'assessment' not in payload else {'minimal':'修改稿','model':'示范稿'}
            return {'data':data,'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'model':'fixture'}
    configs = []
    def service(config, key):
        configs.append(config)
        result = AppService(window.store, config, key)
        result.client = Client()
        return result
    window.service_factory = service
    window.answer_edit.setPlainText('原来提交的答案')
    window.begin_assessment()
    drain(app, lambda:not window.workers)
    assert window.failure_panel.isVisible()
    assert not window.failure_details.isVisible()
    assert window.retry_task_button.isEnabled()
    assert window.store.list_reviews() == []
    window.answer_edit.setPlainText('失败后改写的答案')
    window.config_numbers['max_output_tokens'].setValue(7000)
    window.save_preferences()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k:QMessageBox.StandardButton.Yes)
    window.retry_task_button.click()
    drain(app, lambda:not window.workers)
    assert calls[1]['submission']['answer'] == '原来提交的答案'
    assert calls[0] == calls[1]
    assert configs[-1]['max_output_tokens'] == 7000
    assert window.answer_edit.toPlainText() == '失败后改写的答案'
    assert len(window.store.list_reviews()) == 1
    assert not window.failure_panel.isVisible()


def test_retry_decline_and_cancel_never_resend(window, app, monkeypatch):
    calls = []
    class Service:
        def assess(self, raw, progress, cancel, **kwargs):
            calls.append(raw)
            raise ModelError('JSON 格式错误')
    window.service_factory = lambda *args:Service()
    window.answer_edit.setPlainText('原答')
    window.begin_assessment()
    drain(app, lambda:not window.workers)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k:QMessageBox.StandardButton.No)
    window.retry_task_button.click()
    app.processEvents()
    assert len(calls) == 1
    released = threading.Event()
    window.start_job('慢任务', lambda p,c:released.wait(2), lambda r:None)
    window.cancel_jobs(); released.set()
    drain(app, lambda:not window.workers)
    assert not window.failure_panel.isVisible()


def test_output_budget_conflict_is_visible_without_network(window, app):
    calls = []
    window.service_factory = lambda *args:calls.append(args)
    window.config_numbers['session_token_budget'].setValue(5000)
    window.config_numbers['max_output_tokens'].setValue(5000)
    window.save_preferences()
    assert '预算' in window.limit_guidance.text()
    window.answer_edit.setPlainText('原答')
    window.begin_assessment()
    assert not window.workers and calls == []
    assert '预算' in window.status_label.text()


def test_diagnostics_decline_does_not_save_key_or_start_network(window, app, monkeypatch):
    calls = []
    window.service_factory = lambda *args:calls.append(args)
    window.api_key.setText('not-a-real-key')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k:QMessageBox.StandardButton.No)
    window.test_connection()
    assert calls == [] and not window.workers
    assert window.api_key.text() == 'not-a-real-key'


def test_revision_retry_only_regenerates_revisions_and_preserves_grade(window, app, monkeypatch):
    from test_service import ai_assessment, raw
    from shenlun.service import AppService
    payloads = []
    class Client:
        def complete(self, system, payload, cancel=None):
            payloads.append(copy.deepcopy(payload))
            if len(payloads) == 2: raise ModelError('修改稿响应超时')
            data = ai_assessment(payload) if 'assessment' not in payload else {'minimal':'重试修改稿','model':'重试示范稿'}
            return {'data':data,'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'model':'fixture'}
    def factory(config, key):
        service = AppService(window.store, config, key); service.client = Client(); return service
    window.service_factory = factory
    window.revision_mode.setCurrentIndex(window.revision_mode.findData('with_revision'))
    window._set_submission_fields(raw())
    window.begin_assessment()
    drain(app, lambda:not window.workers)
    before = window.store.list_reviews()[0]
    assert window.failure_panel.isVisible()
    assert window.task_state == 'error'
    assert window.retry_task_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k:QMessageBox.StandardButton.Yes)
    window.retry_task_button.click()
    drain(app, lambda:not window.workers)
    after = window.store.get_review(before['id'])
    assert len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert after['assessment'] == before['assessment']
    assert after['submission'] == before['submission']
    assert after['revisions']['minimal'] == '重试修改稿'
    assert len(window.store.list_reviews()) == 1
    assert not window.failure_panel.isVisible()


def test_result_display_failure_never_offers_network_retry(window, app):
    def display(value): raise ValueError('显示报告失败')
    window.start_job('训练批改', lambda p,c:{'saved':True}, display, retry=lambda:None)
    drain(app, lambda:not window.workers)
    assert window.failure_panel.isVisible()
    assert window.retry_task_button.isHidden()


def test_diagnostic_completion_shows_plain_text_results(window, app, monkeypatch):
    # Isolate only the remote HTTP bytes; use real diagnostic/client/store/window code.
    def response(client, encoded):
        return {'choices':[{'message':{'content':'{"ok":true}'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}}
    from shenlun.model import ModelClient
    monkeypatch.setattr(ModelClient, '_post', response)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k:QMessageBox.StandardButton.Yes)
    window.test_connection()
    drain(app, lambda:not window.workers)
    assert window.compatibility_details.toPlainText().count('通过') >= 3
    assert window.store.usage_summary()['requests'] == 3
    assert '未知' in window.compatibility_details.toPlainText()


def test_diagnostic_summary_marks_changed_settings_and_scopes_declared_limit(window):
    config = window.read_preferences()
    report = {'checks':[{'id':'connection','label':'连接','status':'passed','detail':'通过'}], 'output_limit':8192}
    window.show_compatibility_result(report, config)
    assert window.declared_output_limit() == 8192
    window.model_name.setText('another-model')
    assert window.declared_output_limit() is None
    assert '变更' in window.compatibility_summary.text()
    assert '未知' in window.limit_guidance.text()
