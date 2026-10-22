"""Multi-term search: AND/OR, weighted ordering and stable paging."""
from shenlun.storage import Store


def seed(store):
    first = store.upsert_article({
        'title': '基层治理的人民立场', 'body': '基层治理要坚持以人民为中心。',
        'journal': '人民日报', 'kind': '评论', 'year': 2025,
        'publication': '2025-03-01 00:00:00'})
    second = store.upsert_article({
        'title': '民生答卷', 'body': '基层治理与民生改善同频共振，民生实事要办到群众心坎上。',
        'journal': '求是', 'kind': '理论', 'year': 2024,
        'publication': '2024-01-01 00:00:00'})
    third = store.upsert_article({
        'title': '数字政府', 'body': '数据共享提升治理效能。',
        'journal': '红旗文稿', 'kind': '理论', 'year': 2026,
        'publication': '2026-01-01 00:00:00'})
    return first, second, third


def test_and_requires_every_term_and_or_accepts_any(tmp_path):
    store = Store(tmp_path / 'search.db')
    seed(store)
    both = store.list_article_summaries('基层 民生', match='and', limit=10)
    assert [row['title'] for row in both] == ['民生答卷']
    assert store.count_articles('基层 民生', match='and') == 1
    either = store.list_article_summaries('基层 民生', match='or', limit=10)
    assert {row['title'] for row in either} == {'基层治理的人民立场', '民生答卷'}
    assert store.count_articles('基层 民生', match='or') == 2


def test_chinese_punctuation_splits_terms(tmp_path):
    store = Store(tmp_path / 'search-split.db')
    seed(store)
    assert store.count_articles('基层，民生', match='and') == 1
    assert store.count_articles('基层、数字', match='or') == 3


def test_relevance_prefers_title_then_journal_then_body(tmp_path):
    store = Store(tmp_path / 'relevance.db')
    store.upsert_article({'title': '旧标题', 'body': '民生。',
                          'publication': '2025-01-01 00:00:00'})
    store.upsert_article({'title': '民生答卷', 'body': '正文。',
                          'publication': '2024-01-01 00:00:00'})
    default = store.list_article_summaries('民生', limit=10)
    assert default[0]['title'] == '旧标题'  # newest first
    ranked = store.list_article_summaries('民生', order='relevance', limit=10)
    assert [row['title'] for row in ranked] == ['民生答卷', '旧标题']
    # A journal hit (20) outranks a body hit (10) even when the body row is newer.
    store.upsert_article({'title': '来源命中', 'body': '正文。', 'journal': '求是',
                          'publication': '2023-01-01 00:00:00'})
    store.upsert_article({'title': '正文命中', 'body': '求是。', 'journal': '人民日报',
                          'publication': '2026-01-01 00:00:00'})
    ranked = store.list_article_summaries('求是', order='relevance', limit=10)
    assert [row['title'] for row in ranked][:2] == ['来源命中', '正文命中']


def test_relevance_counts_repeated_body_hits(tmp_path):
    store = Store(tmp_path / 'freq.db')
    store.upsert_article({'title': '甲', 'body': '生态。', 'publication': '2025-01-01 00:00:00'})
    store.upsert_article({'title': '乙', 'body': '生态。生态。生态。生态。',
                          'publication': '2024-01-01 00:00:00'})
    ranked = store.list_article_summaries('生态', order='relevance', limit=10)
    assert [row['title'] for row in ranked] == ['乙', '甲']


def test_year_order_is_newest_first(tmp_path):
    store = Store(tmp_path / 'year-order.db')
    seed(store)
    assert [row['title'] for row in store.list_article_summaries(order='year', limit=10)] == [
        '数字政府', '基层治理的人民立场', '民生答卷']


def test_paging_matches_the_count_without_gaps(tmp_path):
    store = Store(tmp_path / 'paging.db')
    seed(store)
    total = store.count_articles('治理', match='or')
    seen = []
    offset = 0
    while offset < total:
        page = store.list_article_summaries('治理', match='or', order='relevance',
                                            limit=2, offset=offset)
        seen.extend(row['id'] for row in page)
        offset += 2
    assert len(seen) == total and len(set(seen)) == total


def test_filters_still_combine_with_terms_and_tags(tmp_path):
    store = Store(tmp_path / 'filter.db')
    seed(store)
    store.update_articles([store.list_article_summaries(query='民生答卷')[0]['id']],
                          {'tags': ['民生']})
    rows = store.list_article_summaries('民生', tags=('民生',), match='and', limit=10)
    assert [row['title'] for row in rows] == ['民生答卷']
    assert store.count_articles('民生', journal='求是', tags=('民生',)) == 1
    assert store.list_article_summaries('民生', journal='人民日报', limit=10) == []
