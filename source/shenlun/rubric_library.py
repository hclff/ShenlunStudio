"""Named exact-JSON snapshots; no active or historical rubric references."""
import json
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone

from .domain import RUBRICS, validate_rubric
from .state_write import state_write

EDITABLE_FIELDS = frozenset(('name', 'question_type', 'raw_json'))
RECORD_FIELDS = EDITABLE_FIELDS | {'id', 'version', 'created_at', 'updated_at'}


def name_key(name):
    return unicodedata.normalize('NFKC', name).casefold()


def _editable(value):
    if not isinstance(value, dict) or set(value) != EDITABLE_FIELDS:
        raise ValueError('模板字段缺失或含未知字段。')
    name = value['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError('模板名称须为 1—60 字。')
    if not isinstance(value['question_type'], str) or value['question_type'] not in RUBRICS:
        raise ValueError('请选择有效的模板题型。')
    raw = value['raw_json']
    if not isinstance(raw, str):
        raise ValueError('模板须含原始 JSON 文字。')
    validate_rubric(json.loads(raw))
    return {'name': name.strip(), 'question_type': value['question_type'], 'raw_json': raw}


def _version(value, minimum=1):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError('模板版本号无效。')
    return value


def _identity(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError('模板标识无效。') from None
    return value


def _timestamp(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None or instant.timestamp() <= 0:
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise ValueError('模板时间无效。') from None
    return instant


def validate_rubric_preset(value):
    if not isinstance(value, dict) or set(value) != RECORD_FIELDS:
        raise ValueError('模板记录字段缺失或含未知字段。')
    clean = _editable({key: value[key] for key in EDITABLE_FIELDS})
    if clean['name'] != value['name']:
        raise ValueError('模板名称含首尾空格。')
    if _timestamp(value['updated_at']) < _timestamp(value['created_at']):
        raise ValueError('模板更新时间早于创建时间。')
    return {**clean, 'id': _identity(value['id']), 'version': _version(value['version']),
            'created_at': value['created_at'], 'updated_at': value['updated_at']}


def _record(row):
    if row is None:
        return None
    value = validate_rubric_preset(json.loads(row['data']))
    if (row['id'], row['name_key'], row['version']) != (value['id'], name_key(value['name']), value['version']):
        raise ValueError('模板索引与内容不一致。')
    return value


class RubricLibraryStoreMixin:
    def _init_rubric_library(self, conn):
        # Called with the initialization connection, including during guarded restore.
        conn.execute('''CREATE TABLE IF NOT EXISTS rubric_presets(
            id TEXT PRIMARY KEY,name_key TEXT NOT NULL UNIQUE,version INTEGER NOT NULL,data TEXT NOT NULL)''')

    def list_rubric_presets(self, query='', question_type=''):
        with self._connection() as conn:
            rows = [_record(row) for row in conn.execute('SELECT * FROM rubric_presets ORDER BY name_key,id')]
        query = name_key(query.strip())
        return [row for row in rows if query in name_key(row['name'])
                and (not question_type or row['question_type'] == question_type)]

    def get_rubric_preset(self, preset_id):
        _identity(preset_id)
        with self._connection() as conn:
            return _record(conn.execute('SELECT * FROM rubric_presets WHERE id=?', (preset_id,)).fetchone())

    def save_rubric_preset(self, value, *, preset_id=None, expected_version=0, expected_generation):
        clean = _editable(value)
        _version(expected_version, 0)
        if preset_id is not None:
            _identity(preset_id)
        with state_write(self, expected_generation) as conn:
            return self._save_rubric_preset(conn, clean, preset_id=preset_id, expected_version=expected_version)

    def _save_rubric_preset(self, conn, value, *, preset_id=None, expected_version=0):
        """Join an owned state_write transaction, including atomic library copies."""
        if not conn.in_transaction:
            raise ValueError('保存模板需要已开始的事务。')
        clean = _editable(value)
        _version(expected_version, 0)
        if preset_id is not None:
            _identity(preset_id)
        previous = _record(conn.execute('SELECT * FROM rubric_presets WHERE id=?', (preset_id,)).fetchone()) if preset_id else None
        if (preset_id is not None and previous is None) or expected_version != (previous['version'] if previous else 0):
            raise ValueError('模板已更新或删除，请保留输入并重新载入。')
        now = datetime.now(timezone.utc).isoformat()
        record = validate_rubric_preset({**clean, 'id': preset_id or str(uuid.uuid4()),
            'version': expected_version + 1, 'created_at': previous['created_at'] if previous else now, 'updated_at': now})
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False)
        try:
            if previous:
                conn.execute('UPDATE rubric_presets SET name_key=?,version=?,data=? WHERE id=?',
                    (name_key(record['name']), record['version'], payload, record['id']))
            else:
                conn.execute('INSERT INTO rubric_presets VALUES (?,?,?,?)',
                    (record['id'], name_key(record['name']), record['version'], payload))
        except sqlite3.IntegrityError:
            raise ValueError('模板名称已存在，请改用其他名称。') from None
        return record

    def delete_rubric_preset(self, preset_id, *, expected_version, expected_generation):
        _identity(preset_id)
        _version(expected_version)
        with state_write(self, expected_generation) as conn:
            row = _record(conn.execute('SELECT * FROM rubric_presets WHERE id=?', (preset_id,)).fetchone())
            if row is None or row['version'] != expected_version:
                raise ValueError('模板已更新或删除，请保留输入并重新载入。')
            conn.execute('DELETE FROM rubric_presets WHERE id=?', (preset_id,))

    def _validate_rubric_library_restore(self, source, tables):
        if 'rubric_presets' not in tables:
            return
        schema = list(source.execute('PRAGMA table_xinfo(rubric_presets)'))
        if (tuple(row[2].upper() for row in schema) != ('TEXT', 'TEXT', 'INTEGER', 'TEXT')
                or tuple(row[3] for row in schema) != (0, 1, 1, 1)):
            raise ValueError('备份模板表结构不正确。')
        indexes = [row[1] for row in source.execute('PRAGMA index_list(rubric_presets)') if row[2] and not row[4]]
        columns = [tuple(row[2] for row in source.execute('PRAGMA index_info("' + index.replace('"', '""') + '")')) for index in indexes]
        if ('name_key',) not in columns:
            raise ValueError('备份模板名称唯一约束不正确。')
        seen = set()
        for identity, key, version, payload in source.execute('SELECT * FROM rubric_presets'):
            value = _record({'id':identity, 'name_key':key, 'version':version, 'data':payload})
            if type(version) is not int or key in seen:
                raise ValueError('备份模板名称或版本无效。')
            seen.add(name_key(value['name']))
