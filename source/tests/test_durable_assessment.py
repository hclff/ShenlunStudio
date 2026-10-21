"""Recovery exercises real transactions and services; only model bytes are offline."""
import copy
import json
import subprocess
import sys

import pytest

from shenlun.model import CancelledError, ModelError
from shenlun.service import AppService, ASSESSMENT_SYSTEM, PROMPT_VERSION
from shenlun.storage import Store
from shenlun.evaluation import canonical_hash
from test_service import CONFIG, ai_assessment, raw, service


def task(store):
    offline = AppService(store, CONFIG, '')
    submission, payload, sources, methods, points = offline.prepare_grade(raw())
    context = {'payload': payload, 'sources': sources, 'method_snapshot': methods,
               'reference_points_snapshot': points, 'prompt_version': PROMPT_VERSION,
               'system_hash': canonical_hash(ASSESSMENT_SYSTEM),
               'method_choice_snapshot': offline._last_method_choice}
    return store.create_assessment_task(submission, context)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'tasks.db')


def test_failed_grade_reopens_with_frozen_input_and_safe_error(store):
    tid = task(store)
    with pytest.raises(ModelError):
        service(store, [ModelError('Authorization: secret')]).run_assessment_task(tid)
    restarted = Store(store.path)
    saved = restarted.get_assessment_task(tid)
    assert saved['state'] == 'failed' and saved['review_id'] is None
    assert 'secret' not in json.dumps(saved)
    result = service(restarted, [ai_assessment, {'minimal':'修改稿', 'model':'示范稿'}]).run_assessment_task(tid)
    assert result['submission']['answer'] == '方便老人报修。'
    assert restarted.get_assessment_task(tid)['state'] == 'completed'


def test_crash_after_score_commits_association_and_resume_never_regrades(store):
    tid = task(store)
    def crash(payload):
        saved = Store(store.path).get_assessment_task(tid)
        assert saved['review_id'] and saved['state'] == 'revising'
        raise SystemExit('process exited')
    with pytest.raises(SystemExit):
        service(store, [ai_assessment, crash]).run_assessment_task(tid)
    restarted = Store(store.path)
    before = restarted.get_review(restarted.get_assessment_task(tid)['review_id'])
    review = service(restarted, [{'minimal':'续写', 'model':'续写示范'}]).run_assessment_task(tid)
    assert review['assessment'] == before['assessment']
    assert len(restarted.list_reviews()) == 1
    assert restarted.usage_summary()['requests'] == 2
    assert service(restarted, []).run_assessment_task(tid) == review


@pytest.mark.parametrize('failure,state', [(ModelError('sensitive secret'), 'failed'), (CancelledError('secret'), 'cancelled')])
def test_revision_failure_and_cancel_remain_explicitly_resumable(store, failure, state):
    tid = task(store)
    if state == 'cancelled':
        with pytest.raises(CancelledError):
            service(store, [ai_assessment, failure]).run_assessment_task(tid)
    else:
        service(store, [ai_assessment, failure]).run_assessment_task(tid)
    saved = store.get_assessment_task(tid)
    assert saved['state'] == state and saved['review_id']
    assert 'secret' not in json.dumps(saved)
    before = store.get_review(saved['review_id'])
    result = service(Store(store.path), [{'minimal':'续写', 'model':'示范'}]).run_assessment_task(tid)
    assert result['assessment'] == before['assessment']


def test_active_window_blocks_claim_abandon_and_restore(store, tmp_path):
    tid = task(store)
    other = Store(store.path)
    backup = store.backup_to(tmp_path/'backup.db')
    with store.claim_assessment_task(tid) as token:
        assert token and other.assessment_tasks_active()
        assert other.get_assessment_task(tid)['active']
        with pytest.raises(ValueError, match='运行'):
            with other.claim_assessment_task(tid):
                pass
        with pytest.raises(ValueError, match='运行'):
            other.abandon_assessment_task(tid)
        with pytest.raises(ValueError, match='运行'):
            other.restore_from(backup)
    assert not other.assessment_tasks_active()


def test_stale_claim_cannot_save_review_or_update_after_new_claim(store):
    tid = task(store)
    with store.claim_assessment_task(tid) as old:
        pass  # interrupted process releases the OS lock, not a timeout lease
    with store.claim_assessment_task(tid) as current:
        assert current != old
        grade = service(store, [ai_assessment]).evaluate_submission(raw())
        with pytest.raises(ValueError, match='占用'):
            store.save_assessment_review(tid, old, grade, state='scored')
        with pytest.raises(ValueError, match='占用'):
            store.update_assessment_task(tid, old, 'failed')
        assert store.list_reviews() == []


def test_deleted_linked_review_is_never_resurrected(store):
    tid = task(store)
    result = service(store, [ai_assessment, ModelError('timeout')]).run_assessment_task(tid)
    from test_practice_deletion import remove
    remove(store, 'reviews', [result['id']])
    assert not store.get_assessment_task(tid)['recoverable']
    with pytest.raises(ValueError, match='删除|不存在'):
        service(store, []).run_assessment_task(tid)
    assert store.list_reviews() == []


@pytest.mark.parametrize('mutation', ['json', 'snapshot', 'prompt'])
def test_corrupt_or_incompatible_tasks_refuse_calls_and_list_safely(store, mutation):
    tid = task(store)
    with store._connection() as conn:
        data = json.loads(conn.execute('SELECT data FROM assessment_tasks WHERE id=?', (tid,)).fetchone()[0])
        if mutation == 'snapshot':
            data['submission']['answer'] = '损坏的答案'
        if mutation == 'prompt':
            data['prompt_version'] = 'unsupported'
        conn.execute('UPDATE assessment_tasks SET data=? WHERE id=?', ('{' if mutation == 'json' else json.dumps(data), tid))
    assert store.list_assessment_tasks()[0]['recoverable'] is False
    with pytest.raises(ValueError):
        service(store, []).run_assessment_task(tid)


def test_abandon_preserves_report_and_forbids_resume(store):
    tid = task(store)
    review = service(store, [ai_assessment, ModelError('timeout')]).run_assessment_task(tid)
    store.abandon_assessment_task(tid)
    assert store.get_review(review['id']) is not None
    with pytest.raises(ValueError, match='放弃'):
        service(store, []).run_assessment_task(tid)


def test_old_backup_migrates_empty_and_restore_invalidates_claim(store, tmp_path):
    tid = task(store)
    with store.claim_assessment_task(tid) as stale:
        pass
    backup = store.backup_to(tmp_path/'backup.db')
    store.restore_from(backup)
    with pytest.raises(ValueError, match='占用'):
        store.update_assessment_task(tid, stale, 'failed')
    old = Store(tmp_path/'old.db')
    with old._connection() as conn:
        conn.execute('DROP TABLE assessment_tasks')
    store.restore_from(old.path)
    assert store.list_assessment_tasks() == []


def test_review_and_association_rollback_together_on_save_failure(store, monkeypatch):
    tid = task(store)
    def fail(conn, review):
        raise ValueError('cannot link question')
    monkeypatch.setattr(store, '_link_practice_review', fail)
    with pytest.raises(ValueError):
        service(store, [ai_assessment]).run_assessment_task(tid)
    assert store.list_reviews() == []
    assert store.get_assessment_task(tid)['review_id'] is None


def test_interrupted_grade_releases_os_lock_and_does_not_auto_resume(store):
    tid = task(store)
    def crash(payload):
        raise SystemExit('terminated before grade response')
    with pytest.raises(SystemExit):
        service(store, [crash]).run_assessment_task(tid)
    restarted = Store(store.path)
    saved = restarted.get_assessment_task(tid)
    assert saved['state'] == 'grading' and saved['recoverable'] and saved['review_id'] is None
    assert restarted.usage_summary()['requests'] == 0
    assert restarted.list_reviews() == []


def test_os_lock_is_cross_process_and_released_on_termination(store):
    code = ('import sys; from shenlun.task_lock import assessment_lock; '
            'guard=assessment_lock(sys.argv[1]); guard.__enter__(); '
            'print("locked", flush=True); sys.stdin.readline()')
    process = subprocess.Popen([sys.executable, '-c', code, str(store.path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        assert store.assessment_tasks_active()
        with pytest.raises(ValueError, match='运行'):
            with store.assessment_restore_guard():
                pass
        process.terminate()
        process.wait(timeout=5)
        assert not store.assessment_tasks_active()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('column,value', [('updated_at', 'bad time'), ('created_at', ''),
                                         ('state', 'unknown'), ('error_summary', 'Authorization: secret')])
def test_corrupt_task_metadata_is_unusable_and_never_exposes_raw_error(store, column, value):
    tid = task(store)
    with store._connection() as conn:
        conn.execute('UPDATE assessment_tasks SET '+column+'=? WHERE id=?', (value, tid))
    saved = store.get_assessment_task(tid)
    assert not saved['recoverable'] and saved.get('error')
    assert 'secret' not in json.dumps(saved)
    with pytest.raises(ValueError):
        service(store, []).run_assessment_task(tid)


def test_snapshot_allowlist_omits_settings_and_rejects_nested_credentials(store):
    tid = task(store)
    frozen = store.get_assessment_task(tid)
    context = copy.deepcopy(frozen['context'])
    context.update(config={'api_key':'top-secret'}, key='top-secret', raw_exception='top-secret')
    other = store.create_assessment_task({**frozen['submission'], 'api_key':'top-secret'}, context)
    assert 'top-secret' not in json.dumps(store.get_assessment_task(other))
    context['sources'] = [{'api_key':'nested-secret', 'paragraphs':[]}]
    with pytest.raises(ValueError):
        store.create_assessment_task(frozen['submission'], context)


def test_restore_rejects_corrupt_optional_task_table_without_replacing_current_db(store, tmp_path):
    tid = task(store)
    backup = store.backup_to(tmp_path/'bad-backup.db')
    import sqlite3
    with sqlite3.connect(backup) as conn:
        conn.execute("UPDATE assessment_tasks SET data='{}'")
    with pytest.raises(ValueError):
        store.restore_from(backup)
    assert store.get_assessment_task(tid)['recoverable']


def test_stale_worker_cannot_overwrite_a_concurrently_changed_report(store):
    tid = task(store)
    def concurrent(payload):
        report = store.get_review(store.get_assessment_task(tid)['review_id'])
        report['revisions'] = {'minimal':'other result', 'model':'other model', 'status':'complete'}
        store.save_review(report)
        return {'minimal':'late result', 'model':'late model'}
    with pytest.raises(ValueError, match='更新'):
        service(store, [ai_assessment, concurrent]).run_assessment_task(tid)
    saved = store.get_review(store.get_assessment_task(tid)['review_id'])
    assert saved['revisions']['minimal'] == 'other result'


def test_completed_report_can_reopen_after_prompt_upgrade_without_model_call(store, monkeypatch):
    tid = task(store)
    result = service(store, [ai_assessment, {'minimal':'修改', 'model':'示范'}]).run_assessment_task(tid)
    monkeypatch.setattr('shenlun.service.PROMPT_VERSION', 'future-prompt')
    assert service(Store(store.path), []).run_assessment_task(tid) == result


def test_identical_submissions_have_independent_task_associations(store):
    first, second = task(store), task(store)
    one = service(store, [ai_assessment, ModelError('timeout')]).run_assessment_task(first)
    two = service(store, [ai_assessment, ModelError('timeout')]).run_assessment_task(second)
    assert one['id'] != two['id']
    result = service(Store(store.path), [{'minimal':'only second', 'model':'second model'}]).run_assessment_task(second)
    assert result['id'] == two['id']
    assert store.get_review(one['id'])['revisions']['minimal'] == ''


def test_recovery_recognizes_revisions_completed_by_existing_service_workflow(store):
    tid = task(store)
    initial = service(store, [ai_assessment, ModelError('timeout')]).run_assessment_task(tid)
    completed = service(store, [{'minimal':'已有修改', 'model':'已有示范'}]).retry_revisions(initial['id'])
    assert service(Store(store.path), []).run_assessment_task(tid) == completed
    assert store.get_assessment_task(tid)['state'] == 'completed'
