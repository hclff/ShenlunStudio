"""Permanent removal clears recoverable content while preserving frozen history."""
import json
import sqlite3

import pytest

from shenlun.storage import Store
from shenlun.__main__ import create_store
from test_method_library_crud import article_draft


def test_article_purge_removes_card_and_recovery_but_blocks_recreation(tmp_path):
    store=Store(tmp_path/'article.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id'])
    stamp=store.list_deleted_methods('article')[0]['deleted_at']
    store.purge_method('article',card['id'],expected_deleted_at=stamp)
    assert store.list_cards(include_deleted=True)==[] and not store.list_deleted_methods()
    assert store.is_method_deleted('article',card['id']) and store.stats()['cards']==0
    with store._connection() as conn:
        assert conn.execute('SELECT count(*) FROM cards').fetchone()[0]==0
    with pytest.raises(ValueError):store.restore_method('article',card['id'])
    with pytest.raises(ValueError):store.save_card(card)
    assert Store(store.path).list_deleted_methods()==[]


def test_permanent_markers_hide_a_card_reintroduced_by_a_stale_writer(tmp_path):
    store=Store(tmp_path/'stale-writer.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id']);store.purge_method('article',card['id'])
    with store._connection() as conn:
        conn.execute('INSERT INTO cards VALUES (?,?,?)',(card['id'],0,json.dumps(card)))
    assert not store.list_cards() and not store.list_cards(include_deleted=True)
    assert store.stats()['cards']==0


def test_builtin_article_purge_survives_normal_restart_without_extra_backup(tmp_path):
    from test_builtin_article_methods import fixture_catalog
    seed,catalog,_=fixture_catalog(tmp_path);directory=tmp_path/'builtin'
    store=create_store(directory,seed,method_catalog=catalog)
    store.delete_method('article','builtin-test');store.purge_method('article','builtin-test')
    before=len(list(directory.glob('内置文章方法导入前备份-*.db')))
    again=create_store(directory,seed,method_catalog=catalog)
    assert not again.list_cards(include_deleted=True) and not again.list_deleted_methods()
    assert len(list(directory.glob('内置文章方法导入前备份-*.db')))==before


def test_unused_builtin_teacher_versions_are_removed_and_never_resurface(tmp_path):
    store=Store(tmp_path/'teacher.db');original=store.get_teaching_method('GH_BL01')
    store.delete_method('teaching','GH_BL01',expected_version=0)
    store.purge_method('teaching','GH_BL01')
    assert store.get_teaching_method('GH_BL01',include_deleted=True) is None
    assert store.list_teaching_versions('GH_BL01')==[]
    assert not any(r['id']=='GH_BL01' for r in store.list_teaching_methods(include_deleted=True))
    with store._connection() as conn:
        assert conn.execute('SELECT count(*) FROM teaching_method_versions WHERE id=?',('GH_BL01',)).fetchone()[0]==0
    with pytest.raises(ValueError):store.restore_method('teaching','GH_BL01')
    with pytest.raises(ValueError):store.save_teaching_method(original)
    imported=store.import_teaching_methods([original])[0]
    assert imported['id']!='GH_BL01' and not imported['enabled']


def test_only_trash_can_be_purged_and_stale_dialog_does_not_purge_new_deletion(tmp_path):
    store=Store(tmp_path/'stale.db');card=store.save_manual_article_method(article_draft(store))
    with pytest.raises(ValueError):store.purge_method('article',card['id'])
    store.delete_method('article',card['id']);old=store.list_deleted_methods()[0]['deleted_at']
    store.restore_method('article',card['id']);store.delete_method('article',card['id'])
    with pytest.raises(ValueError):store.purge_method('article',card['id'],expected_deleted_at=old)
    assert len(store.list_deleted_methods())==1 and store.list_cards(include_deleted=True)


def test_purge_preserves_only_teacher_versions_used_by_report_and_pending_plan(tmp_path):
    from test_teaching_service import service,RAW,CONFIG
    from shenlun.evaluation import run_evaluation
    store=Store(tmp_path/'history.db')
    first=store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成核对者',note='仅验证软件',expected_version=0)
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    report=service(store).evaluate_submission(RAW);rid=store.save_review(report);report=store.get_review(rid)
    edited=store.save_teaching_method({**first,'name':'第二个方法版本'},expected_version=1)
    second=store.set_teaching_method_enabled(first['id'],True,reviewer='合成核对者',note='仅测试冻结',expected_version=edited['version'])
    dataset=store.create_eval_set('合成冻结计划');case=store.add_eval_case(dataset['id'],RAW,origin='gpt_control')
    plan=store.create_eval_run(dataset['id'],[case['id']],1,CONFIG)
    store.delete_method('teaching',first['id'],expected_version=second['version'])
    store.purge_method('teaching',first['id'])
    with store._connection() as conn:
        kept=[r[0] for r in conn.execute('SELECT version FROM teaching_method_versions WHERE id=? ORDER BY version',(first['id'],))]
    assert kept==[first['version'],second['version']]
    assert store.get_review(rid)==report and store.get_teaching_method(first['id']) is None
    backup=store.backup_to(tmp_path/'history-backup.db');restored=Store(tmp_path/'history-restored.db')
    restored.restore_from(backup)
    assert restored.get_review(rid)==report and restored.get_teaching_method(first['id'],include_deleted=True) is None
    run_evaluation(restored,service(restored),plan['id'])
    assert restored.list_eval_results(plan['id'])[0]['review']['teaching_snapshot']['methods'][0]==second


def test_inflight_report_is_archived_after_other_instance_purges_method(tmp_path):
    from test_teaching_service import service,RAW
    store=Store(tmp_path/'inflight-report.db')
    method=store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='软件测试')
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    result=service(store).evaluate_submission(RAW)
    other=Store(store.path);other.delete_method('teaching',method['id']);other.purge_method('teaching',method['id'])
    rid=store.save_review(result)
    restored=Store(tmp_path/'inflight-restored.db');restored.restore_from(store.backup_to(tmp_path/'inflight-backup.db'))
    assert restored.get_review(rid)['teaching_snapshot']==result['teaching_snapshot']
    assert restored.get_teaching_method(method['id'],include_deleted=True) is None


def test_inflight_eval_plan_is_archived_after_purge(tmp_path,monkeypatch):
    import shenlun.evaluation as evaluation
    from test_teaching_service import RAW,CONFIG
    store=Store(tmp_path/'inflight-plan.db')
    method=store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='软件测试')
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    dataset=store.create_eval_set('合成计划');case=store.add_eval_case(dataset['id'],RAW,origin='gpt_control')
    actual=evaluation.plan_evaluation
    def purge_after_plan(*args,**kwargs):
        plan=actual(*args,**kwargs)
        store.delete_method('teaching',method['id']);store.purge_method('teaching',method['id'])
        return plan
    monkeypatch.setattr(evaluation,'plan_evaluation',purge_after_plan)
    run=store.create_eval_run(dataset['id'],[case['id']],1,CONFIG)
    restored=Store(tmp_path/'plan-restored.db');restored.restore_from(store.backup_to(tmp_path/'plan-backup.db'))
    assert restored.get_eval_run(run['id'])==run and restored.get_teaching_method(method['id']) is None


def test_late_report_cannot_reintroduce_modified_or_unreviewed_method(tmp_path):
    from copy import deepcopy
    from test_teaching_service import service,RAW
    from shenlun.teaching import teaching_content_hash
    store=Store(tmp_path/'forged-history.db')
    method=store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='软件测试')
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    result=service(store).evaluate_submission(RAW)
    store.delete_method('teaching',method['id']);store.purge_method('teaching',method['id'])
    forged=deepcopy(result);snapshot=forged['teaching_snapshot']
    snapshot['methods'][0]['steps']=['未经过原审核的修改']
    snapshot['methods'][0]['content_hash']=teaching_content_hash(snapshot['methods'][0])
    snapshot['content_hash']=teaching_content_hash(snapshot)
    with pytest.raises(ValueError):store.save_review(forged)
    assert not store.list_reviews()
    with store._connection() as conn:
        assert conn.execute('SELECT count(*) FROM teaching_method_versions WHERE id=?',(method['id'],)).fetchone()[0]==0


def test_purge_backup_restore_and_pre_purge_version_backup_compatibility(tmp_path):
    store=Store(tmp_path/'backup-source.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id']);old=store.backup_to(tmp_path/'old.db')
    with sqlite3.connect(old) as conn:conn.execute('DROP TABLE method_library_purged')
    store.purge_method('article',card['id']);backup=store.backup_to(tmp_path/'purged.db')
    restored=Store(tmp_path/'restored.db');restored.restore_from(backup)
    assert restored.is_method_deleted('article',card['id']) and not restored.list_deleted_methods()
    restored.restore_from(old)
    assert restored.list_deleted_methods()[0]['id']==card['id']


@pytest.mark.parametrize('change', ['bad_kind','bad_time','recreated_card'])
def test_invalid_purge_backup_is_rejected_without_touching_current_data(tmp_path,change):
    store=Store(tmp_path/'invalid.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id']);store.purge_method('article',card['id'])
    backup=store.backup_to(tmp_path/'invalid-backup.db')
    with sqlite3.connect(backup) as conn:
        if change=='bad_kind':conn.execute("UPDATE method_library_purged SET kind='invalid'")
        elif change=='bad_time':conn.execute("UPDATE method_library_purged SET purged_at='not-a-date'")
        else:conn.execute('INSERT INTO cards VALUES (?,?,?)',(card['id'],0,json.dumps(card)))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert not store.list_cards(include_deleted=True) and store.is_method_deleted('article',card['id'])


@pytest.mark.parametrize('manifest', ['[]','{"01":"'+'a'*64+'"}','{"1":"bad"}', '{"1":"'+'a'*64+'"}'])
def test_purged_teacher_backup_rejects_invalid_or_mismatched_manifest(tmp_path,manifest):
    from test_teaching_service import service,RAW
    store=Store(tmp_path/'manifest.db')
    store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='软件测试')
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    store.save_review(service(store).evaluate_submission(RAW))
    store.delete_method('teaching','GH_BL01');store.purge_method('teaching','GH_BL01')
    backup=store.backup_to(tmp_path/'manifest-backup.db')
    with sqlite3.connect(backup) as conn:conn.execute('UPDATE method_library_purged SET version_hashes=?',(manifest,))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert store.get_teaching_method('GH_BL01') is None and len(store.list_reviews())==1


def test_failed_late_review_write_rolls_back_historical_rehydration(tmp_path,monkeypatch):
    from test_teaching_service import service,RAW
    store=Store(tmp_path/'rollback.db')
    store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='软件测试')
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    result=service(store).evaluate_submission(RAW)
    store.delete_method('teaching','GH_BL01');store.purge_method('teaching','GH_BL01')
    def fail(*args):raise ValueError('模拟保存失败')
    monkeypatch.setattr(store,'_link_practice_review',fail)
    with pytest.raises(ValueError):store.save_review(result)
    with store._connection() as conn:
        assert conn.execute('SELECT count(*) FROM teaching_method_versions WHERE id=?',('GH_BL01',)).fetchone()[0]==0
    assert not store.list_reviews()
