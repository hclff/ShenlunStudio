import copy
import json
import threading
from pathlib import Path

import pytest

from shenlun.storage import Store
from shenlun.service import AppService
from shenlun.model import CancelledError, ModelError


CONFIG={'base_url':'http://127.0.0.1:12345/v1','model':'fixture','session_token_budget':150000}


def raw(**changes):
    return {'question':'概括成效','materials':'社区增设电话报修，老人能顺利报修。','answer':'方便老人报修。','question_type':'归纳概括','max_score':20,**changes}


def ai_assessment(payload):
    s=payload['submission']
    points=[] if s['mode']=='diagnostic' else [{'point':'老人能顺利报修','material_refs':[{'id':'M1','quote':'老人能顺利报修'}],'coverage':'covered','answer_quote':'老人报修'}]
    return {'summary':'主体与成效明确','task_analysis':'概括成效','dimensions':[{'id':d['id'],'score':80,'reason':'对应材料'} for d in s['rubric']],
            'annotations':[],'material_points':points,'strengths':['表意明确'],'next_action':'练习从做法提取成效','uncertainties':[]}


class ScriptedClient:
    def __init__(self, replies):self.replies=list(replies);self.spent_tokens=0
    def complete(self,system,payload,cancel=None):
        reply=self.replies.pop(0)
        if isinstance(reply,Exception):raise reply
        data=reply(payload) if callable(reply) else reply
        return {'data':data,'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'model':'fixture'}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'test.db')


def service(store,replies):
    app=AppService(store,CONFIG,'')
    app.client=ScriptedClient(replies)
    return app


def test_assessment_is_saved_before_revision_failure(store):
    svc=service(store,[ai_assessment,ModelError('模型超时')])
    result=svc.assess(raw())
    saved=Store(store.path).get_review(result['id'])
    assert saved['submission']['answer']=='方便老人报修。'
    assert saved['assessment']['total_score']==16
    assert saved['revisions']['minimal']=='' and '超时' in saved['revisions']['status']


def test_retry_revisions_refuses_deleted_or_concurrently_changed_review(store):
    svc = service(store, [ai_assessment, ModelError('模型超时')])
    review = svc.assess(raw())
    current = copy.deepcopy(review)
    current['revisions']['status'] = '另一个窗口更新'
    store.save_review(current)
    with pytest.raises(ValueError, match='更新'):
        service(store, []).retry_revisions(review['id'], expected_review=review)
    with pytest.raises(ValueError, match='不存在'):
        service(store, []).retry_revisions('not-a-review')


def test_revision_retry_cannot_overwrite_a_review_changed_during_model_call(store):
    review = service(store, [ai_assessment, ModelError('模型超时')]).assess(raw())
    def concurrent_update(payload):
        current = store.get_review(review['id'])
        current['revisions'] = {'minimal':'另一个窗口的修改稿','model':'另一个示范稿','status':'完成'}
        store.save_review(current)
        return {'minimal':'迟到的修改稿','model':'迟到的示范稿'}
    with pytest.raises(ValueError, match='更新'):
        service(store, [concurrent_update]).retry_revisions(review['id'], expected_review=review)
    assert store.get_review(review['id'])['revisions']['minimal'] == '另一个窗口的修改稿'


def test_revisions_are_separate_and_cannot_change_original_grade(store):
    svc=service(store,[ai_assessment,{'minimal':'便于老人报修。','model':'报修渠道更加多元，服务可及性提高。'}])
    r=svc.assess(raw())
    assert r['assessment']['total_score']==16
    assert r['submission']['answer']=='方便老人报修。'
    assert r['revisions']['minimal']=='便于老人报修。'
    assert store.usage_summary()['requests']==2


def test_diagnostic_cannot_receive_total_score(store):
    svc=service(store,[ai_assessment,{'minimal':'服务有温度。','model':'服务应贴近群众。'}])
    r=svc.assess(raw(question='',materials=''))
    assert r['assessment']['total_score'] is None


def test_invalid_citation_never_persists_success(store):
    def invalid(payload):
        r=ai_assessment(payload)
        r['annotations']=[{'start':0,'end':7,'quote':'方便老人报修。','issue':'遗漏','suggestion':'补充','material_refs':[{'id':'M1','quote':'虚构事实'}],'corpus_refs':[]}]
        return r
    svc=service(store,[invalid])
    with pytest.raises(ValueError):svc.assess(raw())
    assert store.list_reviews()==[]


def test_cancelled_before_request_does_not_save(store):
    svc=service(store,[]);e=threading.Event();e.set()
    with pytest.raises(CancelledError):svc.assess(raw(),cancel=e)
    assert store.list_reviews()==[]


def test_cancel_revision_preserves_original_with_interrupted_status(store):
    svc=service(store,[ai_assessment,CancelledError('任务已取消')])
    with pytest.raises(CancelledError):svc.assess(raw())
    saved=store.list_reviews()[0]
    assert saved['assessment']['total_score']==16
    assert '取消' in saved['revisions']['status']


def add_article(store,aid='a'):
    body='保留人工服务，降低办理门槛。'+('保障实际需求。' if aid!='a' else '')
    store.upsert_article({'id':aid,'title':'服务的温度','journal':'求是','year':2026,'issue':1,'url':'https://www.qstheory.cn/'+aid,'body':body,'status':'available','kind':'理论',
                          'paragraphs':[{'id':aid+':p1','text':body}]})


def extraction(payload):
    p=payload['paragraphs'][0]
    return {'thesis':'服务需要可及性','structure':'因果分析','paragraph_roles':[{'paragraph_id':p['id'],'role':'机制解释','reason':'说明措施如何发挥作用'}],
            'logic_chains':[{'claim':'降低门槛','evidence_refs':[{'id':p['id'],'quote':'降低办理门槛'}],'analysis':'人工渠道降低使用门槛','boundary':'数字使用困难人群'}],
            'language_features':[{'feature':'明确对象','applicability':'解释措施作用','refs':[{'id':p['id'],'quote':'保留人工服务'}]}],
            'methods':[{'name':'解释措施作用机制','purpose':'把措施转为论证','steps':['说明行动','解释如何改变结果'],'applicable_types':['文章写作'],'not_applicable':'仅需简要概括时不扩写','refs':[{'id':p['id'],'quote':'保留人工服务，降低办理门槛'}]}], 'limitations':[]}


def test_extraction_resumes_and_never_auto_enables_method(store):
    add_article(store)
    svc=service(store,[extraction])
    result=svc.extract(['a'])
    assert result['processed']==1
    assert store.get_analysis('a') is not None
    assert not store.list_cards(enabled_only=True)
    result2=svc.extract(['a'])
    assert result2['skipped']==1


def test_invented_extraction_quote_is_not_saved(store):
    add_article(store)
    def bad(payload):
        r=extraction(payload);r['methods'][0]['refs'][0]['quote']='虚构数字'
        return r
    svc=service(store,[bad])
    with pytest.raises(ValueError):svc.extract(['a'])
    assert store.get_analysis('a') is None
    assert store.list_cards()==[]


def test_image_only_available_entry_is_not_fabricated_as_analysis(store):
    store.upsert_article({'id':'image','title':'统计图表','status':'available','body':'','paragraphs':[],'images':[{'url':'https://www.qstheory.cn/chart.jpg'}]})
    svc=service(store,[])
    assert svc.estimate_extraction(['image'])['articles']==0
    r=svc.extract(['image'])
    assert r['processed']==0
    assert store.get_analysis('image') is None


def test_enable_general_method_requires_distinct_sources(store):
    add_article(store)
    svc=service(store,[extraction]);svc.extract(['a'])
    card=store.list_cards()[0]
    with pytest.raises(ValueError,match='两篇'):
        svc.enable_card(card['id'],True)
    add_article(store,'b')
    svc.client=ScriptedClient([extraction]);svc.extract(['b'])
    card=store.list_cards()[0]
    svc.enable_card(card['id'],True)
    assert store.list_cards(enabled_only=True)[0]['enabled'] is True


def test_claimed_extra_source_without_evidence_does_not_enable_method(store):
    add_article(store);add_article(store,'b')
    cid=store.save_card({'name':'待核验方法','source_article_ids':['a','b'],'refs':[{'id':'a:p1','quote':'降低办理门槛'}]})
    svc=service(store,[])
    with pytest.raises(ValueError,match='两篇'):
        svc.enable_card(cid,True)


def test_updated_article_invalidates_method_before_assessment(store):
    add_article(store);add_article(store,'b')
    svc = service(store,[extraction,extraction])
    svc.extract(['a','b'])
    card = store.list_cards()[0]
    svc.enable_card(card['id'],True)
    article = store.get_article('b')
    article['body'] += '原文发生修订。'
    store.upsert_article(article)
    def verify(payload):
        assert payload['method_guidance'] == []
        return ai_assessment(payload)
    svc.client = ScriptedClient([verify,{'minimal':'保留原意。','model':'示范稿。'}])
    result = svc.assess(raw(question_type='文章写作'))
    assert not store.list_cards(enabled_only=True)
    assert result['method_snapshot'] == []


def test_reextract_updated_article_replaces_obsolete_method_evidence(store):
    add_article(store);add_article(store,'b')
    svc=service(store,[extraction,extraction])
    svc.extract(['a','b'])
    article=store.get_article('a')
    article['body']='保持线下窗口，方便群众办事。'
    store.upsert_article(article)
    def updated(payload):
        data=extraction(payload)
        quote=payload['paragraphs'][0]['text']
        for item in data['logic_chains']:
            item['evidence_refs'][0]['quote']=quote
        for item in data['language_features']+data['methods']:
            item['refs'][0]['quote']=quote
        return data
    svc.client=ScriptedClient([updated])
    svc.extract(['a'])
    card=store.list_cards()[0]
    svc.enable_card(card['id'],True)
    assert not any(r['id']=='a:p1' and '降低办理门槛' in r['quote'] for r in card['refs'])
    assert any(r['id']=='b:p1' for r in card['refs'])
    assert len(store.list_cards(enabled_only=True))==1
