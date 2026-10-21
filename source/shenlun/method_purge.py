"""Permanent library removal with minimal suppression markers and frozen history."""
import json
import re
from datetime import datetime, timezone


class MethodPurgeMixin:
    @staticmethod
    def _purge_hashes(payload,kind):
        hashes=json.loads(payload)
        if (not isinstance(hashes,dict) or len(hashes)>2147483647 or
            (kind=='article' and hashes) or any(
                not re.fullmatch(r'[1-9][0-9]{0,9}',key) or int(key)>2147483647 or
                not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value)
                for key,value in hashes.items())):
            raise ValueError('彻底删除的历史版本指纹无效。')
        return hashes

    def _preserve_purged_teaching_history(self,conn,snapshot,submission):
        """Archive a pre-purge in-flight snapshot in the same write transaction.

        Only exact, previously enabled versions in the purge manifest qualify;
        these rows remain inaccessible through library and recovery APIs.
        """
        if snapshot is None:return
        from .teaching import validate_teaching_snapshot
        from .method_library import _dump
        snapshot=validate_teaching_snapshot(snapshot,submission)
        for method in snapshot['methods']:
            row=conn.execute("SELECT version_hashes FROM method_library_purged WHERE kind='teaching' AND method_id=?",
                             (method['id'],)).fetchone()
            if not row:continue
            hashes=self._purge_hashes(row[0],'teaching')
            if hashes.get(str(method['version']))!=method['content_hash']:
                raise ValueError('已彻底删除的方法只允许保存原审核版本的历史快照。')
            existing=conn.execute('SELECT data FROM teaching_method_versions WHERE id=? AND version=?',
                                  (method['id'],method['version'])).fetchone()
            if existing:
                if json.loads(existing[0])!=method:raise ValueError('历史方法版本与原快照不一致。')
            else:
                conn.execute('INSERT INTO teaching_method_versions VALUES (?,?,?)',
                             (method['id'],method['version'],_dump(method)))

    def _method_purged(self,conn,kind,identity):
        return conn.execute('SELECT 1 FROM method_library_purged WHERE kind=? AND method_id=?',
                            (kind,identity)).fetchone() is not None

    def purged_method_ids(self,kind):
        from .method_library import _kind
        _kind(kind)
        with self._connection() as conn:
            return {r[0] for r in conn.execute('SELECT method_id FROM method_library_purged WHERE kind=?',(kind,))}

    def _referenced_teaching_versions(self,conn,identity):
        """Reports, pending/completed plans and results all pin their exact version."""
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions=set()
        def read(snapshot):
            if snapshot is None:return
            if not isinstance(snapshot,dict) or not isinstance(snapshot.get('methods'),list):
                raise ValueError('历史方法快照不可读取，未执行彻底删除。')
            for method in snapshot['methods']:
                if not isinstance(method,dict):raise ValueError('历史方法快照内容不正确。')
                if method.get('id')==identity:
                    version=method.get('version')
                    if type(version) is not int or version<1:raise ValueError('历史方法版本不正确。')
                    versions.add(version)
        for table in ('reviews','eval_runs','eval_results'):
            if table not in tables:continue
            for row in conn.execute('SELECT data FROM '+table+' WHERE instr(data,?)>0',(identity,)):
                record=json.loads(row[0])
                if not isinstance(record,dict):raise ValueError('历史记录不可读取，未执行彻底删除。')
                if table=='reviews':read(record.get('teaching_snapshot'))
                elif table=='eval_results':read((record.get('review') or {}).get('teaching_snapshot'))
                else:
                    cases=record.get('cases',[])
                    if not isinstance(cases,list):raise ValueError('冻结评测计划不可读取。')
                    for case in cases:
                        if not isinstance(case,dict):raise ValueError('冻结评测计划内容不正确。')
                        read(case.get('grade_context',{}).get('payload',{}).get('teaching_guidance'))
        return versions

    def purge_method(self,kind,identity,*,expected_deleted_at=None):
        from .method_library import _method_id,MethodConflictError
        identity=_method_id(kind,identity)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            deleted=conn.execute('SELECT deleted_at FROM method_library_trash WHERE kind=? AND method_id=?',
                                 (kind,identity)).fetchone()
            if not deleted or self._method_purged(conn,kind,identity):
                raise MethodConflictError('只能彻底删除已删除列表中的方法，请刷新。')
            if expected_deleted_at is not None and deleted[0]!=expected_deleted_at:
                raise MethodConflictError('方法删除状态已在其他窗口变化，请刷新后重新确认。')
            if conn.execute('SELECT count(*) FROM method_library_purged').fetchone()[0]>=10000:
                raise ValueError('彻底删除标记数量已达上限。')
            hashes={}
            if kind=='article':
                conn.execute('DELETE FROM cards WHERE id=?',(identity,))
            else:
                from .teaching import validate_teaching_record
                for version,payload in conn.execute('SELECT version,data FROM teaching_method_versions WHERE id=?',(identity,)):
                    method=validate_teaching_record(json.loads(payload))
                    if method['id']!=identity or method['version']!=version:raise ValueError('方法版本索引不一致。')
                    if method['enabled']:hashes[str(version)]=method['content_hash']
                keep=self._referenced_teaching_versions(conn,identity)
                for version in keep:
                    if str(version) not in hashes:
                        raise ValueError('历史引用的方法版本缺失，未执行彻底删除。')
                for row in conn.execute('SELECT version FROM teaching_method_versions WHERE id=?',(identity,)).fetchall():
                    if row[0] not in keep:
                        conn.execute('DELETE FROM teaching_method_versions WHERE id=? AND version=?',(identity,row[0]))
            conn.execute('INSERT INTO method_library_purged VALUES (?,?,?,?)',
                         (kind,identity,datetime.now(timezone.utc).isoformat(),json.dumps(hashes,separators=(',',':'))))
            conn.execute('DELETE FROM method_library_trash WHERE kind=? AND method_id=?',(kind,identity))

    def _validate_method_purge_restore(self,source,tables):
        if 'method_library_purged' not in tables:return
        from .method_library import _method_id
        from .teaching import _text
        columns=source.execute('PRAGMA table_info(method_library_purged)').fetchall()
        if [(r[1],r[2].upper(),r[3],r[5]) for r in columns]!=[
            ('kind','TEXT',1,1),('method_id','TEXT',1,2),('purged_at','TEXT',1,0),('version_hashes','TEXT',1,0)]:
            raise ValueError('彻底删除标记备份结构不正确。')
        if source.execute('SELECT count(*) FROM method_library_purged').fetchone()[0]>10000:
            raise ValueError('彻底删除标记数量过多。')
        for kind,identity,timestamp,payload in source.execute('SELECT * FROM method_library_purged'):
            _method_id(kind,identity);timestamp=_text(timestamp,'彻底删除时间',80)
            hashes=self._purge_hashes(payload,kind)
            try:
                instant=datetime.fromisoformat(timestamp)
                if instant.tzinfo is None or instant.utcoffset() is None:raise ValueError
            except (ValueError,OverflowError):raise ValueError('彻底删除时间须含时区。') from None
            if ('method_library_trash' in tables and source.execute(
                'SELECT 1 FROM method_library_trash WHERE kind=? AND method_id=?',(kind,identity)).fetchone()):
                raise ValueError('已删除与彻底删除状态冲突。')
            if kind=='article':
                if source.execute('SELECT 1 FROM cards WHERE id=?',(identity,)).fetchone():
                    raise ValueError('彻底删除的文章方法仍含可恢复内容。')
            elif 'teaching_method_versions' in tables:
                keep=self._referenced_teaching_versions(source,identity)
                retained={r[0] for r in source.execute('SELECT version FROM teaching_method_versions WHERE id=?',(identity,))}
                if not keep<=retained:raise ValueError('彻底删除的方法缺少历史所用版本。')
                from .teaching import validate_teaching_record
                for version,data in source.execute('SELECT version,data FROM teaching_method_versions WHERE id=?',(identity,)):
                    method=validate_teaching_record(json.loads(data))
                    if not method['enabled'] or hashes.get(str(version))!=method['content_hash']:
                        raise ValueError('彻底删除的方法保留了非原审核历史版本。')
