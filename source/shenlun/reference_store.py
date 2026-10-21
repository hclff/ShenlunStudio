"""Append-only confirmed reference-point persistence on independent connections."""
import json
from datetime import datetime, timezone

from .practice import question_fingerprint
from .reference_points import (_text, reference_content_hash, validate_reference_points,
                               validate_reference_version)


class ReferenceStoreMixin:
    def _init_reference_points(self, conn):
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS reference_point_versions(
                question_id TEXT NOT NULL, version INTEGER NOT NULL,
                question_fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, content_hash TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(question_id,version));
            CREATE INDEX IF NOT EXISTS reference_point_active
                ON reference_point_versions(question_id,status,version);
        ''')

    def _reference_question(self, conn, question_id):
        if not isinstance(question_id, str):
            raise ValueError('题目标识须为文字。')
        row = conn.execute('SELECT data FROM question_bank WHERE id=?', (question_id,)).fetchone()
        if not row:
            raise ValueError('题目不存在。')
        return json.loads(row[0])['submission']

    def _reference_latest(self, conn, question_id, expected_version):
        if expected_version is not None and (type(expected_version) is not int or not 0 <= expected_version <= 2147483647):
            raise ValueError('预期版本号须为非负整数。')
        row = conn.execute('SELECT max(version) FROM reference_point_versions WHERE question_id=?', (question_id,)).fetchone()
        latest = row[0] or 0
        if expected_version is not None and expected_version != latest:
            raise ValueError('参考要点已在其他窗口更新，请保留当前编辑文字并重新载入版本。')
        if latest >= 2147483647:
            raise ValueError('参考要点版本号已达上限。')
        return latest

    def _append_reference(self, conn, question_id, submission, points, reviewer, note, status, latest):
        record = {'question_id': question_id, 'question_fingerprint': question_fingerprint(submission),
                  'version': latest + 1, 'status': status, 'points': points, 'reviewer': reviewer,
                  'note': note, 'created_at': datetime.now(timezone.utc).isoformat()}
        record['content_hash'] = reference_content_hash(record)
        record = validate_reference_version(record, submission)
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        conn.execute('INSERT INTO reference_point_versions VALUES(?,?,?,?,?,?,?)',
                     (question_id, record['version'], record['question_fingerprint'], status,
                      record['created_at'], record['content_hash'], encoded))
        return record

    def save_reference_points(self, question_id, points, reviewer='', note='', confirm=False, expected_version=None):
        if type(confirm) is not bool:
            raise ValueError('确认操作须明确为布尔值。')
        reviewer = _text(reviewer, '确认人', 200, allow_empty=not confirm)
        note = _text(note, '备注', 4000, allow_empty=True)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            submission = self._reference_question(conn, question_id)
            latest = self._reference_latest(conn, question_id, expected_version)
            canonical = validate_reference_points(points, submission, allow_empty=not confirm)
            return self._append_reference(conn, question_id, submission, canonical, reviewer, note,
                                          'confirmed' if confirm else 'draft', latest)

    def withdraw_reference_points(self, question_id, reviewer, reason, expected_version=None):
        reviewer = _text(reviewer, '撤回人', 200)
        reason = _text(reason, '撤回原因', 4000)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            submission = self._reference_question(conn, question_id)
            latest = self._reference_latest(conn, question_id, expected_version)
            previous = conn.execute('SELECT data FROM reference_point_versions WHERE question_id=? ORDER BY version DESC LIMIT 1',
                                    (question_id,)).fetchone()
            points = validate_reference_version(json.loads(previous[0]), submission)['points'] if previous else []
            return self._append_reference(conn, question_id, submission, points, reviewer, reason, 'withdrawn', latest)

    def list_reference_versions(self, question_id):
        with self._connection() as conn:
            submission = self._reference_question(conn, question_id)
            return [validate_reference_version(json.loads(row[0]), submission) for row in conn.execute(
                'SELECT data FROM reference_point_versions WHERE question_id=? ORDER BY version DESC', (question_id,))]

    def get_reference_points(self, question_id, version=None):
        if version is not None and (type(version) is not int or not 1 <= version <= 2147483647):
            raise ValueError('参考要点版本号须为1—2147483647的整数。')
        with self._connection() as conn:
            submission = self._reference_question(conn, question_id)
            if version is None:
                row = conn.execute('SELECT data FROM reference_point_versions WHERE question_id=? ORDER BY version DESC LIMIT 1',
                                   (question_id,)).fetchone()
            else:
                row = conn.execute('SELECT data FROM reference_point_versions WHERE question_id=? AND version=?',
                                   (question_id, version)).fetchone()
            return validate_reference_version(json.loads(row[0]), submission) if row else None

    def get_active_reference_points(self, question_id):
        with self._connection() as conn:
            submission = self._reference_question(conn, question_id)
            row = conn.execute("SELECT data FROM reference_point_versions WHERE question_id=? AND status IN ('confirmed','withdrawn') ORDER BY version DESC LIMIT 1",
                               (question_id,)).fetchone()
            value = validate_reference_version(json.loads(row[0]), submission) if row else None
            return value if value and value['status'] == 'confirmed' else None

    def reference_points_for_submission(self, submission):
        if not isinstance(submission, dict):
            raise ValueError('题目信息须为对象。')
        # Match actual question/material/type. Caller-provided IDs and paragraph maps
        # never select a record, and the saved question remains the cite authority.
        fingerprint = question_fingerprint(submission)
        with self._connection() as conn:
            question = conn.execute('SELECT id,data FROM question_bank WHERE fingerprint=?', (fingerprint,)).fetchone()
            if not question:
                return None
            fields = json.loads(question[1])['submission']
            if question_fingerprint(fields) != fingerprint:
                return None
            row = conn.execute("SELECT data FROM reference_point_versions WHERE question_id=? AND status IN ('confirmed','withdrawn') ORDER BY version DESC LIMIT 1",
                               (question[0],)).fetchone()
            value = validate_reference_version(json.loads(row[0]), fields) if row else None
            return value if value and value['status'] == 'confirmed' else None
