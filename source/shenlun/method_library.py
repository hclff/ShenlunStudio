"""Manual article drafts and reversible library deletion, preserving history."""
import json
import uuid
from datetime import datetime, timezone

from .domain import RUBRICS
from .teaching import _text, _strings, _identity, validate_teaching_method
from .method_purge import MethodPurgeMixin


class MethodConflictError(ValueError):
    """A stale or deleted method must not be overwritten by another operation."""


def _kind(value):
    if value not in ('article','teaching'):
        raise ValueError('方法类别须为文章方法或教学方法。')
    return value


def _method_id(kind, identity):
    _kind(kind)
    return _identity(identity) if kind=='teaching' else _text(identity,'方法标识',1024)


def _dump(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class MethodLibraryMixin(MethodPurgeMixin):
    def _init_method_library(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS method_library_trash(
            kind TEXT NOT NULL, method_id TEXT NOT NULL, deleted_at TEXT NOT NULL,
            PRIMARY KEY(kind,method_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS method_library_purged(
            kind TEXT NOT NULL, method_id TEXT NOT NULL, purged_at TEXT NOT NULL, version_hashes TEXT NOT NULL,
            PRIMARY KEY(kind,method_id))''')

    def _method_deleted(self, conn, kind, identity):
        return self._method_purged(conn,kind,identity) or conn.execute('SELECT 1 FROM method_library_trash WHERE kind=? AND method_id=?',
                            (kind,identity)).fetchone() is not None

    def is_method_deleted(self, kind, identity):
        identity=_method_id(kind,identity)
        with self._connection() as conn:
            return self._method_deleted(conn,kind,identity)

    def _require_live_method(self, conn, kind, identity):
        if self._method_purged(conn,kind,identity):raise MethodConflictError('方法已彻底删除，不能恢复、编辑或启用。')
        if self._method_deleted(conn,kind,identity):
            raise MethodConflictError('方法已删除，请从“已删除”中恢复后再编辑或启用。')

    def _library_method(self, conn, kind, identity):
        if self._method_purged(conn,kind,identity):return None
        if kind=='article':
            row=conn.execute('SELECT data FROM cards WHERE id=?',(identity,)).fetchone()
            return json.loads(row[0]) if row else None
        from .teaching_catalog import builtin_teaching_methods
        return self._latest_teaching(conn,identity) or next(
            (r for r in builtin_teaching_methods() if r['id']==identity),None)

    def save_manual_article_method(self, value, identity=None, *, expected_card=None):
        if not isinstance(value,dict):raise ValueError('方法内容须为对象。')
        editable={key:_text(value.get(key,''),label,maximum,empty=key=='not_applicable')
                  for key,label,maximum in [('name','名称',100),('purpose','目的',800),
                                             ('not_applicable','适用边界',2000)]}
        editable['steps']=_strings(value.get('steps',[]),'步骤',15,600,1)
        types=_strings(value.get('applicable_types',[]),'题型',5,100,1)
        if len(set(types))!=len(types) or any(t not in RUBRICS for t in types):
            raise ValueError('请选择有效且不重复的题型。')
        editable['applicable_types']=types
        refs=value.get('refs',[])
        if not isinstance(refs,list) or len(refs)>100:raise ValueError('原文证据最多100条。')
        versions={};clean_refs=[]
        for ref in refs:
            if not isinstance(ref,dict):raise ValueError('原文证据格式不正确。')
            pid=_text(ref.get('id'),'原文段落',1024)
            quote=_text(ref.get('quote'),'精确引文',4000,strip=False)
            aid=pid.rsplit(':p',1)[0]
            article=self.get_article(aid)
            if not article or article.get('status')!='available' or not article.get('training_eligible',True):
                raise ValueError('证据文章不存在、不可读或不适合训练。')
            paragraph=next((p for p in article['paragraphs'] if p['id']==pid),None)
            if not paragraph or quote not in paragraph['text']:
                raise ValueError('引文须原样位于所选文章段落中。')
            item={'id':pid,'quote':quote}
            if item not in clean_refs:clean_refs.append(item)
            versions[aid]=article['fingerprint']
        creating=identity is None
        identity=_method_id('article',identity) if not creating else 'manual-'+uuid.uuid4().hex
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,'article',identity)
            previous=self._library_method(conn,'article',identity)
            if previous is None and not creating:
                raise ValueError('所编辑的方法不存在。')
            if expected_card is not None and previous!=expected_card:
                raise MethodConflictError('方法已在其他窗口更新，请保留编辑内容并重新载入。')
            card={**(previous or {}),**editable,'id':identity,'refs':clean_refs,
                  'source_article_ids':list(versions),'source_versions':versions,
                  'source_fingerprints':list(dict.fromkeys(versions.values())),
                  'enabled':False,'status':'手动方法草稿','version':'manual-1',
                  'created_at':(previous or {}).get('created_at',datetime.now(timezone.utc).isoformat()),
                  'curation':{'origin':'local_manual','human_reviewed':False}}
            conn.execute('INSERT INTO cards VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET enabled=0,data=excluded.data',
                         (identity,0,_dump(card)))
        return card

    def delete_method(self, kind, identity, expected_version=None):
        identity=_method_id(kind,identity)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,kind,identity)
            record=self._library_method(conn,kind,identity)
            if record is None:raise ValueError('方法不存在，请刷新列表。')
            if conn.execute('SELECT count(*) FROM method_library_trash').fetchone()[0]>=10000:
                raise ValueError('已删除列表数量已达上限。')
            if kind=='article':
                record.update(enabled=False,status='已删除')
                conn.execute('UPDATE cards SET enabled=0,data=? WHERE id=?',(_dump(record),identity))
            else:
                version=self._teaching_version(record if record['version'] else None,expected_version)
                self._append_teaching(conn,validate_teaching_method(record),version,'disabled',
                                      note='从方法库删除；历史版本和报告保留。')
            conn.execute('INSERT INTO method_library_trash VALUES (?,?,?)',
                         (kind,identity,datetime.now(timezone.utc).isoformat()))

    def list_deleted_methods(self, kind=None):
        if kind is not None:_kind(kind)
        with self._connection() as conn:
            query='SELECT kind,method_id,deleted_at FROM method_library_trash'
            rows=conn.execute(query+(' WHERE kind=?' if kind else '')+' ORDER BY deleted_at DESC,method_id',
                              (kind,) if kind else ()).fetchall()
            result=[]
            for item_kind,identity,deleted_at in rows:
                record=self._library_method(conn,item_kind,identity)
                if record is not None:
                    result.append({'kind':item_kind,'id':identity,'name':record.get('name',identity),
                                   'deleted_at':deleted_at})
            return result

    def restore_method(self, kind, identity):
        identity=_method_id(kind,identity)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if self._method_purged(conn,kind,identity):raise ValueError('方法已彻底删除，无法恢复。')
            if not self._method_deleted(conn,kind,identity):raise ValueError('此方法不在已删除列表中，请刷新。')
            record=self._library_method(conn,kind,identity)
            if record is None:raise ValueError('已删除方法的内容缺失。')
            if kind=='article':
                record.update(enabled=False,status='恢复的方法草稿')
                conn.execute('UPDATE cards SET enabled=0,data=? WHERE id=?',(_dump(record),identity))
            else:
                self._restore_teaching_owner(conn,record['owner_id'])
                self._check_teaching_cap(conn,[identity])
                version=self._teaching_version(record if record['version'] else None,None)
                self._append_teaching(conn,validate_teaching_method(record),version,note='')
            conn.execute('DELETE FROM method_library_trash WHERE kind=? AND method_id=?',(kind,identity))

    def _validate_method_library_restore(self, source, tables):
        if 'method_library_trash' not in tables:return
        columns=source.execute('PRAGMA table_info(method_library_trash)').fetchall()
        if [(r[1],r[2].upper(),r[3],r[5]) for r in columns]!=[
            ('kind','TEXT',1,1),('method_id','TEXT',1,2),('deleted_at','TEXT',1,0)]:
            raise ValueError('已删除方法备份结构不正确。')
        if source.execute('SELECT count(*) FROM method_library_trash').fetchone()[0]>10000:
            raise ValueError('已删除方法备份数量过多。')
        from .teaching_catalog import builtin_teaching_methods
        builtin={r['id']:r for r in builtin_teaching_methods()}
        for kind,identity,timestamp in source.execute('SELECT * FROM method_library_trash'):
            _method_id(kind,identity)
            timestamp=_text(timestamp,'删除时间',80)
            try:
                instant=datetime.fromisoformat(timestamp)
                if instant.tzinfo is None or instant.utcoffset() is None:raise ValueError
            except (ValueError,OverflowError):raise ValueError('方法删除时间须含时区。') from None
            if kind=='article':
                row=source.execute('SELECT enabled,data FROM cards WHERE id=?',(identity,)).fetchone()
                if (not row or row[0]!=0 or json.loads(row[1]).get('enabled') is not False
                        or json.loads(row[1]).get('id')!=identity):
                    raise ValueError('已删除方法内容缺失或仍被启用。')
            else:
                row=(source.execute('SELECT data FROM teaching_method_versions WHERE id=? ORDER BY version DESC LIMIT 1',
                     (identity,)).fetchone() if 'teaching_method_versions' in tables else None)
                record=json.loads(row[0]) if row else builtin.get(identity)
                if record is None or record.get('enabled') is not False:
                    raise ValueError('已删除教学方法缺失或仍被启用。')
