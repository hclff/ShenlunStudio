"""Atomic links between two ordinary frozen runs; optional backwards-compatible table."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import re
import uuid

from .method_comparison import plan_method_comparison,validate_paired_plans
from .evaluation import canonical_hash


FIELDS={'schema','id','set_id','baseline_run_id','method_run_id','preset_snapshot','created_at','plan_hash',
    'baseline_plan_hash','method_plan_hash'}


def validate_comparison_record(value,baseline,method):
    if (not isinstance(value,dict) or set(value)!=FIELDS or value['schema']!='method-comparison-1' or
        not isinstance(value['id'],str) or not re.fullmatch('mc-[a-f0-9]{32}',value['id']) or not baseline or not method or
        baseline['id']!=value['baseline_run_id'] or method['id']!=value['method_run_id'] or baseline['id']==method['id']):
        raise ValueError('方法对照的结构或轮次关联不正确。')
    if any(r.get('comparison_id')!=value['id'] or r.get('comparison_arm')!=arm
           for r,arm in ((baseline,'off'),(method,'preset'))):raise ValueError('方法对照轮次归属不正确。')
    if value['set_id']!=baseline['set_id']:raise ValueError('方法对照的评测集不一致。')
    from .preset_store import validate_method_preset
    validate_method_preset(value['preset_snapshot'])
    try:
        timestamp=datetime.fromisoformat(value['created_at'])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:raise ValueError
    except (TypeError,ValueError,OverflowError):raise ValueError('方法对照时间无效。') from None
    if value['baseline_plan_hash']!=baseline['plan_hash'] or value['method_plan_hash']!=method['plan_hash']:
        raise ValueError('方法对照的冻结计划指纹不一致。')
    expected=canonical_hash({'baseline':baseline['plan_hash'],'method':method['plan_hash'],'preset':value['preset_snapshot']})
    if value['plan_hash']!=expected:raise ValueError('方法对照的内容指纹不一致。')
    validate_paired_plans(baseline,method,value['preset_snapshot'])


def validate_comparison_result(review,case,run):
    context=case['grade_context']
    if (review.get('method_snapshot',[])!=context['method_snapshot'] or review.get('sources',[])!=context['sources'] or
        review.get('config_snapshot')!=run['config'] or review.get('prompt_version')!=run['prompt_version'] or
        review.get('reference_points_snapshot')!=case.get('reference_points_snapshot')):
        raise ValueError('方法对照结果与冻结输入或模型条件不一致。')


class ComparisonStoreMixin:
    def create_method_comparison(self,set_id,case_ids,repeats,config,preset_id,expected_plan_hash=None):
        plan=plan_method_comparison(self,set_id,case_ids,repeats,config,preset_id)
        if expected_plan_hash is not None and plan['plan_hash']!=expected_plan_hash:
            raise ValueError('方法组合或评测内容已变化，请重新估算并确认。')
        identifier='mc-'+uuid.uuid4().hex;created=datetime.now(timezone.utc).isoformat()
        runs=[{**deepcopy(plan[key]),'id':'er-'+uuid.uuid4().hex,'status':'pending','created_at':created,
            'comparison_id':identifier,'comparison_arm':arm} for key,arm in (('baseline','off'),('method','preset'))]
        value={'schema':'method-comparison-1','id':identifier,'set_id':set_id,
            'baseline_run_id':runs[0]['id'],'method_run_id':runs[1]['id'],'preset_snapshot':plan['preset_snapshot'],
            'created_at':created,'plan_hash':plan['plan_hash'],'baseline_plan_hash':runs[0]['plan_hash'],'method_plan_hash':runs[1]['plan_hash']}
        validate_comparison_record(value,*runs)
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            dataset=conn.execute('SELECT data FROM eval_sets WHERE id=?',(set_id,)).fetchone()
            if not dataset or json.loads(dataset[0])['version']!=runs[0]['dataset_version']:raise ValueError('评测集已变化，请重新估算。')
            if conn.execute('SELECT count(*) FROM method_comparisons').fetchone()[0]>=1000:raise ValueError('最多保留1000组方法对照。')
            for run in runs:
                for case in run['cases']:
                    self._preserve_purged_teaching_history(conn,case['grade_context']['payload'].get('teaching_guidance'),case['submission'])
                conn.execute('INSERT INTO eval_runs VALUES (?,?,?)',(run['id'],set_id,json.dumps(run,ensure_ascii=False,allow_nan=False)))
            conn.execute('INSERT INTO method_comparisons VALUES (?,?)',(identifier,json.dumps(value,ensure_ascii=False,allow_nan=False)))
        return value

    def get_method_comparison(self,identifier):
        with self._connection() as conn:row=conn.execute('SELECT data FROM method_comparisons WHERE id=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_method_comparisons(self,set_id):
        with self._connection() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT data FROM method_comparisons ORDER BY rowid DESC') if json.loads(r[0])['set_id']==set_id]

    def _validate_method_comparisons_restore(self,source,tables):
        marked={}
        if 'eval_runs' in tables:
            for identity,payload in source.execute('SELECT id,data FROM eval_runs'):
                record=json.loads(payload)
                if 'comparison_id' in record or 'comparison_arm' in record:marked[identity]=record
        if 'method_comparisons' not in tables:
            if marked:raise ValueError('方法对照备份缺少关联记录，不能恢复孤立轮次。')
            return
        if source.execute('SELECT count(*) FROM method_comparisons').fetchone()[0]>1000:raise ValueError('方法对照数量超过上限。')
        linked=set()
        for identifier,payload in source.execute('SELECT id,data FROM method_comparisons'):
            value=json.loads(payload)
            if value.get('id')!=identifier or not {'eval_runs','eval_results'}<=tables:raise ValueError('方法对照备份关联错误。')
            runs=[]
            for key in ('baseline_run_id','method_run_id'):
                row=source.execute('SELECT data FROM eval_runs WHERE id=?',(value.get(key),)).fetchone()
                runs.append(json.loads(row[0]) if row else None)
            validate_comparison_record(value,*runs)
            for run in runs:
                if run['id'] in linked:raise ValueError('方法对照重复引用同一轮次。')
                linked.add(run['id'])
                cases={c['id']:c for c in run['cases']}
                for row in source.execute('SELECT data FROM eval_results WHERE run_id=?',(run['id'],)):
                    result=json.loads(row[0])
                    if result['status']=='success':validate_comparison_result(result['review'],cases[result['case_id']],run)
        if linked!=set(marked):raise ValueError('方法对照轮次缺少唯一关联记录。')
