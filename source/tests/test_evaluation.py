"""Real provenance, blind human observations and immutable repeated-run inputs."""
import importlib
import importlib.util
import json
import threading

import pytest
from shenlun.storage import Store
from shenlun.model import CancelledError


def module():
    assert importlib.util.find_spec('shenlun.evaluation'), 'evaluation domain required'
    return importlib.import_module('shenlun.evaluation')


def submission(answer='保留人工窗口。'):
    return {'question':'概括服务站做法。','materials':'服务站保留人工窗口。\n社区提供电话预约。',
            'answer':answer,'question_type':'归纳概括','max_score':20,'word_limit':100}


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'evaluation.db')


def dataset(store):
    assert hasattr(store,'create_eval_set'),'evaluation storage required'
    return store.create_eval_set('真实答卷试点')


def test_sets_cases_provenance_and_question_group_split(store):
    module();d=dataset(store)
    c=store.add_eval_case(d['id'],submission(),origin='public_candidate',source='作者公开自答，身份未核验')
    assert c['origin']=='public_candidate' and c['answer_complete'] and c['content_hash']
    with pytest.raises(ValueError):store.add_eval_case(d['id'],submission('社区开通电话。'),partition='holdout')
    with pytest.raises(ValueError):store.add_eval_case(d['id'],submission(),origin='official_truth')
    assert store.get_eval_set(d['id'])['version']==2
    assert not store.list_reviews() and not store.list_questions()


def test_human_zero_explicit_independence_and_version_history(store):
    module();d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    for value in (None,True,float('nan'),-1,21):
        with pytest.raises(ValueError):store.save_eval_label(c['id'],value,reviewer='阅卷者')
    first=store.save_eval_label(c['id'],0,reason='缺少主要信息',reviewer='老师甲',independent=True,expected_version=0)
    assert first['score']==0 and first['independent'] and first['case_hash']==c['content_hash']
    with pytest.raises(ValueError):store.save_eval_label(c['id'],10,reviewer='老师乙',expected_version=0)
    second=store.save_eval_label(c['id'],2,reviewer='老师甲',expected_version=1)
    assert second['version']==2 and len(store.list_eval_labels(c['id']))==2


def test_blind_export_hides_ai_origin_and_variant_and_import_is_atomic(store):
    m=module();d=dataset(store)
    c=store.add_eval_case(d['id'],submission(),origin='gpt_control',source='flawed-expected-low')
    rows=m.blind_eval_rows(store,d['id'])
    encoded=json.dumps(rows,ensure_ascii=False)
    assert 'flawed' not in encoded and 'gpt_control' not in encoded and 'ai_score' not in encoded
    good={**rows[0],'human':0,'reviewer':'盲评甲','human_reason':'待完善','independent':True}
    with pytest.raises(ValueError):store.import_eval_labels(d['id'],[good,{**good,'case_id':'wrong'}])
    assert store.get_eval_label(c['id']) is None
    assert store.import_eval_labels(d['id'],[good])['imported']==1
    assert store.get_eval_label(c['id'])['score']==0
    with pytest.raises(ValueError):store.import_eval_labels(d['id'],[{**good,'content_hash':'altered'}])
    with pytest.raises(ValueError):store.import_eval_labels(d['id'],[{**good,'answer':'评阅时已改过的原答'}])


def test_incomplete_source_excerpt_cannot_be_scored_as_complete_answer(store):
    m=module();d=dataset(store)
    c=store.add_eval_case(d['id'],submission('公开帖子短摘录'),origin='public_candidate',answer_complete=False)
    with pytest.raises(ValueError):m.plan_evaluation(store,d['id'],[c['id']],3,{'base_url':'http://127.0.0.1:1/v1','model':'fixture'})


def test_run_freezes_cases_labels_points_config_and_blocks_changed_plan(store):
    m=module();d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    config={'base_url':'http://127.0.0.1:1/v1','model':'fixture','api_key':'must-not-save','session_token_budget':1000000}
    plan=m.plan_evaluation(store,d['id'],[c['id']],2,config)
    assert plan['calls']==2 and plan['estimated_total_tokens']>0
    run=store.create_eval_run(d['id'],[c['id']],2,config,expected_plan_hash=plan['plan_hash'])
    assert run['cases'][0]['content_hash']==c['content_hash'] and 'api_key' not in json.dumps(run)
    store.add_eval_case(d['id'],submission('电话预约。'),origin='unknown')
    assert len(store.get_eval_run(run['id'])['cases'])==1
    with pytest.raises(ValueError):store.create_eval_run(d['id'],[c['id']],2,config,expected_plan_hash=plan['plan_hash'])


def fixture_review(s,score):
    from shenlun.domain import prepare_submission
    prepared=prepare_submission(s)
    return {'submission':prepared,'assessment':{'total_score':score,'max_score':20,'mode':'full',
        'rubric_fingerprint':prepared['rubric_fingerprint']},'model':'fixture','prompt_version':'fixed',
        'config_snapshot':{'base_url':'http://127.0.0.1:1/v1'},'usage':{'total_tokens':20}}


def test_runner_one_grade_per_unit_no_revisions_or_regular_review_pollution(store):
    m=module();d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    config={'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000}
    run=store.create_eval_run(d['id'],[c['id']],2,config)
    class Service:
        def __init__(self):self.config=run['config'];self.calls=[]
        def evaluate_submission(self,s,reference_points_snapshot=None,progress=None,cancel=None,grade_context=None):
            self.calls.append(s);return fixture_review(s,10+len(self.calls))
    service=Service();result=m.run_evaluation(store,service,run['id'])
    assert result['status']=='completed' and len(service.calls)==2
    assert len(store.list_eval_results(run['id']))==2 and not store.list_reviews()
    assert m.eval_run_summary(store,run['id'])['repeat_groups'][0]['spread']==1


def test_cancelled_late_result_is_not_saved_as_success_and_later_calls_stop(store):
    m=module();d=dataset(store);c=store.add_eval_case(d['id'],submission())
    run=store.create_eval_run(d['id'],[c['id']],3,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    event=threading.Event()
    class Service:
        config=run['config'];calls=0
        def evaluate_submission(self,s,**kwargs):
            self.calls+=1
            if self.calls==2:event.set()
            return fixture_review(s,10)
    service=Service();result=m.run_evaluation(store,service,run['id'],cancel=event)
    rows=store.list_eval_results(run['id'])
    assert service.calls==2 and result['status']=='cancelled'
    assert sum(row['status']=='success' for row in rows)==1


def test_metrics_separate_real_independent_labels_from_ai_controls_and_missing_labels(store):
    m=module();d=dataset(store)
    real=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    controlled=store.add_eval_case(d['id'],submission('电话服务。'),origin='gpt_control')
    store.save_eval_label(real['id'],0,reviewer='独立阅卷者',independent=True)
    store.save_eval_label(controlled['id'],20,reviewer='控制集标注',independent=True)
    run=store.create_eval_run(d['id'],[real['id'],controlled['id']],2,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    for c in (real,controlled):
        for repeat in range(1,3):store.save_eval_result(run['id'],c['id'],repeat,'success',review=fixture_review(c['submission'],10))
    summary=m.eval_run_summary(store,run['id'])
    assert summary['human_pairs']==1 and summary['mae_percent']==50 and summary['bias_percent']==50
    assert summary['origin_counts']['gpt_control']==1 and len(summary['repeat_groups'])==2
    assert '不代表' in summary['notice']


def test_backup_restore_retains_eval_and_point_tables_without_affecting_legacy(store,tmp_path):
    module();d=dataset(store);c=store.add_eval_case(d['id'],submission())
    store.save_eval_label(c['id'],0,reviewer='阅卷者')
    backup=store.backup_to(tmp_path/'evaluation-backup.db')
    restored=Store(tmp_path/'restored.db');restored.restore_from(backup)
    assert restored.get_eval_case(c['id'])['content_hash']==c['content_hash']
    assert restored.get_eval_label(c['id'])['score']==0


def test_repeated_real_service_uses_frozen_corpus_even_if_live_articles_change(store):
    m=module();from shenlun.service import AppService
    d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    store.upsert_article({'id':'a','title':'便民服务','body':'保留人工窗口，提供便民服务，服务站通过人工协助满足群众实际需求，避免办理困难。','status':'available'})
    config={'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000}
    run=store.create_eval_run(d['id'],[c['id']],2,config)
    payloads=[]
    class Client:
        def complete(self,system,payload,cancel=None):
            payloads.append(json.dumps(payload,ensure_ascii=False,sort_keys=True))
            store.upsert_article({'id':'a','title':'便民服务','body':'调整后的新服务证据。','status':'available'})
            s=payload['submission']
            result={'summary':'核对','task_analysis':'概括做法','dimensions':[{'id':row['id'],'score':70,'reason':'对应材料'} for row in s['rubric']],
                'annotations':[],'material_points':[{'point':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}],'coverage':'covered','answer_quote':'保留人工窗口'}],
                'strengths':[],'next_action':'完善概括','uncertainties':[]}
            return {'data':result,'model':'fixture','usage':{'total_tokens':20}}
    service=AppService(store,run['config'],'');service.client=Client()
    m.run_evaluation(store,service,run['id'])
    assert len(payloads)==2 and '避免办理困难' in payloads[0] and payloads[0]==payloads[1]


def test_multiple_actual_model_conditions_do_not_merge_human_error(store):
    m=module();d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='real_candidate')
    store.save_eval_label(c['id'],10,reviewer='独立人工',independent=True)
    run=store.create_eval_run(d['id'],[c['id']],2,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    for n,model in ((1,'model-a'),(2,'model-b')):
        review=fixture_review(c['submission'],10);review['model']=model
        store.save_eval_result(run['id'],c['id'],n,'success',review=review)
    summary=m.eval_run_summary(store,run['id'])
    assert summary['mae_percent'] is None and len(summary['metrics_groups'])==2


def test_malformed_new_tables_or_case_hash_backup_is_rejected_before_restore(store,tmp_path):
    module();import sqlite3
    d=dataset(store);c=store.add_eval_case(d['id'],submission())
    path=store.backup_to(tmp_path/'malformed.db')
    with sqlite3.connect(path) as conn:
        row=json.loads(conn.execute('SELECT data FROM eval_cases WHERE id=?',(c['id'],)).fetchone()[0])
        row['content_hash']='forged';conn.execute('UPDATE eval_cases SET data=? WHERE id=?',(json.dumps(row),c['id']))
    with pytest.raises(ValueError):store.restore_from(path)
    assert store.get_eval_case(c['id'])['content_hash']==c['content_hash']


def test_same_run_cannot_start_twice_or_be_completed_without_all_results(store):
    module();d=dataset(store);c=store.add_eval_case(d['id'],submission())
    run=store.create_eval_run(d['id'],[c['id']],2,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    store.finish_eval_run(run['id'],'running')
    with pytest.raises(ValueError):store.finish_eval_run(run['id'],'running')
    with pytest.raises(ValueError):store.finish_eval_run(run['id'],'completed')


def test_success_result_must_bind_to_frozen_case_and_cannot_write_after_finish(store):
    module();d=dataset(store);c=store.add_eval_case(d['id'],submission())
    run=store.create_eval_run(d['id'],[c['id']],1,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    wrong=fixture_review(submission('另一份原答。'),10)
    with pytest.raises(ValueError):store.save_eval_result(run['id'],c['id'],1,'success',review=wrong)
    store.finish_eval_run(run['id'],'failed')
    with pytest.raises(ValueError):store.save_eval_result(run['id'],c['id'],1,'success',review=fixture_review(c['submission'],10))


@pytest.mark.parametrize('mutation',['origin','source','source_metadata','answer_complete','config_secret'])
def test_restore_rejects_frozen_provenance_changes_and_secret_config(store,tmp_path,mutation):
    m=module();import sqlite3
    d=dataset(store);c=store.add_eval_case(d['id'],submission(),origin='gpt_control',source='合成对照')
    run=store.create_eval_run(d['id'],[c['id']],1,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    path=store.backup_to(tmp_path/(mutation+'.db'))
    with sqlite3.connect(path) as conn:
        value=json.loads(conn.execute('SELECT data FROM eval_runs WHERE id=?',(run['id'],)).fetchone()[0])
        if mutation=='config_secret':value['config']['api_key']='synthetic-secret-must-not-export'
        else:value['cases'][0][mutation]={'origin':'real_candidate','source':'伪造真实来源','source_metadata':{'note':'伪造授权'},'answer_complete':False}[mutation]
        value['plan_hash']=m.canonical_hash({key:value.get(key) for key in ('set_id','dataset_version','cases','repeats','config','prompt_version')})
        conn.execute('UPDATE eval_runs SET data=? WHERE id=?',(json.dumps(value),run['id']))
    with pytest.raises(ValueError):store.restore_from(path)
    assert store.get_eval_run(run['id'])['cases'][0]['origin']=='gpt_control'


def test_malformed_import_identifiers_and_enums_raise_validation_error(store):
    m=module();d=dataset(store)
    for records in ([{'submission':submission(),'origin':[]}],[{'submission':submission(),'partition':{}}]):
        with pytest.raises(ValueError):store.import_eval_cases(d['id'],records)
    with pytest.raises(ValueError):store.import_eval_labels(d['id'],[{'case_id':[]}])
    with pytest.raises(ValueError):m.plan_evaluation(store,d['id'],[[]],3,{})


def test_report_recursively_redacts_secret_keys_in_stored_records(store,monkeypatch):
    m=module();d=dataset(store);c=store.add_eval_case(d['id'],submission())
    run=store.create_eval_run(d['id'],[c['id']],1,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    raw=store.get_eval_run(run['id']);raw['config']['api_key']='export-sentinel'
    monkeypatch.setattr(store,'get_eval_run',lambda identifier:raw)
    assert 'export-sentinel' not in json.dumps(m.evaluation_report(store,run['id']))


@pytest.mark.parametrize('cancelled',[False,True])
def test_received_invalid_or_cancelled_model_response_keeps_actual_usage(store,monkeypatch,cancelled):
    m=module();from shenlun.model import ModelClient
    from shenlun.service import AppService
    d=dataset(store);c=store.add_eval_case(d['id'],submission())
    run=store.create_eval_run(d['id'],[c['id']],2,{'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000})
    event=threading.Event();calls=[]
    def received(self,encoded):
        calls.append(encoded)
        if cancelled:event.set()
        return {'model':'fixture','choices':[{'message':{'content':'invalid-json'}}],
                'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}}
    monkeypatch.setattr(ModelClient,'_post',received)
    service=AppService(store,run['config'],'fixture-not-real-key')
    result=m.run_evaluation(store,service,run['id'],cancel=event)
    assert result['status']==('cancelled' if cancelled else 'failed') and len(calls)==1
    assert store.usage_summary()['requests']==1 and store.usage_summary()['total_tokens']==18
    assert service.client.spent_tokens==18 and not store.list_reviews()
    assert not any(row['status']=='success' for row in store.list_eval_results(run['id']))
