"""Offscreen review of disabled extraction candidates and the two enable paths."""
from PySide6.QtCore import Qt

from shenlun.service import AppService
from shenlun.storage import Store
from shenlun.ui.library import candidate_pool as pool
from shenlun.ui.library.candidate_pool import CandidatePoolDialog, candidate_cards, evidence_count


CONFIG = {'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture', 'session_token_budget': 150000}


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, system, payload, cancel=None):
        reply = self.replies.pop(0)
        data = reply(payload) if callable(reply) else reply
        return {'data': data, 'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
                'model': 'fixture'}


class FakeResultDialog:
    def __init__(self, results, parent=None):
        self.results = results

    def exec(self):
        return 0


def add_article(store, aid, body):
    store.upsert_article({'id': aid, 'title': f'文章{aid}', 'journal': '人民日报', 'year': 2026, 'issue': 1,
                          'url': 'https://www.qstheory.cn/' + aid, 'body': body, 'status': 'available',
                          'kind': '评论', 'paragraphs': [{'id': aid + ':p1', 'text': body}]})


def method_payload(name):
    def build(payload):
        paragraph = payload['paragraphs'][0]
        return {'methods': [{'name': name, 'purpose': '把措施转为论证',
                             'steps': ['说明行动', '解释如何改变结果'],
                             'applicable_types': ['文章写作'],
                             'not_applicable': '仅需简要概括时不扩写',
                             'refs': [{'id': paragraph['id'], 'quote': paragraph['text'][:6]}]}]}
    return build


def service(store, replies):
    app = AppService(store, CONFIG, '')
    app.client = ScriptedClient(replies)
    return app


def seeded(tmp_path):
    store = Store(tmp_path / 'pool.db')
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    add_article(store, 'c', '公开办理进度，回应群众关切。')
    app = service(store, [method_payload('解释措施作用机制'), method_payload('解释措施作用机制'),
                          method_payload('公开进度回应关切')])
    app.extract_methods(['a', 'b'])
    app.extract_methods(['c'])
    return store


def test_pool_shows_all_candidates_with_evidence_and_checks_eligible(app, tmp_path):
    store = seeded(tmp_path)
    dialog = CandidatePoolDialog(store)
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 1).text() == '解释措施作用机制'
    assert dialog.table.item(0, 3).text() == '2'
    assert dialog.table.item(0, 4).text() == '可启用'
    assert dialog.table.item(1, 4).text() == '证据不足'
    assert '至少需要两篇' in dialog.table.item(1, 4).toolTip()
    assert [card['name'] for card in dialog.checked_cards()] == ['解释措施作用机制']
    dialog.only_eligible.setChecked(True)
    assert dialog.table.rowCount() == 1
    dialog.close()


def test_batch_enable_only_passes_eligible_candidates(app, tmp_path, monkeypatch):
    store = seeded(tmp_path)
    dialog = CandidatePoolDialog(store)
    monkeypatch.setattr(pool, 'confirm_batch', lambda *args, **kwargs: True)
    monkeypatch.setattr(pool, 'MethodBatchResultDialog', FakeResultDialog)
    changed = []
    dialog.changed.connect(lambda: changed.append(True))
    dialog.enable_selected()
    cards = {card['name']: card for card in store.list_cards()}
    assert cards['解释措施作用机制']['enabled'] is True
    assert cards['公开进度回应关切']['enabled'] is False
    assert changed and dialog.table.rowCount() == 1  # enabled card left the pool
    dialog.close()


def test_merge_in_pool_reaches_two_article_evidence_and_keeps_cards_recoverable(app, tmp_path, monkeypatch):
    store = seeded(tmp_path)
    dialog = CandidatePoolDialog(store)
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    from shenlun.ui.common import dialogs
    monkeypatch.setattr(dialogs, 'confirm_destructive', lambda *args, **kwargs: True)
    dialog.merge_selected()
    card = next(card for card in store.list_cards() if card['name'] == '解释措施作用机制')
    assert evidence_count(card) == 3
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 4).text() == '可启用'
    assert '已合并' in dialog.status.text()
    dialog.close()


def test_pool_ignores_enabled_and_manual_cards(app, tmp_path):
    store = seeded(tmp_path)
    card = next(card for card in store.list_cards() if card['name'] == '解释措施作用机制')
    app_service = AppService(store, {}, '')
    app_service.enable_card(card['id'], True, expected_card=card)
    store.save_card({'id': 'method-manual', 'name': '手写方法', 'purpose': '自拟',
                     'steps': ['一步', '两步'], 'applicable_types': ['文章写作'],
                     'not_applicable': '不适用时不用', 'refs': [{'id': 'a:p1', 'quote': '保留人工服务'}],
                     'enabled': False, 'status': '手动方法草稿'})
    names = [item['name'] for item in candidate_cards(store)]
    assert names == ['公开进度回应关切']
