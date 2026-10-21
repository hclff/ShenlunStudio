"""Confirmed material points are authoritative snapshots, never raw JSON claims."""
import copy
import pytest
from shenlun.service import AppService
from shenlun.storage import Store
from shenlun.practice import comparison
from shenlun.reports import review_html


RAW={'question':'概括社区成效。','materials':'社区开通电话服务，老人能顺利报修。','answer':'方便老人报修。','question_type':'归纳概括','max_score':20}
POINTS=[{'id':'P1','text':'方便老人报修','material_refs':[{'id':'M1','quote':'老人能顺利报修'}]}]
CONFIG={'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000}


def assessment(payload):
    s=payload['submission']
    result={'summary':'核对材料','task_analysis':'概括成效','dimensions':[{'id':row['id'],'score':80,'reason':'符合材料'} for row in s['rubric']],
            'annotations':[],'material_points':[{'point':'老人能顺利报修','material_refs':[{'id':'M1','quote':'老人能顺利报修'}],'coverage':'covered','answer_quote':'老人报修'}],
            'strengths':[],'next_action':'继续概括','uncertainties':[]}
    if payload.get('confirmed_reference_points'):
        result['reference_point_coverage']=[{'id':'P1','coverage':'covered','answer_quote':'老人报修','reason':'语义对应'}]
    return result


class Client:
    def __init__(self,reply=assessment):self.payloads=[];self.reply=reply
    def complete(self,system,payload,cancel=None):
        self.payloads.append(copy.deepcopy(payload))
        data=self.reply(payload) if len(self.payloads)==1 else {'minimal':'方便老人报修。','model':'电话服务方便老人报修。'}
        return {'data':data,'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'model':'fixture'}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'reference-service.db')


def service(store,reply=assessment):
    s=AppService(store,CONFIG,'');s.client=Client(reply);return s


def test_evaluation_grade_only_records_usage_without_review_or_revision(store):
    s=service(store)
    assert hasattr(s,'evaluate_submission'),'grade-only service required'
    result=s.evaluate_submission(RAW)
    assert result['assessment']['total_score']==16 and len(s.client.payloads)==1
    assert not store.list_reviews() and store.usage_summary()['requests']==1


def test_confirmed_snapshot_sent_and_persisted_but_drafts_and_raw_claims_ignored(store):
    qid=store.save_question(RAW)
    draft=store.save_reference_points(qid,POINTS)
    s=service(store);r=s.assess({**RAW,'confirmed_reference_points':{'status':'confirmed','points':POINTS}})
    assert not s.client.payloads[0].get('confirmed_reference_points') and not r.get('reference_points_snapshot')
    confirmed=store.save_reference_points(qid,POINTS,reviewer='教师甲',confirm=True,expected_version=draft['version'])
    s=service(store);r=s.assess(RAW)
    assert r['reference_points_snapshot']==confirmed and s.client.payloads[0]['confirmed_reference_points']==confirmed
    assert r['assessment']['reference_point_coverage'][0]['coverage']=='covered'
    assert '人工确认参考要点' in review_html(r) and '教师甲' in review_html(r)
    store.withdraw_reference_points(qid,'教师甲','修订参考')
    assert store.get_review(r['id'])['reference_points_snapshot']==confirmed


def test_bad_point_coverage_is_rejected_after_usage_and_before_review_success(store):
    qid=store.save_question(RAW);store.save_reference_points(qid,POINTS,reviewer='教师甲',confirm=True)
    def bad(payload):
        result=assessment(payload);result['reference_point_coverage'][0]['answer_quote']='虚构答案'
        return result
    with pytest.raises(ValueError):service(store,bad).assess(RAW)
    assert not store.list_reviews() and store.usage_summary()['requests']==1


def test_new_point_version_blocks_false_score_gain_and_old_snapshot_remains_frozen(store):
    qid=store.save_question(RAW);store.save_reference_points(qid,POINTS,reviewer='教师甲',confirm=True)
    left=service(store).assess(RAW)
    store.save_reference_points(qid,POINTS,reviewer='教师乙',confirm=True)
    right=service(store).assess(RAW)
    result=comparison(left,right)
    assert not result['comparable'] and any('参考要点' in reason for reason in result['reasons'])


def test_evaluation_none_snapshot_stays_none_after_live_confirmation(store):
    qid=store.save_question(RAW);store.save_reference_points(qid,POINTS,reviewer='教师甲',confirm=True)
    s=service(store)
    assert hasattr(s,'evaluate_submission'),'grade-only service required'
    result=s.evaluate_submission(RAW,reference_points_snapshot=None)
    assert not result.get('reference_points_snapshot') and not s.client.payloads[0].get('confirmed_reference_points')
