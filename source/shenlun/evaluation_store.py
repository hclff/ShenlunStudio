"""Separate, bounded evaluation collections, append-only labels and frozen runs."""
import copy
import json
import sqlite3
import uuid
from datetime import datetime,timezone

from .domain import finite_number
from .evaluation import MAX_CASES,ORIGINS,PARTITIONS,canonical_hash,case_submission
from .practice import question_fingerprint
from .comparison_store import ComparisonStoreMixin

def dump(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
def now():return datetime.now(timezone.utc).isoformat()
def bounded(value,name,maximum,empty=True):
    if not isinstance(value,str) or len(value)>maximum or (not empty and not value.strip()):raise ValueError(name+'缺失或过长。')
    return value.strip()


def validate_result_teaching(review,case):
    """A saved result must describe the exact methods sent in its frozen call."""
    frozen=case.get('grade_context',{}).get('payload',{}).get('teaching_guidance')
    if review.get('teaching_snapshot')!=frozen:
        raise ValueError('评测结果中的教学方法与冻结输入不一致。')
    if frozen is not None:
        from .teaching import validate_teaching_snapshot,validate_teaching_output
        snapshot=validate_teaching_snapshot(frozen,case['submission'])
        validate_teaching_output(review.get('assessment',{}).get('teaching_analysis'),snapshot,case['submission'])


def validate_teaching_history(source,tables,snapshot,submission):
    from .teaching import validate_teaching_snapshot
    validate_teaching_snapshot(snapshot,submission)
    if 'teaching_method_versions' not in tables:raise ValueError('冻结教学方法缺少本地版本记录。')
    for method in snapshot['methods']:
        row=source.execute('SELECT data FROM teaching_method_versions WHERE id=? AND version=?',(method['id'],method['version'])).fetchone()
        if not row or json.loads(row[0])!=method:raise ValueError('冻结教学方法与本地版本记录不一致。')


class EvaluationStoreMixin(ComparisonStoreMixin):
    def _init_evaluation(self,conn):
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS eval_sets(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS eval_cases(id TEXT PRIMARY KEY,set_id TEXT NOT NULL,question_fingerprint TEXT NOT NULL,content_hash TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS eval_case_sets ON eval_cases(set_id);
            CREATE UNIQUE INDEX IF NOT EXISTS eval_case_identity ON eval_cases(set_id,content_hash);
            CREATE TABLE IF NOT EXISTS eval_labels(case_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(case_id,version));
            CREATE TABLE IF NOT EXISTS eval_runs(id TEXT PRIMARY KEY,set_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS eval_results(run_id TEXT NOT NULL,case_id TEXT NOT NULL,repeat INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(run_id,case_id,repeat));
            CREATE TABLE IF NOT EXISTS method_comparisons(id TEXT PRIMARY KEY,data TEXT NOT NULL);
        ''')

    def create_eval_set(self,name,description=''):
        value={'id':'es-'+uuid.uuid4().hex,'name':bounded(name,'评测集名称',100,False),
               'description':bounded(description,'评测集说明',5000),'version':1,'created_at':now()}
        with self._connection() as conn:conn.execute('INSERT INTO eval_sets VALUES(?,?)',(value['id'],dump(value)))
        return value

    def get_eval_set(self,identifier):
        with self._connection() as conn:row=conn.execute('SELECT data FROM eval_sets WHERE id=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_eval_sets(self):
        with self._connection() as conn:
            return [{**json.loads(row[0]),'case_count':row[1]} for row in conn.execute('SELECT s.data,(SELECT count(*) FROM eval_cases c WHERE c.set_id=s.id) FROM eval_sets s ORDER BY s.rowid DESC LIMIT 1000')]

    def _add_eval_case(self,conn,set_id,submission,origin,source,partition,answer_complete,source_metadata):
        row=conn.execute('SELECT data FROM eval_sets WHERE id=?',(set_id,)).fetchone()
        if not row:raise ValueError('评测集不存在。')
        if not isinstance(origin,str) or not isinstance(partition,str) or origin not in ORIGINS or partition not in PARTITIONS:raise ValueError('作答来源类型或分组不受支持。')
        if type(answer_complete) is not bool:raise ValueError('作答完整标记须为布尔值。')
        if conn.execute('SELECT count(*) FROM eval_cases WHERE set_id=?',(set_id,)).fetchone()[0]>=MAX_CASES:raise ValueError('每个小规模评测集最多100份作答。')
        prepared=case_submission(submission);fingerprint=question_fingerprint(prepared);content_hash=canonical_hash(prepared)
        for other in conn.execute('SELECT data FROM eval_cases WHERE set_id=? AND question_fingerprint=?',(set_id,fingerprint)):
            if json.loads(other[0])['partition']!=partition:raise ValueError('同一题目的所有作答须在同一开发/留出组，避免泄漏。')
        if conn.execute('SELECT 1 FROM eval_cases WHERE set_id=? AND content_hash=?',(set_id,content_hash)).fetchone():raise ValueError('该评测集已有相同题目和作答。')
        source=bounded(source,'作答来源',3000)
        metadata=source_metadata or {}
        if not isinstance(metadata,dict) or len(dump(metadata))>12000:raise ValueError('来源附注须为不超过12000字符的对象。')
        # Only descriptive provenance, never arbitrary credentials/provider records.
        allowed=('url','title','published_at','author_claim','rights_status','license_url','material_status','source_kind','note')
        metadata={key:bounded(value,'来源附注',3000) for key,value in metadata.items() if key in allowed}
        value={'id':'ec-'+uuid.uuid4().hex,'set_id':set_id,'submission':prepared,'origin':origin,'source':source,
               'partition':partition,'answer_complete':answer_complete,'source_metadata':metadata,
               'content_hash':content_hash,'question_fingerprint':fingerprint,'created_at':now()}
        conn.execute('INSERT INTO eval_cases VALUES(?,?,?,?,?)',(value['id'],set_id,fingerprint,content_hash,dump(value)))
        dataset=json.loads(row[0]);dataset['version']+=1
        conn.execute('UPDATE eval_sets SET data=? WHERE id=?',(dump(dataset),set_id))
        return value

    def add_eval_case(self,set_id,submission,origin='unknown',source='',partition='development',answer_complete=True,source_metadata=None):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return self._add_eval_case(conn,set_id,submission,origin,source,partition,answer_complete,source_metadata)

    def import_eval_cases(self,set_id,records):
        if not isinstance(records,list) or not 1<=len(records)<=MAX_CASES:raise ValueError('请导入1—100份作答。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE');values=[]
            for row in records:
                if not isinstance(row,dict):raise ValueError('评测作答须为对象。')
                values.append(self._add_eval_case(conn,set_id,row.get('submission',{}),row.get('origin','unknown'),
                    row.get('source',''),row.get('partition','development'),row.get('answer_complete',False),row.get('source_metadata')))
        return values

    def get_eval_case(self,identifier):
        with self._connection() as conn:row=conn.execute('SELECT data FROM eval_cases WHERE id=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_eval_cases(self,set_id):
        with self._connection() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM eval_cases WHERE set_id=? ORDER BY rowid',(set_id,))]

    def set_eval_partition(self,set_id,fingerprint,partition):
        if not isinstance(partition,str) or partition not in PARTITIONS:raise ValueError('评测分组无效。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows=conn.execute('SELECT id,data FROM eval_cases WHERE set_id=? AND question_fingerprint=?',(set_id,fingerprint)).fetchall()
            if not rows:raise ValueError('评测题目不存在。')
            for identifier,payload in rows:
                case=json.loads(payload);case['partition']=partition
                conn.execute('UPDATE eval_cases SET data=? WHERE id=?',(dump(case),identifier))
            dataset=json.loads(conn.execute('SELECT data FROM eval_sets WHERE id=?',(set_id,)).fetchone()[0]);dataset['version']+=1
            conn.execute('UPDATE eval_sets SET data=? WHERE id=?',(dump(dataset),set_id))

    def _save_eval_label(self,conn,case_id,score,reason,reviewer,independent,expected_version):
        row=conn.execute('SELECT data FROM eval_cases WHERE id=?',(case_id,)).fetchone()
        if not row:raise ValueError('评测作答不存在。')
        case=json.loads(row[0]);score=finite_number(score,'人工分数',0,case['submission']['max_score'])
        if type(independent) is not bool:raise ValueError('独立评阅标记须为布尔值。')
        version=conn.execute('SELECT coalesce(max(version),0) FROM eval_labels WHERE case_id=?',(case_id,)).fetchone()[0]
        if expected_version is not None and (type(expected_version) is not int or expected_version!=version):raise ValueError('人工评分版本已变化，请重新读取后保存；当前输入保留。')
        value={'case_id':case_id,'version':version+1,'case_hash':case['content_hash'],'score':score,
               'reason':bounded(reason,'人工评阅说明',10000),'reviewer':bounded(reviewer,'评阅人',100,False),
               'independent':independent,'created_at':now()}
        conn.execute('INSERT INTO eval_labels VALUES(?,?,?)',(case_id,version+1,dump(value)))
        return value

    def save_eval_label(self,case_id,score,reason='',reviewer='',independent=False,expected_version=None):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return self._save_eval_label(conn,case_id,score,reason,reviewer,independent,expected_version)

    def get_eval_label(self,case_id):
        with self._connection() as conn:row=conn.execute('SELECT data FROM eval_labels WHERE case_id=? ORDER BY version DESC LIMIT 1',(case_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_eval_labels(self,case_id):
        with self._connection() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM eval_labels WHERE case_id=? ORDER BY version DESC',(case_id,))]

    def import_eval_labels(self,set_id,rows):
        if not isinstance(rows,list) or len(rows)>MAX_CASES:raise ValueError('人工评分最多100条。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE');count=0;seen=set()
            for row in rows:
                if not isinstance(row,dict):raise ValueError('人工评分须为对象。')
                identifier=row.get('case_id')
                if not isinstance(identifier,str):raise ValueError('人工评分作答标识须为文字。')
                if identifier in seen:raise ValueError('导入表存在重复作答。')
                seen.add(identifier)
                payload=conn.execute('SELECT data FROM eval_cases WHERE id=? AND set_id=?',(identifier,set_id)).fetchone()
                if not payload:raise ValueError('人工评分引用了未知作答。')
                case=json.loads(payload[0])
                if row.get('content_hash')!=case['content_hash'] or row.get('max_score')!=case['submission']['max_score']:
                    raise ValueError('人工评分内容指纹或满分已变化。')
                if any(key in row and row[key]!=case['submission'][key] for key in ('question','materials','answer','question_type','word_limit')):
                    raise ValueError('评阅表中的题干、材料或原答已改动，不能绑定旧作答。')
                if row.get('human') is None:continue
                self._save_eval_label(conn,identifier,row['human'],row.get('human_reason',''),row.get('reviewer',''),row.get('independent',False),None);count+=1
        return {'imported':count,'skipped':len(rows)-count}

    def create_eval_run(self,set_id,case_ids,repeats,config,expected_plan_hash=None,method_choice=None):
        from .evaluation import plan_evaluation
        plan=plan_evaluation(self,set_id,case_ids,repeats,config,method_choice=method_choice)
        if expected_plan_hash is not None and plan['plan_hash']!=expected_plan_hash:raise ValueError('评测内容或参考要点已变化，请重新估算并确认。')
        value={**plan,'id':'er-'+uuid.uuid4().hex,'status':'pending','created_at':now()}
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=json.loads(conn.execute('SELECT data FROM eval_sets WHERE id=?',(set_id,)).fetchone()[0])
            if current['version']!=plan['dataset_version']:raise ValueError('评测集已变化，请重新估算。')
            for case in value['cases']:
                self._preserve_purged_teaching_history(conn,case['grade_context']['payload'].get('teaching_guidance'),case['submission'])
            conn.execute('INSERT INTO eval_runs VALUES(?,?,?)',(value['id'],set_id,dump(value)))
        return value

    def get_eval_run(self,identifier):
        with self._connection() as conn:row=conn.execute('SELECT data FROM eval_runs WHERE id=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_eval_runs(self,set_id):
        with self._connection() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM eval_runs WHERE set_id=? ORDER BY rowid DESC LIMIT 500',(set_id,))]

    def finish_eval_run(self,run_id,status):
        if status not in ('running','completed','failed','cancelled'):raise ValueError('评测状态无效。')
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT data FROM eval_runs WHERE id=?',(run_id,)).fetchone()
            if not row:raise ValueError('评测轮次不存在。')
            value=json.loads(row[0])
            if value['status'] in ('completed','failed','cancelled'):raise ValueError('已结束的评测轮次不能重新开始或改写状态。')
            if status=='running' and value['status']!='pending':raise ValueError('该评测已经在执行，不能重复启动。')
            if status=='completed' and conn.execute('SELECT count(*) FROM eval_results WHERE run_id=?',(run_id,)).fetchone()[0]!=value['calls']:
                raise ValueError('本轮尚有未执行单元，不能记为全部完成。')
            value['status']=status;value['updated_at']=now()
            conn.execute('UPDATE eval_runs SET data=? WHERE id=?',(dump(value),run_id))

    def save_eval_result(self,run_id,case_id,repeat,status,review=None,error=''):
        run=self.get_eval_run(run_id)
        if not run or case_id not in {case['id'] for case in run['cases']}:raise ValueError('评测结果引用了未知作答。')
        if type(repeat) is not int or not 1<=repeat<=run['repeats'] or status not in ('success','error','cancelled'):raise ValueError('评测结果次数或状态无效。')
        if status=='success' and not isinstance(review,dict):raise ValueError('成功结果必须包含评价。')
        case=next(case for case in run['cases'] if case['id']==case_id)
        if status=='success':
            from .practice import score_value
            score=score_value(review)
            if canonical_hash(review.get('submission'))!=case['content_hash'] or score is None or not 0<=score<=case['submission']['max_score']:
                raise ValueError('成功评测的原答或分数与冻结作答不一致。')
            validate_result_teaching(review,case)
            if run.get('comparison_id'):
                from .comparison_store import validate_comparison_result
                validate_comparison_result(review,case,run)
        # Shared service constructs this record without credentials. Defence in depth for imported/test clients.
        from .reports import _redacted
        value={'run_id':run_id,'case_id':case_id,'repeat':repeat,'status':status,
               'review':_redacted(copy.deepcopy(review)) if review is not None else None,
               'error':bounded(error,'评测错误',2000),'created_at':now()}
        if len(dump(value))>2*1024*1024:raise ValueError('单次评测记录超过2MB。')
        try:
            with self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current=json.loads(conn.execute('SELECT data FROM eval_runs WHERE id=?',(run_id,)).fetchone()[0])
                if current['status'] not in ('pending','running'):raise ValueError('已结束的评测不能继续写入结果。')
                if status=='success':
                    self._preserve_purged_teaching_history(conn,value['review'].get('teaching_snapshot'),case['submission'])
                conn.execute('INSERT INTO eval_results VALUES(?,?,?,?)',(run_id,case_id,repeat,dump(value)))
        except sqlite3.IntegrityError:raise ValueError('该次评测结果已经保存，不能覆盖历史。') from None
        return value

    def list_eval_results(self,run_id):
        with self._connection() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM eval_results WHERE run_id=? ORDER BY rowid',(run_id,))]

    def _validate_evaluation_restore(self,source,tables):
        names={'eval_sets','eval_cases','eval_labels','eval_runs','eval_results'}
        if not names&tables:return
        if not names<=tables:raise ValueError('评测备份表不完整。')
        sets={}
        for identifier,payload in source.execute('SELECT id,data FROM eval_sets'):
            value=json.loads(payload)
            if value.get('id')!=identifier or type(value.get('version')) is not int or value['version']<1:raise ValueError('评测集备份标识或版本错误。')
            bounded(value.get('name'),'评测集名称',100,False);bounded(value.get('description'),'评测集说明',5000)
            sets[identifier]=value
        cases={};groups={};counts={}
        for identifier,set_id,fingerprint,digest,payload in source.execute('SELECT * FROM eval_cases'):
            value=json.loads(payload);prepared=case_submission(value.get('submission'))
            if (value.get('id'),value.get('set_id'),value.get('question_fingerprint'),value.get('content_hash'))!=(identifier,set_id,fingerprint,digest):raise ValueError('评测作答备份索引不一致。')
            if set_id not in sets or digest!=canonical_hash(prepared) or fingerprint!=question_fingerprint(prepared):raise ValueError('评测作答备份内容指纹错误。')
            if not isinstance(value.get('origin'),str) or not isinstance(value.get('partition'),str) or value.get('origin') not in ORIGINS or value.get('partition') not in PARTITIONS or type(value.get('answer_complete')) is not bool:raise ValueError('评测作答来源或分组错误。')
            bounded(value.get('source'),'作答来源',3000)
            group=(set_id,fingerprint)
            if group in groups and groups[group]!=value['partition']:raise ValueError('评测备份存在同题跨开发/留出组。')
            groups[group]=value['partition'];counts[set_id]=counts.get(set_id,0)+1
            if counts[set_id]>MAX_CASES:raise ValueError('评测备份每集超过100作答。')
            cases[identifier]=value
        for identifier,version,payload in source.execute('SELECT * FROM eval_labels'):
            value=json.loads(payload);case=cases.get(identifier)
            if not case or value.get('case_id')!=identifier or value.get('version')!=version or type(version) is not int or version<1 or value.get('case_hash')!=case['content_hash']:raise ValueError('人工评测标签备份关联错误。')
            finite_number(value.get('score'),'人工分数',0,case['submission']['max_score'])
            bounded(value.get('reviewer'),'评阅人',100,False);bounded(value.get('reason'),'评阅说明',10000)
            if type(value.get('independent')) is not bool:raise ValueError('人工独立标记错误。')
        runs={}
        from .reference_points import validate_reference_snapshot
        from .model import DEFAULT_CONFIG,ModelClient
        for identifier,set_id,payload in source.execute('SELECT * FROM eval_runs'):
            value=json.loads(payload)
            if value.get('id')!=identifier or value.get('set_id')!=set_id or set_id not in sets:raise ValueError('评测轮次备份关联错误。')
            if type(value.get('repeats')) is not int or not 1<=value['repeats']<=5 or value.get('status') not in ('pending','running','completed','failed','cancelled'):raise ValueError('评测轮次备份状态错误。')
            frozen=value.get('cases')
            if not isinstance(frozen,list) or not 1<=len(frozen)<=MAX_CASES or any(not isinstance(c,dict) or not isinstance(c.get('id'),str) for c in frozen) or len({c.get('id') for c in frozen})!=len(frozen) or value.get('calls')!=len(frozen)*value['repeats']:raise ValueError('评测轮次备份作答列表错误。')
            basis={key:value.get(key) for key in ('set_id','dataset_version','cases','repeats','config','prompt_version')}
            if canonical_hash(basis)!=value.get('plan_hash'):raise ValueError('评测轮次冻结计划指纹错误。')
            config=value.get('config')
            if not isinstance(config,dict) or set(config)!=set(DEFAULT_CONFIG):raise ValueError('评测轮次配置含未知字段或密钥。')
            ModelClient(config,'restore-validation-placeholder')
            for case in frozen:
                original=cases.get(case.get('id'))
                if not original or original['set_id']!=set_id or case.get('content_hash')!=original['content_hash'] or canonical_hash(case.get('submission'))!=original['content_hash']:raise ValueError('评测轮次冻结作答错误。')
                immutable=('id','set_id','submission','origin','source','answer_complete','source_metadata','content_hash','question_fingerprint','created_at')
                if any(case.get(key)!=original.get(key) for key in immutable):raise ValueError('评测轮次冻结来源与原始作答不一致。')
                snapshot=case.get('reference_points_snapshot')
                if snapshot is not None:validate_reference_snapshot(snapshot,case['submission'])
                context=case.get('grade_context')
                if not isinstance(context,dict) or context.get('payload',{}).get('submission')!=case['submission'] or context.get('reference_points_snapshot')!=snapshot:raise ValueError('评测轮次冻结上下文错误。')
                teaching=context['payload'].get('teaching_guidance')
                if teaching is not None:
                    validate_teaching_history(source,tables,teaching,case['submission'])
            runs[identifier]=value
        for run_id,case_id,repeat,payload in source.execute('SELECT * FROM eval_results'):
            value=json.loads(payload);run=runs.get(run_id)
            if not run or (value.get('run_id'),value.get('case_id'),value.get('repeat'))!=(run_id,case_id,repeat) or type(repeat) is not int or not 1<=repeat<=run['repeats']:raise ValueError('评测结果备份关联错误。')
            frozen=next((case for case in run['cases'] if case['id']==case_id),None)
            if not frozen or value.get('status') not in ('success','error','cancelled'):raise ValueError('评测结果备份状态错误。')
            if value['status']=='success':
                review=value.get('review')
                if not isinstance(review,dict) or canonical_hash(review.get('submission'))!=frozen['content_hash']:raise ValueError('评测成功结果与冻结作答不一致。')
                from .practice import score_value
                score=score_value(review)
                if score is None or not 0<=score<=frozen['submission']['max_score']:
                    raise ValueError('评测成功结果的分数缺失或超出本题满分。')
                validate_result_teaching(review,frozen)
