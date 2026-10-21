"""Exact saved method choices are bounded, versioned and fail closed."""
import copy
import importlib.util
import json
import sqlite3

import pytest

from shenlun.storage import Store
from shenlun.service import AppService
from shenlun.method_library import MethodConflictError

RAW={'question':'概括做法','materials':'保留人工窗口。','answer':'保留人工窗口。',
     'question_type':'归纳概括','max_score':20}


def methods():
    assert importlib.util.find_spec('shenlun.method_presets'), 'method choice resolution is required'
    from shenlun import method_presets
    return method_presets


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'presets.db')


def draft(store):
    return store.save_method_preset({'name':'核对材料','question_type':'归纳概括',
                                     'teaching_ids':['LM01'],'article_ids':[]})


def enable(store):
    return store.set_teaching_method_enabled('LM01',True,reviewer='合成测试',note='本地测试核对')


def article(store,identity='am',enabled=True):
    for aid,body in [('source-a','保留人工窗口，方便居民。'),('source-b','开通电话预约，降低门槛。')]:
        store.upsert_article({'id':aid,'body':body,'title':'合成材料'})
    card=store.save_manual_article_method({'name':'核对行动','purpose':'核对材料',
        'steps':['寻找行动'],'applicable_types':['归纳概括'],'not_applicable':'不补造材料',
        'refs':[{'id':'source-a:p1','quote':'保留人工窗口'},
                {'id':'source-b:p1','quote':'开通电话预约'}]})
    card['id']=identity
    store.save_card(card)
    if enabled:AppService(store,{},'').enable_card(identity,True)
    return next(c for c in store.list_cards() if c['id']==identity)


def test_choices_are_strict_and_have_explicit_default():
    m=methods()
    assert m.normalize_method_choice(None)=={'mode':'auto'}
    for value in ({'mode':'auto'},{'mode':'off'},{'mode':'preset','preset_id':'mp-test'}):
        assert m.normalize_method_choice(value)==value
    for value in ('auto',{},[],{'mode':'unknown'},{'mode':'preset'},
                  {'mode':'off','preset_id':'mp-test'},{'mode':'auto','extra':True},
                  {'mode':'preset','preset_id':'../escape'}):
        with pytest.raises(ValueError):m.normalize_method_choice(value)


def test_presets_roundtrip_update_cas_and_delete_reset_only_active_choice(store):
    methods();first=draft(store)
    assert first['version']==1 and first['id']
    assert Store(store.path).get_method_preset(first['id'])==first
    second=store.save_method_preset({**first,'name':'新名称'},expected_version=1)
    assert second['version']==2 and second['created_at']==first['created_at']
    with pytest.raises(MethodConflictError):store.save_method_preset(first,expected_version=1)
    with pytest.raises(MethodConflictError):store.delete_method_preset(first['id'],expected_version=1)
    store.set_setting('method_choice',{'mode':'preset','preset_id':first['id']})
    store.delete_method_preset(first['id'],expected_version=2)
    assert store.get_method_preset(first['id']) is None
    assert store.get_setting('method_choice')=={'mode':'off'}
    with pytest.raises(MethodConflictError):store.save_method_preset(first,expected_version=2)


@pytest.mark.parametrize('change',[{'name':''},{'name':'x'*101},{'question_type':'无效'},
    {'teaching_ids':[]},{'teaching_ids':['LM01','LM01']},{'article_ids':['missing']},
    {'teaching_ids':['LM01']*7},{'teaching_ids':'LM01'},{'extra':True},
    {'version':True}])
def test_invalid_saves_are_atomic(store,change):
    methods();before=store.get_setting('method_presets')
    value={'name':'组合','question_type':'归纳概括','teaching_ids':['LM01'],'article_ids':[]}
    with pytest.raises(ValueError):store.save_method_preset({**value,**change})
    assert store.get_setting('method_presets')==before


def test_preset_cap_and_collision_resistant_generated_ids(store):
    methods();rows=[draft(store) for _ in range(100)]
    assert len({r['id'] for r in rows})==100
    with pytest.raises(ValueError):draft(store)
    assert len(store.list_method_presets())==100
    store.save_method_preset({**rows[0],'name':'可更新'},expected_version=1)


def test_drafts_can_be_saved_but_resolve_never_drops_unavailable_members(store):
    m=methods();preset=draft(store);choice={'mode':'preset','preset_id':preset['id']}
    with pytest.raises(ValueError):m.resolve_method_choice(store,RAW,choice)
    current=enable(store)
    selected=m.resolve_method_choice(store,RAW,choice)
    assert selected['preset']==preset and selected['article_methods']==[]
    assert selected['teaching_snapshot']['methods']==[current]
    assert selected['teaching_snapshot']['owner_ids']==[]
    assert selected['teaching_snapshot']['mode']=='combined'
    assert store.get_setting('method_choice') is None
    with pytest.raises(ValueError):m.resolve_method_choice(store,{**RAW,'question_type':'文章写作'},choice)
    store.delete_method('teaching','LM01')
    with pytest.raises(ValueError):m.resolve_method_choice(store,RAW,choice)
    renamed=store.save_method_preset({**preset,'name':'保留已失效方法'},expected_version=1)
    assert renamed['teaching_ids']==['LM01']
    with pytest.raises(ValueError):m.resolve_method_choice(store,RAW,choice)
    with pytest.raises(ValueError):draft(store)


def test_exact_article_selection_rechecks_sources_without_retiring_or_global_mutation(store):
    m=methods();chosen=article(store,'chosen');article(store,'other')
    preset=store.save_method_preset({'name':'只用指定文章','question_type':'归纳概括',
                                     'teaching_ids':[],'article_ids':['chosen']})
    store.set_setting('method_choice',{'mode':'preset','preset_id':preset['id']})
    assert m.resolve_method_choice(store,RAW)['article_methods']==[chosen]
    store.upsert_article({'id':'source-a','body':'正文已经变化'})
    with pytest.raises(ValueError):m.resolve_method_choice(store,RAW,retire_invalid=True)
    assert next(c for c in store.list_cards() if c['id']=='chosen')['enabled']
    assert store.get_setting('method_choice')=={'mode':'preset','preset_id':preset['id']}


def test_off_ignores_legacy_selection_and_leaves_all_library_state_unchanged(store):
    m=methods();enable(store);article(store)
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    before=(store.list_cards(),store.list_teaching_methods(),store.get_setting('teaching_selection'))
    selected=m.resolve_method_choice(store,RAW,{'mode':'off'},retire_invalid=True)
    assert selected=={'choice':{'mode':'off'},'preset':None,'article_methods':[],'teaching_snapshot':None}
    assert before==(store.list_cards(),store.list_teaching_methods(),store.get_setting('teaching_selection'))


def test_auto_preserves_first_six_and_only_explicit_retirement_changes_invalid_cards(store):
    m=methods();enable(store);good=article(store)
    for index in range(8):store.save_card({**good,'id':f'good-{index}'})
    store.save_card({**good,'id':'invalid','refs':[]})
    selected=m.resolve_method_choice(store,RAW)
    assert [c['id'] for c in selected['article_methods']]==['am','good-0','good-1','good-2','good-3','good-4']
    assert [c['id'] for c in selected['teaching_snapshot']['methods']]==['LM01']
    assert next(c for c in store.list_cards() if c['id']=='invalid')['enabled']
    m.resolve_method_choice(store,RAW,retire_invalid=True)
    assert not next(c for c in store.list_cards() if c['id']=='invalid')['enabled']


def test_unavailable_members_can_be_kept_or_removed_but_not_newly_added(store):
    m=methods();chosen=article(store,'chosen');preset=store.save_method_preset({
        'name':'保留引用','question_type':'归纳概括','teaching_ids':['LM01'],'article_ids':['chosen']})
    store.delete_method('article','chosen')
    renamed=store.save_method_preset({**preset,'name':'改名'},expected_version=1)
    assert renamed['article_ids']==['chosen']
    removed=store.save_method_preset({**renamed,'article_ids':[]},expected_version=2)
    assert removed['article_ids']==[]
    with pytest.raises(ValueError):store.save_method_preset({**removed,'article_ids':['chosen']},expected_version=3)


def test_retirement_cas_never_overwrites_newer_article_edit(store,monkeypatch):
    m=methods();old=article(store,'stale')
    store.upsert_article({'id':'source-a','body':'新正文'})
    original=store.save_card
    def concurrent_edit(value,**kwargs):
        original({**old,'name':'另一窗口修订'})
        return original(value,**kwargs)
    monkeypatch.setattr(store,'save_card',concurrent_edit)
    assert m.resolve_method_choice(store,RAW,retire_invalid=True)['article_methods']==[]
    current=next(c for c in store.list_cards() if c['id']=='stale')
    assert current['enabled'] and current['name']=='另一窗口修订'


def test_choice_actual_payload_uses_exact_order_and_off_keeps_retrieval(store):
    m=methods();enable(store);article(store,'first');article(store,'second')
    preset=store.save_method_preset({'name':'固定顺序','question_type':'归纳概括',
        'teaching_ids':['LM01'],'article_ids':['second','first']})
    app=AppService(store,{},'')
    off=app.prepare_grade(RAW,method_choice={'mode':'off'})
    exact=app.prepare_grade(RAW,method_choice={'mode':'preset','preset_id':preset['id']})
    assert [c['id'] for c in exact[3]]==['second','first']
    assert exact[1]['teaching_guidance']['methods'][0]['id']=='LM01'
    assert off[1]['method_guidance']==[] and 'teaching_guidance' not in off[1]
    assert off[1]['corpus_context']==exact[1]['corpus_context'] and off[2]==exact[2]
    assert store.get_setting('method_choice') is None


@pytest.mark.parametrize('setting,value',[
    ('method_presets',{}),('method_presets',[{'id':'invalid'}]),
    ('method_choice',{'mode':'preset','preset_id':'missing'}),
    ('method_choice',{'mode':'auto','extra':True}),
    ('method_choice',None),
])
def test_restore_rejects_invalid_preset_settings_atomically(store,tmp_path,setting,value):
    methods();preset=draft(store);backup=store.backup_to(tmp_path/'invalid.db')
    with sqlite3.connect(backup) as conn:
        conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(setting,json.dumps(value)))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert store.get_method_preset(preset['id'])==preset


def test_restore_rejects_bad_timestamp_version_duplicates_and_counts(store,tmp_path):
    methods();preset=draft(store)
    variants=[[{**preset,'created_at':'2026-10-17T10:00:00'}],
              [{**preset,'version':True}],[preset,preset],[preset]*101]
    for index,rows in enumerate(variants):
        backup=store.backup_to(tmp_path/f'bad-{index}.db')
        with sqlite3.connect(backup) as conn:
            conn.execute('UPDATE settings SET data=? WHERE key=?',(json.dumps(rows),'method_presets'))
        with pytest.raises(ValueError):store.restore_from(backup)


def test_restore_allows_deleted_references_and_old_backups_without_settings(store,tmp_path):
    m=methods();preset=draft(store);store.delete_method('teaching','LM01')
    store.set_setting('method_choice',{'mode':'preset','preset_id':preset['id']})
    backup=store.backup_to(tmp_path/'unavailable.db')
    restored=Store(tmp_path/'restored.db');restored.restore_from(backup)
    assert restored.get_method_preset(preset['id'])==preset
    with pytest.raises(ValueError):m.resolve_method_choice(restored,RAW)
    with sqlite3.connect(backup) as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('method_presets','method_choice')")
    restored.restore_from(backup)
    assert restored.list_method_presets()==[]
