"""Library deletion is reversible, durable, and never rewrites frozen reports."""
import copy
import sqlite3

import pytest

from shenlun.__main__ import create_store
from shenlun.service import AppService
from shenlun.storage import Store
from shenlun.teaching import resolve_teaching_guidance


def article_draft(store):
    for key,body in [('a','保留人工窗口，帮助群众办理。'),('b','开通电话预约，便于居民咨询。')]:
        store.upsert_article({'id':key,'title':'合成文章'+key,'body':body})
    return {'name':'手动核对行动作用','purpose':'联系行动与效果','steps':['明确行动','核对效果'],
            'applicable_types':['文章写作'],'not_applicable':'缺少证据时不补造因果',
            'refs':[{'id':'a:p1','quote':'保留人工窗口'},{'id':'b:p1','quote':'开通电话预约'}],
            'enabled':True,'id':'forged','source_versions':{'a':'forged'}}


def test_manual_article_creation_computes_evidence_and_revokes_imported_trust(tmp_path):
    store=Store(tmp_path/'manual.db')
    card=store.save_manual_article_method(article_draft(store))
    assert card['id'] != 'forged' and not card['enabled']
    assert card['status'] == '手动方法草稿'
    assert set(card['source_article_ids'])=={'a','b'}
    assert card['source_versions']=={key:store.get_article(key)['fingerprint'] for key in ('a','b')}
    service=AppService(store,{},'');service.enable_card(card['id'],True)
    edited=store.save_manual_article_method({**card,'name':'改后的方法'}, identity=card['id'])
    assert not edited['enabled'] and edited['id']==card['id']
    assert len(store.list_cards())==1


def test_manual_empty_evidence_stays_draft_and_bad_quote_is_atomic(tmp_path):
    store=Store(tmp_path/'draft.db');draft=article_draft(store)
    card=store.save_manual_article_method({**draft,'refs':[]})
    assert not card['enabled']
    with pytest.raises(ValueError):AppService(store,{},'').enable_card(card['id'],True)
    before=copy.deepcopy(store.list_cards())
    with pytest.raises(ValueError):
        store.save_manual_article_method({**draft,'refs':[{'id':'a:p1','quote':'编造的原文'}]})
    assert store.list_cards()==before


def test_manual_edit_detects_newer_content_and_keeps_both_inputs(tmp_path):
    store=Store(tmp_path/'manual-conflict.db');draft=article_draft(store)
    old=store.save_manual_article_method(draft)
    latest=store.save_manual_article_method({**draft,'name':'另一窗口的新名称'},identity=old['id'])
    with pytest.raises(ValueError):
        store.save_manual_article_method({**draft,'name':'尚未保存的名称'},identity=old['id'],expected_card=old)
    assert store.list_cards()==[latest]


def test_article_delete_hides_disables_and_restore_requires_review(tmp_path):
    store=Store(tmp_path/'article.db');card=store.save_manual_article_method(article_draft(store))
    AppService(store,{},'').enable_card(card['id'],True)
    frozen=copy.deepcopy(store.list_cards(True)[0])
    review=store.save_review({'method_snapshot':[frozen],'submission':{'answer':'原答'},'assessment':{}})
    store.delete_method('article',card['id'])
    assert not store.list_cards() and not store.list_cards(True)
    assert not store.list_cards(include_deleted=True)[0]['enabled']
    assert store.list_deleted_methods('article')[0]['id']==card['id']
    with pytest.raises(ValueError):store.set_card_enabled(card['id'],True)
    with pytest.raises(ValueError):store.save_card({**card,'enabled':True})
    assert store.get_review(review)['method_snapshot']==[frozen]
    again=Store(store.path);again.restore_method('article',card['id'])
    assert len(again.list_cards())==1 and not again.list_cards(True)
    assert again.list_cards()[0]['status'] == '恢复的方法草稿'
    assert not again.list_deleted_methods('article')


def test_teacher_deletion_keeps_versions_and_local_versions_win_on_restart(tmp_path):
    store=Store(tmp_path/'teacher.db')
    original=store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成审核者',note='仅测软件链路',expected_version=0)
    frozen=copy.deepcopy(original)
    store.delete_method('teaching','GH_BL01',expected_version=1)
    assert store.get_teaching_method('GH_BL01') is None
    assert not any(r['id']=='GH_BL01' for r in store.list_teaching_methods())
    deleted=store.get_teaching_method('GH_BL01',include_deleted=True)
    assert deleted['version']==2 and not deleted['enabled']
    assert store.list_teaching_versions('GH_BL01')[-1]==frozen
    with pytest.raises(ValueError):store.set_teaching_method_enabled('GH_BL01',True,reviewer='合成',note='核对')
    with pytest.raises(ValueError):store.save_teaching_method(frozen)
    with pytest.raises(ValueError):resolve_teaching_guidance(store,{'question_type':'归纳概括'},{'mode':'source','owner_ids':['bailu']})
    again=Store(store.path)
    assert again.get_teaching_method('GH_BL01') is None
    again.restore_method('teaching','GH_BL01')
    restored=again.get_teaching_method('GH_BL01')
    assert restored['version']==3 and not restored['enabled']
    assert restored['note'] == ''
    assert again.list_teaching_versions('GH_BL01')[-1]==frozen


def test_builtin_article_does_not_reappear_or_make_repeated_backups(tmp_path):
    from test_builtin_article_methods import fixture_catalog
    seed,catalog,_=fixture_catalog(tmp_path)
    directory=tmp_path/'builtin-user';store=create_store(directory,seed,method_catalog=catalog)
    store.delete_method('article','builtin-test')
    backup_count=len(list(directory.glob('内置文章方法导入前备份-*.db')))
    again=create_store(directory,seed,method_catalog=catalog)
    assert not again.list_cards() and again.list_deleted_methods('article')
    assert len(list(directory.glob('内置文章方法导入前备份-*.db')))==backup_count


def test_backup_restore_preserves_deletion_and_rejects_bad_trash_atomically(tmp_path):
    store=Store(tmp_path/'backup-source.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id']);store.delete_method('teaching','LM01',expected_version=0)
    backup=store.backup_to(tmp_path/'backup.db');restored=Store(tmp_path/'restored.db')
    restored.restore_from(backup)
    assert not restored.list_cards() and restored.get_teaching_method('LM01') is None
    assert len(restored.list_deleted_methods())==2
    with sqlite3.connect(backup) as conn:
        conn.execute("UPDATE method_library_trash SET kind='invalid'")
    before=restored.list_deleted_methods()
    with pytest.raises(ValueError):restored.restore_from(backup)
    assert restored.list_deleted_methods()==before


def test_teacher_version_conflict_does_not_delete_newer_edit(tmp_path):
    store=Store(tmp_path/'conflict.db');old=store.get_teaching_method('LM01')
    saved=store.save_teaching_method({**old,'name':'另一窗口的新编辑'},expected_version=0)
    with pytest.raises(ValueError):store.delete_method('teaching','LM01',expected_version=0)
    assert store.get_teaching_method('LM01')==saved and not store.list_deleted_methods()


def test_deletion_frees_live_capacity_but_restore_does_not_overfill_it(tmp_path,monkeypatch):
    import shenlun.teaching_store as module
    monkeypatch.setattr(module,'MAX_TEACHING_METHODS',26)
    store=Store(tmp_path/'capacity.db');draft=store.get_teaching_method('LM01')
    for identity in ('capacity-a','capacity-b'):
        store.save_teaching_method({**draft,'id':identity},expected_version=0)
    store.delete_method('teaching','LM01',expected_version=0)
    store.save_teaching_method({**draft,'id':'capacity-c'},expected_version=0)
    assert len(store.list_teaching_methods())==26
    with pytest.raises(ValueError):store.restore_method('teaching','LM01')
    assert store.get_teaching_method('LM01') is None
    backup=store.backup_to(tmp_path/'capacity-backup.db')
    restored=Store(tmp_path/'capacity-restored.db');restored.restore_from(backup)
    assert len(restored.list_teaching_methods())==26


def test_deleted_methods_stay_out_of_reextraction_and_real_report_restore(tmp_path):
    from test_teaching_service import service,RAW,enable
    store=Store(tmp_path/'frozen.db');card=enable(store)
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['limengyuan']})
    result=service(store).evaluate_submission(RAW)
    review_id=store.save_review(result)
    store.delete_method('teaching',card['id'],expected_version=card['version'])
    backup=store.backup_to(tmp_path/'frozen-backup.db')
    restored=Store(tmp_path/'frozen-restored.db');restored.restore_from(backup)
    assert restored.get_review(review_id)['teaching_snapshot']==result['teaching_snapshot']
    assert restored.get_teaching_method(card['id']) is None
    draft=article_draft(store)
    api=AppService(store,{},'');api._store_method(draft,store.get_article('a'))
    stored=store.list_cards()[0];store.delete_method('article',stored['id'])
    api._store_method(draft,store.get_article('a'))
    assert not store.list_cards()


def test_manual_changes_to_generated_method_survive_future_extraction(tmp_path):
    store=Store(tmp_path/'manual-generated.db');draft=article_draft(store)
    service=AppService(store,{},'')
    service._store_method(draft,store.get_article('a'))
    generated=store.list_cards()[0]
    manual=store.save_manual_article_method({**draft,'name':'本人手动修订的名称',
        'steps':['本人明确改过的步骤']},identity=generated['id'])
    service._store_method(draft,store.get_article('b'))
    assert store.list_cards()==[manual]


def test_enable_detects_evidence_edit_after_validation(tmp_path,monkeypatch):
    store=Store(tmp_path/'enable-race.db');card=store.save_manual_article_method(article_draft(store))
    actual=store.set_card_enabled
    def concurrent_edit(identity,flag,**kwargs):
        store.save_manual_article_method({**card,'refs':[]},identity=identity)
        return actual(identity,flag,**kwargs)
    monkeypatch.setattr(store,'set_card_enabled',concurrent_edit)
    with pytest.raises(ValueError):AppService(store,{},'').enable_card(card['id'],True)
    latest=store.list_cards()[0]
    assert not latest['enabled'] and not latest['refs']


def test_extraction_write_does_not_overwrite_a_concurrent_manual_edit(tmp_path,monkeypatch):
    store=Store(tmp_path/'extraction-race.db');draft=article_draft(store);api=AppService(store,{},'')
    api._store_method(draft,store.get_article('a'));original=store.list_cards()[0]
    actual=store.save_card
    def concurrent_edit(value,**kwargs):
        store.save_manual_article_method({**draft,'name':'刚刚保存的手动修改'},identity=original['id'])
        return actual(value,**kwargs)
    monkeypatch.setattr(store,'save_card',concurrent_edit)
    api._store_method(draft,store.get_article('b'))
    assert store.list_cards()[0]['name']=='刚刚保存的手动修改'


def test_retiring_stale_evidence_cannot_overwrite_new_manual_draft(tmp_path,monkeypatch):
    from test_teaching_service import RAW
    store=Store(tmp_path/'retire-race.db');draft=article_draft(store);api=AppService(store,{},'')
    card=store.save_manual_article_method(draft);api.enable_card(card['id'],True)
    store.upsert_article({'id':'a','body':'变化后的原文不再含旧引文。'})
    actual=store.save_card
    def concurrent_edit(value,**kwargs):
        store.save_manual_article_method({**draft,'name':'新的人工草稿','refs':[]},identity=card['id'])
        return actual(value,**kwargs)
    monkeypatch.setattr(store,'save_card',concurrent_edit)
    api.prepare_grade(RAW,retire_invalid=True)
    latest=store.list_cards()[0]
    assert latest['name']=='新的人工草稿' and not latest['refs'] and not latest['enabled']


def test_restore_rejects_mismatched_deleted_article_identity(tmp_path):
    store=Store(tmp_path/'identity-source.db');card=store.save_manual_article_method(article_draft(store))
    store.delete_method('article',card['id']);backup=store.backup_to(tmp_path/'identity-backup.db')
    with sqlite3.connect(backup) as conn:
        conn.execute("UPDATE cards SET data=json_set(data,'$.id','different-id') WHERE id=?",(card['id'],))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert store.list_deleted_methods('article')[0]['id']==card['id']
