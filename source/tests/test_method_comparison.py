"""Controlled off-versus-preset plans and paired metrics use real frozen records."""
from copy import deepcopy
import importlib.util
import sqlite3
import threading

import pytest

from shenlun.storage import Store
from shenlun.model import DEFAULT_CONFIG
from test_evaluation import submission,fixture_review

CONFIG={**DEFAULT_CONFIG,'base_url':'http://127.0.0.1:1/v1','model':'fixture','session_token_budget':1000000}


def module():
    assert importlib.util.find_spec('shenlun.method_comparison'), 'paired method evaluation is required'
    import shenlun.method_comparison as result
    return result


def setup(store):
    module()
    store.set_teaching_method_enabled('LM01',True,reviewer='合成核对者',note='仅软件链路测试')
    preset=store.save_method_preset({'name':'合成方法组合','question_type':'归纳概括','teaching_ids':['LM01'],'article_ids':[]})
    dataset=store.create_eval_set('合成对照评测')
    first=store.add_eval_case(dataset['id'],submission(),origin='public_candidate',source='合成测试记录，不代表真实教师校准')
    second=store.add_eval_case(dataset['id'],submission('人工窗口、电话预约。'),origin='gpt_control')
    return dataset,[first,second],preset


class Service:
    def __init__(self,model_changed=False):self.config=CONFIG;self.calls=[];self.model_changed=model_changed
    def evaluate_submission(self,s,reference_points_snapshot=None,progress=None,cancel=None,grade_context=None):
        from shenlun.service import PROMPT_VERSION
        self.calls.append(deepcopy(grade_context));teaching=grade_context['payload'].get('teaching_guidance')
        review=fixture_review(s,11 if teaching else 8)
        review.update(prompt_version=PROMPT_VERSION,config_snapshot=deepcopy(CONFIG),
            sources=deepcopy(grade_context['sources']),method_snapshot=deepcopy(grade_context['method_snapshot']))
        if reference_points_snapshot:review['reference_points_snapshot']=deepcopy(reference_points_snapshot)
        if teaching:
            review['teaching_snapshot']=deepcopy(teaching)
            review['assessment']['teaching_analysis']={'applications':[{'method_id':m['id'],'status':'not_applicable',
                'observation':'合成测试，仅验证冻结方法链路','material_refs':[],'answer_quote':''} for m in teaching['methods']],
                'consensus':[],'differences':[]}
            if self.model_changed:review['model']='different-fixture'
        return review


def test_comparison_plan_freezes_matching_inputs_without_mutation(tmp_path):
    m=module();store=Store(tmp_path/'plan.db');dataset,cases,preset=setup(store)
    plan=m.plan_method_comparison(store,dataset['id'],[c['id'] for c in cases],2,CONFIG,preset['id'])
    assert plan['calls']==8 and plan['estimated_total_tokens']>0
    assert store.list_eval_runs(dataset['id'])==[] and store.get_setting('method_choice') is None
    baseline=plan['baseline'];variant=plan['method']
    for a,b in zip(baseline['cases'],variant['cases']):
        assert a['submission']==b['submission'] and a['reference_points_snapshot']==b['reference_points_snapshot']
        assert a['grade_context']['sources']==b['grade_context']['sources']
        assert not a['grade_context']['payload'].get('teaching_guidance') and not a['grade_context']['method_snapshot']
        assert b['grade_context']['payload']['teaching_guidance']['methods'][0]['id']=='LM01'


def test_comparison_paired_human_mae_and_repeat_spread_do_not_count_gpt_as_humans(tmp_path):
    m=module();store=Store(tmp_path/'metrics.db');dataset,cases,preset=setup(store)
    store.save_eval_label(cases[0]['id'],12,reviewer='合成评阅占位',independent=True)
    store.save_eval_label(cases[1]['id'],12,reviewer='合成评阅占位',independent=True)
    group=store.create_method_comparison(dataset['id'],[c['id'] for c in cases],2,CONFIG,preset['id'])
    service=Service();m.run_method_comparison(store,service,group['id'])
    report=m.method_comparison_report(store,group['id'])
    assert len(service.calls)==8 and not store.list_reviews()
    assert report['paired_cases']==2 and report['human_pairs']==1
    assert report['baseline_mae_percent']==20 and report['method_mae_percent']==5
    assert report['mae_change_percent']==-15
    assert all(row['baseline_spread']==0 and row['method_spread']==0 for row in report['pairs'])


def test_missing_humans_and_changed_actual_model_cannot_claim_accuracy(tmp_path):
    m=module();store=Store(tmp_path/'no-labels.db');dataset,cases,preset=setup(store)
    group=store.create_method_comparison(dataset['id'],[c['id'] for c in cases],1,CONFIG,preset['id'])
    m.run_method_comparison(store,Service(model_changed=True),group['id'])
    report=m.method_comparison_report(store,group['id'])
    assert report['paired_cases']==0 and report['human_pairs']==0
    assert report['baseline_mae_percent'] is None and report['method_mae_percent'] is None
    assert report['unmatched_cases']==2


def test_saved_comparison_uses_frozen_methods_after_disable_and_backup_restore(tmp_path):
    m=module();store=Store(tmp_path/'frozen.db');dataset,cases,preset=setup(store)
    group=store.create_method_comparison(dataset['id'],[c['id'] for c in cases],1,CONFIG,preset['id'])
    store.set_teaching_method_enabled('LM01',False)
    store.set_setting('method_choice',{'mode':'off'})
    restored=Store(tmp_path/'restored.db');restored.restore_from(store.backup_to(tmp_path/'frozen-backup.db'))
    m.run_method_comparison(restored,Service(),group['id'])
    assert m.method_comparison_report(restored,group['id'])['paired_cases']==2
    assert restored.get_setting('method_choice')=={'mode':'off'}


def test_comparison_budget_confirmation_hash_and_cancel_keep_results_honest(tmp_path):
    m=module();store=Store(tmp_path/'cancel.db');dataset,cases,preset=setup(store);ids=[c['id'] for c in cases]
    plan=m.plan_method_comparison(store,dataset['id'],ids,1,CONFIG,preset['id'])
    with pytest.raises(ValueError):m.plan_method_comparison(store,dataset['id'],ids,1,
        {**CONFIG,'session_token_budget':plan['estimated_total_tokens']-1},preset['id'])
    with pytest.raises(ValueError):store.create_method_comparison(dataset['id'],ids,1,CONFIG,preset['id'],expected_plan_hash='wrong')
    assert store.list_method_comparisons(dataset['id'])==[]
    group=store.create_method_comparison(dataset['id'],ids,1,CONFIG,preset['id'])
    event=threading.Event();event.set();service=Service()
    m.run_method_comparison(store,service,group['id'],cancel=event)
    assert not service.calls
    assert store.get_eval_run(group['baseline_run_id'])['status']=='cancelled'
    assert store.get_eval_run(group['method_run_id'])['status']=='cancelled'


def test_invalid_comparison_links_in_backup_are_rejected_atomically(tmp_path):
    import json
    m=module();store=Store(tmp_path/'bad.db');dataset,cases,preset=setup(store)
    group=store.create_method_comparison(dataset['id'],[c['id'] for c in cases],1,CONFIG,preset['id'])
    backup=store.backup_to(tmp_path/'bad-backup.db')
    with sqlite3.connect(backup) as conn:
        changed={**group,'method_run_id':group['baseline_run_id']}
        conn.execute('UPDATE method_comparisons SET data=? WHERE id=?',(json.dumps(changed),group['id']))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert store.get_method_comparison(group['id'])==group


def test_incomplete_repeat_counts_are_not_reported_as_paired_accuracy(tmp_path):
    m=module();store=Store(tmp_path/'incomplete.db');dataset,cases,preset=setup(store)
    store.save_eval_label(cases[0]['id'],12,reviewer='合成标签',independent=True)
    group=store.create_method_comparison(dataset['id'],[cases[0]['id']],2,CONFIG,preset['id'])
    m.run_method_comparison(store,Service(),group['id'])
    with store._connection() as conn:
        conn.execute('DELETE FROM eval_results WHERE run_id=? AND repeat=?',(group['method_run_id'],2))
    report=m.method_comparison_report(store,group['id'])
    assert report['paired_cases']==0 and report['human_pairs']==0 and report['mae_change_percent'] is None
    assert report['unmatched_cases']==1


def test_restoring_comparison_with_out_of_range_score_is_rejected_atomically(tmp_path):
    import json
    m=module();store=Store(tmp_path/'invalid-score.db');dataset,cases,preset=setup(store)
    group=store.create_method_comparison(dataset['id'],[cases[0]['id']],1,CONFIG,preset['id'])
    m.run_method_comparison(store,Service(),group['id'])
    backup=store.backup_to(tmp_path/'score-backup.db')
    with sqlite3.connect(backup) as conn:
        row=conn.execute('SELECT data FROM eval_results WHERE run_id=?',(group['baseline_run_id'],)).fetchone()
        bad=json.loads(row[0]);bad['review']['assessment']['total_score']=999
        conn.execute('UPDATE eval_results SET data=? WHERE run_id=?',(json.dumps(bad),group['baseline_run_id']))
    with pytest.raises(ValueError):store.restore_from(backup)
    assert m.method_comparison_report(store,group['id'])['pairs'][0]['baseline_mean']==8
    with store._connection() as conn:
        conn.execute('UPDATE eval_results SET data=? WHERE run_id=?',(json.dumps(bad),group['baseline_run_id']))
    report=m.method_comparison_report(store,group['id'])
    assert report['paired_cases']==0 and report['baseline_mae_percent'] is None


@pytest.mark.parametrize('damage',['drop_table','empty_groups','orphan_run'])
def test_restore_rejects_orphan_comparison_runs_but_old_plain_backups_remain_compatible(tmp_path,damage):
    import json
    module();store=Store(tmp_path/'orphan.db');dataset,cases,preset=setup(store)
    group=store.create_method_comparison(dataset['id'],[cases[0]['id']],1,CONFIG,preset['id'])
    backup=store.backup_to(tmp_path/'orphan-backup.db')
    with sqlite3.connect(backup) as conn:
        if damage=='drop_table':conn.execute('DROP TABLE method_comparisons')
        elif damage=='empty_groups':conn.execute('DELETE FROM method_comparisons')
        else:
            row=conn.execute('SELECT data FROM eval_runs WHERE id=?',(group['baseline_run_id'],)).fetchone()
            value=json.loads(row[0]);value['comparison_id']='mc-'+'0'*32
            conn.execute('UPDATE eval_runs SET data=? WHERE id=?',(json.dumps(value),group['baseline_run_id']))
    with pytest.raises(ValueError):store.restore_from(backup)
    plain=Store(tmp_path/'plain.db');old=plain.backup_to(tmp_path/'old-plain.db')
    with sqlite3.connect(old) as conn:conn.execute('DROP TABLE method_comparisons')
    plain.restore_from(old)
    assert not plain.list_method_comparisons('missing')
