"""Batch activation preserves individual eligibility, review and concurrency checks."""
from copy import deepcopy
import importlib.util

import pytest

from shenlun.storage import Store
from test_method_library_crud import article_draft


def apply(store,kind,records,enabled,**kwargs):
    assert importlib.util.find_spec('shenlun.method_batch'), 'local batch operation is required'
    from shenlun.method_batch import set_methods_enabled
    return set_methods_enabled(store,kind,records,enabled,**kwargs)


def test_article_batch_checks_each_card_and_skips_unchanged(tmp_path):
    store=Store(tmp_path/'articles.db');draft=article_draft(store)
    good=store.save_manual_article_method(draft)
    bad=store.save_manual_article_method({**draft,'name':'无证据草稿','refs':[]})
    result=apply(store,'article',[good,bad],True)
    assert [r['status'] for r in result]==['success','failed'] and result[1]['reason']
    live=store.list_cards(True);assert [r['id'] for r in live]==[good['id']]
    again=apply(store,'article',live,True)
    assert again[0]['status']=='skipped'
    store.upsert_article({**store.get_article('a'),'body':'原文更新，不再匹配。'})
    result=apply(store,'article',live,False)
    assert result[0]['status']=='success' and not store.list_cards(True)


def test_teaching_batch_keeps_named_audit_and_cannot_enable_catalog(tmp_path):
    from test_teaching_ui import method
    store=Store(tmp_path/'teachers.db')
    good=store.get_teaching_method('LM01')
    bad=store.save_teaching_method(method('catalog-only',scope='catalog'))
    result=apply(store,'teaching',[good,bad],True,reviewer='合成审核人',note='逐项核对来源')
    assert [r['status'] for r in result]==['success','failed']
    record=store.get_teaching_method('LM01')
    assert record['enabled'] and record['reviewer']=='合成审核人' and record['note']=='逐项核对来源'
    assert store.get_teaching_method('catalog-only')==bad
    result=apply(store,'teaching',[record],True,reviewer='合成审核人',note='未重复审核')
    assert result[0]['status']=='skipped' and store.get_teaching_method('LM01')['version']==record['version']
    assert apply(store,'teaching',[record],False)[0]['status']=='success'


@pytest.mark.parametrize('kind',['article','teaching'])
def test_batch_does_not_activate_another_windows_newer_edit_or_deleted_record(tmp_path,kind):
    store=Store(tmp_path/'conflict.db')
    if kind=='article':
        original=store.save_manual_article_method(article_draft(store))
        store.save_manual_article_method({**original,'name':'其他窗口编辑'},identity=original['id'])
    else:
        original=store.get_teaching_method('LM01')
        store.save_teaching_method({**original,'name':'其他窗口编辑'})
    result=apply(store,kind,[deepcopy(original)],True,reviewer='合成审核人',note='软件测试')
    assert result[0]['status']=='failed'
    store.delete_method(kind,original['id'])
    assert apply(store,kind,[original],True,reviewer='合成审核人',note='软件测试')[0]['status']=='failed'


def test_failed_item_does_not_prevent_other_selected_items(tmp_path,monkeypatch):
    store=Store(tmp_path/'io.db')
    rows=[store.get_teaching_method(i) for i in ['LM01','LM02']]
    actual=store.set_teaching_method_enabled
    def fail_first(identity,*args,**kwargs):
        if identity=='LM01':raise OSError('合成单项失败')
        return actual(identity,*args,**kwargs)
    monkeypatch.setattr(store,'set_teaching_method_enabled',fail_first)
    result=apply(store,'teaching',rows,True,reviewer='合成',note='软件测试')
    assert [r['status'] for r in result]==['failed','success'] and '合成单项失败' in result[0]['reason']


def test_batch_requires_explicit_boolean_and_unique_targets(tmp_path):
    store=Store(tmp_path/'invalid.db');row=store.get_teaching_method('LM01')
    for kind,records,flag in [('unknown',[row],True),('teaching',[row],1),('teaching',[row,row],True),('teaching',[],True)]:
        with pytest.raises(ValueError):apply(store,kind,records,flag)

