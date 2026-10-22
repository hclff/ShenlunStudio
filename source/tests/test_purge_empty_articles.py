"""Only rows without readable text may go, and never without a backup or a guard check."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from purge_empty_articles import collect, purge

from shenlun.storage import Store


def build(path):
    """A database with one text article, two blank ones and a stale analysis row."""
    store = Store(path)
    text_id = store.upsert_article({'title': '有正文的文章', 'journal': '求是', 'kind': '理论',
                                   'body': '第一段\n第二段'})
    empty_id = store.upsert_article({'title': '经济社会发展统计图表', 'journal': '求是', 'kind': '统计'})
    editor_id = store.upsert_article({'title': '本版责编', 'journal': '人民日报', 'kind': '其他'})
    store.save_analysis(empty_id, {'summary': '旧提炼'})
    return store, text_id, empty_id, editor_id


def rows(path, sql, params=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def test_dry_run_lists_blank_rows_without_writing(tmp_path):
    db = tmp_path / 'studio.db'
    _, text_id, empty_id, editor_id = build(db)

    report = collect(db)
    assert report['total'] == 3
    assert {item['id'] for item in report['candidates']} == {empty_id, editor_id}
    assert report['anomalous'] == [] and report['skipped'] == []

    assert purge(db)['deleted'] == 0
    assert rows(db, 'SELECT COUNT(*) FROM articles')[0][0] == 3
    assert rows(db, 'SELECT COUNT(*) FROM analyses')[0][0] == 1


def test_apply_backs_up_before_deleting(tmp_path):
    db = tmp_path / 'studio.db'
    _, text_id, empty_id, _ = build(db)

    report = purge(db, apply=True)
    assert report['deleted'] == 2
    assert report['remaining'] == 1
    backup = Path(report['backup'])
    assert backup.is_file() and backup.parent == db.parent
    # The backup keeps every row, including the stale analysis row.
    assert rows(backup, 'SELECT COUNT(*) FROM articles')[0][0] == 3
    assert rows(db, 'SELECT id FROM articles')[0][0] == text_id
    assert rows(db, 'SELECT COUNT(*) FROM analyses WHERE article_id=?', (empty_id,))[0][0] == 0


def test_second_run_finds_nothing_and_takes_no_backup(tmp_path):
    db = tmp_path / 'studio.db'
    build(db)
    first = purge(db, apply=True)
    second = purge(db, apply=True)
    assert second['candidates'] == [] and second['deleted'] == 0 and second['backup'] == ''
    assert Path(first['backup']).is_file()


def test_flag_without_blank_body_is_reported_not_deleted(tmp_path):
    db = tmp_path / 'studio.db'
    _, _, empty_id, _ = build(db)
    store = Store(db)
    article = store.get_article(empty_id)
    article['body'] = '正文其实还在'
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE articles SET data=?,has_body=0 WHERE id=?',
                     (json.dumps(article, ensure_ascii=False), empty_id))

    report = collect(db)
    assert [item['id'] for item in report['anomalous']] == [empty_id]
    assert empty_id not in [item['id'] for item in report['candidates']]
    purge(db, apply=True)
    assert rows(db, 'SELECT COUNT(*) FROM articles WHERE id=?', (empty_id,))[0][0] == 1


def test_referenced_article_is_skipped(tmp_path):
    db = tmp_path / 'studio.db'
    store, _, empty_id, _ = build(db)
    store.save_card({'id': 'method-empty', 'enabled': False, 'source_article_ids': [empty_id]})

    report = collect(db)
    assert [item['id'] for item in report['skipped']] == [empty_id]
    purge(db, apply=True)
    assert rows(db, 'SELECT COUNT(*) FROM articles WHERE id=?', (empty_id,))[0][0] == 1
