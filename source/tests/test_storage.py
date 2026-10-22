import sqlite3
from concurrent.futures import ThreadPoolExecutor

from shenlun.storage import Store


def test_chinese_search_preserves_sources_and_quotes(tmp_path):
    store = Store(tmp_path / 'corpus.db')
    first = store.upsert_article({'url': 'https://www.qstheory.cn/a', 'title': '推进基层治理', 'body': '坚持以人民为中心。\n提升公共服务水平。', 'journal': '求是', 'kind': '理论'})
    second = store.upsert_article({'url': 'https://www.qstheory.cn/b', 'title': '另一来源', 'body': '坚持以人民为中心。\n提升公共服务水平。', 'journal': '红旗文稿'})
    missing = store.upsert_article({'url': 'https://www.qstheory.cn/missing', 'title': '待补文章', 'status': 'unavailable'})
    assert first != second
    assert [a['id'] for a in store.list_articles('基层治理')] == [first]
    assert len(store.list_articles('公共服务')) == 2
    for hostile in ["' OR 1=1 --", '"基层" AND (', '%', '_']:
        assert store.list_articles(hostile) == []
    assert store.list_articles(journal='求是', kind='理论')[0]['id'] == first
    assert store.get_article(first)['paragraphs'] == [{'id': first + ':p1', 'text': '坚持以人民为中心。'}, {'id': first + ':p2', 'text': '提升公共服务水平。'}]
    assert store.get_article(missing)['body'] == ''
    assert store.stats() == {'articles': 3, 'available': 2, 'unavailable': 1, 'unique_bodies': 1,
                             'text_bearing': 2, 'analyses': 0, 'cards': 0, 'reviews': 0}
    assert store.upsert_article(store.get_article(first)) == first
    assert Store(tmp_path / 'corpus.db').stats()['articles'] == 3
    with sqlite3.connect(tmp_path / 'corpus.db') as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_json_roundtrip_and_worker_connections(tmp_path):
    store = Store(tmp_path / 'app.db')
    store.set_setting('设置', {'中文': [1, True, None]})
    assert store.get_setting('设置') == {'中文': [1, True, None]}
    assert store.get_setting('missing', []) == []
    article_id = store.upsert_article({'body': '段落'})
    store.save_analysis(article_id, {'refs': [article_id + ':p1']}, 'v2')
    assert store.get_analysis(article_id) is None
    assert store.get_analysis(article_id, 'v2')['refs'] == [article_id + ':p1']
    review_id = store.save_review({'title': '批改', 'submission': {'answer': '答案'}, 'assessment': {'total_score': None}})
    assert store.get_review(review_id)['created_at']
    assert store.list_reviews()[0]['submission']['answer'] == '答案'
    card_id = store.save_card({'title': '方法', 'evidence': ['原文']})
    assert store.list_cards(enabled_only=True) == []
    store.set_card_enabled(card_id, True)
    assert store.list_cards(enabled_only=True)[0]['enabled'] is True
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: store.record_usage({'input_tokens': i, 'output_tokens': 2, 'total_tokens': i + 2}), range(8)))
    assert store.usage_summary() == {'requests': 8, 'input_tokens': 28, 'output_tokens': 16, 'total_tokens': 44}
    assert store.stats()['analyses'] == 1
    assert store.stats()['reviews'] == 1
    assert store.stats()['cards'] == 1


def test_compact_returns_pages_freed_by_a_drop(tmp_path):
    store = Store(tmp_path / 'app.db')
    for index in range(40):
        store.upsert_article({'title': f'文章 {index}', 'body': f'正文 {index} ' * 400})
    with sqlite3.connect(store.path) as conn:
        conn.execute('CREATE TABLE scratch AS SELECT * FROM articles')
    assert store.reclaimable()['free'] == 0
    with sqlite3.connect(store.path) as conn:
        conn.execute('DROP TABLE scratch')
    before = store.reclaimable()
    assert before['free'] > 0
    result = store.compact()
    assert result['after']['free'] == 0
    assert result['after']['pages'] < before['pages']
    assert store.stats()['articles'] == 40
