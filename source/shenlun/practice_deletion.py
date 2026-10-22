"""Confirmed, atomic practice deletion with dependency snapshots and late-write guards."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone


def _identifiers(kind, ids):
    if kind not in ('reviews', 'questions'):
        raise ValueError('删除范围不正确。')
    if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= 10000:
        raise ValueError('请选择 1—10000 项待删除内容。')
    if any(not isinstance(value, str) or not value.strip() or len(value) > 512 for value in ids):
        raise ValueError('待删除内容的标识不正确。')
    return list(dict.fromkeys(ids))


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)


class PracticeDeletionStoreMixin:
    def _init_practice_deletion(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS deleted_practice_reviews(
            review_id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL)''')

    def _require_live_review(self, conn, identifier):
        if conn.execute('SELECT 1 FROM deleted_practice_reviews WHERE review_id=?', (identifier,)).fetchone():
            raise ValueError('这份训练记录已删除，不能再次写入；请重新进行训练。')

    def _practice_deletion_snapshot(self, conn, kind, ids):
        """Read exact rows under a transaction; only the selected dependency closure participates."""
        table = 'reviews' if kind == 'reviews' else 'question_bank'
        primary, links, reviews, humans, drafts, references = [], [], [], [], [], []
        titles = []
        for identifier in sorted(ids):
            row = conn.execute('SELECT * FROM ' + table + ' WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('所选内容已删除或不存在，请刷新列表后重新选择。')
            primary.append(tuple(row))
            value = json.loads(row['data'])
            submission = value.get('submission')
            title = value.get('title') or (submission.get('question') if isinstance(submission, dict) else '') or '未命名内容'
            titles.append({'id': identifier, 'title': str(title)[:200]})
            selected_links = conn.execute('SELECT review_id,question_id FROM review_questions WHERE ' +
                                          ('review_id' if kind == 'reviews' else 'question_id') + '=? ORDER BY review_id',
                                          (identifier,)).fetchall()
            links.extend(tuple(item) for item in selected_links)
            related_ids = [identifier] if kind == 'reviews' else [item['review_id'] for item in selected_links]
            for rid in related_ids:
                if kind == 'questions':
                    reviews.extend(tuple(item) for item in conn.execute('SELECT * FROM reviews WHERE id=?', (rid,)))
                humans.extend(tuple(item) for item in conn.execute('SELECT * FROM human_scores WHERE review_id=?', (rid,)))
            if kind == 'questions':
                drafts.extend(tuple(item) for item in conn.execute('SELECT * FROM question_drafts WHERE question_id=?', (identifier,)))
                references.extend(tuple(item) for item in conn.execute(
                    'SELECT * FROM reference_point_versions WHERE question_id=? ORDER BY version', (identifier,)))
        workspaces = {}
        if kind == 'questions':
            selected = set(ids)
            for name in ('practice_workspace', 'workspace_history'):
                workspaces[name] = [tuple(row) for row in conn.execute('SELECT * FROM ' + name + ' ORDER BY id')
                                    if json.loads(row['data']).get('question_id') in selected]
        content = {'kind': kind, 'ids': sorted(ids), 'primary': primary, 'links': links, 'reviews': reviews,
                   'humans': humans, 'drafts': drafts, 'references': references, 'workspaces': workspaces}
        token = hashlib.sha256(_encoded(content).encode('utf-8')).hexdigest()
        by_id = {item['id']: item['title'] for item in titles}
        return {'kind': kind, 'ids': list(ids), 'count': len(ids), 'titles': [by_id[item] for item in ids],
                'review_count': len(ids) if kind == 'reviews' else len(reviews), 'human_count': len(humans),
                'draft_count': len(drafts), 'reference_version_count': len(references), 'token': token}

    def prepare_practice_deletion(self, kind, ids):
        ids = _identifiers(kind, ids)
        with self._connection() as conn:
            conn.execute('BEGIN')
            return self._practice_deletion_snapshot(conn, kind, ids)

    def delete_practice_items(self, kind, ids, *, expected_token):
        ids = _identifiers(kind, ids)
        if not isinstance(expected_token, str) or not re.fullmatch('[0-9a-f]{64}', expected_token):
            raise ValueError('请先预览并确认待删除内容。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = self._practice_deletion_snapshot(conn, kind, ids)
            if not hmac.compare_digest(current['token'], expected_token):
                raise ValueError('所选内容或关联数据已更新，本次未删除任何内容；请重新核对后删除。')
            if kind == 'reviews':
                timestamp = datetime.now(timezone.utc).isoformat()
                for rid in ids:
                    conn.execute('INSERT INTO deleted_practice_reviews VALUES(?,?)', (rid, timestamp))
                    conn.execute('DELETE FROM human_scores WHERE review_id=?', (rid,))
                    conn.execute('DELETE FROM review_questions WHERE review_id=?', (rid,))
                    conn.execute('DELETE FROM review_rechecks WHERE review_id=?', (rid,))
                    conn.execute('DELETE FROM reviews WHERE id=?', (rid,))
            else:
                selected = set(ids)
                for table in ('practice_workspace', 'workspace_history'):
                    for row in conn.execute('SELECT id,data FROM ' + table).fetchall():
                        value = json.loads(row['data'])
                        if value.get('question_id') in selected:
                            value['question_id'] = None
                            conn.execute('UPDATE ' + table + ' SET data=? WHERE id=?', (_encoded(value), row['id']))
                for qid in ids:
                    conn.execute('UPDATE review_questions SET question_id=NULL WHERE question_id=?', (qid,))
                    conn.execute('DELETE FROM question_drafts WHERE question_id=?', (qid,))
                    conn.execute('DELETE FROM reference_point_versions WHERE question_id=?', (qid,))
                    conn.execute('DELETE FROM question_bank WHERE id=?', (qid,))
        return len(ids)

    def _validate_practice_deletion_restore(self, source, tables):
        if 'deleted_practice_reviews' not in tables:
            return
        for identifier, deleted_at in source.execute('SELECT review_id,deleted_at FROM deleted_practice_reviews'):
            if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 512:
                raise ValueError('备份的已删除训练标识不正确。')
            if not isinstance(deleted_at, str) or not 1 <= len(deleted_at) <= 100:
                raise ValueError('备份的删除时间不正确。')
            try:
                timestamp = datetime.fromisoformat(deleted_at)
            except ValueError:
                raise ValueError('备份的删除时间不正确。') from None
            if timestamp.utcoffset() is None:
                raise ValueError('备份的删除时间缺少时区。')
            for table, column in (('reviews', 'id'), ('review_questions', 'review_id'), ('human_scores', 'review_id')):
                if table in tables and source.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + '=?', (identifier,)).fetchone():
                    raise ValueError('备份的已删除训练仍有关联记录。')
