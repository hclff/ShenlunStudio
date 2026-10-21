"""Durable local teaching subjects; deletion archives all their live methods."""
import json
import uuid
from datetime import datetime, timezone

from .teaching import OWNER_IDS, _owner_identity, _text, validate_teaching_method

MAX_OWNERS = 256
MAX_OWNER_HISTORY = 4096


class TeachingOwnerStoreMixin:
    def _init_teaching_owners(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS teaching_owners(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, deleted INTEGER NOT NULL)''')

    def _teaching_owner_rows(self, conn, include_deleted=False):
        from .teaching_catalog import teaching_owners
        rows = {row['id']:{**row,'deleted':False} for row in teaching_owners()}
        for identity, name, deleted in conn.execute('SELECT id,name,deleted FROM teaching_owners ORDER BY rowid'):
            rows[identity] = {**rows.get(identity, {'id':identity,'status':'local','description':'','urls':[]}),
                              'name':name,'deleted':bool(deleted)}
        return [row for row in rows.values() if include_deleted or not row['deleted']]

    def list_teaching_owners(self, *, include_deleted=False):
        with self._connection() as conn:
            return self._teaching_owner_rows(conn, include_deleted)

    def _require_teaching_owner(self, conn, identity):
        owner = next((o for o in self._teaching_owner_rows(conn) if o['id']==identity), None)
        if owner is None:
            raise ValueError('教学主体不存在或已删除，请刷新主体列表。')
        return owner

    def add_teaching_owner(self, name):
        name = _text(name, '主体名称', 100)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            owners = self._teaching_owner_rows(conn)
            if any(o['name'].casefold()==name.casefold() for o in owners):
                raise ValueError('同名教学主体已存在。')
            if len(owners)>=MAX_OWNERS or len(self._teaching_owner_rows(conn,True))>=MAX_OWNER_HISTORY:
                raise ValueError('教学主体数量已达上限。')
            identity = 'owner-'+uuid.uuid4().hex
            conn.execute('INSERT INTO teaching_owners VALUES (?,?,0)',(identity,name))
            return self._require_teaching_owner(conn,identity)

    def delete_teaching_owner(self, identity, *, expected_methods=None):
        identity = _owner_identity(identity)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            owner = self._require_teaching_owner(conn, identity)
            # The writer reservation prevents another window changing these methods
            # between comparison and committing the complete archive operation.
            methods = [m for m in self.list_teaching_methods() if m['owner_id']==identity]
            if expected_methods is not None:
                signature = lambda rows: sorted((r['id'],r['version'],r['content_hash']) for r in rows)
                if signature(methods)!=signature(expected_methods):
                    raise ValueError('主体下的方法已发生变化，请刷新后重新确认。')
            if conn.execute('SELECT count(*) FROM method_library_trash').fetchone()[0]+len(methods)>10000:
                raise ValueError('已删除列表数量已达上限。')
            timestamp = datetime.now(timezone.utc).isoformat()
            for record in methods:
                version = self._teaching_version(record if record['version'] else None,record['version'])
                self._append_teaching(conn,validate_teaching_method(record),version,'disabled')
                conn.execute('INSERT INTO method_library_trash VALUES (?,?,?)',('teaching',record['id'],timestamp))
            conn.execute('INSERT INTO teaching_owners VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET deleted=1',
                         (identity,owner['name']))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
                selected = conn.execute("SELECT data FROM settings WHERE key='teaching_selection'").fetchone()
                if selected and identity in json.loads(selected[0]).get('owner_ids',[]):
                    conn.execute("UPDATE settings SET data=? WHERE key='teaching_selection'",
                                 (json.dumps({'mode':'combined','owner_ids':[]}),))
            return len(methods)

    def _restore_teaching_owner(self, conn, identity):
        owner = next((o for o in self._teaching_owner_rows(conn,True) if o['id']==identity),None)
        if owner is None:
            raise ValueError('该方法的教学主体缺失。')
        if owner['deleted']:
            live = self._teaching_owner_rows(conn)
            if len(live)>=MAX_OWNERS or any(o['name'].casefold()==owner['name'].casefold() for o in live):
                raise ValueError('主体数量已满或存在同名主体，无法恢复。')
            conn.execute('UPDATE teaching_owners SET deleted=0 WHERE id=?',(identity,))

    def _validate_teaching_owners_restore(self, source, tables):
        if 'teaching_owners' not in tables:
            return set(OWNER_IDS)
        if source.execute('SELECT count(*) FROM teaching_owners').fetchone()[0]>MAX_OWNER_HISTORY:
            raise ValueError('备份教学主体数量过多。')
        from .teaching_catalog import teaching_owners
        builtin = {o['id']:o['name'] for o in teaching_owners()}
        known = dict(builtin)
        live = dict(builtin)
        for identity,name,deleted in source.execute('SELECT id,name,deleted FROM teaching_owners'):
            _owner_identity(identity)
            if name!=_text(name,'主体名称',100) or type(deleted) is not int or deleted not in (0,1):
                raise ValueError('备份教学主体字段无效。')
            if identity in builtin and name!=builtin[identity]:
                raise ValueError('备份内置主体名称不一致。')
            known[identity]=name
            if deleted:live.pop(identity,None)
            else:live[identity]=name
        names=[name.casefold() for name in live.values()]
        if len(names)>MAX_OWNERS or len(set(names))!=len(names):
            raise ValueError('备份教学主体重复或数量过多。')
        return set(known)
