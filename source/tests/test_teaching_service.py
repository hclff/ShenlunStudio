"""Teacher guidance is sourced, locally reviewed and frozen in actual model input."""
import copy
import json
import sqlite3

import pytest

from shenlun.evaluation import plan_evaluation,run_evaluation
from shenlun.model import CancelledError
from shenlun.practice import comparison
from shenlun.reports import review_html
from shenlun.service import AppService
from shenlun.storage import Store

RAW={'question':'概括社区服务做法。','materials':'社区保留人工窗口。\n服务站开通电话预约。',
     'answer':'保留人工窗口，开通电话预约。','question_type':'归纳概括','max_score':20,'word_limit':100}
CONFIG={'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'teaching-service.db')


def enable(store,owner='limengyuan'):
    assert hasattr(store,'list_teaching_methods'),'separate teaching library required'
    draft=next(card for card in store.list_teaching_methods() if card['owner_id']==owner and '归纳概括' in card['applicable_types'])
    return store.set_teaching_method_enabled(draft['id'],True,reviewer='合成测试确认者',note='已核对合成测试来源',expected_version=draft['version'])


def reply(payload):
    s=payload['submission']
    result={'summary':'核对材料','task_analysis':'概括做法','dimensions':[{'id':d['id'],'score':70,'reason':'对应材料'} for d in s['rubric']],
            'annotations':[],'material_points':[{'point':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}],
                                               'coverage':'covered','answer_quote':'保留人工窗口'}],
            'strengths':[],'next_action':'核对分类','uncertainties':[]}
    snapshot=payload.get('teaching_guidance')
    if snapshot:
        result['teaching_analysis']={'applications':[{'method_id':m['id'],'status':'applied','observation':'以材料核对要点',
                    'material_refs':[{'id':'M1','quote':'保留人工窗口'}],'answer_quote':'保留人工窗口'} for m in snapshot['methods']],
                    'consensus':['保留具体事实'],'differences':['分类须依本题材料决定'] if snapshot['mode']=='compare' else []}
    return result


class Client:
    def __init__(self,transform=None):self.payloads=[];self.transform=transform
    def complete(self,system,payload,cancel=None):
        self.payloads.append(copy.deepcopy(payload))
        if 'answer_segments' not in payload:data={'minimal':'保留人工窗口，开通电话预约。','model':'保留人工窗口，开通电话预约。'}
        else:
            data=reply(payload)
            if self.transform:self.transform(data)
        return {'data':data,'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'model':'fixture'}


def service(store,transform=None):
    s=AppService(store,CONFIG,'');s.client=Client(transform);return s


def test_separate_draft_catalog_never_injects_fake_confirmed_teacher_or_model_claim(store):
    assert hasattr(store,'list_teaching_methods'),'teaching drafts required'
    assert len(store.list_teaching_methods())==24 and not store.list_teaching_methods(enabled_only=True)
    def claim(data):data['teaching_analysis']={'applications':[{'method_id':'forged'}]}
    s=service(store,claim)
    review=s.evaluate_submission({**RAW,'teaching_guidance':{'enabled':True,'owner_id':'bailu'}})
    assert 'teaching_guidance' not in s.client.payloads[0] and 'teaching_snapshot' not in review
    assert 'teaching_analysis' not in review['assessment'] and len(s.client.payloads)==1
    assert not store.list_cards() and not store.list_reviews()


def test_reviewed_single_source_methods_sent_validated_and_reported_without_extra_call(store):
    card=enable(store);store.set_setting('teaching_selection',{'mode':'source','owner_ids':['limengyuan']})
    s=service(store);review=s.evaluate_submission(RAW)
    assert review['teaching_snapshot']['methods'][0]['id']==card['id']
    assert review['assessment']['teaching_analysis']['applications'][0]['method_id']==card['id']
    assert s.client.payloads[0]['teaching_guidance']==review['teaching_snapshot'] and len(s.client.payloads)==1
    report=review_html(review)
    assert '名师公开方法' in report and '合成测试确认者' in report and card['sources'][0]['url'] in report
    assert store.usage_summary()['requests']==1 and not store.list_reviews()


def test_compare_uses_same_actual_assessment_call_and_no_teacher_score_average(store):
    a=enable(store);b=enable(store,'fenbi')
    store.set_setting('teaching_selection',{'mode':'compare','owner_ids':['limengyuan','fenbi']})
    s=service(store);review=s.evaluate_submission(RAW)
    assert {m['id'] for m in review['teaching_snapshot']['methods']}=={a['id'],b['id']}
    assert review['assessment']['total_score']==14 and len(s.client.payloads)==1
    assert review['assessment']['teaching_analysis']['differences']


def test_pending_source_rejected_before_model_and_usage(store):
    store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    s=service(store)
    with pytest.raises(ValueError):s.evaluate_submission(RAW)
    assert not s.client.payloads and store.usage_summary()['requests']==0


@pytest.mark.parametrize('bad',['method','material','answer'])
def test_unverifiable_teaching_output_rejected_after_usage_without_success(store,bad):
    enable(store)
    def corrupt(data):
        app=data['teaching_analysis']['applications'][0]
        if bad=='method':app['method_id']='forged'
        elif bad=='material':app['material_refs'][0]['quote']='材料里没有的引文'
        else:app['answer_quote']='答案里没有的引文'
    s=service(store,corrupt)
    with pytest.raises(ValueError):s.evaluate_submission(RAW)
    assert store.usage_summary()['requests']==1 and not store.list_reviews()


def test_eval_repeats_pin_actual_teacher_payload_after_live_disable(store):
    card=enable(store);dataset=store.create_eval_set('合成教学测试')
    case=store.add_eval_case(dataset['id'],RAW,origin='gpt_control')
    plan=plan_evaluation(store,dataset['id'],[case['id']],2,CONFIG)
    run=store.create_eval_run(dataset['id'],[case['id']],2,CONFIG,expected_plan_hash=plan['plan_hash'])
    store.set_teaching_method_enabled(card['id'],False,expected_version=card['version'])
    store.set_setting('teaching_selection',{'mode':'off','owner_ids':[]})
    s=service(store);run_evaluation(store,s,run['id'])
    assert len(s.client.payloads)==2 and s.client.payloads[0]==s.client.payloads[1]
    assert s.client.payloads[0]['teaching_guidance']['methods'][0]['version']==card['version']
    assert all(r['review']['teaching_snapshot']['content_hash'] for r in store.list_eval_results(run['id']))


def test_method_version_changes_block_false_progress_and_normal_revision_stays_two_calls(store):
    first=enable(store);s=service(store);left=s.assess(RAW)
    edited=store.save_teaching_method({**first,'purpose':'合成新目的'},expected_version=first['version'])
    store.set_teaching_method_enabled(edited['id'],True,reviewer='合成再次确认',note='合成核对',expected_version=edited['version'])
    t=service(store);right=t.assess(RAW)
    compared=comparison(left,right)
    assert not compared['comparable'] and compared['delta'] is None and any('教学方法' in reason for reason in compared['reasons'])
    assert len(s.client.payloads)==len(t.client.payloads)==2
    assert store.get_review(left['id'])['teaching_snapshot']==left['teaching_snapshot']


def test_teaching_backup_roundtrip_and_record_hash_tamper_rejected(store,tmp_path):
    card=enable(store);backup=store.backup_to(tmp_path/'valid.db');restored=Store(tmp_path/'restored.db');restored.restore_from(backup)
    assert restored.get_teaching_method(card['id'])==card
    with sqlite3.connect(backup) as conn:
        data=json.loads(conn.execute('SELECT data FROM teaching_method_versions WHERE id=?',(card['id'],)).fetchone()[0])
        data['purpose']='forged';conn.execute('UPDATE teaching_method_versions SET data=? WHERE id=?',(json.dumps(data),card['id']))
    with pytest.raises(ValueError):restored.restore_from(backup)
    assert restored.get_teaching_method(card['id'])==card


def test_result_cannot_drop_or_replace_frozen_teacher_snapshot(store):
    enable(store);dataset=store.create_eval_set('合成冻结方法')
    case=store.add_eval_case(dataset['id'],RAW,origin='gpt_control')
    run=store.create_eval_run(dataset['id'],[case['id']],1,CONFIG)
    review=service(store).evaluate_submission(RAW)
    review.pop('teaching_snapshot')
    with pytest.raises(ValueError):store.save_eval_result(run['id'],case['id'],1,'success',review=review)
    assert not store.list_eval_results(run['id'])


def test_restore_rejects_rehashed_frozen_teacher_provenance_that_disagrees_with_local_history(store,tmp_path):
    from shenlun.evaluation import canonical_hash
    from shenlun.teaching import teaching_content_hash
    enable(store);dataset=store.create_eval_set('合成备份来源')
    case=store.add_eval_case(dataset['id'],RAW,origin='gpt_control')
    run=store.create_eval_run(dataset['id'],[case['id']],1,CONFIG)
    backup=store.backup_to(tmp_path/'forged-teacher.db')
    with sqlite3.connect(backup) as conn:
        value=json.loads(conn.execute('SELECT data FROM eval_runs WHERE id=?',(run['id'],)).fetchone()[0])
        snapshot=value['cases'][0]['grade_context']['payload']['teaching_guidance']
        method=snapshot['methods'][0];method['owner_id']='fenbi';method['content_hash']=teaching_content_hash(method)
        snapshot['content_hash']=teaching_content_hash(snapshot)
        from shenlun.teaching import validate_teaching_snapshot
        validate_teaching_snapshot(snapshot,value['cases'][0]['submission'])
        value['plan_hash']=canonical_hash({key:value[key] for key in ('set_id','dataset_version','cases','repeats','config','prompt_version')})
        conn.execute('UPDATE eval_runs SET data=? WHERE id=?',(json.dumps(value),run['id']))
    with pytest.raises(ValueError):store.restore_from(backup)


@pytest.mark.parametrize('bad',['hash','rehash','missing_history','output'])
def test_ordinary_review_restore_validates_historical_methods_and_exact_output(store,tmp_path,bad):
    from shenlun.teaching import teaching_content_hash
    card=enable(store);review=service(store).assess(RAW)
    store.set_teaching_method_enabled(card['id'],False,expected_version=card['version'])
    backup=store.backup_to(tmp_path/'ordinary.db');restored=Store(tmp_path/'ordinary-restored.db')
    restored.restore_from(backup)
    assert restored.get_review(review['id'])['teaching_snapshot']==review['teaching_snapshot']
    with sqlite3.connect(backup) as conn:
        value=json.loads(conn.execute('SELECT data FROM reviews WHERE id=?',(review['id'],)).fetchone()[0])
        if bad in ('hash','rehash'):
            value['teaching_snapshot']['methods'][0]['reviewer']='伪造审核者'
            if bad=='rehash':
                value['teaching_snapshot']['methods'][0]['content_hash']=teaching_content_hash(value['teaching_snapshot']['methods'][0])
                value['teaching_snapshot']['content_hash']=teaching_content_hash(value['teaching_snapshot'])
        elif bad=='missing_history':conn.execute('DELETE FROM teaching_method_versions WHERE id=?',(card['id'],))
        else:value['assessment']['teaching_analysis']['applications'][0]['material_refs'][0]['quote']='不存在的引文'
        conn.execute('UPDATE reviews SET data=? WHERE id=?',(json.dumps(value),review['id']))
    with pytest.raises(ValueError):restored.restore_from(backup)
    assert restored.get_review(review['id'])['teaching_snapshot']==review['teaching_snapshot']
