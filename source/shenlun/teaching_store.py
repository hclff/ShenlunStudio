"""Append-only teaching methods on independent Store connections; no active seeds."""
import json
import uuid
from datetime import datetime, timezone

from .teaching import (_identity, _text, teaching_content_hash, validate_teaching_method,
                       validate_teaching_record)
from .teaching_catalog import builtin_teaching_methods
from .method_library import MethodLibraryMixin
from .teaching_owners import TeachingOwnerStoreMixin

# Twelve new built-ins must not consume the 488 local slots of version 0.8.1.
MAX_TEACHING_METHODS = 512
MAX_TEACHING_HISTORY_IDS = 4096


class TeachingStoreMixin(TeachingOwnerStoreMixin,MethodLibraryMixin):
    def _init_teaching(self,conn):
        self._init_method_library(conn)
        self._init_teaching_owners(conn)
        conn.execute('''CREATE TABLE IF NOT EXISTS teaching_method_versions(
            id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(id,version))''')

    def _latest_teaching(self,conn,identity):
        row = conn.execute('SELECT data FROM teaching_method_versions WHERE id=? ORDER BY version DESC LIMIT 1',(identity,)).fetchone()
        return validate_teaching_record(json.loads(row[0])) if row else None

    def _teaching_version(self,previous,expected_version):
        latest = previous['version'] if previous else 0
        if expected_version is not None and (type(expected_version) is not int or not 0<=expected_version<=2147483647):
            raise ValueError('预期版本号须为非负整数。')
        if expected_version is not None and expected_version!=latest:
            raise ValueError('教学方法已在其他窗口更新，请保留编辑内容并重新载入。')
        if latest>=2147483647:
            raise ValueError('教学方法版本号已达上限。')
        return latest+1

    def _append_teaching(self,conn,editable,version,status='draft',reviewer='',note=''):
        record = {**editable,'version':version,'enabled':status=='enabled','status':status,
                  'reviewer':reviewer,'note':note,'created_at':datetime.now(timezone.utc).isoformat()}
        record['content_hash'] = teaching_content_hash(record)
        record = validate_teaching_record(record)
        conn.execute('INSERT INTO teaching_method_versions(id,version,data) VALUES(?,?,?)',
                     (record['id'],version,json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':'))))
        return record

    def _check_teaching_cap(self,conn,identities):
        local = {row[0] for row in conn.execute('SELECT DISTINCT id FROM teaching_method_versions')}
        builtin = {row['id'] for row in builtin_teaching_methods()}
        deleted={row[0] for row in conn.execute("SELECT method_id FROM method_library_trash WHERE kind='teaching'")}
        purged={row[0] for row in conn.execute("SELECT method_id FROM method_library_purged WHERE kind='teaching'")}
        deleted.update(purged)
        if len(((local | builtin)-deleted) | set(identities))>MAX_TEACHING_METHODS:
            raise ValueError(f'当前教学方法最多保留{MAX_TEACHING_METHODS}个不同标识。')
        if len(local | builtin | purged | set(identities))>MAX_TEACHING_HISTORY_IDS:
            raise ValueError('教学方法历史标识数量已达上限。')

    def list_teaching_methods(self,enabled_only=False,*,include_deleted=False):
        if type(enabled_only) is not bool:
            raise ValueError('启用筛选须为布尔值。')
        result = {r['id']:r for r in builtin_teaching_methods()}
        with self._connection() as conn:
            rows = conn.execute('''SELECT v.data FROM teaching_method_versions v JOIN
                (SELECT id,max(version) AS version FROM teaching_method_versions GROUP BY id) latest
                ON v.id=latest.id AND v.version=latest.version''')
            for row in rows:
                record = validate_teaching_record(json.loads(row[0]))
                result[record['id']] = record
            if not include_deleted:
                deleted={row[0] for row in conn.execute("SELECT method_id FROM method_library_trash WHERE kind='teaching'")}
                result={key:value for key,value in result.items() if key not in deleted}
            purged={row[0] for row in conn.execute("SELECT method_id FROM method_library_purged WHERE kind='teaching'")}
            result={key:value for key,value in result.items() if key not in purged}
        return [result[i] for i in sorted(result) if not enabled_only or result[i]['enabled']]

    def get_teaching_method(self,identity,*,include_deleted=False):
        identity = _identity(identity)
        with self._connection() as conn:
            if self._method_purged(conn,'teaching',identity):return None
            if not include_deleted and self._method_deleted(conn,'teaching',identity):return None
            row = self._latest_teaching(conn,identity)
        return row or next((r for r in builtin_teaching_methods() if r['id']==identity),None)

    def list_teaching_versions(self,identity):
        identity = _identity(identity)
        with self._connection() as conn:
            if self._method_purged(conn,'teaching',identity):return []
            return [validate_teaching_record(json.loads(row[0])) for row in conn.execute(
                'SELECT data FROM teaching_method_versions WHERE id=? ORDER BY version DESC',(identity,))]

    def save_teaching_method(self,value,expected_version=None):
        editable = validate_teaching_method(value)
        editable.setdefault('id','tm-'+uuid.uuid4().hex)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,'teaching',editable['id'])
            previous = self._latest_teaching(conn,editable['id'])
            self._require_teaching_owner(conn,editable['owner_id'])
            version = self._teaching_version(previous,expected_version)
            self._check_teaching_cap(conn,[editable['id']])
            # Editing any previously enabled/disabled method revokes its live use.
            status = 'disabled' if previous and previous['status']!='draft' else 'draft'
            return self._append_teaching(conn,editable,version,status)

    def set_teaching_method_enabled(self,identity,enabled,reviewer='',note='',expected_version=None):
        identity = _identity(identity)
        if type(enabled) is not bool:
            raise ValueError('启用操作须明确为布尔值。')
        reviewer = _text(reviewer,'本地审核者',200,empty=not enabled)
        note = _text(note,'本地审核说明',4000,empty=not enabled)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,'teaching',identity)
            previous = self._latest_teaching(conn,identity)
            version = self._teaching_version(previous,expected_version)
            source = previous or next((r for r in builtin_teaching_methods() if r['id']==identity),None)
            if source is None:
                raise ValueError('教学方法不存在。')
            self._require_teaching_owner(conn,source['owner_id'])
            return self._append_teaching(conn,validate_teaching_method(source),version,
                                         'enabled' if enabled else 'disabled',reviewer,note)

    def import_teaching_methods(self,rows):
        if not isinstance(rows,list) or not 1<=len(rows)<=100:
            raise ValueError('每次导入须为1—100项教学方法。')
        prepared = [validate_teaching_method(row) for row in rows]
        for row in prepared:
            row.setdefault('id','tm-'+uuid.uuid4().hex)
        identities = [r['id'] for r in prepared]
        if len(set(identities))!=len(identities):
            raise ValueError('导入教学方法的标识重复。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for row in prepared:self._require_teaching_owner(conn,row['owner_id'])
            existing = {row[0] for row in conn.execute('SELECT DISTINCT id FROM teaching_method_versions')}
            existing.update(row['id'] for row in builtin_teaching_methods())
            existing.update(row[0] for row in conn.execute("SELECT method_id FROM method_library_purged WHERE kind='teaching'"))
            # Imports are new drafts. Colliding identifiers are cloned so a file
            # cannot silently replace or disable a currently trusted local method.
            for row in prepared:
                if row['id'] in existing:
                    row['id'] = 'tm-'+uuid.uuid4().hex
                existing.add(row['id'])
            self._check_teaching_cap(conn,[row['id'] for row in prepared])
            result = []
            for row in prepared:
                result.append(self._append_teaching(conn,row,1))
            return result

    def export_teaching_methods(self):
        return [validate_teaching_method(r) for r in self.list_teaching_methods()]

    def _validate_teaching_restore(self,source,tables):
        known_owners = self._validate_teaching_owners_restore(source,tables)
        if 'teaching_method_versions' not in tables:
            return
        schema = source.execute("SELECT type FROM sqlite_master WHERE name='teaching_method_versions'").fetchone()
        if not schema or schema[0]!='table':
            raise ValueError('教学方法备份须为实际数据表。')
        if source.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='teaching_method_versions'").fetchone():
            raise ValueError('教学方法备份不能包含触发器。')
        columns = source.execute('PRAGMA table_info(teaching_method_versions)').fetchall()
        if [(r[1],r[2].upper(),r[3],r[5]) for r in columns]!= [('id','TEXT',1,1),('version','INTEGER',1,2),('data','TEXT',1,0)]:
            raise ValueError('教学方法备份表结构不符合版本约定。')
        identities = set()
        latest = {r['id']:r for r in builtin_teaching_methods()}
        for identity,version,data in source.execute('SELECT id,version,data FROM teaching_method_versions'):
            if not isinstance(data,str) or len(data.encode('utf8'))>65536:
                raise ValueError('教学方法备份记录过长或格式无效。')
            try:
                record = validate_teaching_record(json.loads(data))
            except (TypeError,ValueError,OverflowError,RecursionError):
                raise ValueError('教学方法备份记录无效。') from None
            if record['id']!=identity or type(version) is not int or record['version']!=version:
                raise ValueError('教学方法备份索引与记录内容不一致。')
            if record['owner_id'] not in known_owners:
                raise ValueError('教学方法备份引用了未登记主体。')
            identities.add(identity)
            if identity not in latest or record['version']>latest[identity]['version']:
                latest[identity]=record
        all_ids=identities | {r['id'] for r in builtin_teaching_methods()}
        deleted=({r[0] for r in source.execute("SELECT method_id FROM method_library_trash WHERE kind='teaching'")}
                 if 'method_library_trash' in tables else set())
        if 'method_library_purged' in tables:
            purged={r[0] for r in source.execute("SELECT method_id FROM method_library_purged WHERE kind='teaching'")}
            deleted.update(purged);all_ids.update(purged)
        if len(all_ids-deleted)>MAX_TEACHING_METHODS or len(all_ids)>MAX_TEACHING_HISTORY_IDS:
            raise ValueError('备份教学方法数量超过上限。')
        if 'teaching_owners' in tables:
            live = {o['id'] for o in self._teaching_owner_rows(source)}
            if any(r['owner_id'] not in live and identity not in deleted for identity,r in latest.items()):
                raise ValueError('备份中已删除主体仍有关联方法未移入已删除列表。')

