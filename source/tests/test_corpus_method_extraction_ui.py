"""The corpus entry must send exactly the filtered ids and never auto-open on cancel."""
import pytest

from shenlun.storage import Store
from shenlun.ui import MainWindow
from shenlun.ui.library import imports as imports_module
from shenlun.ui.library.imports import method_extraction_summary


@pytest.fixture
def host(app, tmp_path):
    window = MainWindow(Store(tmp_path / 'host.db'))
    yield window
    window.close()
    app.processEvents()


def article(store, aid, body):
    store.upsert_article({'id': aid, 'title': f'文章{aid}', 'journal': '人民日报', 'year': 2026,
                          'issue': 1, 'url': 'https://www.qstheory.cn/' + aid, 'body': body,
                          'status': 'available', 'kind': '评论',
                          'paragraphs': [{'id': aid + ':p1', 'text': body}]})


class FakeService:
    def __init__(self):
        self.ids = None
        self.result = {'processed': 2, 'skipped': 0, 'unavailable': 0, 'image_only': 0,
                       'batches': 2, 'methods': 2, 'created': 1, 'updated': 1, 'candidates': 1,
                       'eligible': 1, 'failed': [], 'candidate_ids': ['method-x']}

    def estimate_method_extraction(self, ids):
        return {'articles': len(ids), 'characters': 20, 'estimated_input_tokens': 10,
                'estimated_batches': 1}

    def extract_methods(self, ids, progress=None, cancel=None):
        self.ids = list(ids)
        return self.result


def prepare(host):
    article(host.store, 'a', '保留人工服务，降低办理门槛。')
    article(host.store, 'b', '设置助老窗口，减少往返次数。')
    host.refresh_corpus()
    host.corpus_search.setText('保留')
    host.refresh_corpus()


def test_filter_extract_sends_filtered_ids_and_opens_pool(host, app, monkeypatch):
    prepare(host)
    service = FakeService()
    captured = {}
    monkeypatch.setattr(host, 'get_service', lambda **kwargs: service)
    monkeypatch.setattr(imports_module, 'confirm_billable', lambda *args, **kwargs: True)

    def start_job(title, operation, done, **kwargs):
        captured['title'] = title
        captured['domains'] = kwargs.get('domains')
        captured['result'] = operation(None, None)
        done(captured['result'])

    monkeypatch.setattr(host, 'start_job', start_job)
    opened = []
    monkeypatch.setattr(host, 'open_candidate_pool', lambda: opened.append(True))
    host.extract_methods_filtered()
    app.processEvents()
    assert service.ids == ['a']
    assert captured['domains'] == ('methods', 'usage')
    assert opened == [True]
    assert '候选方法 1 条' in host.status_label.text()


def test_declined_confirmation_reads_no_credentials(host, monkeypatch):
    prepare(host)
    calls = []
    monkeypatch.setattr(host, 'get_service', lambda **kwargs: calls.append('config'))
    monkeypatch.setattr(imports_module, 'confirm_billable', lambda *args, **kwargs: False)
    monkeypatch.setattr(host, 'start_job', lambda *args, **kwargs: pytest.fail('job started after decline'))
    host.extract_methods_filtered()
    assert calls == []


def test_summary_names_skipped_and_failed_articles():
    text = method_extraction_summary({
        'processed': 3, 'skipped': 2, 'skipped_titles': ['甲', '乙'],
        'unavailable': 0, 'image_only': 1, 'image_only_titles': ['丙'],
        'candidates': 4, 'created': 2, 'updated': 2, 'eligible': 1,
        'failed': [{'title': '丁', 'error': '方法步骤须为2至6条。'}]})
    assert '复用缓存 2 篇（甲、乙）' in text
    assert '仅图片 1 篇（丙）' in text
    assert '候选方法 4 条（新增 2，补充证据 2）' in text
    assert '其中 1 条可启用' in text
    assert '失败 1 篇（丁：方法步骤须为2至6条。）' in text
