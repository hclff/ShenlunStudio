"""Article list views read scalar summaries, never deserialized bodies."""
import json
import sqlite3

import pytest

from shenlun.storage import Store, normalize_article

LEGACY_SCHEMA = '''
CREATE TABLE articles (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, journal TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL,
    publication TEXT NOT NULL, search_text TEXT NOT NULL, data TEXT NOT NULL);
'''


def article(title, journal='求是', year=2025, body='正文内容。', kind='评论'):
    # Image-only corpus entries stay reachable but carry no extractable text.
    return {'id': 'a-' + title, 'title': title, 'journal': journal, 'year': year,
            'kind': kind, 'publication': '2025-01', 'body': body, 'author': '作者',
            'status': 'available'}


def legacy_database(path):
    """A 0.9.5 database: articles has no derived ``year``/``has_body`` columns."""
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
        for item in (article('旧文章', year=2024), article('旧图片', body='', year=2023)):
            value = normalize_article(item)
            search = '\n'.join(value[key] for key in ('title', 'author', 'journal', 'body'))
            conn.execute('INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?)',
                         (value['id'], value['title'], value['journal'], value['kind'],
                          value['status'], value['fingerprint'], value['publication'],
                          search, json.dumps(value, ensure_ascii=False)))
    return path


def test_summaries_omit_body_and_paragraphs(tmp_path):
    store = Store(tmp_path / 'studio.db')
    store.upsert_article(article('要点'))
    row = store.list_article_summaries()[0]
    assert 'body' not in row and 'paragraphs' not in row and 'data' not in row
    assert set(row) == {'id', 'title', 'journal', 'year', 'kind', 'status',
                        'publication', 'has_body'}
    assert row['has_body'] is True and row['year'] == 2025


def test_stats_report_text_bearing_without_parsing_bodies(tmp_path):
    store = Store(tmp_path / 'studio.db')
    store.upsert_article(article('有正文'))
    store.upsert_article(article('仅图片', body=''))
    stats = store.stats()
    assert stats['articles'] == 2
    assert stats['text_bearing'] == 1
    assert stats['available'] == 2


def test_year_filter_matches_full_records(tmp_path):
    store = Store(tmp_path / 'studio.db')
    for year in (2023, 2024, 2025):
        store.upsert_article(article(f'文章{year}', year=year))
    summaries = store.list_article_summaries(year='2024')
    assert [row['title'] for row in summaries] == ['文章2024']
    assert store.count_articles(year='2024') == 1
    assert [item['title'] for item in store.list_articles(year='2024')] == ['文章2024']
    assert [row['title'] for row in store.list_article_summaries()] == \
        [item['title'] for item in store.list_articles()]


def test_pagination_matches_count(tmp_path):
    store = Store(tmp_path / 'studio.db')
    for index in range(7):
        store.upsert_article(article(f'文章{index}'))
    first = store.list_article_summaries(limit=3, offset=0)
    second = store.list_article_summaries(limit=3, offset=3)
    assert len(first) == len(second) == 3
    assert not {row['id'] for row in first} & {row['id'] for row in second}
    assert store.count_articles() == 7
    assert len(store.list_article_summaries(limit=3, offset=6)) == 1
    assert store.list_article_summaries(limit=0) == []


def test_batch_fetch_keeps_order_and_skips_missing(tmp_path):
    store = Store(tmp_path / 'studio.db')
    ids = [store.upsert_article(article(f'文章{index}')) for index in range(3)]
    assert [row['id'] for row in store.get_articles([ids[2], 'missing', ids[0]])] == \
        [ids[2], ids[0]]
    assert store.article_titles([ids[1], 'missing']) == {ids[1]: '文章1'}
    assert store.get_articles([]) == [] and store.article_titles([]) == {}


def test_batch_fetch_chunks_large_id_lists(tmp_path):
    store = Store(tmp_path / 'chunked.db')
    for index in range(450):
        store.upsert_article(article(f'文章{index:03d}'))
    ids = [row['id'] for row in store.list_article_summaries(limit=450)]
    fetched = store.get_articles(ids + ['missing'])
    assert len(fetched) == 450
    assert len(store.article_titles(ids)) == 450


def test_summary_query_uses_covering_index(tmp_path):
    path = tmp_path / 'studio.db'
    store = Store(path)
    for index in range(30):
        store.upsert_article(article(f'文章{index:02d}'))
    with sqlite3.connect(path) as conn:
        plan = ' '.join(row[3] for row in conn.execute(
            'EXPLAIN QUERY PLAN SELECT id,title,journal,year,kind,status,publication,has_body '
            'FROM articles ORDER BY publication DESC,title ASC,id ASC LIMIT 100000 OFFSET 0'))
    assert 'COVERING INDEX article_summary_scan' in plan


def strip_derived_columns(path):
    """Turn a current backup into the 0.9.5 shape for restore compatibility."""
    with sqlite3.connect(path) as conn:
        conn.execute('DROP INDEX IF EXISTS article_summary_scan')
        conn.execute('DROP INDEX IF EXISTS article_year')
        conn.execute('ALTER TABLE articles DROP COLUMN has_body')
        conn.execute('ALTER TABLE articles DROP COLUMN year')
        conn.execute('PRAGMA user_version=0')


def test_restore_accepts_legacy_and_current_article_columns(tmp_path):
    source = Store(tmp_path / 'source.db')
    source.upsert_article(article('旧备份文章'))
    legacy = source.backup_to(tmp_path / 'legacy.db')
    strip_derived_columns(legacy)
    target = Store(tmp_path / 'target.db')
    target.restore_from(legacy)
    assert target.stats()['text_bearing'] == 1
    assert target.list_article_summaries()[0]['title'] == '旧备份文章'
    current = source.backup_to(tmp_path / 'current.db')
    target.restore_from(current)
    assert target.count_articles() == 1


def test_restore_still_rejects_unknown_article_columns(tmp_path):
    source = Store(tmp_path / 'source.db')
    source.upsert_article(article('文章'))
    backup = source.backup_to(tmp_path / 'foreign.db')
    with sqlite3.connect(backup) as conn:
        conn.execute('ALTER TABLE articles ADD COLUMN injected TEXT')
    with pytest.raises(ValueError):
        Store(tmp_path / 'target.db').restore_from(backup)


def test_legacy_database_gains_columns_and_backfills(tmp_path):
    path = tmp_path / 'legacy.db'
    legacy_database(path)
    store = Store(path)
    stats = store.stats()
    assert stats['text_bearing'] == 1
    summaries = {row['title']: row for row in store.list_article_summaries()}
    assert summaries['旧文章']['year'] == 2024 and summaries['旧文章']['has_body'] is True
    assert summaries['旧图片']['has_body'] is False
    # Reopening must not duplicate columns or refill with wrong values.
    reopened = Store(path)
    assert {row['title']: row for row in reopened.list_article_summaries()} == summaries
    reopened.upsert_article(article('新文章', year=2026))
    assert reopened.count_articles(year='2026') == 1
