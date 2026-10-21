"""Exact rubric snapshots and restore-safe Store behavior on real SQLite."""
import json
import sqlite3

import pytest

from shenlun.storage import Store


RAW = ' [ {"id":"a","name":"甲","weight":33.333333333333333333,"extra":{"x":0.1234567890123456789}},\n {"id":"b","name":"乙","weight":66.666666666666666667} ] '


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'library.db')
    return value


def test_named_rubric_snapshot_api_exists(store):
    assert hasattr(store, 'save_rubric_preset'), 'named rubric snapshot API is required'


def save(store, name='我的量表', **kwargs):
    return store.save_rubric_preset({'name': name, 'question_type': '归纳概括', 'raw_json': RAW},
        expected_generation=store.assessment_generation(), **kwargs)


def test_exact_restart_filters_and_allowlisted_fields(store):
    row = save(store, '  我的量表  ')
    assert row['name'] == '我的量表' and row['version'] == 1
    assert Store(store.path).get_rubric_preset(row['id'])['raw_json'] == RAW
    assert store.list_rubric_presets('量表', '归纳概括') == [row]
    assert store.list_rubric_presets(question_type='文章写作') == []
    assert store.list_rubric_presets("' OR 1=1 --") == []
    assert set(row) == {'id', 'name', 'question_type', 'raw_json', 'version', 'created_at', 'updated_at'}


@pytest.mark.parametrize('first,second', [('Straße', ' STRASSE '), ('Café', 'Cafe\u0301'), ('ＡＢＣ', 'abc')])
def test_normalized_unicode_name_collisions_never_overwrite(store, first, second):
    row = save(store, first)
    with pytest.raises(ValueError):
        save(store, second)
    assert store.list_rubric_presets() == [row]


@pytest.mark.parametrize('field,value', [('name',' '), ('name','字'*61), ('name',12),
    ('question_type','未知'), ('raw_json','[{'), ('raw_json','[]'), ('raw_json',[]), ('api_key','secret')])
def test_invalid_values_do_not_write(store, field, value):
    data = {'name':'新量表', 'question_type':'归纳概括', 'raw_json':RAW, field:value}
    with pytest.raises(ValueError):
        store.save_rubric_preset(data, expected_generation=store.assessment_generation())
    assert store.list_rubric_presets() == []


def test_versioned_update_delete_and_missing_id_never_upsert(store):
    first = save(store)
    other = Store(store.path)
    updated = save(other, '另一个名字', preset_id=first['id'], expected_version=1)
    assert updated['id'] == first['id'] and updated['version'] == 2
    assert updated['created_at'] == first['created_at']
    with pytest.raises(ValueError):
        save(store, '旧窗口', preset_id=first['id'], expected_version=1)
    with pytest.raises(ValueError):
        store.delete_rubric_preset(first['id'], expected_version=1, expected_generation=store.assessment_generation())
    other.delete_rubric_preset(first['id'], expected_version=2, expected_generation=other.assessment_generation())
    with pytest.raises(ValueError):
        save(store, preset_id=first['id'], expected_version=2)
    assert store.get_rubric_preset(first['id']) is None


def test_backup_restore_exact_and_stale_generation_writes_rejected(store, tmp_path):
    row = save(store)
    backup = tmp_path / 'backup.db'
    store.backup_to(backup)
    generation = store.assessment_generation()
    store.restore_from(backup)
    assert store.get_rubric_preset(row['id'])['raw_json'] == RAW
    with pytest.raises(ValueError):
        store.save_rubric_preset({'name':'过期', 'question_type':'归纳概括', 'raw_json':RAW}, expected_generation=generation)
    with pytest.raises(ValueError):
        store.delete_rubric_preset(row['id'], expected_version=1, expected_generation=generation)


def test_old_backup_without_table_restores_empty_library(store, tmp_path):
    old = tmp_path / 'old.db'
    store.backup_to(old)
    with sqlite3.connect(old) as conn:
        conn.execute('DROP TABLE rubric_presets')
    save(store)
    store.restore_from(old)
    assert store.list_rubric_presets() == []


@pytest.mark.parametrize('corruption', ['raw', 'key', 'version', 'id', 'timestamp', 'type', 'extra', 'unique', 'duplicate'])
def test_corrupt_backup_rejected_before_active_changes(store, tmp_path, corruption):
    row = save(store)
    backup = tmp_path / 'bad.db'
    store.backup_to(backup)
    with sqlite3.connect(backup) as conn:
        if corruption in ('unique', 'duplicate'):
            conn.execute('ALTER TABLE rubric_presets RENAME TO old_presets')
            conn.execute('CREATE TABLE rubric_presets(id TEXT PRIMARY KEY,name_key TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL)')
            conn.execute('INSERT INTO rubric_presets SELECT * FROM old_presets')
            conn.execute('DROP TABLE old_presets')
            if corruption == 'duplicate':
                copy = {**row, 'id':'00b80f8b-b259-4010-83d3-aa27737d3c89'}
                conn.execute('INSERT INTO rubric_presets VALUES (?,?,?,?)', (copy['id'], row['name'].casefold(),1,json.dumps(copy)))
        else:
            data = dict(row)
            if corruption == 'raw': data['raw_json'] = '[]'
            if corruption == 'version': data['version'] = True
            if corruption == 'id': data['id'] = 'different'
            if corruption == 'timestamp': data['created_at'] = 'yesterday'
            if corruption == 'type': data['question_type'] = '未知'
            if corruption == 'extra': data['api_key'] = 'secret'
            conn.execute('UPDATE rubric_presets SET data=?,name_key=?', (json.dumps(data), 'wrong' if corruption=='key' else row['name'].casefold()))
    generation = store.assessment_generation()
    with pytest.raises(ValueError): store.restore_from(backup)
    assert store.get_rubric_preset(row['id']) == row
    assert store.assessment_generation() == generation


def test_empty_backup_table_must_have_declared_types_and_required_constraints(store, tmp_path):
    backup = tmp_path / 'schema.db'; store.backup_to(backup)
    with sqlite3.connect(backup) as conn:
        conn.execute('DROP TABLE rubric_presets')
        conn.execute('CREATE TABLE rubric_presets(id INTEGER PRIMARY KEY,name_key TEXT UNIQUE,version TEXT,data TEXT)')
    with pytest.raises(ValueError):
        store.restore_from(backup)
