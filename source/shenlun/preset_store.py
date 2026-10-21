"""Bounded, versioned method presets stored atomically in settings."""
import json
import uuid
from datetime import datetime, timezone

from .domain import RUBRICS
from .method_library import MethodConflictError, _method_id
from .method_presets import normalize_method_choice
from .teaching import _identity, _text

PRESET_FIELDS=frozenset(('id','name','version','question_type','teaching_ids','article_ids','created_at','updated_at'))
EDITABLE_FIELDS=frozenset(('name','question_type','teaching_ids','article_ids'))
MAX_METHOD_PRESETS=100
MAX_PRESET_VERSION=2147483647


def _dump(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def _timestamp(value):
    value=_text(value,'组合时间',80,strip=False)
    try:
        instant=datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
    except (ValueError,OverflowError):
        raise ValueError('方法组合时间须为含时区的时间。') from None
    return instant


def _version(value,minimum=1):
    if type(value) is not int or not minimum<=value<=MAX_PRESET_VERSION:
        raise ValueError('方法组合版本号无效。')
    return value


def _editable(value):
    if not isinstance(value,dict) or not EDITABLE_FIELDS.issubset(value) or set(value)-PRESET_FIELDS:
        raise ValueError('方法组合字段缺失或含未知字段。')
    name=_text(value['name'],'组合名称',100)
    question_type=value['question_type']
    if not isinstance(question_type,str) or question_type not in RUBRICS:
        raise ValueError('方法组合题型无效。')
    result={'name':name,'question_type':question_type}
    for kind in ('teaching','article'):
        identities=value[kind+'_ids']
        if not isinstance(identities,list) or len(identities)>6:
            raise ValueError('组合的每类方法最多6项。')
        clean=[_method_id(kind,identity) for identity in identities]
        if clean!=identities or len(set(clean))!=len(clean):
            raise ValueError('组合方法标识无效或重复。')
        result[kind+'_ids']=clean
    if not result['teaching_ids'] and not result['article_ids']:
        raise ValueError('方法组合至少须有一个方法。')
    if 'id' in value:_identity(value['id'])
    if 'version' in value:_version(value['version'])
    for field in ('created_at','updated_at'):
        if field in value:_timestamp(value[field])
    return result


def validate_method_preset(value):
    if not isinstance(value,dict) or set(value)!=PRESET_FIELDS:
        raise ValueError('方法组合记录字段缺失或含未知字段。')
    editable=_editable(value)
    if _timestamp(value['updated_at'])<_timestamp(value['created_at']):
        raise ValueError('组合更新时间不能早于创建时间。')
    return {**editable,'id':_identity(value['id']),'version':_version(value['version']),
            'created_at':value['created_at'],'updated_at':value['updated_at']}


def _validated_presets(value):
    if not isinstance(value,list) or len(value)>MAX_METHOD_PRESETS:
        raise ValueError('方法组合须为至多100项的列表。')
    rows=[validate_method_preset(row) for row in value]
    if len({r['id'] for r in rows})!=len(rows):
        raise ValueError('方法组合标识重复。')
    return rows


class MethodPresetStoreMixin:
    def _method_presets(self,conn):
        row=conn.execute("SELECT data FROM settings WHERE key='method_presets'").fetchone()
        return _validated_presets(json.loads(row[0]) if row else [])

    def list_method_presets(self):
        with self._connection() as conn:
            return self._method_presets(conn)

    def get_method_preset(self,identity):
        identity=_identity(identity)
        return next((r for r in self.list_method_presets() if r['id']==identity),None)

    def save_method_preset(self,value,expected_version=None):
        editable=_editable(value)
        if expected_version is not None:_version(expected_version,0)
        identity=value.get('id')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows=self._method_presets(conn)
            previous=next((r for r in rows if r['id']==identity),None)
            if identity is not None and previous is None:
                raise MethodConflictError('方法组合已删除，请保留编辑内容并重新载入。')
            version=previous['version'] if previous else 0
            if expected_version is not None and expected_version!=version:
                raise MethodConflictError('方法组合已在其他窗口更新，请保留编辑内容并重新载入。')
            if version>=MAX_PRESET_VERSION:
                raise ValueError('方法组合版本号已达上限。')
            if previous is None and len(rows)>=MAX_METHOD_PRESETS:
                raise ValueError('最多保留100个方法组合。')
            for kind in ('teaching','article'):
                for member in editable[kind+'_ids']:
                    if previous and member in previous[kind+'_ids']:
                        # Keep explicit old references when renaming; use still
                        # requires every member to be live, enabled and eligible.
                        continue
                    if self._method_deleted(conn,kind,member) or self._library_method(conn,kind,member) is None:
                        raise ValueError(f'组合中的方法 {member} 不存在或已删除，请重新选择。')
            now=datetime.now(timezone.utc).isoformat()
            # Generated identities cannot overwrite a stale or existing preset.
            if previous is None:
                identity='mp-'+uuid.uuid4().hex
                while any(r['id']==identity for r in rows):identity='mp-'+uuid.uuid4().hex
            record=validate_method_preset({**editable,'id':identity,'version':version+1,
                'created_at':previous['created_at'] if previous else now,'updated_at':now})
            rows=[record if r['id']==identity else r for r in rows] if previous else [*rows,record]
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('method_presets',_dump(rows)))
            return record

    def delete_method_preset(self,identity,expected_version=None):
        identity=_identity(identity)
        if expected_version is not None:_version(expected_version,0)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows=self._method_presets(conn)
            previous=next((r for r in rows if r['id']==identity),None)
            if previous is None or (expected_version is not None and expected_version!=previous['version']):
                raise MethodConflictError('方法组合已更新或已删除，请刷新后重试。')
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                         ('method_presets',_dump([r for r in rows if r['id']!=identity])))
            active=conn.execute("SELECT data FROM settings WHERE key='method_choice'").fetchone()
            choice=normalize_method_choice(json.loads(active[0]) if active else None)
            if choice=={'mode':'preset','preset_id':identity}:
                conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('method_choice',_dump({'mode':'off'})))

    def _validate_method_presets_restore(self,source,tables):
        rows=self._method_presets(source)
        raw=source.execute("SELECT data FROM settings WHERE key='method_choice'").fetchone()
        if raw:
            value=json.loads(raw[0])
            if value is None:
                raise ValueError('备份方法组合选择不能为null。')
            choice=normalize_method_choice(value)
            if choice['mode']=='preset' and choice['preset_id'] not in {r['id'] for r in rows}:
                raise ValueError('备份引用了不存在的方法组合。')
        # Deleted or purged methods deliberately remain unavailable references;
        # exact-choice preflight explains them, while historical backups survive.
