"""Migration, draft durability, bindings and database recovery on real SQLite."""
from copy import deepcopy
import json
import sqlite3

import pytest

from shenlun.storage import Store


def fields(answer='我的原答'):
    return {'question':'概括做法','materials':'社区开通电话服务。','answer':answer,'question_type':'归纳概括',
            'max_score':20,'word_limit':200,'exam':'江苏省考','year':'2024','category':'B','reference':''}


def review(answer='我的原答',score=12):
    return {'submission':fields(answer),'assessment':{'total_score':score,'max_score':20,'mode':'full','rubric_fingerprint':'r1'},
            'model':'m1','prompt_version':'p1','title':'社区做法'}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'app.db')


def require(store):assert hasattr(store,'save_question'), 'persistent question bank required'


def test_bank_dedup_metadata_tags_and_status_filter(store):
    require(store)
    qid=store.save_question(fields(),title='社区题',tags=['服务','概括'],source='公开试卷')
    assert store.save_question(fields('另一答'))==qid
    value=store.get_question(qid)
    assert value['title']=='社区题' and value['tags']==['服务','概括'] and 'answer' not in value['submission']
    assert store.list_questions(query='服务',status='未练')[0]['id']==qid
    store.mark_question_retry(qid,True)
    assert store.list_questions(status='待重练')[0]['id']==qid
    assert not store.list_questions(status='未练')


def test_attempt_linking_keeps_original_question_and_revision_is_not_new_attempt(store):
    require(store);first=review();rid=store.save_review(first)
    qid=store.question_for_review(rid)
    assert qid and store.get_question(qid)['attempt_count']==1
    saved=store.get_review(rid);saved['revisions']={'minimal':'修改'};store.save_review(saved)
    assert len(store.list_question_attempts(qid))==1
    store.save_review(review('第二次原答',15))
    assert len(store.list_question_attempts(qid))==2
    other=fields();other['materials']='新材料'
    changed=store.save_question(other)
    assert changed!=qid and store.question_for_review(rid)==qid
    assert store.get_question(qid)['status']=='已练'


def test_old_reviews_and_manual_draft_migrate_once_without_changing_old_data(tmp_path):
    path=tmp_path/'legacy.db'
    value=review();value.update(id='legacy',created_at='2025-01-01T00:00:00+00:00')
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE reviews(id TEXT PRIMARY KEY,created_at TEXT,data TEXT); CREATE TABLE settings(key TEXT PRIMARY KEY,data TEXT);')
        conn.execute('INSERT INTO reviews VALUES(?,?,?)',('legacy',value['created_at'],json.dumps(value,ensure_ascii=False)))
        conn.execute('INSERT INTO settings VALUES(?,?)',('draft',json.dumps(fields(),ensure_ascii=False)))
    store=Store(path);require(store)
    assert store.question_for_review('legacy') and store.load_workspace()['submission']['answer']=='我的原答'
    assert store.get_review('legacy')==value
    current={'submission':{**fields('新的草稿'),'rubric_text':'{还没写完'},'question_id':None}
    store.save_workspace(current)
    again=Store(path)
    assert len(again.list_questions())==1 and again.load_workspace()['submission']['rubric_text']=='{还没写完'
    assert again.get_setting('draft')['answer']=='我的原答'


def test_drafts_survive_invalid_rubric_and_changed_question_never_overwrites_old_question_draft(store):
    require(store);qid=store.save_question(fields())
    snapshot={'submission':{**fields(),'rubric_text':'{"维度":'},'question_id':qid}
    store.save_workspace(snapshot)
    changed=deepcopy(snapshot);changed['submission']['materials']='换题材料';changed['submission']['answer']='另一答'
    result=store.save_workspace(changed)
    assert result['question_id'] is None
    assert store.load_question_draft(qid)['submission']['answer']=='我的原答'
    assert store.load_workspace()['submission']['rubric_text']=='{"维度":'
    assert store.list_workspace_history()


def test_explicit_new_blank_workspace_keeps_recoverable_previous_answer(store):
    require(store)
    store.save_workspace({'submission':fields(),'question_id':None})
    store.save_workspace({'submission':{},'question_id':None},archive_previous=True)
    assert store.list_workspace_history()[0]['submission']['answer']=='我的原答'


def test_human_score_zero_is_valid_and_attached_to_specific_review(store):
    require(store);rid=store.save_review(review())
    store.save_human_score(rid,0,reason='遗漏全部要点',reviewer='本人',independent=False)
    assert store.get_human_score(rid)['score']==0
    pair=store.human_calibration_pairs()[0]
    assert pair['human']==0 and pair['ai']==12 and pair['max_score']==20
    assert store.practice_summary()['independent_human_count']==0
    with pytest.raises(ValueError):store.save_human_score(rid,21)
    with pytest.raises(ValueError):store.save_human_score(rid,float('nan'))
    diagnostic=review();diagnostic['assessment'].update(total_score=None,mode='diagnostic')
    with pytest.raises(ValueError):store.save_human_score(store.save_review(diagnostic),10)


def test_repeat_stability_uses_same_answer_and_same_scoring_conditions(store):
    require(store)
    store.save_review(review(score=12));store.save_review(review(score=14))
    other=review(score=20);other['model']='other';store.save_review(other)
    store.save_review(review('不同的原答',18))
    groups=store.practice_summary()['repeat_groups']
    assert len(groups)==1 and groups[0]['n']==2 and groups[0]['spread']==2


def test_backup_includes_wal_and_restore_creates_safety_backup(store,tmp_path):
    require(store);store.set_setting('probe','original');qid=store.save_question(fields())
    backup=tmp_path/'saved.db';store.backup_to(backup)
    store.set_setting('probe','after');store.save_review(review())
    safety=store.restore_from(backup)
    assert safety.is_file() and Store(safety).get_setting('probe')=='after'
    assert store.get_setting('probe')=='original' and store.get_question(qid)
    assert not store.list_reviews()
    with pytest.raises(ValueError):store.backup_to(store.path)


def test_bad_restore_is_rejected_without_modifying_active_database(store,tmp_path):
    require(store);store.set_setting('probe','keep')
    bad=tmp_path/'foreign.db'
    with sqlite3.connect(bad) as conn:conn.execute('CREATE TABLE unrelated(x)')
    with pytest.raises(ValueError):store.restore_from(bad)
    assert store.get_setting('probe')=='keep'


def test_linking_old_review_update_does_not_revert_explicit_question_metadata(store):
    require(store);rid=store.save_review(review());qid=store.question_for_review(rid)
    newer=fields();newer['reference']='后来核验过的参考答案';newer['max_score']=25
    store.save_question(newer,title='自定义标题')
    old=store.get_review(rid);old['revisions']={'minimal':'修改稿'};store.save_review(old)
    assert store.get_question(qid)['submission']['reference']=='后来核验过的参考答案'
    assert store.get_question(qid)['submission']['max_score']==25


def test_human_label_is_not_reassigned_when_an_existing_id_changes_answer(store):
    require(store);rid=store.save_review(review())
    store.save_human_score(rid,14)
    changed=store.get_review(rid);changed['submission']['answer']='被换成另一份答卷'
    store.save_review(changed)
    assert store.get_human_score(rid)['stale']
    assert not store.human_calibration_pairs()


def test_malformed_legacy_assessment_does_not_break_saving_or_summary(store):
    require(store)
    store.save_review({'submission':fields(),'assessment':None})
    assert store.practice_summary()['repeat_groups']==[]


@pytest.mark.parametrize('damage',[
    'DROP TABLE human_scores; CREATE TABLE human_scores(review_id TEXT PRIMARY KEY,unrelated TEXT);',
    'DROP TABLE human_scores; CREATE TABLE human_scores(review_id TEXT,data TEXT);',
    'DROP TABLE human_scores; CREATE TABLE human_scores(data TEXT,review_id TEXT PRIMARY KEY);',
    "INSERT INTO practice_workspace VALUES('current','now','[]');",
    "INSERT INTO question_bank(id,fingerprint,title,search_text,created_at,updated_at,data)"
    " VALUES('bad','bad','bad','','now','now','{}');",
])
def test_restore_rejects_malformed_practice_tables_before_replacing_data(store,tmp_path,damage):
    store.set_setting('probe','keep');backup=tmp_path/'malformed.db';store.backup_to(backup)
    with sqlite3.connect(backup) as conn:conn.executescript(damage)
    with pytest.raises(ValueError):store.restore_from(backup)
    assert store.get_setting('probe')=='keep'
    assert not list(tmp_path.glob('恢复前备份-*.db'))


def test_restore_and_backup_reject_existing_physical_alias_before_connecting(store,tmp_path,monkeypatch):
    alias=tmp_path/'alias.db';alias.write_bytes(b'alias')
    monkeypatch.setattr('os.path.samefile',lambda left,right:True)
    with pytest.raises(ValueError):store.restore_from(alias)
    with pytest.raises(ValueError):store.backup_to(alias,overwrite=True)
    assert alias.read_bytes()==b'alias'


@pytest.mark.parametrize('change',['model','score'])
def test_human_pair_does_not_silently_move_to_replaced_ai_judgment(store,change):
    rid=store.save_review(review());store.save_human_score(rid,14)
    changed=store.get_review(rid)
    if change=='model':changed['model']='another-model'
    else:changed['assessment']['total_score']=18
    store.save_review(changed)
    assert store.get_human_score(rid)['stale']
    assert not store.human_calibration_pairs()
