"""Deletion previews use real SQLite snapshots and never affect user databases."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
import sqlite3
import threading

import pytest

from shenlun.storage import Store


def fields(material='社区开通电话服务。'):
    return {'question': '概括社区做法', 'materials': material, 'answer': '我的原答',
            'question_type': '归纳概括', 'max_score': 20, 'word_limit': 200}


def review(material='社区开通电话服务。'):
    return {'title': '社区题', 'submission': fields(material),
            'assessment': {'total_score': 12, 'max_score': 20, 'mode': 'full', 'rubric_fingerprint': 'r1'},
            'model': 'm1', 'prompt_version': 'p1'}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'delete.db')


def remove(store, kind, ids):
    plan = store.prepare_practice_deletion(kind, ids)
    assert plan['count'] == len(set(ids))
    return store.delete_practice_items(kind, ids, expected_token=plan['token'])


def test_single_review_delete_keeps_question_and_other_attempt(store):
    rid = store.save_review(review()); other = store.save_review(review())
    qid = store.question_for_review(rid)
    store.save_human_score(rid, 13)
    plan = store.prepare_practice_deletion('reviews', [rid, rid])
    assert plan['ids'] == [rid] and plan['review_count'] == 1 and plan['human_count'] == 1
    assert plan['draft_count'] == plan['reference_version_count'] == 0
    old = store.get_review(rid)
    assert store.delete_practice_items('reviews', [rid], expected_token=plan['token']) == 1
    assert store.get_review(rid) is None and store.get_human_score(rid) is None
    assert store.get_review(other) and store.get_question(qid)['attempt_count'] == 1
    with pytest.raises(ValueError): store.save_review(old)
    with pytest.raises(ValueError): store.save_human_score(rid, 13)
    assert Store(store.path).get_review(rid) is None


def test_bulk_question_delete_detaches_links_preserves_reports_humans_and_workspace(store, tmp_path):
    ids = [store.save_review(review(material)) for material in ('社区开通电话服务。', '社区设置服务站。')]
    qids = [store.question_for_review(rid) for rid in ids]
    store.save_human_score(ids[0], 14)
    snapshot = {'submission': fields(), 'question_id': qids[0]}
    store.save_workspace(snapshot)
    store.save_workspace({'submission': fields('社区设置服务站。'), 'question_id': qids[1]}, archive_previous=True)
    store.save_reference_points(qids[0], [], expected_version=0)
    originals = [store.get_review(rid) for rid in ids]
    plan = store.prepare_practice_deletion('questions', qids)
    assert (plan['review_count'], plan['human_count'], plan['draft_count'], plan['reference_version_count']) == (2, 1, 2, 1)
    assert store.delete_practice_items('questions', qids, expected_token=plan['token']) == 2
    assert not store.list_questions()
    assert store.load_workspace()['question_id'] is None
    assert store.load_workspace()['submission']['answer'] == '我的原答'
    assert all(item['question_id'] is None for item in store.list_workspace_history())
    assert all(store.load_question_draft(qid) is None for qid in qids)
    assert store.get_human_score(ids[0])['score'] == 14
    for rid, original in zip(ids, originals):
        assert store.get_review(rid) == original and store.question_for_review(rid) is None
        updated = deepcopy(original); updated['revisions'] = {'minimal': '修改稿'}
        store.save_review(updated)
    again = Store(store.path)
    assert not again.list_questions() and len(again.list_reviews()) == 2
    backup = tmp_path / 'detached.db'; again.backup_to(backup)
    restored = Store(tmp_path / 'restore.db'); restored.restore_from(backup)
    assert not restored.list_questions() and len(restored.list_reviews()) == 2
    assert restored.get_human_score(ids[0])['score'] == 14


@pytest.mark.parametrize('change', ['metadata', 'attempt', 'human', 'reference', 'draft', 'workspace'])
def test_question_preview_conflict_aborts_entire_batch(store, change):
    rid = store.save_review(review()); qid = store.question_for_review(rid)
    other = store.save_question(fields('第二题材料'))
    store.save_workspace({'submission': fields(), 'question_id': qid})
    plan = store.prepare_practice_deletion('questions', [qid, other])
    writer = Store(store.path)
    if change == 'metadata': writer.save_question(fields(), title='新标题')
    elif change == 'attempt': writer.save_review(review())
    elif change == 'human': writer.save_human_score(rid, 13)
    elif change == 'reference': writer.save_reference_points(qid, [])
    elif change == 'draft':
        writer.save_workspace({'submission': {**fields(), 'answer': '新草稿'}, 'question_id': qid})
    else:
        writer.save_workspace({'submission': {}, 'question_id': None}, archive_previous=True)
    with pytest.raises(ValueError): store.delete_practice_items('questions', [qid, other], expected_token=plan['token'])
    assert store.get_question(qid) and store.get_question(other) and store.get_review(rid)


@pytest.mark.parametrize('change', ['review', 'human', 'missing'])
def test_review_preview_conflict_is_atomic(store, change):
    ids = [store.save_review(review()) for _ in range(2)]
    plan = store.prepare_practice_deletion('reviews', ids)
    if change == 'review':
        value = store.get_review(ids[0]); value['revisions'] = {'minimal': '改写'}; store.save_review(value)
    elif change == 'human': store.save_human_score(ids[0], 11)
    else: remove(store, 'reviews', [ids[0]])
    with pytest.raises(ValueError): store.delete_practice_items('reviews', ids, expected_token=plan['token'])
    assert store.get_review(ids[1])


@pytest.mark.parametrize('kind,ids', [('bad', ['x']), ('reviews', 'abc'), ('reviews', []),
                                     ('questions', [None]), ('questions', ['']), ('reviews', ['missing'])])
def test_invalid_or_missing_inputs_never_delete(store, kind, ids):
    rid = store.save_review(review())
    with pytest.raises(ValueError): store.prepare_practice_deletion(kind, ids)
    with pytest.raises(ValueError): store.delete_practice_items(kind, ids, expected_token='forged')
    assert store.get_review(rid)


def test_required_confirmation_token_and_scope_binding(store):
    rid = store.save_review(review()); qid = store.question_for_review(rid)
    plan = store.prepare_practice_deletion('reviews', [rid])
    for token in (None, '', True, '0' * 64):
        with pytest.raises(ValueError): store.delete_practice_items('reviews', [rid], expected_token=token)
    with pytest.raises(ValueError): store.delete_practice_items('questions', [qid], expected_token=plan['token'])
    assert store.get_review(rid) and store.get_question(qid)


def test_delete_rollback_restores_all_rows_when_write_fails(store):
    ids = [store.save_review(review()) for _ in range(2)]
    store.save_human_score(ids[0], 13)
    plan = store.prepare_practice_deletion('reviews', ids)
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TRIGGER forbid_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT,'blocked'); END")
    with pytest.raises(sqlite3.Error): store.delete_practice_items('reviews', ids, expected_token=plan['token'])
    assert all(store.get_review(rid) for rid in ids) and store.get_human_score(ids[0])
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT count(*) FROM deleted_practice_reviews').fetchone()[0] == 0


def test_deleted_review_ids_backup_restore_and_old_backup_compatibility(store, tmp_path):
    rid = store.save_review(review()); original = store.get_review(rid)
    remove(store, 'reviews', [rid])
    backup = tmp_path / 'deleted.db'; store.backup_to(backup)
    restored = Store(tmp_path / 'restored.db'); restored.restore_from(backup)
    with pytest.raises(ValueError): restored.save_review(original)
    legacy = tmp_path / 'legacy.db'; store.backup_to(legacy)
    with sqlite3.connect(legacy) as conn: conn.execute('DROP TABLE deleted_practice_reviews')
    restored.restore_from(legacy)
    assert restored.list_reviews() == []


def test_question_delete_keeps_frozen_reference_and_independent_evaluation(store, tmp_path):
    rid = store.save_review(review()); qid = store.question_for_review(rid)
    snapshot = store.save_reference_points(qid, [{'id': 'P1', 'text': '开通电话服务',
        'material_refs': [{'id': 'M1', 'quote': '开通电话服务'}]}], reviewer='本人', confirm=True)
    original = store.get_review(rid); original['reference_points_snapshot'] = deepcopy(snapshot)
    store.save_review(original)
    dataset = store.create_eval_set('独立验证')
    case = store.add_eval_case(dataset['id'], fields(), origin='real_candidate')
    run = store.create_eval_run(dataset['id'], [case['id']], 1,
                               {'base_url': 'http://127.0.0.1:1/v1', 'model': 'fixture', 'session_token_budget': 1000000})
    remove(store, 'questions', [qid])
    assert store.get_eval_run(run['id']) == run
    assert store.get_review(rid)['reference_points_snapshot'] == snapshot
    backup = tmp_path / 'frozen.db'; store.backup_to(backup)
    restored = Store(tmp_path / 'frozen-restored.db'); restored.restore_from(backup)
    assert restored.get_eval_run(run['id']) == run and restored.get_review(rid) == original


def test_unrelated_changes_do_not_invalidate_preview_and_same_ids_reorder_is_supported(store):
    ids = [store.save_review(review()) for _ in range(2)]
    plan = store.prepare_practice_deletion('reviews', ids)
    store.save_question(fields('无关题目'))
    store.save_review(review('另一题材料'))
    assert store.delete_practice_items('reviews', ids[::-1], expected_token=plan['token']) == 2
    assert len(store.list_reviews()) == 1


def test_malformed_legacy_submission_can_still_be_deleted(store):
    rid = store.save_review({'submission': None})
    assert remove(store, 'reviews', [rid]) == 1


def test_human_score_write_holds_lock_until_insert_so_delete_cannot_leave_orphan(tmp_path):
    before_insert = threading.Event(); release_insert = threading.Event(); before_delete_lock = threading.Event()

    class PausedStore(Store):
        pause_human = False
        observe_delete = False

        @contextmanager
        def _connection(self):
            with super()._connection() as conn:
                def trace(sql):
                    if self.pause_human and sql.startswith('INSERT INTO human_scores'):
                        before_insert.set()
                        assert release_insert.wait(10)
                    if self.observe_delete and sql == 'BEGIN IMMEDIATE':
                        before_delete_lock.set()
                conn.set_trace_callback(trace)
                yield conn

    store = PausedStore(tmp_path / 'concurrent.db'); rid = store.save_review(review())
    writer = PausedStore(store.path)
    store.pause_human = True; writer.observe_delete = True
    # Take preview before label write. The pending delete must first wait for
    # its transaction, then reject its now-stale dependency snapshot.
    plan = writer.prepare_practice_deletion('reviews', [rid])
    with ThreadPoolExecutor(max_workers=2) as pool:
        score_job = pool.submit(store.save_human_score, rid, 14)
        assert before_insert.wait(10)
        delete_job = pool.submit(writer.delete_practice_items, 'reviews', [rid], expected_token=plan['token'])
        try:
            assert before_delete_lock.wait(10)
            with pytest.raises(TimeoutError): delete_job.result(timeout=0.1)
        finally:
            release_insert.set()
        score_job.result(timeout=10)
        with pytest.raises(ValueError): delete_job.result(timeout=10)
    assert store.get_review(rid) and store.get_human_score(rid)['score'] == 14
    assert remove(writer, 'reviews', [rid]) == 1 and store.get_human_score(rid) is None


@pytest.mark.parametrize('damage', ['bad_date', 'overlap', 'bad_schema'])
def test_malformed_tombstone_backup_rejected_without_changing_active_db(store, tmp_path, damage):
    rid = store.save_review(review()); backup = tmp_path / 'bad.db'; store.backup_to(backup)
    with sqlite3.connect(backup) as conn:
        if damage == 'bad_schema':
            conn.executescript('DROP TABLE deleted_practice_reviews; CREATE TABLE deleted_practice_reviews(review_id TEXT,deleted_at TEXT)')
        else:
            conn.execute('INSERT INTO deleted_practice_reviews VALUES(?,?)',
                         (rid if damage == 'overlap' else 'gone', '2026-01-01T00:00:00+00:00' if damage == 'overlap' else 'bad'))
    with pytest.raises(ValueError): store.restore_from(backup)
    assert store.get_review(rid)
