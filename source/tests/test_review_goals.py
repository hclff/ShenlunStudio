"""Frozen evidence uses real reports, question mappings and SQLite transactions."""
import copy
import importlib
import json
import sqlite3

import pytest

from shenlun.storage import Store
from test_review_learning import reviewed


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'goals.db')


def setup_report(store):
    value = reviewed()
    value['prompt_version'] = 'p1'
    rid = store.save_review(value)
    return rid, store.question_for_review(rid), store.get_review(rid)


def api(store):
    assert hasattr(store, 'add_review_goals'), 'evidence-bound selected-ID goal append API is required'
    return importlib.import_module('shenlun.review_goals')


def add(store, rid, original, ids=None, version=0, **changes):
    module = api(store)
    ids = [module.review_goal_candidates(original)[0]['id']] if ids is None else ids
    return store.add_review_goals(rid, ids, {'status':'待复习','due_date':'','tags':['遗漏要点'], **changes},
        expected_version=version, expected_basis=module.review_goal_basis(original),
        expected_generation=store.assessment_generation(), expected_question_id=store.question_for_review(rid))


def test_subset_exact_evidence_dedup_and_original_snapshots(store):
    module = api(store)
    rid, qid, original = setup_report(store)
    candidates = module.review_goal_candidates(original)
    assert len(candidates) == 3
    result = add(store, rid, original, [candidates[0]['id'], candidates[0]['id']])
    assert result['goals'][0]['title'] == '未覆盖：保留人工窗口'
    assert result['goals'][0]['action'] == '核对并补充该材料要点。'
    assert result['goals'][0]['refs'] == [{'id':'M2','quote':'保留人工窗口'}]
    assert len(result['goals']) == 1 and result['version'] == 1
    result = add(store, rid, original, version=1, status='已解决')
    assert len(result['goals']) == 1 and result['status'] == '已解决'
    result = add(store, rid, original, [candidates[1]['id']], version=2, status='已解决')
    assert result['goals'][1]['annotation']['quote'] == '开通电话。'
    assert result['goals'][1]['action'] == '补充预约用途'
    assert not store.get_question(qid)['needs_retry']
    assert store.get_review(rid) == original
    assert Store(store.path).get_question(qid)['review_plan'] == result


@pytest.mark.parametrize('selected', [[], ['unknown'], [None], 'bad'])
def test_empty_or_unknown_selection_never_creates_plan(store, selected):
    api(store); rid, qid, original = setup_report(store)
    with pytest.raises(ValueError): add(store, rid, original, selected)
    assert 'review_plan' not in store.get_question(qid)


def test_no_summary_and_invalid_material_refs_cannot_invent_goals(store):
    module = api(store)
    rid = store.save_review({'submission':reviewed()['submission'],'assessment':{}})
    original = store.get_review(rid)
    assert module.review_goal_candidates(original) == []
    with pytest.raises(ValueError): add(store, rid, original, ['fabricated'])
    value = reviewed(); value['id'] = 'invalid-ref'
    value['assessment']['material_points'][0]['material_refs'][0]['quote'] = '不存在'
    store.save_review(value)
    assert all(g['title'] != '未覆盖：保留人工窗口' for g in module.review_goal_candidates(store.get_review(value['id'])))


def test_basis_ignores_revisions_usage_and_times_but_tracks_scoring(store):
    module = api(store); rid, _, original = setup_report(store)
    value = copy.deepcopy(original); value.update(revisions={'minimal':'新稿'}, usage={'tokens':5}, updated_at='later')
    assert module.review_goal_basis(value) == module.review_goal_basis(original)
    assert module.review_goal_candidates(value) == module.review_goal_candidates(original)
    value['assessment']['total_score'] = 8
    assert module.review_goal_basis(value) != module.review_goal_basis(original)


@pytest.mark.parametrize('deleted', ['reviews','questions'])
def test_deleted_source_or_question_is_never_recreated(store, deleted):
    api(store); rid, qid, original = setup_report(store)
    ids = [rid] if deleted == 'reviews' else [qid]
    preview = store.prepare_practice_deletion(deleted, ids)
    store.delete_practice_items(deleted, ids, expected_token=preview['token'])
    with pytest.raises(ValueError): add(store, rid, original)
    if deleted == 'questions':
        assert store.get_question(qid) is None
        assert store.question_for_review(rid) is None


def test_cas_basis_and_generation_fail_atomically(store, tmp_path):
    module = api(store); rid, qid, original = setup_report(store)
    selected = [module.review_goal_candidates(original)[0]['id']]
    backup = store.backup_to(tmp_path/'before.db')
    generation = store.assessment_generation()
    add(store, rid, original)
    with pytest.raises(ValueError, match='更新'): add(store, rid, original)
    changed = copy.deepcopy(original); changed['assessment']['total_score'] = 7; store.save_review(changed)
    with pytest.raises(ValueError, match='变化|变更'): add(store, rid, original, version=1)
    assert store.get_question(qid)['review_plan']['version'] == 1
    store.restore_from(backup)
    with pytest.raises(ValueError, match='恢复'):
        store.add_review_goals(rid, selected, {'status':'待复习'}, expected_version=0,
            expected_basis=module.review_goal_basis(original), expected_generation=generation, expected_question_id=qid)
    assert 'review_plan' not in store.get_question(qid)


def test_exact_opening_mapping_is_checked_even_when_basis_is_unchanged(store):
    module = api(store); rid, qid, original = setup_report(store)
    other = store.save_question({**original['submission'],'materials':'另一份材料'},title='另一题')
    with store._connection() as conn:
        conn.execute('UPDATE review_questions SET question_id=? WHERE review_id=?',(other,rid))
    with pytest.raises(ValueError,match='关联'):
        store.add_review_goals(rid,[module.review_goal_candidates(original)[0]['id']],{'status':'待复习'},
            expected_version=0,expected_basis=module.review_goal_basis(original),
            expected_generation=store.assessment_generation(),expected_question_id=qid)
    assert 'review_plan' not in store.get_question(qid) and 'review_plan' not in store.get_question(other)
    assert store.get_review(rid) == original


def test_ordinary_edit_preserves_or_explicitly_removes_but_cannot_forge(store):
    api(store); rid, qid, original = setup_report(store); saved = add(store, rid, original)
    saved = store.save_review_plan(qid, {'status':'已解决'}, expected_version=1)
    assert saved['goals'][0]['title'] == '未覆盖：保留人工窗口'
    store.mark_question_retry(qid, True)
    assert store.get_question(qid)['review_plan']['goals'] == saved['goals']
    store.save_question(original['submission'], title='改题名')
    assert store.get_question(qid)['review_plan']['goals'] == saved['goals']
    forged = copy.deepcopy(saved['goals']); forged[0]['action'] = '伪造建议'
    with pytest.raises(ValueError):
        store.save_review_plan(qid, {'status':'待复习','goals':forged}, expected_version=3)
    store.save_review_plan(qid, {'status':'待复习','goals':[]}, expected_version=3)
    assert store.get_question(qid)['review_plan']['goals'] == []


def test_partial_append_metadata_preserves_existing_due_date_and_tags(store):
    module = api(store); rid, qid, original = setup_report(store)
    store.save_review_plan(qid,{'status':'已解决','due_date':'2026-10-25','tags':['已有标签']},expected_version=0)
    result = store.add_review_goals(rid,[module.review_goal_candidates(original)[0]['id']],{'status':'已解决'},
        expected_version=1,expected_basis=module.review_goal_basis(original),
        expected_generation=store.assessment_generation(),expected_question_id=qid)
    assert result['due_date'] == '2026-10-25' and result['tags'] == ['已有标签']


@pytest.mark.parametrize('detached',['missing','null'])
def test_detached_mapping_is_read_only_and_never_relinked(store, detached):
    module = api(store); rid, qid, original = setup_report(store)
    with store._connection() as conn:
        if detached == 'missing': conn.execute('DELETE FROM review_questions WHERE review_id=?',(rid,))
        else: conn.execute('UPDATE review_questions SET question_id=NULL WHERE review_id=?',(rid,))
    before = store.list_questions()
    assert store.question_for_review(rid) is None
    with pytest.raises(ValueError):
        store.add_review_goals(rid,[module.review_goal_candidates(original)[0]['id']],{'status':'待复习'},
            expected_version=0,expected_basis=module.review_goal_basis(original),
            expected_generation=store.assessment_generation(),expected_question_id=qid)
    assert store.list_questions() == before and store.question_for_review(rid) is None
    assert store.get_review(rid) == original


def test_source_deleted_snapshot_still_editable_and_backup_roundtrip(store, tmp_path):
    api(store); rid, qid, original = setup_report(store); saved = add(store, rid, original)
    preview = store.prepare_practice_deletion('reviews', [rid])
    store.delete_practice_items('reviews', [rid], expected_token=preview['token'])
    edited = store.save_review_plan(qid, {'status':'复习中','due_date':'2026-10-22'}, expected_version=1)
    assert edited['goals'] == saved['goals']
    backup = store.backup_to(tmp_path/'goals-backup.db')
    restored = Store(tmp_path/'restored.db'); restored.restore_from(backup)
    assert restored.get_question(qid)['review_plan'] == edited


@pytest.mark.parametrize('mutation', ['duplicate','ref','id','extra','annotation','source-evidence'])
def test_tampered_goal_backup_rejected_before_restore(store, tmp_path, mutation):
    module = api(store); rid, qid, original = setup_report(store)
    add(store, rid, original, [g['id'] for g in module.review_goal_candidates(original)])
    backup = store.backup_to(tmp_path/'tampered.db'); generation = store.assessment_generation()
    with sqlite3.connect(backup) as conn:
        value = json.loads(conn.execute('SELECT data FROM question_bank WHERE id=?',(qid,)).fetchone()[0])
        goal = value['review_plan']['goals'][0]
        if mutation == 'duplicate': value['review_plan']['goals'].append(copy.deepcopy(goal))
        elif mutation == 'ref': goal['refs'][0]['id'] = 'file:///secret'
        elif mutation == 'id': goal['id'] = 'fabricated'
        elif mutation == 'extra': goal['api_key'] = 'secret'
        elif mutation == 'annotation': value['review_plan']['goals'][1]['annotation']['end'] = 999
        else:
            goal['refs'][0]['quote'] = '不存在的引用'
            goal['id'] = module.goal_identity(goal)
        conn.execute('UPDATE question_bank SET data=? WHERE id=?',(json.dumps(value),qid))
    with pytest.raises(ValueError): store.restore_from(backup)
    assert store.assessment_generation() == generation


@pytest.mark.parametrize('source_changed',[False,True])
def test_new_and_old_backups_and_manual_score_are_preserved(store,tmp_path,source_changed):
    module = api(store); rid,qid,original = setup_report(store)
    store.save_human_score(rid,17,reason='人工核对',reviewer='合成评阅人',independent=True)
    human = store.get_human_score(rid)
    old = store.backup_to(tmp_path/'old-no-goals.db')
    saved = add(store,rid,original)
    assert store.get_human_score(rid) == human and store.get_review(rid) == original
    if source_changed:
        changed = copy.deepcopy(original); changed['assessment']['total_score'] = 5; store.save_review(changed)
    backup = store.backup_to(tmp_path/'new-goals.db')
    restored = Store(tmp_path/'new-restored.db'); restored.restore_from(backup)
    assert restored.get_question(qid)['review_plan'] == saved
    restored.restore_from(old)
    assert 'review_plan' not in restored.get_question(qid)
    assert restored.get_review(rid) == original
    assert restored.get_human_score(rid) == human


def test_ordinary_plan_edit_cannot_add_a_valid_unselected_canonical_candidate(store):
    module = api(store); rid,qid,original = setup_report(store)
    saved = add(store,rid,original)
    candidate = module.review_goal_candidates(original)[1]
    with pytest.raises(ValueError,match='勾选'):
        store.save_review_plan(qid,{'status':'待复习','goals':saved['goals']+[candidate]},expected_version=1)
    assert store.get_question(qid)['review_plan'] == saved


def test_regular_plan_editor_epoch_rejects_restore_without_erasing_existing_goals(store,tmp_path):
    api(store); rid,qid,original = setup_report(store); saved = add(store,rid,original)
    backup = store.backup_to(tmp_path/'regular-epoch.db'); epoch = store.assessment_generation()
    store.restore_from(backup)
    with pytest.raises(ValueError,match='恢复'):
        store.save_review_plan(qid,{'status':'已解决'},expected_version=1,expected_generation=epoch)
    assert store.get_question(qid)['review_plan'] == saved
