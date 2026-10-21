"""Controlled paired evaluation of identical answers with methods off or preset."""
from copy import deepcopy
import html
import json
import statistics

from .evaluation import canonical_hash,plan_evaluation,run_evaluation
from .practice import score_value,scoring_conditions
from .model import check_cancel,CancelledError


def _common_context(case):
    context=deepcopy(case['grade_context']);payload=context['payload']
    payload.pop('method_guidance',None);payload.pop('teaching_guidance',None)
    payload.get('output_schema',{}).pop('teaching_analysis',None)
    context.pop('method_snapshot',None);context.pop('method_choice_snapshot',None)
    return {key:case.get(key) for key in ('id','content_hash','submission','origin','source','answer_complete',
        'source_metadata','reference_points_snapshot')}|{'context':context}


def validate_paired_plans(baseline,method,preset):
    if (baseline['set_id']!=method['set_id'] or baseline['dataset_version']!=method['dataset_version'] or
        baseline['config']!=method['config'] or baseline['prompt_version']!=method['prompt_version'] or
        baseline['repeats']!=method['repeats'] or len(baseline['cases'])!=len(method['cases'])):
        raise ValueError('对照的模型设置、数据版本或重复次数不一致。')
    for a,b in zip(baseline['cases'],method['cases']):
        if a['submission']['question_type']!=preset['question_type']:raise ValueError('对照作答题型与组合不一致。')
        if _common_context(a)!=_common_context(b):raise ValueError('对照的答卷、材料或参考条件已变化，请重新估算。')
        ca=a['grade_context'];cb=b['grade_context']
        if (ca['method_snapshot'] or ca['payload'].get('method_guidance') or ca['payload'].get('teaching_guidance')
            or ca['payload'].get('output_schema',{}).get('teaching_analysis')):
            raise ValueError('关闭方法组仍包含写作或教学方法。')
        articles=cb['method_snapshot'];teaching=cb['payload'].get('teaching_guidance')
        if ([r['id'] for r in articles]!=preset['article_ids'] or
            [r['id'] for r in (teaching or {}).get('methods',[])]!=preset['teaching_ids']):
            raise ValueError('方法组实际使用的方法与指定组合不一致。')
        if any(r.get('enabled') is not True or preset['question_type'] not in r.get('applicable_types',[]) for r in articles):
            raise ValueError('对照中有未启用或题型不符的文章方法。')
        expected=[{key:c.get(key) for key in ('name','purpose','steps','not_applicable','applicable_types')} for c in articles]
        if cb['payload'].get('method_guidance')!=expected:raise ValueError('文章方法快照与实际模型输入不一致。')


def plan_method_comparison(store,set_id,case_ids,repeats,config,preset_id):
    preset=store.get_method_preset(preset_id)
    if preset is None:raise ValueError('请先创建并选择一个方法组合。')
    baseline=plan_evaluation(store,set_id,case_ids,repeats,config,method_choice={'mode':'off'})
    method=plan_evaluation(store,set_id,case_ids,repeats,config,method_choice={'mode':'preset','preset_id':preset_id})
    if store.get_method_preset(preset_id)!=preset:raise ValueError('方法组合已变化，请重新估算。')
    validate_paired_plans(baseline,method,preset)
    total=baseline['estimated_total_tokens']+method['estimated_total_tokens']
    if total>baseline['config']['session_token_budget']:
        raise ValueError(f'两组合计估算 {total} token，超过任务预算；请减少作答或重复次数。')
    return {'schema':'method-comparison-plan-1','baseline':baseline,'method':method,'preset_snapshot':deepcopy(preset),
        'calls':baseline['calls']+method['calls'],'estimated_total_tokens':total,
        'plan_hash':canonical_hash({'baseline':baseline['plan_hash'],'method':method['plan_hash'],'preset':preset})}


def run_method_comparison(store,service,identifier,progress=None,cancel=None):
    group=store.get_method_comparison(identifier)
    if not group:raise ValueError('方法对照不存在。')
    ids=[group['baseline_run_id'],group['method_run_id']]
    if any(store.get_eval_run(i)['status']!='pending' for i in ids):raise ValueError('该对照已执行或取消，请新建对照。')
    try:
        for index,run_id in enumerate(ids):
            check_cancel(cancel)
            if progress:progress(('关闭方法组' if index==0 else '指定组合组')+'：开始冻结评测…')
            run=run_evaluation(store,service,run_id,progress=progress,cancel=cancel)
            if run['status'] in ('cancelled','failed'):break
    except CancelledError:pass
    finally:
        # Never leave an unstarted second arm looking runnable after the job ends.
        for run_id in ids:
            if store.get_eval_run(run_id)['status']=='pending':store.finish_eval_run(run_id,'cancelled')
    return store.get_method_comparison(identifier)


def _score_conditions(review):
    result=scoring_conditions(review)
    result.pop('methods',None);result.pop('teaching_methods',None)
    return result


def method_comparison_report(store,identifier):
    group=store.get_method_comparison(identifier)
    if not group:raise ValueError('方法对照不存在。')
    baseline=store.get_eval_run(group['baseline_run_id']);method=store.get_eval_run(group['method_run_id'])
    from .comparison_store import validate_comparison_record
    validate_comparison_record(group,baseline,method)
    all_results=[store.list_eval_results(baseline['id']),store.list_eval_results(method['id'])]
    pairs=[];unmatched=[];labels=[];base_errors=[];method_errors=[]
    for case in baseline['cases']:
        arms=[[r['review'] for r in results if r['case_id']==case['id'] and r['status']=='success'
               and score_value(r['review']) is not None and 0<=score_value(r['review'])<=case['submission']['max_score']]
               for results in all_results]
        if not all(arms):unmatched.append({'case_id':case['id'],'reason':'一组尚无成功结果。'});continue
        if len(arms[0])!=baseline['repeats'] or len(arms[1])!=method['repeats']:
            unmatched.append({'case_id':case['id'],'reason':'两组重复评分尚未全部成功，不作配对统计。'});continue
        conditions=[_score_conditions(r) for arm in arms for r in arm]
        if any(c!=conditions[0] for c in conditions[1:]):
            unmatched.append({'case_id':case['id'],'reason':'实际模型或其他评分条件不一致。'});continue
        scores=[[score_value(r) for r in arm] for arm in arms]
        a,b=map(statistics.mean,scores)
        row={'case_id':case['id'],'origin':case['origin'],'max_score':case['submission']['max_score'],
            'baseline_n':len(scores[0]),'method_n':len(scores[1]),'baseline_mean':a,'method_mean':b,'score_change':b-a,
            'baseline_spread':max(scores[0])-min(scores[0]) if len(scores[0])>=2 else None,
            'method_spread':max(scores[1])-min(scores[1]) if len(scores[1])>=2 else None,'human_score':None}
        label=store.get_eval_label(case['id'])
        if (case['origin'] in ('real_candidate','public_candidate') and label and label['independent']
            and label['case_hash']==case['content_hash']):
            row['human_score']=label['score'];labels.append(deepcopy(label));maximum=case['submission']['max_score']
            base_errors.append(abs(a-label['score'])/maximum*100);method_errors.append(abs(b-label['score'])/maximum*100)
        pairs.append(row)
    base_mae=statistics.mean(base_errors) if base_errors else None
    method_mae=statistics.mean(method_errors) if method_errors else None
    return {'schema':'method-comparison-report-1','comparison':group,
        'baseline_run':baseline,'method_run':method,'results':{'baseline':all_results[0],'method':all_results[1]},
        'paired_cases':len(pairs),'unmatched_cases':len(unmatched),'pairs':pairs,'unmatched':unmatched,
        'human_pairs':len(labels),'human_labels':labels,'baseline_mae_percent':base_mae,'method_mae_percent':method_mae,
        'mae_change_percent':method_mae-base_mae if labels else None,
        'notice':'分数提高不等于建议更准确。仅对完整、条件匹配且有独立人工分数的真实/公开作者作答计算误差；'
            '人工独立性与作答身份为声明。无人工对照时不计算准确性，小样本不代表评分已校准。'}


def method_comparison_text(report):
    group=report['comparison'];preset=group['preset_snapshot']
    lines=[f'方法对照：关闭方法 / {preset["name"]}（组合 v{preset["version"]}）',
        f'关闭组状态：{report["baseline_run"]["status"]}；组合组状态：{report["method_run"]["status"]}',
        f'可配对作答 {report["paired_cases"]} 份；未配对 {report["unmatched_cases"]} 份；独立人工对照 {report["human_pairs"]} 份。']
    if report['human_pairs']:
        lines.append(f'百分制 MAE：关闭方法 {report["baseline_mae_percent"]:g}；指定组合 {report["method_mae_percent"]:g}；变化 {report["mae_change_percent"]:+g}（负值表示误差减小）。')
    else:lines.append('人工对照样本不足：不计算 MAE 或准确率。')
    lines+=[report['notice'],'','实际使用方法：教学 '+('、'.join(preset['teaching_ids']) or '无')+'；文章 '+('、'.join(preset['article_ids']) or '无'),'']
    for row in report['pairs']:
        a=f'{row["baseline_spread"]:g}' if row['baseline_spread'] is not None else '重复样本不足'
        b=f'{row["method_spread"]:g}' if row['method_spread'] is not None else '重复样本不足'
        lines.append(f'{row["case_id"]}：关闭组均分 {row["baseline_mean"]:g} / {row["max_score"]}（{row["baseline_n"]} 次，分差 {a}）；'
            f'组合组均分 {row["method_mean"]:g}（{row["method_n"]} 次，分差 {b}）；变化 {row["score_change"]:+g}。')
    for row in report['unmatched']:lines.append(row['case_id']+'：未配对，'+row['reason'])
    return '\n'.join(lines)


def method_comparison_html(report):
    return '<!doctype html><meta charset="utf-8"><title>方法对照评测</title><h1>方法对照评测</h1><pre style="white-space:pre-wrap">'+html.escape(method_comparison_text(report))+'</pre>'
