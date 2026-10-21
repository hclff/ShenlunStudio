"""Review plans are local, versioned question metadata, not new model grades."""
import json
import sqlite3
import threading
from contextlib import contextmanager

import pytest

from shenlun.storage import Store
from test_practice_store import fields, review


@pytest.fixture
def store(tmp_path): return Store(tmp_path/'plans.db')


def plan(**changes):
    return {'status':'待复习','due_date':'2026-10-19','tags':['遗漏要点'], **changes}


def test_save_plan_persists_and_preserves_report_and_question_metadata(store):
    rid = store.save_review(review()); qid = store.question_for_review(rid)
    original = store.get_review(rid)
    store.save_question(fields(), title='我的题名', source='公开试卷', tags=['题目标签'])
    result = store.save_review_plan(qid, plan(), expected_version=0)
    assert result['version'] == 1
    reopened = Store(store.path)
    value = reopened.get_question(qid)
    assert value['title'] == '我的题名' and value['tags'] == ['题目标签']
    assert value['review_plan']['tags'] == ['遗漏要点'] and value['needs_retry'] is True
    assert reopened.get_review(rid) == original
    assert reopened.list_review_plans(today='2026-10-19', due='due')[0]['id'] == qid


def test_plan_edit_uses_cas_and_never_recreates_deleted_question(store):
    qid = store.save_question(fields())
    other = Store(store.path)
    store.save_review_plan(qid, plan(), expected_version=0)
    with pytest.raises(ValueError, match='更新'):
        other.save_review_plan(qid, plan(tags=['另一个标签']), expected_version=0)
    snapshot = store.prepare_practice_deletion('questions', [qid])
    store.delete_practice_items('questions', [qid], expected_token=snapshot['token'])
    with pytest.raises(ValueError, match='不存在'):
        store.save_review_plan(qid, plan(), expected_version=1)
    assert store.list_review_plans() == []


@pytest.mark.parametrize('change', [{'status':'自动合格'}, {'due_date':'2026-02-30'},
    {'due_date':'2026-1-1'}, {'tags':'遗漏'}, {'tags':['x'*41]}, {'tags':['x']*21}])
def test_invalid_plan_does_not_write(store, change):
    qid = store.save_question(fields())
    with pytest.raises(ValueError): store.save_review_plan(qid, plan(**change), expected_version=0)
    assert 'review_plan' not in store.get_question(qid)


def test_filters_and_legacy_retry_state_remain_consistent(store):
    a = store.save_question(fields(), title='旧题', needs_retry=True)
    b = store.save_question({**fields(),'question':'另一题'}, title='新题')
    assert [r['id'] for r in store.list_review_plans()] == [a]
    store.save_review_plan(b, plan(status='复习中', due_date=''), expected_version=0)
    assert [r['id'] for r in store.list_review_plans(query='遗漏', status='复习中', due='unscheduled')] == [b]
    store.save_review_plan(b, plan(status='已解决'), expected_version=1)
    assert store.get_question(b)['needs_retry'] is False
    store.mark_question_retry(b, True)
    assert store.get_question(b)['review_plan']['status'] == '待复习'
    assert store.get_question(b)['review_plan']['version'] == 3
    store.save_review(review())  # A new grade never automatically solves a plan.
    assert store.get_question(a)['needs_retry'] is True


def test_backup_round_trip_validates_plan_and_preserves_original_on_bad_restore(store, tmp_path):
    qid = store.save_question(fields())
    store.save_review_plan(qid, plan(), expected_version=0)
    backup = store.backup_to(tmp_path/'good.db')
    restored = Store(tmp_path/'restored.db'); restored.restore_from(backup)
    assert restored.get_question(qid)['review_plan']['due_date'] == '2026-10-19'
    with sqlite3.connect(backup) as connection:
        value = json.loads(connection.execute('SELECT data FROM question_bank WHERE id=?',(qid,)).fetchone()[0])
        value['review_plan']['due_date'] = 'bad-date'
        connection.execute('UPDATE question_bank SET data=? WHERE id=?',(json.dumps(value),qid))
    with pytest.raises(ValueError): restored.restore_from(backup)
    assert restored.get_question(qid)['review_plan']['due_date'] == '2026-10-19'


def test_plan_change_invalidates_an_open_delete_confirmation(store):
    qid = store.save_question(fields())
    preview = store.prepare_practice_deletion('questions',[qid])
    store.save_review_plan(qid, plan(), expected_version=0)
    with pytest.raises(ValueError, match='更新'):
        store.delete_practice_items('questions',[qid],expected_token=preview['token'])
    assert store.get_question(qid)


def test_concurrent_question_metadata_save_cannot_erase_new_plan(store, monkeypatch):
    qid = store.save_question(fields())
    other = Store(store.path)
    attempted, finished = threading.Event(), threading.Event()
    failures = []; threads = []
    def write_plan():
        attempted.set()
        try: other.save_review_plan(qid, plan(), expected_version=0)
        except Exception as exc: failures.append(exc)
        finally: finished.set()
    original = store._connection
    @contextmanager
    def traced():
        with original() as conn:
            def trace(sql):
                if 'INSERT INTO question_bank VALUES' in sql and not threads:
                    thread = threading.Thread(target=write_plan); threads.append(thread); thread.start()
                    assert attempted.wait(2)
                    finished.wait(.25)  # Old code permits this commit before its stale JSON overwrite.
            conn.set_trace_callback(trace)
            yield conn
    monkeypatch.setattr(store,'_connection',traced)
    try: store.save_question(fields(),title='同时修改题名')
    finally:
        for thread in threads: thread.join(5)
    assert finished.is_set() and not failures
    value = other.get_question(qid)
    assert value['title'] == '同时修改题名'
    assert value.get('review_plan',{}).get('tags') == ['遗漏要点']
    assert value['review_plan']['version'] == 1
