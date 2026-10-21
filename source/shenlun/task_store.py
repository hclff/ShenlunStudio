"""Frozen assessment snapshots, CAS stages and atomic report association."""
import copy
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .task_lock import assessment_active, assessment_lock

TASK_COLUMNS = ('id', 'state', 'claim_token', 'review_id', 'created_at', 'updated_at', 'error_summary', 'data')
STATES = {'pending', 'grading', 'scored', 'revising', 'completed', 'failed', 'cancelled', 'abandoned'}
SAFE_ERRORS = {'', '评分未完成，请确认后重试。', '修改稿未完成，原答评分已保存。', '任务已取消，可明确选择继续。'}
CONTEXT_FIELDS = {'payload', 'sources', 'method_snapshot', 'reference_points_snapshot',
                  'prompt_version', 'system_hash', 'method_choice_snapshot'}
PAYLOAD_FIELDS = {'submission', 'answer_segments', 'corpus_context', 'method_guidance',
                  'output_schema', 'training_notice', 'confirmed_reference_points', 'teaching_guidance'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {'api_key', 'key', 'authorization', 'authentication', 'headers',
                                   'config', 'config_snapshot', 'raw_exception', 'exception', 'password', 'secret'}:
                raise ValueError('任务快照含有不允许的设置字段。')
            _no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _no_credentials(item)


def _validate_task_metadata(task):
    if (not isinstance(task['id'], str) or not task['id'] or task['state'] not in STATES
            or task['error_summary'] not in SAFE_ERRORS
            or any(task[key] is not None and (not isinstance(task[key], str) or not task[key])
                   for key in ('claim_token', 'review_id'))):
        raise ValueError('任务阶段记录损坏。')
    for key in ('created_at', 'updated_at'):
        if not isinstance(task[key], str) or datetime.fromisoformat(task[key]).tzinfo is None:
            raise ValueError('任务时间记录损坏。')


def validate_snapshot(value, *, compatible=True):
    """Validate before either stage, including a post-score revision-only resume."""
    from .domain import prepare_submission
    from .evaluation import canonical_hash
    from .service import ASSESSMENT_SYSTEM, REVISION_SYSTEM, PROMPT_VERSION, _answer_segments
    from .reference_points import validate_reference_snapshot
    from .teaching import validate_teaching_snapshot
    if not isinstance(value, dict) or set(value) != {'schema_version', 'prompt_version', 'revision_prompt_hash',
                                                    'submission', 'context', 'snapshot_hash'}:
        raise ValueError('任务快照损坏，无法继续。')
    content = {k:v for k,v in value.items() if k != 'snapshot_hash'}
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or canonical_hash(content) != value['snapshot_hash']:
        raise ValueError('任务快照损坏，无法继续。')
    _no_credentials(value)
    context = value['context']
    if not isinstance(context, dict) or set(context) - CONTEXT_FIELDS:
        raise ValueError('任务上下文损坏，无法继续。')
    if compatible and (value['prompt_version'] != PROMPT_VERSION or context.get('prompt_version') != PROMPT_VERSION
                       or context.get('system_hash') != canonical_hash(ASSESSMENT_SYSTEM)
                       or value['revision_prompt_hash'] != canonical_hash(REVISION_SYSTEM)):
        raise ValueError('任务提示版本不兼容，无法继续。')
    submission = prepare_submission(value['submission'])
    payload = context.get('payload')
    if (submission != value['submission'] or not isinstance(payload, dict) or set(payload) - PAYLOAD_FIELDS
            or payload.get('submission') != submission or payload.get('answer_segments') != _answer_segments(submission['answer'])):
        raise ValueError('任务题目原答快照损坏，无法继续。')
    sources, methods = context.get('sources'), context.get('method_snapshot')
    if not isinstance(sources, list) or not isinstance(methods, list):
        raise ValueError('任务依据快照损坏，无法继续。')
    expected = [p for source in sources for p in source['paragraphs']]
    actual = payload.get('corpus_context')
    if (not isinstance(actual, list) or len(expected) != len(actual)
            or {p['id']:p['text'] for p in expected} != {p['id']:p['text'] for p in actual}):
        raise ValueError('任务语料快照损坏，无法继续。')
    guidance = [{k:m.get(k) for k in ('name', 'purpose', 'steps', 'not_applicable', 'applicable_types')} for m in methods]
    if guidance != payload.get('method_guidance') or not isinstance(payload.get('output_schema'), dict):
        raise ValueError('任务方法快照损坏，无法继续。')
    points = context.get('reference_points_snapshot')
    if points is not None:
        validate_reference_snapshot(points, submission)
    if payload.get('confirmed_reference_points') != points:
        raise ValueError('任务参考要点快照损坏，无法继续。')
    if payload.get('teaching_guidance') is not None:
        validate_teaching_snapshot(payload['teaching_guidance'], submission)
    return value


class AssessmentTaskStoreMixin:
    def _init_assessment_tasks(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS assessment_tasks (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, claim_token TEXT, review_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            error_summary TEXT NOT NULL, data TEXT NOT NULL)''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS assessment_review_link ON assessment_tasks(review_id) WHERE review_id IS NOT NULL')
        conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('assessment_generation', _dump(uuid.uuid4().hex)))

    def create_assessment_task(self, submission, context):
        from .domain import prepare_submission
        from .evaluation import canonical_hash
        from .service import PROMPT_VERSION, REVISION_SYSTEM
        if not isinstance(context, dict) or not isinstance(context.get('payload'), dict):
            raise ValueError('任务上下文格式不正确。')
        frozen = copy.deepcopy({k:v for k,v in context.items() if k in CONTEXT_FIELDS})
        frozen['payload'] = {k:v for k,v in frozen['payload'].items() if k in PAYLOAD_FIELDS}
        snapshot = {'schema_version':1, 'prompt_version':PROMPT_VERSION,
                    'revision_prompt_hash':canonical_hash(REVISION_SYSTEM),
                    'submission':prepare_submission(submission), 'context':frozen}
        snapshot['snapshot_hash'] = canonical_hash(snapshot)
        validate_snapshot(snapshot)
        task_id, now = str(uuid.uuid4()), _now()
        with self._connection() as conn:
            conn.execute('INSERT INTO assessment_tasks VALUES (?,?,?,?,?,?,?,?)',
                         (task_id, 'pending', None, None, now, now, '', _dump(snapshot)))
        return task_id

    def _assessment_task(self, conn, row, *, compatible=True):
        task = dict(zip(TASK_COLUMNS, row))
        task['active'] = bool(task['claim_token']) and self.assessment_tasks_active()
        task['recoverable'] = False
        task['title'] = '无法读取的任务'
        try:
            _validate_task_metadata(task)
            # Reading a completed report needs no prompt compatibility; only
            # unfinished tasks can reach a provider using their frozen context.
            snapshot = validate_snapshot(json.loads(task.pop('data')), compatible=compatible and task['state'] != 'completed')
            task.update(snapshot)
            task['title'] = snapshot['submission']['question'].split('\n')[0][:60] or '原答诊断'
            if task['review_id']:
                review = conn.execute('SELECT data FROM reviews WHERE id=?', (task['review_id'],)).fetchone()
                if not review:
                    raise ValueError('关联训练记录不存在或已删除，无法继续。')
                if json.loads(review[0]).get('submission') != snapshot['submission']:
                    raise ValueError('关联训练记录已改变，无法继续。')
            elif task['state'] in {'scored', 'revising', 'completed'}:
                raise ValueError('任务缺少已保存的训练记录，无法继续。')
            task['recoverable'] = task['state'] not in {'abandoned', 'completed'} and not task['active']
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            # Never expose serialized data or raw parser exceptions in the UI.
            task.pop('data', None)
            task['error_summary'] = ''
            task['updated_at'] = task['updated_at'] if isinstance(task['updated_at'], str) else ''
            task['created_at'] = task['created_at'] if isinstance(task['created_at'], str) else ''
            task['error'] = '关联训练记录不存在、已删除或任务快照损坏 / 版本不兼容，无法继续。'
        return task

    def get_assessment_task(self, task_id):
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM assessment_tasks WHERE id=?', (task_id,)).fetchone()
            return self._assessment_task(conn, row) if row else None

    def list_assessment_tasks(self):
        with self._connection() as conn:
            return [self._assessment_task(conn, row) for row in conn.execute(
                "SELECT * FROM assessment_tasks WHERE state!='abandoned' ORDER BY created_at DESC,id DESC")]

    def assessment_task_for_review(self, review_id):
        with self._connection() as conn:
            row = conn.execute('SELECT id FROM assessment_tasks WHERE review_id=?', (review_id,)).fetchone()
            return row[0] if row else None

    def assessment_generation(self):
        return self.get_setting('assessment_generation')

    def assessment_tasks_active(self):
        return assessment_active(self.path)

    @contextmanager
    def assessment_restore_guard(self):
        """Backup integration must hold this across replacement and migration."""
        with assessment_lock(self.path):
            yield

    @contextmanager
    def claim_assessment_task(self, task_id, *, expected_generation=None):
        with assessment_lock(self.path):
            token = uuid.uuid4().hex
            with self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if expected_generation is not None:
                    generation = conn.execute("SELECT data FROM settings WHERE key='assessment_generation'").fetchone()
                    if not generation or json.loads(generation[0]) != expected_generation:
                        raise ValueError('数据库已恢复，旧任务启动已失效；请重新确认后继续。')
                row = conn.execute('SELECT * FROM assessment_tasks WHERE id=?', (task_id,)).fetchone()
                if not row:
                    raise ValueError('任务不存在。')
                task = self._assessment_task(conn, row)
                if task.get('error'):
                    raise ValueError(task['error'])
                if task['state'] == 'abandoned':
                    raise ValueError('任务已放弃，无法继续。')
                if task['state'] == 'completed':
                    raise ValueError('任务已完成，请打开报告。')
                conn.execute('UPDATE assessment_tasks SET claim_token=?,state=?,updated_at=?,error_summary=? WHERE id=?',
                             (token, 'revising' if task['review_id'] else 'grading', _now(), '', task_id))
            try:
                yield token
            finally:
                with self._connection() as conn:
                    conn.execute('UPDATE assessment_tasks SET claim_token=NULL WHERE id=? AND claim_token=?', (task_id, token))

    def _require_assessment_claim(self, conn, task_id, token):
        row = conn.execute('SELECT * FROM assessment_tasks WHERE id=? AND claim_token=?', (task_id, token)).fetchone()
        if not token or not row or row['state'] in {'completed', 'abandoned'}:
            raise ValueError('任务运行占用已改变，旧结果未保存。')
        task = self._assessment_task(conn, row)
        if task.get('error'):
            raise ValueError(task['error'])
        return task

    def update_assessment_task(self, task_id, token, state):
        if state not in {'revising', 'failed', 'cancelled'}:
            raise ValueError('不支持的任务阶段。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = self._require_assessment_claim(conn, task_id, token)
            if state == 'revising' and not task['review_id']:
                raise ValueError('原答评分尚未保存。')
            error = ('任务已取消，可明确选择继续。' if state == 'cancelled' else
                     ('修改稿未完成，原答评分已保存。' if task['review_id'] else '评分未完成，请确认后重试。') if state == 'failed' else '')
            conn.execute('UPDATE assessment_tasks SET state=?,updated_at=?,error_summary=? WHERE id=? AND claim_token=?',
                         (state, _now(), error, task_id, token))

    def save_assessment_review(self, task_id, token, review, *, state, expected_review=None):
        if state not in {'scored', 'completed', 'failed', 'cancelled'}:
            raise ValueError('不支持的任务保存阶段。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = self._require_assessment_claim(conn, task_id, token)
            if review.get('submission') != task['submission']:
                raise ValueError('任务与报告内容不一致。')
            if task['review_id']:
                if state == 'scored' or review.get('id') != task['review_id'] or expected_review is None:
                    raise ValueError('已保存原答评分，不能重新评分。')
                review_id = self.save_review(review, expected_review=expected_review, connection=conn)
            else:
                if state != 'scored' or task['state'] != 'grading':
                    raise ValueError('原答评分尚未保存。')
                review_id = self.save_review(review, connection=conn)
            error = {'failed':'修改稿未完成，原答评分已保存。', 'cancelled':'任务已取消，可明确选择继续。'}.get(state, '')
            conn.execute('UPDATE assessment_tasks SET review_id=?,state=?,updated_at=?,error_summary=? WHERE id=? AND claim_token=?',
                         (review_id, state, _now(), error, task_id, token))
        return review_id

    def abandon_assessment_task(self, task_id):
        with assessment_lock(self.path):
            with self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if not conn.execute('SELECT 1 FROM assessment_tasks WHERE id=?', (task_id,)).fetchone():
                    raise ValueError('任务不存在。')
                conn.execute("UPDATE assessment_tasks SET state='abandoned',claim_token=NULL,updated_at=? WHERE id=?", (_now(), task_id))

    def _invalidate_assessment_claims(self, conn):
        conn.execute('UPDATE assessment_tasks SET claim_token=NULL')
        conn.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data',
                     ('assessment_generation', _dump(uuid.uuid4().hex)))

    def _validate_assessment_restore(self, source, tables):
        if 'assessment_tasks' not in tables:
            return
        for row in source.execute('SELECT * FROM assessment_tasks'):
            task = dict(zip(TASK_COLUMNS, row))
            _validate_task_metadata(task)
            validate_snapshot(json.loads(task['data']), compatible=False)
            if not task['review_id'] and task['state'] in {'scored', 'revising', 'completed'}:
                raise ValueError('备份任务缺少关联训练记录。')
            if task['review_id']:
                report = source.execute('SELECT data FROM reviews WHERE id=?', (task['review_id'],)).fetchone()
                if report and json.loads(report[0]).get('submission') != json.loads(task['data'])['submission']:
                    raise ValueError('备份任务与报告内容不一致。')
        if source.execute('SELECT review_id FROM assessment_tasks WHERE review_id IS NOT NULL GROUP BY review_id HAVING count(*)>1').fetchone():
            raise ValueError('备份任务关联重复的训练记录。')
