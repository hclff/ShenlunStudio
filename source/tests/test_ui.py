"""Focused real Qt contract checks; service injection only at the remote boundary."""
import importlib.util
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from shenlun.storage import Store


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    assert importlib.util.find_spec('shenlun.ui') is not None, 'Qt interface package is required'
    from shenlun.ui import MainWindow
    w = MainWindow(Store(tmp_path / 'ui.db'))
    yield w
    w.close()
    app.processEvents()


def drain(app, predicate, seconds=3):
    until = time.monotonic() + seconds
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        QTest.qWait(10)
    assert predicate()


def review():
    return {'title':'一次概括练习', 'submission':{'answer':'甲😀乙。','question':'概括','materials':'材料','question_type':'归纳概括'},
            'assessment':{'total_score':16,'max_score':20,'summary':'训练结果','annotations':[{'start':2,'end':3,'quote':'乙','issue':'具体化','suggestion':'补充对象'}]},
            'revisions':{'minimal':'修改原答','model':'重新组织','status':'完成'}, 'sources':[]}


def test_initial_offline_state_has_no_result_or_request(window):
    assert window.current_review is None
    assert window.store.list_reviews() == []
    assert window.pages.count() == 6
    assert window.findChild(type(window.answer_edit), 'answer_edit') is window.answer_edit
    assert not window.workers


def test_unconfigured_assessment_does_not_generate_a_result(window):
    window.load_example()
    window.begin_assessment()
    assert not window.workers
    assert window.current_review is None
    assert window.store.list_reviews() == []
    assert '设置' in window.status_label.text()


def test_settings_numeric_limits_match_transport(window):
    from shenlun.model import ModelClient
    window.model_url.setText('http://127.0.0.1:12345/v1')
    window.model_name.setText('fixture')
    for control in window.config_numbers.values():
        control.setValue(control.maximum())
    ModelClient(window.read_preferences(), '')
    for control in window.config_numbers.values():
        control.setValue(control.minimum())
    ModelClient(window.read_preferences(), '')


def test_large_model_limits_survive_settings_save_and_reload(window):
    from shenlun.settings import load_config
    from shenlun.model import ModelClient
    window.model_url.setText('http://127.0.0.1:12345/v1')
    window.model_name.setText('fixture')
    fields = ('max_input_chars','max_output_tokens','session_token_budget')
    for key in fields:
        window.config_numbers[key].setValue(9999999)
    window.save_preferences()
    saved = load_config(window.store)
    assert {key:saved[key] for key in fields} == {key:9999999 for key in fields}
    ModelClient(saved, '')


def test_submission_canonicalization_preserves_internal_text(window):
    window.question_edit.setPlainText('概括')
    window.materials_edit.setPlainText('材料')
    window.answer_edit.setPlainText('  第一行\n第二行  ')
    assert window.submission()['answer'] == '第一行\n第二行'
    window.save_draft()
    assert window.store.get_setting('draft')['answer'] == '第一行\n第二行'


def test_draft_can_preserve_unanswered_question(window):
    window.question_edit.setPlainText('  先保存题目  ')
    window.save_draft()
    assert window.store.get_setting('draft')['question'] == '先保存题目'
    assert window.store.get_setting('draft')['answer'] == ''


def test_corpus_search_and_original_paragraph_detail(window, app):
    aid = window.store.upsert_article({'title':'基层治理','body':'用好热线。\n及时解决问题。','journal':'人民日报','year':2025})
    window.corpus_search.setText('热线')
    window.refresh_corpus()
    assert window.corpus_table.rowCount() == 1
    window.corpus_table.selectRow(0)
    app.processEvents()
    assert aid + ':p1' in window.corpus_body.toPlainText()
    assert '用好热线。' in window.corpus_body.toPlainText()


def test_all_bundled_journals_can_be_filtered(window):
    window.store.upsert_article({'title':'红旗文章','body':'明确任务。','journal':'红旗文稿','year':2026})
    index = window.journal_filter.findText('红旗文稿')
    assert index >= 0
    window.journal_filter.setCurrentIndex(index)
    window.refresh_corpus()
    assert window.corpus_table.rowCount() == 1
    assert window.corpus_table.item(0, 1).text() == '红旗文稿'


def test_history_restores_score_snapshot_and_unicode_annotation(window, app):
    rid = window.store.save_review(review())
    window.refresh_history()
    window.review_list.setCurrentRow(0)
    app.processEvents()
    assert window.current_review['id'] == rid
    assert window.result_original.toPlainText() == '甲😀乙。'
    assert '16' in window.result_report.toPlainText()
    window.annotation_list.setCurrentRow(0)
    app.processEvents()
    assert window.result_original.textCursor().selectedText() == '乙'


def test_method_activation_uses_validating_service(window):
    calls = []
    class Service:
        def enable_card(self, card_id, enabled):
            calls.append((card_id, enabled))
            raise ValueError('证据不足')
    window.service_factory = lambda config, key: Service()
    window.store.save_card({'id':'candidate','name':'论证方法','enabled':False,'steps':['核对证据']})
    window.refresh_methods()
    window.method_table.selectRow(0)
    window.set_method_enabled(True)
    assert calls == [('candidate', True)]
    assert not window.store.list_cards()[0]['enabled']
    assert '证据不足' in window.status_label.text()


def test_worker_cancellation_close_retains_running_thread(window, app):
    started = threading.Event()
    released = threading.Event()
    def operation(progress, cancel):
        started.set()
        while not released.is_set():
            time.sleep(.01)
        return {'cancelled':cancel.is_set()}
    window.show()
    window.start_job('慢任务', operation, lambda result: None)
    drain(app, started.is_set)
    worker = next(iter(window.workers))
    window.close()
    assert worker in window.workers and worker.isRunning()
    assert worker.cancel_event.is_set()
    assert window.isVisible()
    released.set()
    drain(app, lambda: not window.workers)
    drain(app, lambda: not window.isVisible())


def test_entry_seed_is_copy_once(tmp_path):
    assert importlib.util.find_spec('shenlun.__main__') is not None, 'desktop entry point required'
    from shenlun.__main__ import create_store
    seed = tmp_path / 'seed.db'
    source = Store(seed)
    source.upsert_article({'title':'种子文章','body':'真实正文'})
    data = tmp_path / 'data'
    store = create_store(data, seed)
    store.save_review(review())
    source.upsert_article({'title':'后来新增','body':'不应覆盖用户数据库'})
    again = create_store(data, seed)
    assert again.stats()['articles'] == 1
    assert again.stats()['reviews'] == 1


def test_existing_article_methods_without_builtin_marker_open_article_tab(app, tmp_path):
    from shenlun.ui.window import MainWindow
    store = Store(tmp_path / 'prior-methods.db')
    store.save_card({'id':'prior-method', 'name':'此前导入的方法', 'enabled':False})
    window = MainWindow(store)
    try:
        assert window.methods_tabs.currentIndex() == 1
        assert window.method_table.rowCount() == 1
    finally:
        window.close()


def test_running_assessment_uses_submitted_snapshot(window, app):
    from shenlun.service import AppService
    from shenlun.settings import save_config
    from shenlun.ui.preferences import load_config
    from test_service import ai_assessment
    window.wait_for_backup()
    window.backup_manager.configure(enabled=False)
    save_config(window.store, {'base_url':'http://localhost:1234/v1','model':'local-test'})
    window.config = load_config(window.store)
    released = threading.Event()
    submitted = threading.Event()
    class Client:
        def complete(self, system, payload, cancel=None):
            if 'assessment' not in payload:
                submitted.set()
                if not released.wait(5):
                    raise RuntimeError('offline assessment barrier was not released')
                data = ai_assessment(payload)
            else:
                data = {'minimal':'离线修改稿', 'model':'离线示范稿'}
            return {'data':data, 'usage':{'input_tokens':10, 'output_tokens':10, 'total_tokens':20},
                    'model':'local-test'}
    def factory(config, key):
        service = AppService(window.store, config, key)
        service.client = Client()
        return service
    window.service_factory = factory
    window.answer_edit.setPlainText('原来提交的答案。')
    window.begin_assessment()
    try:
        drain(app, submitted.is_set)
        window.answer_edit.setPlainText('运行时已经修改。')
    finally:
        released.set()
    drain(app, lambda: window.current_review is not None)
    assert window.current_review['submission']['answer'] == '原来提交的答案。'
    assert window.result_original.toPlainText() == '原来提交的答案。'
    assert window.answer_edit.toPlainText() == '运行时已经修改。'
    drain(app, lambda: not window.workers)
    saved = window.store.get_review(window.current_review['id'])
    assert saved == window.current_review and len(window.store.list_reviews()) == 1
    tasks = window.store.list_assessment_tasks()
    assert len(tasks) == 1 and tasks[0]['state'] == 'completed'
    assert tasks[0]['review_id'] == saved['id']
    assert tasks[0]['submission']['answer'] == '原来提交的答案。'


def test_csv_calibration_converts_numeric_fields(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'human.csv'
    path.write_text('human,ai,max_score,answer_id,version\n16,14,20,a,v1\n18,16,20,b,v1\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    window.import_calibration()
    assert 'n = 2' in window.calibration_label.toPlainText()
    assert '10.0' in window.calibration_label.toPlainText()


def test_pdf_export_has_real_document(window, tmp_path):
    from pypdf import PdfReader
    window.show_review(review())
    path = tmp_path / 'review.pdf'
    window.write_pdf(path)
    reader = PdfReader(path)
    assert len(reader.pages) >= 1
    assert '16' in ''.join(p.extract_text() for p in reader.pages)


def test_chinese_font_is_available_for_offscreen_render(window):
    from PySide6.QtGui import QFontDatabase
    assert 'Microsoft YaHei' in QFontDatabase.families()


def test_large_people_date_range_is_split_into_at_most_31_days(window, monkeypatch):
    from datetime import date
    import shenlun.ui.library as library
    calls = []
    def importing(start, end, store, progress, cancel):
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        calls.append((a, b))
        return {'processed':1}
    monkeypatch.setattr(library, 'import_people_range', importing)
    result = window.import_people_chunks(date(2026, 1, 1), date(2026, 3, 15), lambda message: None, threading.Event())
    assert result['processed'] == 3
    assert all((b - a).days <= 30 for a, b in calls)
    assert calls[0][0] == date(2026, 1, 1) and calls[-1][1] == date(2026, 3, 15)


def test_empty_password_field_preserves_existing_secret(window, monkeypatch):
    import shenlun.ui.preferences as preferences
    writes = []
    monkeypatch.setattr(preferences, 'set_api_key', lambda key: writes.append(key) or True)
    assert window.api_key.text() == ''
    assert window.save_preferences()
    assert writes == []
    assert 'api_key' not in window.store.get_setting('model_config')
