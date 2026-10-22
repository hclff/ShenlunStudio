"""Method-only extraction: merge by identity, per-article failures, cache reuse."""
import pytest

from shenlun.method_library import merge_article_methods, validate_article_card
from shenlun.service import AppService
from shenlun.storage import Store


CONFIG = {'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture',
          'session_token_budget': 150000}


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.spent_tokens = 0
        self.calls = 0

    def complete(self, system, payload, cancel=None):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        data = reply(payload) if callable(reply) else reply
        return {'data': data, 'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
                'model': 'fixture'}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'methods.db')


def service(store, replies):
    app = AppService(store, CONFIG, '')
    app.client = ScriptedClient(replies)
    return app


def add_article(store, aid, body):
    store.upsert_article({'id': aid, 'title': f'文章{aid}', 'journal': '人民日报', 'year': 2026, 'issue': 1,
                          'url': 'https://www.qstheory.cn/' + aid, 'body': body, 'status': 'available',
                          'kind': '评论', 'paragraphs': [{'id': aid + ':p1', 'text': body}]})


def method_payload(name='解释措施作用机制'):
    def build(payload):
        paragraph = payload['paragraphs'][0]
        return {'methods': [{'name': name, 'purpose': '把措施转为论证',
                             'steps': ['说明行动', '解释如何改变结果'],
                             'applicable_types': ['文章写作'],
                             'not_applicable': '仅需简要概括时不扩写',
                             'refs': [{'id': paragraph['id'], 'quote': paragraph['text'][:6]}]}]}
    return build


def full_payload(payload):
    paragraph = payload['paragraphs'][0]
    quote = paragraph['text'][:6]
    return {'thesis': '服务要有温度', 'structure': '先叙后议',
            'paragraph_roles': [{'paragraph_id': paragraph['id'], 'role': '论据', 'reason': '支撑主张'}],
            'logic_chains': [{'claim': '服务要落地', 'evidence_refs': [{'id': paragraph['id'], 'quote': quote}],
                              'analysis': '措施改变结果', 'boundary': '材料不足时不展开'}],
            'language_features': [{'feature': '短句', 'applicability': '议论段',
                                   'refs': [{'id': paragraph['id'], 'quote': quote}]}],
            'methods': method_payload()(payload)['methods'],
            'limitations': ['样本有限']}


def test_methods_reuse_a_full_analysis_without_paying_again(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    service(store, [full_payload]).extract(['a'])
    offline = service(store, [])
    result = offline.extract_methods(['a'])
    assert offline.client.calls == 0
    assert result['skipped'] == 1 and result['candidates'] == 1
    assert store.list_cards()[0]['enabled'] is False


def test_methods_reuse_batches_of_an_interrupted_full_analysis(store, monkeypatch):
    """A cancelled full analysis only leaves batch rows; those still count."""
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    original = store.save_analysis

    def skip_merged(article_id, data, version):
        if version == '1' or (version.startswith('extract-') and '-batch' not in version):
            return
        original(article_id, data, version)

    monkeypatch.setattr(store, 'save_analysis', skip_merged)
    service(store, [full_payload]).extract(['a'])
    monkeypatch.setattr(store, 'save_analysis', original)
    offline = service(store, [])
    result = offline.extract_methods(['a'])
    assert offline.client.calls == 0
    assert result['skipped'] == 1 and result['batches'] == 0
    assert result['candidates'] == 1 and result['failed'] == []


def test_methods_merge_across_articles_and_stay_disabled(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    svc = service(store, [method_payload(), method_payload()])
    result = svc.extract_methods(['a', 'b'])
    assert result['processed'] == 2 and result['batches'] == 2
    assert result['candidates'] == 1 and (result['created'], result['updated']) == (1, 1)
    card = store.list_cards()[0]
    assert card['enabled'] is False
    assert card['source_article_ids'] == ['a', 'b']
    assert len(card['source_fingerprints']) == 2
    assert len(card['refs']) == 2
    assert result['eligible'] == 1
    assert validate_article_card(store, card) is None
    svc.enable_card(card['id'], True, expected_card=card)
    assert store.list_cards()[0]['enabled'] is True


def test_resume_after_a_bad_batch_only_pays_for_the_missing_part(store):
    """A failed second chunk must not re-buy the first chunk on the retry."""
    long_paragraphs = [{'id': 'a:p1', 'text': '甲' * 9000}, {'id': 'a:p2', 'text': '乙' * 9000}]
    store.upsert_article({'id': 'a', 'title': '长文', 'journal': '人民日报', 'year': 2026, 'issue': 1,
                          'url': 'https://www.qstheory.cn/a', 'status': 'available', 'kind': '评论',
                          'body': '\n'.join(p['text'] for p in long_paragraphs), 'paragraphs': long_paragraphs})

    def broken(payload):
        data = method_payload()(payload)
        data['methods'][0]['refs'][0]['quote'] = '原文里没有这句话'
        return data

    first = service(store, [method_payload(), broken]).extract_methods(['a'])
    assert first['processed'] == 0 and first['batches'] == 1
    assert [item['id'] for item in first['failed']] == ['a']
    assert store.list_cards() == []

    retry = service(store, [method_payload()])
    second = retry.extract_methods(['a'])
    assert retry.client.calls == 1
    assert second['processed'] == 1 and second['batches'] == 1
    assert second['candidates'] == 1 and second['failed'] == []


def test_empty_cache_is_reused_but_a_corrupt_one_is_refetched(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    svc = service(store, [method_payload()])
    svc.extract_methods(['a'])
    version = svc._method_version(store.get_article('a'))

    # A legitimately empty result must stay free and must not loop on failure.
    store.save_analysis('a', {'methods': []}, version)
    empty = service(store, [])
    assert empty.extract_methods(['a'])['skipped'] == 1
    assert empty.client.calls == 0

    # A truncated row must be treated as absent, not as a permanent failure.
    store.save_analysis('a', {'methods': '被截断'}, version)
    store.save_analysis('a', {'methods': '被截断'}, version + '-batch1')
    retry = service(store, [method_payload()])
    result = retry.extract_methods(['a'])
    assert retry.client.calls == 1
    assert result['processed'] == 1 and result['candidates'] == 1 and result['failed'] == []


def test_cached_rerun_costs_nothing_and_does_not_duplicate_refs(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    service(store, [method_payload(), method_payload()]).extract_methods(['a', 'b'])
    before = store.list_cards()[0]
    offline = service(store, [])
    result = offline.extract_methods(['a', 'b'])
    assert result['skipped'] == 2 and result['batches'] == 0
    assert offline.client.calls == 0
    after = store.list_cards()[0]
    assert after['refs'] == before['refs'] and len(after['variants']) == 2


def test_one_invalid_article_does_not_discard_the_batch(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')

    def broken(payload):
        data = method_payload()(payload)
        data['methods'][0]['refs'][0]['quote'] = '原文里没有这句话'
        return data

    result = service(store, [method_payload(), broken]).extract_methods(['a', 'b'])
    assert result['processed'] == 1
    assert [item['id'] for item in result['failed']] == ['b']
    card = store.list_cards()[0]
    assert {ref['id'].split(':')[0] for ref in card['refs']} == {'a'}
    assert result['eligible'] == 0


def test_estimate_counts_only_extractable_articles(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    store.upsert_article({'id': 'image', 'title': '只有图片', 'body': '', 'status': 'available',
                          'paragraphs': [], 'training_eligible': False})
    estimate = service(store, []).estimate_method_extraction(['a', 'b', 'image'])
    assert estimate['articles'] == 2
    assert estimate['characters'] == 28
    assert estimate['estimated_batches'] == 2


def test_list_article_ids_follows_filters_without_paging(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    store.set_article_tags(['a'], ['评论'])
    assert store.list_article_ids(tags=['评论']) == ['a']
    assert store.list_article_ids(tags=['评论'], only_text=True) == ['a']
    assert store.list_article_ids(query='助老') == ['b']
    assert set(store.list_article_ids()) == {'a', 'b'}


def test_merge_candidates_keeps_target_wording_and_restores_eligibility(store):
    add_article(store, 'a', '保留人工服务，降低办理门槛。')
    add_article(store, 'b', '设置助老窗口，减少往返次数。')
    svc = service(store, [method_payload('先立后破'), method_payload('破立结合')])
    first = svc.extract_methods(['a'])['candidate_ids'][0]
    second = svc.extract_methods(['b'])['candidate_ids'][0]
    assert first != second
    result = merge_article_methods(store, first, [second])
    assert result['merged'] == 1 and result['refs'] == 2 and result['failed'] == []
    cards = {card['id']: card for card in store.list_cards()}
    merged = cards[first]
    assert merged['name'] == '先立后破'
    assert merged['source_article_ids'] == ['a', 'b']
    assert len(merged['refs']) == 2 and merged['enabled'] is False
    assert validate_article_card(store, merged) is None
    assert store.is_method_deleted('article', second)

    svc.enable_card(first, True, expected_card=merged)
    with pytest.raises(ValueError, match='请先停用'):
        merge_article_methods(store, first, [second])
