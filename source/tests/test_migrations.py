"""Ordered schema upgrades: version stamp, pre-migration backup, rollback."""
import json
import sqlite3
from pathlib import Path

import pytest

from shenlun import migrations
from shenlun.migrations import (SCHEMA_VERSION, STEPS, MigrationTooNewError,
                                ensure_schema, migrate)
from shenlun.storage import Store


def legacy_article_database(path, *, with_rows=True):
    """0.9.5 shape: articles and question_bank without the derived columns."""
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE articles(
            id TEXT PRIMARY KEY,title TEXT NOT NULL,journal TEXT NOT NULL,kind TEXT NOT NULL,
            status TEXT NOT NULL,fingerprint TEXT NOT NULL,publication TEXT NOT NULL,
            search_text TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE question_bank(
            id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,title TEXT NOT NULL,
            search_text TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            data TEXT NOT NULL);
    ''')
    if with_rows:
        article = {'id': 'a-1', 'title': '旧文章', 'journal': '求是', 'year': 2024,
                   'body': '正文', 'kind': '理论', 'status': 'available',
                   'publication': '2024-03', 'author': '作者', 'url': ''}
        conn.execute('INSERT INTO articles VALUES(?,?,?,?,?,?,?,?,?)',
                     ('a-1', '旧文章', '求是', '理论', 'available', 'f', '2024-03',
                      '旧文章', json.dumps(article, ensure_ascii=False)))
        planned = {'id': 'q-plan', 'title': '有计划', 'created_at': '2026-01-01',
                   'updated_at': '2026-01-02', 'needs_retry': True,
                   'submission': {'question': '题干', 'materials': '材料',
                                  'question_type': '归纳概括'},
                   'review_plan': {'version': 2, 'status': '复习中',
                                   'due_date': '2026-11-01', 'tags': ['材料']}}
        plain = {'id': 'q-plain', 'title': '无计划', 'created_at': '2026-01-01',
                 'updated_at': '2026-01-03', 'needs_retry': True,
                 'submission': {'question': '题干2', 'materials': '材料2',
                                'question_type': '文章写作'}}
        for value in (planned, plain):
            conn.execute('INSERT INTO question_bank VALUES(?,?,?,?,?,?,?)',
                         (value['id'], 'f-' + value['id'], value['title'], value['title'],
                          value['created_at'], value['updated_at'],
                          json.dumps(value, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return path


def columns(path, table):
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')]


def user_version(path):
    with sqlite3.connect(path) as conn:
        return conn.execute('PRAGMA user_version').fetchone()[0]


def backups(path):
    return sorted(path.parent.glob(path.stem + '-迁移前备份-v*.db'))


def row(path, sql, params=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchone()


def test_fresh_database_is_stamped_with_current_schema_version(tmp_path):
    store = Store(tmp_path / 'fresh.db')
    assert user_version(store.path) == SCHEMA_VERSION
    assert 'year' in columns(store.path, 'articles')
    assert 'plan_status' in columns(store.path, 'question_bank')


def test_legacy_database_gains_projection_columns_and_keeps_rows(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    result = ensure_schema(path)
    assert result['from'] == 0 and result['to'] == SCHEMA_VERSION
    assert result['applied'] == [1, 2, 3]
    assert user_version(path) == SCHEMA_VERSION
    with sqlite3.connect(path) as conn:
        article = conn.execute('SELECT year,has_body FROM articles').fetchone()
        assert article == (2024, 1)
        rows = dict(conn.execute(
            'SELECT id,plan_status||"|"||plan_due FROM question_bank').fetchall())
    assert rows['q-plan'] == '复习中|2026-11-01'
    # A retry flag without an explicit plan reads as 待复习, matching question_plan().
    assert rows['q-plain'] == '待复习|'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM question_bank').fetchone()[0] == 2


def test_question_columns_are_backfilled_from_stored_json(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    ensure_schema(path)
    with sqlite3.connect(path) as conn:
        rows = dict(conn.execute(
            'SELECT id,question_type||"|"||needs_retry FROM question_bank').fetchall())
    assert rows == {'q-plan': '归纳概括|1', 'q-plain': '文章写作|1'}


def test_upgrade_writes_one_backup_before_touching_data(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    result = ensure_schema(path)
    assert result['backup'] is not None and result['backup'].exists()
    # The backup is the pre-migration shape, so a downgrade can still read it.
    assert user_version(result['backup']) == 0
    assert 'year' not in columns(result['backup'], 'articles')
    assert row(result['backup'], 'SELECT count(*) FROM question_bank')[0] == 2


def test_current_database_is_not_backed_up_again(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    ensure_schema(path)
    before = backups(path)
    again = ensure_schema(path)
    assert again['applied'] == [] and again['backup'] is None
    assert backups(path) == before


def test_empty_database_is_stamped_without_a_backup(tmp_path):
    path = legacy_article_database(tmp_path / 'empty.db', with_rows=False)
    result = ensure_schema(path)
    assert result['applied'] == [1, 2, 3]
    assert result['backup'] is None
    assert user_version(path) == SCHEMA_VERSION


def test_newer_database_is_rejected_without_changes(tmp_path):
    path = legacy_article_database(tmp_path / 'future.db')
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
        conn.commit()
    with pytest.raises(MigrationTooNewError) as error:
        ensure_schema(path)
    assert str(SCHEMA_VERSION + 1) in str(error.value)
    assert user_version(path) == SCHEMA_VERSION + 1
    assert 'year' not in columns(path, 'articles')


def test_failed_step_rolls_back_schema_and_version(tmp_path, monkeypatch):
    path = legacy_article_database(tmp_path / 'legacy.db')
    import shenlun.migrations as migrations

    def broken(conn):
        conn.execute('ALTER TABLE articles ADD COLUMN half_done TEXT')
        raise RuntimeError('合成迁移失败')

    monkeypatch.setattr(migrations, 'STEPS', migrations.STEPS + ((4, 'broken', broken),))
    with pytest.raises(RuntimeError):
        ensure_schema(path)
    assert user_version(path) == 0
    assert 'half_done' not in columns(path, 'articles')
    assert 'year' not in columns(path, 'articles')


def test_store_opens_legacy_database_and_keeps_existing_rows(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    store = Store(path)
    assert store.stats()['articles'] == 1
    assert store.list_articles()[0]['title'] == '旧文章'
    assert store.list_questions()[0]['title'] in ('有计划', '无计划')
    assert user_version(path) == SCHEMA_VERSION


def test_database_stamped_before_question_bank_existed_can_save_questions(tmp_path):
    """The bundled seed is stamped once, then tables appear on first open."""
    path = tmp_path / 'seed.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE articles(id TEXT PRIMARY KEY,title TEXT NOT NULL,'
                     'journal TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,'
                     'fingerprint TEXT NOT NULL,publication TEXT NOT NULL,'
                     'search_text TEXT NOT NULL,data TEXT NOT NULL)')
    ensure_schema(path)
    store = Store(path)
    question_id = store.save_question({'question': '题干', 'materials': '材料',
                                       'question_type': '归纳概括', 'max_score': 20})
    assert store.get_question(question_id)['submission']['question_type'] == '归纳概括'
    assert user_version(path) == SCHEMA_VERSION


def test_startup_reports_a_too_new_database_without_a_traceback(tmp_path):
    from shenlun.__main__ import open_store
    path = tmp_path / 'studio.db'
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
        conn.commit()
    messages = []
    store, message = open_store(tmp_path, report=messages.append)
    assert store is None
    assert messages and '升级程序' in messages[0]
    assert message == messages[0]


def test_every_step_file_is_registered_for_packaging():
    """A step file missing from STEPS would never run, and a registered step
    without a file breaks startup: PyInstaller must collect the submodules."""
    directory = Path(migrations.__file__).parent
    on_disk = sorted(path.stem for path in directory.glob('[0-9][0-9][0-9][0-9]_*.py'))
    registered = ['%04d_%s' % (number, name) for number, name, _ in STEPS]
    assert on_disk == registered


def test_migrate_returns_only_newly_applied_steps(tmp_path):
    path = legacy_article_database(tmp_path / 'legacy.db')
    with sqlite3.connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert migrate(conn) == [1, 2, 3]
        assert migrate(conn) == []
        conn.commit()
