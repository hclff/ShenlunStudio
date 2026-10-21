"""Reproducible small evaluation plans; provenance is never inferred from scores."""
from collections import Counter,defaultdict
import copy
import hashlib
import html
import json
import statistics
import random

from .domain import finite_number,prepare_submission
from .model import DEFAULT_CONFIG,CancelledError,ModelError,ModelClient,check_cancel
from .practice import question_fingerprint,score_value,scoring_version

ORIGINS={'real_candidate':'本人/获授权真实作答','public_candidate':'作者公开自答（身份自述）',
         'gpt_control':'GPT受控样本','institution_example':'机构示范','unknown':'来源未确认'}
PARTITIONS={'development':'开发集','holdout':'留出集'}
MAX_CASES=100


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf8')).hexdigest()


def case_submission(value):
    return prepare_submission(value)


def blind_eval_rows(store,set_id):
    rows=[{'case_id':case['id'],'content_hash':case['content_hash'],
             **{k:case['submission'][k] for k in ('question','materials','answer','max_score','question_type','word_limit')},
             'human':None,'human_reason':'','reviewer':'','independent':False}
            for case in store.list_eval_cases(set_id)]
    random.SystemRandom().shuffle(rows)
    return rows


def blind_eval_html(rows):
    esc=lambda value:html.escape(str(value)).replace('\n','<br>')
    parts=['<!doctype html><meta charset="utf-8"><title>独立人工评阅</title>',
           '<style>body{max-width:960px;margin:30px auto;font-family:Microsoft YaHei;line-height:1.8}section{border-bottom:1px solid #ccd;padding:20px}pre{white-space:pre-wrap}</style>',
           '<h1>独立人工评阅</h1><p>不含AI分数与预设排序。请先独立阅读题干、材料和作答，再填写分数、理由及评阅人；零分有效。独立标记是评阅者自声明。</p>']
    for row in rows:
        parts.append('<section><h2>'+esc(row['case_id'])+'</h2><small>内容指纹 '+esc(row['content_hash'])+'</small>')
        for key,label in (('question','题干'),('materials','材料'),('answer','原答')):
            parts.append('<h3>'+label+'</h3><p>'+esc(row[key])+'</p>')
        parts.append('<p>满分 '+esc(row['max_score'])+'；人工分：________　评阅人：________</p><p>理由：________________________</p></section>')
    return ''.join(parts)


def plan_evaluation(store,set_id,case_ids,repeats,config,method_choice=None):
    dataset=store.get_eval_set(set_id)
    if not dataset:raise ValueError('评测集不存在。')
    if type(repeats) is not int or not 1<=repeats<=5:raise ValueError('同答案重复次数须为1—5。')
    if not isinstance(case_ids,list) or not 1<=len(case_ids)<=MAX_CASES or any(not isinstance(identifier,str) for identifier in case_ids) or len(set(case_ids))!=len(case_ids):
        raise ValueError('请选择1—100条不重复的评测作答。')
    config={**DEFAULT_CONFIG,**{k:v for k,v in config.items() if k in DEFAULT_CONFIG}}
    # Constructor validates public config only; placeholder is never sent/read from a vault.
    client=ModelClient(config,'estimate-only-placeholder')
    from .service import AppService,ASSESSMENT_SYSTEM,PROMPT_VERSION
    service=AppService(store,config,'')
    cases=[];total=0;per_case=[]
    for identifier in case_ids:
        case=store.get_eval_case(identifier)
        if not case or case['set_id']!=set_id:raise ValueError('所选作答不属于本评测集。')
        if not case['answer_complete'] or case['submission']['mode']!='full':
            raise ValueError('短摘录或缺题干/材料的条目不能运行完整评分。')
        point_getter=getattr(store,'reference_points_for_submission',lambda value:None)
        snapshot=point_getter(case['submission'])
        # Read-only exact payload construction; same serialization as the actual transport.
        _,payload,sources,methods,_=service.prepare_grade(case['submission'],reference_points_snapshot=snapshot,method_choice=method_choice)
        encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(ASSESSMENT_SYSTEM)+len(encoded)>client.max_input:
            raise ValueError('有作答超过模型输入字符上限，请减少材料或调整设置。')
        estimate=max(1,(len(ASSESSMENT_SYSTEM.encode('utf8'))+len(encoded.encode('utf8')))//2)
        reservation=estimate+client.max_output
        total+=reservation*repeats
        grade_context={'payload':payload,'sources':sources,'method_snapshot':methods,
                       'reference_points_snapshot':snapshot,'prompt_version':PROMPT_VERSION,
                       'system_hash':canonical_hash(ASSESSMENT_SYSTEM)}
        grade_context['method_choice_snapshot']=copy.deepcopy(service._last_method_choice)
        cases.append({**copy.deepcopy(case),'reference_points_snapshot':snapshot,'grade_context':grade_context})
        per_case.append({'case_id':identifier,'estimated_input_tokens':estimate,'estimated_total_tokens':reservation})
    if total>client.budget:raise ValueError(f'本轮估算{total} token，超过任务预算{client.budget}；请减少作答/次数或调整设置。')
    basis={'set_id':set_id,'dataset_version':dataset['version'],'cases':cases,'repeats':repeats,'config':config,'prompt_version':PROMPT_VERSION}
    return {**basis,'calls':len(cases)*repeats,'estimated_total_tokens':total,'per_case':per_case,
            'plan_hash':canonical_hash(basis),'notice':'用量为估算，不是保证价格；小样本结果不代表评分已校准。'}


def run_evaluation(store,service,run_id,progress=None,cancel=None):
    run=store.get_eval_run(run_id)
    if not run or run['status']!='pending':raise ValueError('只可启动尚未执行的评测；重跑请创建新轮次。')
    actual={**DEFAULT_CONFIG,**{k:v for k,v in service.config.items() if k in DEFAULT_CONFIG}}
    if actual!=run['config']:raise ValueError('模型设置已变化，请重新估算并确认评测。')
    from .service import PROMPT_VERSION
    if run.get('prompt_version')!=PROMPT_VERSION:raise ValueError('软件提示版本已变化，请重新创建并确认评测。')
    store.finish_eval_run(run_id,'running')
    completed=0
    try:
        for case in run['cases']:
            for repeat in range(1,run['repeats']+1):
                check_cancel(cancel)
                if progress:progress(f'评测{completed+1}/{run["calls"]}：作答{case["id"][:12]}，第{repeat}次…')
                try:
                    review=service.evaluate_submission(copy.deepcopy(case['submission']),
                        reference_points_snapshot=copy.deepcopy(case['reference_points_snapshot']),progress=progress,cancel=cancel,
                        grade_context=copy.deepcopy(case['grade_context']))
                    check_cancel(cancel)
                    store.save_eval_result(run_id,case['id'],repeat,'success',review=review)
                except CancelledError:
                    store.save_eval_result(run_id,case['id'],repeat,'cancelled',error='任务取消，迟到结果不记成功。')
                    raise
                except ModelError as exc:
                    store.save_eval_result(run_id,case['id'],repeat,'error',error=str(exc))
                    store.finish_eval_run(run_id,'failed');return store.get_eval_run(run_id)
                except ValueError as exc:
                    store.save_eval_result(run_id,case['id'],repeat,'error',error=str(exc))
                completed+=1
        store.finish_eval_run(run_id,'completed')
    except CancelledError:store.finish_eval_run(run_id,'cancelled')
    except Exception:
        store.finish_eval_run(run_id,'failed')
        raise
    return store.get_eval_run(run_id)


def eval_run_summary(store,run_id):
    run=store.get_eval_run(run_id)
    if not run:raise ValueError('评测轮次不存在。')
    results=store.list_eval_results(run_id);cases={case['id']:case for case in run['cases']}
    grouped=defaultdict(list);repeated=defaultdict(list);labels_used=[]
    for result in results:
        if result['status']!='success':continue
        review=result['review'];score=score_value(review);case=cases.get(result['case_id'])
        if case is None or score is None or not 0<=score<=case['submission']['max_score']:continue
        version=scoring_version(review)
        if version:repeated[(case['id'],version)].append(score)
        label=store.get_eval_label(case['id'])
        if (version and case['origin'] in ('real_candidate','public_candidate') and label and label['independent']
                and label['case_hash']==case['content_hash']):
            grouped[(version,case['id'])].append((score-label['score'])/case['submission']['max_score']*100)
            if not any(item['case_id']==case['id'] for item in labels_used):labels_used.append(copy.deepcopy(label))
    # Each labelled answer contributes once, using its mean AI score, not repeats as extra humans.
    cohorts=defaultdict(list)
    for (version,identifier),values in grouped.items():cohorts[version].append(statistics.mean(values))
    metrics_groups=[{'version':version,'human_pairs':len(values),'mae_percent':round(statistics.mean(abs(value) for value in values),6),
                     'bias_percent':round(statistics.mean(values),6)} for version,values in cohorts.items()]
    errors=next(iter(cohorts.values())) if len(cohorts)==1 else []
    return {'run_id':run_id,'status':run['status'],'requested_calls':run['calls'],
            'successes':sum(row['status']=='success' for row in results),
            'errors':sum(row['status']=='error' for row in results),'cancelled':sum(row['status']=='cancelled' for row in results),
            'pending':run['calls']-len(results),'human_pairs':len({identifier for _,identifier in grouped}),
            'mae_percent':round(statistics.mean(abs(value) for value in errors),6) if errors else None,
            'bias_percent':round(statistics.mean(errors),6) if errors else None,
            'origin_counts':dict(Counter(case['origin'] for case in cases.values())),
            'repeat_groups':[{'case_id':identifier,'version':version,'n':len(values),'spread':round(max(values)-min(values),6)}
                             for (identifier,version),values in repeated.items() if len(values)>=2],
            'human_labels':labels_used,'metrics_groups':metrics_groups,
            'notice':'人工独立性为声明；公开作答身份为作者自述。不同评分条件分别统计，小样本不代表官方准确率或评分已校准。'}


def evaluation_report(store,run_id):
    from .reports import _redacted
    return _redacted({'schema':'shenlun-evaluation-report-1','run':store.get_eval_run(run_id),
            'results':store.list_eval_results(run_id),'summary':eval_run_summary(store,run_id)})


def evaluation_report_html(report):
    esc=lambda value:html.escape(str(value))
    summary=report['summary']
    parts=['<!doctype html><meta charset="utf-8"><title>小规模评测报告</title>',
           '<style>body{font-family:Microsoft YaHei;max-width:1000px;margin:30px auto;line-height:1.8}td,th{padding:8px;border:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap}</style>',
           '<h1>小规模评测报告</h1><p>'+esc(summary['notice'])+'</p>',
           '<p>成功 '+str(summary['successes'])+' / '+str(summary['requested_calls'])+' 次；独立人工对照作答 '+str(summary['human_pairs'])+' 份。</p>',
           '<p>百分制MAE：'+esc(summary['mae_percent'] if summary['mae_percent'] is not None else '样本不足')+'；平均偏差：'+esc(summary['bias_percent'] if summary['bias_percent'] is not None else '样本不足')+'</p>',
           '<pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><h2>逐次结果</h2><table><tr><th>作答</th><th>次数</th><th>状态</th><th>分数/错误</th></tr>']
    for row in report['results']:
        parts.append('<tr>'+''.join('<td>'+esc(value)+'</td>' for value in (row['case_id'],row['repeat'],row['status'],score_value(row.get('review',{})) if row['status']=='success' else row.get('error','')))+'</tr>')
    return ''.join(parts)+'</table>'
