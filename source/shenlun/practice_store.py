"""Additive practice persistence; every operation uses Store's own connection."""
import copy
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime,timezone

from .domain import finite_number
from .practice import answer_identity,mapping,normalize_question,question_fingerprint,score_value,scoring_conditions,scoring_version,text


def dump(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
def now():return datetime.now(timezone.utc).isoformat()


def meaningful(snapshot):
    return any(text(snapshot.get('submission',{}).get(key)) for key in ('question','materials','answer','reference','rubric_text'))


def human_basis(record):
    condition=scoring_conditions(record)
    return dump({'answer_id':answer_identity(record),'max_score':condition['max_score'],
                 'conditions':condition,'ai_score':score_value(record)})


from .review_plans import ReviewPlanStoreMixin, normalize_review_plan, plan_projection


class PracticeStoreMixin(ReviewPlanStoreMixin):
    def _init_practice(self,conn):
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS question_bank(
                id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,title TEXT NOT NULL,
                search_text TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,data TEXT NOT NULL,
                question_type TEXT NOT NULL DEFAULT '',needs_retry INTEGER NOT NULL DEFAULT 0,
                plan_status TEXT NOT NULL DEFAULT '待复习',plan_due TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS review_questions(review_id TEXT PRIMARY KEY,question_id TEXT);
            CREATE INDEX IF NOT EXISTS question_attempts ON review_questions(question_id);
            CREATE TABLE IF NOT EXISTS practice_workspace(id TEXT PRIMARY KEY,updated_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS question_drafts(question_id TEXT PRIMARY KEY,updated_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workspace_history(id TEXT PRIMARY KEY,updated_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS human_scores(review_id TEXT PRIMARY KEY,data TEXT NOT NULL);
        ''')
        if not conn.execute("SELECT 1 FROM practice_workspace WHERE id='current'").fetchone():
            legacy=conn.execute("SELECT data FROM settings WHERE key='draft'").fetchone()
            if legacy:
                try:
                    value=json.loads(legacy[0])
                    if isinstance(value,dict):
                        value=copy.deepcopy(value)
                        if value.get('rubric') is not None:value['rubric_text']=json.dumps(value.pop('rubric'),ensure_ascii=False,indent=2)
                        self._save_workspace(conn,{'submission':value,'question_id':None},False)
                except (ValueError,TypeError,OverflowError):pass

    def _link_legacy_reviews(self):
        """Backfill review→question links once tables and projection columns exist."""
        with self._connection() as conn:
            rows=conn.execute('SELECT r.data FROM reviews r LEFT JOIN review_questions q ON r.id=q.review_id WHERE q.review_id IS NULL').fetchall()
            for row in rows:
                try:self._link_practice_review(conn,json.loads(row[0]))
                except (ValueError,TypeError,OverflowError,KeyError):continue

    def _save_question(self,conn,submission,title='',tags=None,source='',needs_retry=None):
        if not conn.in_transaction: conn.execute('BEGIN IMMEDIATE')
        fields=normalize_question(submission)
        fingerprint=question_fingerprint(fields)
        old=conn.execute('SELECT data FROM question_bank WHERE fingerprint=?',(fingerprint,)).fetchone()
        value=json.loads(old[0]) if old else {'id':'q-'+uuid.uuid4().hex,'created_at':now(),'tags':[],'source':'','needs_retry':False}
        if not isinstance(title,str) or not isinstance(source,str):raise ValueError('题目名称和来源须为文字。')
        if len(title)>200 or len(source)>2000:raise ValueError('题目名称或来源过长。')
        if tags is not None:
            if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(tag,str) or len(tag)>40 for tag in tags):raise ValueError('最多20个标签，每个不超过40字。')
            value['tags']=list(dict.fromkeys(text(tag) for tag in tags if text(tag)))
        if needs_retry is not None:
            if type(needs_retry) is not bool:raise ValueError('重练标记须为布尔值。')
            value['needs_retry']=needs_retry
            if 'review_plan' in value:
                plan = normalize_review_plan(value['review_plan'])
                status = ('待复习' if plan['status']=='已解决' else plan['status']) if needs_retry else '已解决'
                if status != plan['status']:
                    plan.update(status=status,version=plan['version']+1)
                    value['review_plan']=plan
        value.update(submission=fields,updated_at=now())
        value['title']=text(title) or value.get('title') or (fields['question'] or fields['materials'])[:50]
        if source:value['source']=text(source)
        searchable='\n'.join([value['title'],*fields.values()] if all(isinstance(v,str) for v in fields.values()) else
                              [value['title'],*[fields.get(k,'') for k in ('question','materials','exam','year','category')]])
        searchable+='\n'+' '.join(value['tags'])+'\n'+value['source']
        plan_status,plan_due=plan_projection(value)
        conn.execute('''INSERT INTO question_bank(id,fingerprint,title,search_text,created_at,
                        updated_at,data,question_type,needs_retry,plan_status,plan_due)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                        fingerprint=excluded.fingerprint,title=excluded.title,search_text=excluded.search_text,
                        updated_at=excluded.updated_at,data=excluded.data,
                        question_type=excluded.question_type,needs_retry=excluded.needs_retry,
                        plan_status=excluded.plan_status,plan_due=excluded.plan_due''',
                     (value['id'],fingerprint,value['title'],searchable,value['created_at'],value['updated_at'],dump(value),
                      text(fields.get('question_type')),1 if value.get('needs_retry') else 0,plan_status,plan_due))
        return value['id']

    def save_question(self,submission,title='',tags=None,source='',needs_retry=None):
        with self._connection() as conn:return self._save_question(conn,submission,title,tags,source,needs_retry)

    def _decorate_question(self,conn,value,count=None):
        if count is None:
            count=conn.execute('SELECT count(*) FROM review_questions WHERE question_id=?',(value['id'],)).fetchone()[0]
        value['attempt_count']=count
        value['status']='待重练' if value.get('needs_retry') else '已练' if count else '未练'
        return value

    def get_question(self,id):
        with self._connection() as conn:
            row=conn.execute('SELECT data FROM question_bank WHERE id=?',(id,)).fetchone()
            return self._decorate_question(conn,json.loads(row[0])) if row else None

    def _question_clauses(self,query='',status='',question_type=''):
        clauses,params=[],[]
        if query:
            clauses.append('instr(search_text,?)>0');params.append(str(query))
        if status:
            if status not in ('未练','已练','待重练'):raise ValueError('题目状态不受支持。')
            attempts='EXISTS (SELECT 1 FROM review_questions rq WHERE rq.question_id=question_bank.id)'
            clauses.append({'待重练':'needs_retry=1','已练':'needs_retry=0 AND '+attempts,
                            '未练':'needs_retry=0 AND NOT '+attempts}[status])
        if question_type:
            clauses.append('question_type=?');params.append(str(question_type))
        return clauses,params

    def list_questions_page(self,query='',status='',question_type='',limit=100,offset=0):
        limit=max(0,min(10000,int(limit)));offset=max(0,int(offset))
        if not limit:return []
        clauses,params=self._question_clauses(query,status,question_type)
        sql=('SELECT data FROM question_bank'+(' WHERE '+' AND '.join(clauses) if clauses else '')
             +' ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?')
        with self._connection() as conn:
            # One aggregate instead of a count per question.
            counts=dict(conn.execute('SELECT question_id,count(*) FROM review_questions '
                                     'WHERE question_id IS NOT NULL GROUP BY question_id'))
            result=[]
            for row in conn.execute(sql,params+[limit,offset]):
                value=json.loads(row[0])
                result.append(self._decorate_question(conn,value,counts.get(value['id'],0)))
            return result

    def count_questions(self,query='',status='',question_type=''):
        clauses,params=self._question_clauses(query,status,question_type)
        sql='SELECT count(*) FROM question_bank'+(' WHERE '+' AND '.join(clauses) if clauses else '')
        with self._connection() as conn:
            return conn.execute(sql,params).fetchone()[0]

    def list_questions(self,query='',status='',question_type='',limit=1000):
        return self.list_questions_page(query,status,question_type,limit,0)

    def mark_question_retry(self,id,value):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT data FROM question_bank WHERE id=?',(id,)).fetchone()
            if not row:raise ValueError('题目不存在。')
            item=json.loads(row[0])
            self._save_question(conn,item['submission'],needs_retry=value)

    def _link_practice_review(self,conn,review):
        # NULL is a durable detachment marker after a question is deleted. Keep
        # it when revisions save an existing report, so old content cannot
        # silently recreate the deleted question on save or application start.
        previous=conn.execute('SELECT question_id FROM review_questions WHERE review_id=?',(review['id'],)).fetchone()
        if previous is not None and previous[0] is None:return
        question_id=None
        submission=review.get('submission',{})
        if isinstance(submission,dict) and (submission.get('question') or submission.get('materials')):
            try:
                fields=copy.deepcopy(submission)
                fields.setdefault('max_score',mapping(review.get('assessment')).get('max_score',20))
                fields=normalize_question(fields)
                existing=conn.execute('SELECT id FROM question_bank WHERE fingerprint=?',(question_fingerprint(fields),)).fetchone()
                question_id=existing[0] if existing else self._save_question(conn,fields)
            except (ValueError,TypeError,OverflowError,KeyError):pass
        conn.execute('INSERT INTO review_questions VALUES(?,?) ON CONFLICT(review_id) DO UPDATE SET question_id=excluded.question_id',
                     (review['id'],question_id))

    def question_for_review(self,review_id):
        with self._connection() as conn:
            row=conn.execute('SELECT question_id FROM review_questions WHERE review_id=?',(review_id,)).fetchone()
        return row[0] if row else None

    def list_question_attempts(self,id):
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute('''SELECT r.data FROM reviews r JOIN review_questions q
                        ON r.id=q.review_id WHERE q.question_id=? ORDER BY r.created_at ASC,r.id ASC''',(id,))]

    def _workspace_snapshot(self,snapshot):
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get('submission'),dict):raise ValueError('恢复草稿格式不正确。')
        raw=snapshot['submission'];fields={}
        for key in ('question','materials','answer','reference','exam','year','category','question_type','rubric_text'):
            value=raw.get(key,'')
            if not isinstance(value,str):raise ValueError('草稿文字字段格式不正确。')
            fields[key]=value.replace('\r\n','\n').replace('\r','\n')
        if not fields['rubric_text'] and raw.get('rubric') is not None:
            fields['rubric_text']=json.dumps(raw['rubric'],ensure_ascii=False,indent=2,allow_nan=False)
        if sum(len(v) for v in fields.values())>2000000:raise ValueError('自动草稿超过200万字符，请先导出或缩小内容。')
        score=raw.get('max_score',20);limit=raw.get('word_limit',0)
        if type(score) is not int or not 1<=score<=1000:raise ValueError('草稿满分格式不正确。')
        if type(limit) is not int or not 0<=limit<=10000:raise ValueError('草稿字数限制格式不正确。')
        fields.update(max_score=score,word_limit=limit)
        selection=raw.get('rubric_selection','auto')
        if not isinstance(selection,str) or len(selection)>100:raise ValueError('草稿量表选项格式不正确。')
        fields['rubric_selection']=selection
        qid=snapshot.get('question_id')
        if qid is not None and not isinstance(qid,str):raise ValueError('草稿题目关联格式不正确。')
        return {'submission':fields,'question_id':qid,'updated_at':now()}

    def _save_workspace(self,conn,snapshot,archive_previous):
        value=self._workspace_snapshot(snapshot)
        qid=value['question_id']
        if qid:
            row=conn.execute('SELECT fingerprint FROM question_bank WHERE id=?',(qid,)).fetchone()
            if not row or row[0]!=question_fingerprint(value['submission']):value['question_id']=None
        old=conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
        if old:
            previous=json.loads(old[0])
            changed=question_fingerprint(previous['submission'])!=question_fingerprint(value['submission'])
            if meaningful(previous) and (archive_previous or changed):
                conn.execute('INSERT INTO workspace_history VALUES(?,?,?)',(uuid.uuid4().hex,previous.get('updated_at',now()),dump(previous)))
                conn.execute('DELETE FROM workspace_history WHERE id NOT IN (SELECT id FROM workspace_history ORDER BY rowid DESC LIMIT 20)')
        conn.execute("INSERT INTO practice_workspace VALUES('current',?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,data=excluded.data",(value['updated_at'],dump(value)))
        if value['question_id']:
            conn.execute('INSERT INTO question_drafts VALUES(?,?,?) ON CONFLICT(question_id) DO UPDATE SET updated_at=excluded.updated_at,data=excluded.data',
                         (value['question_id'],value['updated_at'],dump(value)))
        return value

    def save_workspace(self,snapshot,archive_previous=False):
        with self._connection() as conn:return self._save_workspace(conn,snapshot,archive_previous)

    def commit_routed_workspace(self, before, after, *, expected_workspace, expected_generation):
        """Archive the latest editor draft and adopt all routed fields atomically."""
        from .intake_routing import FIELDS
        from .state_write import state_write
        old = self._workspace_snapshot(before)
        new = self._workspace_snapshot(after)
        if ({key: value for key, value in before.items() if key not in ('submission', 'question_id', 'updated_at')}
                != {key: value for key, value in after.items() if key not in ('submission', 'question_id', 'updated_at')}
                or {key: value for key, value in before['submission'].items() if key not in FIELDS}
                != {key: value for key, value in after['submission'].items() if key not in FIELDS}
                or new['question_id'] not in (old['question_id'], None)):
            raise ValueError('内容分配只能修改四个输入栏。')
        with state_write(self, expected_generation) as conn:
            row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
            current = json.loads(row[0]) if row else None
            if current != expected_workspace:
                raise ValueError('草稿已在其他窗口更新，请重新载入后分配。')
            qid = old['question_id']
            if qid:
                question = conn.execute('SELECT fingerprint FROM question_bank WHERE id=?', (qid,)).fetchone()
                matches = question and question[0] == question_fingerprint(new['submission'])
                if new['question_id'] is None and matches:
                    raise ValueError('不能解除仍然匹配的题目关联。')
                if not matches:
                    new['question_id'] = None
            # Both internal writes share this transaction. A second-write failure
            # rolls back even the first write's history and question draft changes.
            self._save_workspace(conn, old, False)
            return self._save_workspace(conn, new, True)

    def load_workspace(self):
        with self._connection() as conn:
            row=conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
        return json.loads(row[0]) if row else None

    def load_question_draft(self,id):
        with self._connection() as conn:row=conn.execute('SELECT data FROM question_drafts WHERE question_id=?',(id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_workspace_history(self,limit=20):
        with self._connection() as conn:
            return [{**json.loads(row['data']),'id':row['id']} for row in conn.execute('SELECT id,data FROM workspace_history ORDER BY rowid DESC LIMIT ?',(max(0,min(20,int(limit))),))]

    def save_human_score(self,review_id,score,reason='',reviewer='',independent=False):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT data FROM reviews WHERE id=?',(review_id,)).fetchone()
            record=json.loads(row[0]) if row else None
            if not record:raise ValueError('训练记录不存在。')
            ai=score_value(record);conditions=scoring_conditions(record);maximum=conditions['max_score']
            if ai is None or maximum is None or conditions['mode']!='full':raise ValueError('诊断记录没有可对照的训练总分。')
            if not 0<=ai<=maximum:raise ValueError('训练记录得分超出满分范围，请先核对原记录。')
            score=finite_number(score,'人工分数',0,maximum)
            if not isinstance(reason,str) or not isinstance(reviewer,str) or len(reason)>10000 or len(reviewer)>100:raise ValueError('评阅说明或评阅人过长。')
            if type(independent) is not bool:raise ValueError('独立评阅标记须为布尔值。')
            value={'review_id':review_id,'score':score,'reason':reason,'reviewer':reviewer,'independent':independent,'updated_at':now(),'basis':human_basis(record)}
            conn.execute('INSERT INTO human_scores VALUES(?,?) ON CONFLICT(review_id) DO UPDATE SET data=excluded.data',(review_id,dump(value)))

    def get_human_score(self,review_id):
        with self._connection() as conn:row=conn.execute('SELECT data FROM human_scores WHERE review_id=?',(review_id,)).fetchone()
        if not row:return None
        value=json.loads(row[0]);record=self.get_review(review_id)
        value['stale']=not record or value.get('basis')!=human_basis(record)
        return value

    def human_calibration_pairs(self):
        result=[]
        with self._connection() as conn:
            for row in conn.execute('SELECT r.data,h.data FROM human_scores h JOIN reviews r ON r.id=h.review_id ORDER BY r.created_at,r.id'):
                record,human=json.loads(row[0]),json.loads(row[1]);conditions=scoring_conditions(record)
                score=score_value(record);maximum=conditions['max_score']
                if score is None or maximum is None or conditions['mode']!='full':continue
                if human.get('basis')!=human_basis(record) or not 0<=score<=maximum or not 0<=human['score']<=maximum:continue
                result.append({'review_id':record['id'],'human':human['score'],'ai':score,'max_score':maximum,
                               'answer_id':answer_identity(record),'version':scoring_version(record) or '版本信息不完整',
                               'independent':human.get('independent',False)})
        return result

    def practice_summary(self):
        groups={}
        with self._connection() as conn:
            counts={name:conn.execute(sql).fetchone()[0] for name,sql in (
                ('question_count','SELECT count(*) FROM question_bank'),('attempt_count','SELECT count(*) FROM review_questions WHERE question_id IS NOT NULL'),
                ('human_count','SELECT count(*) FROM human_scores'))}
            for row in conn.execute('SELECT data FROM reviews'):
                record=json.loads(row[0]);version=scoring_version(record);score=score_value(record)
                if version is None or score is None:continue
                maximum=scoring_conditions(record)['max_score']
                if not 0<=score<=maximum:continue
                key=(answer_identity(record),version)
                group=groups.setdefault(key,{'answer_id':key[0],'version':version,'n':0,'score_min':score,'score_max':score})
                group['n']+=1;group['score_min']=min(score,group['score_min']);group['score_max']=max(score,group['score_max'])
        repeated=[]
        for group in groups.values():
            if group['n']>=2:group['spread']=round(group['score_max']-group['score_min'],6);repeated.append(group)
        pairs=self.human_calibration_pairs();counts['human_count']=len(pairs)
        return {**counts,'independent_human_count':sum(bool(pair['independent']) for pair in pairs),'repeat_groups':repeated}

    def backup_to(self,path,overwrite=False,*,register=True,backup_kind='manual'):
        target=Path(path).resolve()
        if target==self.path.resolve() or (target.exists() and os.path.samefile(target,self.path)):
            raise ValueError('备份位置不能是正在使用的数据库。')
        if target.exists() and not overwrite:raise FileExistsError('备份文件已存在，请另选文件名。')
        target.parent.mkdir(parents=True,exist_ok=True)
        handle,temporary=tempfile.mkstemp(prefix='.shenlun-backup-',suffix='.db',dir=target.parent);os.close(handle)
        temporary=Path(temporary);reserved=False
        try:
            with self._connection() as source:
                with closing(sqlite3.connect(temporary)) as destination:
                    source.backup(destination)
                    destination.execute('PRAGMA journal_mode=DELETE')
                    if destination.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份数据库完整性检查失败。')
            if not overwrite:
                with target.open('xb'):pass
                reserved=True
            os.replace(temporary,target);reserved=False
            if register:
                from .auto_backup import AutoBackupManager
                AutoBackupManager(self).register(target, backup_kind)
            return target
        finally:
            if temporary.exists():temporary.unlink()
            if reserved and target.exists() and target.stat().st_size==0:target.unlink()

    def _validated_restore_source(self,path):
        path=Path(path).resolve()
        if path==self.path.resolve() or (path.exists() and os.path.samefile(path,self.path)):
            raise ValueError('恢复文件不能是当前数据库。')
        if not path.is_file() or not 0<path.stat().st_size<=8*1024**3:raise ValueError('恢复文件不存在、为空或超过8GB。')
        source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
        try:
            source.execute('PRAGMA trusted_schema=OFF')
            if source.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份数据库损坏。')
            required={'articles':('id','title','journal','kind','status','fingerprint','publication','search_text','data'),
                      'settings':('key','data'),'reviews':('id','created_at','data'),'analyses':('article_id','version','data'),
                      'cards':('id','enabled','data'),'usage':('id','input_tokens','output_tokens','total_tokens','data')}
            optional={'question_bank':('id','fingerprint','title','search_text','created_at','updated_at','data'),
                      'rubric_presets':('id','name_key','version','data'),
                      'review_questions':('review_id','question_id'),
                      'practice_workspace':('id','updated_at','data'),'question_drafts':('question_id','updated_at','data'),
                      'workspace_history':('id','updated_at','data'),'human_scores':('review_id','data'),
                      'reference_point_versions':('question_id','version','question_fingerprint','status','created_at','content_hash','data'),
                      'eval_sets':('id','data'),'eval_cases':('id','set_id','question_fingerprint','content_hash','data'),
                      'eval_labels':('case_id','version','data'),'eval_runs':('id','set_id','data'),
                      'eval_results':('run_id','case_id','repeat','data'),
                      'teaching_method_versions':('id','version','data'),
                      'teaching_owners':('id','name','deleted'),
                      'method_library_trash':('kind','method_id','deleted_at'),
                      'method_library_purged':('kind','method_id','purged_at','version_hashes'),
                      'method_comparisons':('id','data'),
                      'deleted_practice_reviews':('review_id','deleted_at'),
                      'assessment_tasks':('id','state','claim_token','review_id','created_at','updated_at','error_summary','data')}
            tables={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # Additive trailing columns; a backup either lacks them (upgraded in
            # place after the copy) or carries them in this exact order.
            derived={'articles':('year','has_body'),
                     'question_bank':('question_type','needs_retry','plan_status','plan_due')}
            for table,columns in {**required,**{k:v for k,v in optional.items() if k in tables}}.items():
                if table not in tables:raise ValueError('这不是兼容的申论研习数据库。')
                schema=list(source.execute('PRAGMA table_xinfo('+table+')'))
                expected_key={'analyses':('article_id','version'),'reference_point_versions':('question_id','version'),
                              'eval_labels':('case_id','version'),'eval_results':('run_id','case_id','repeat'),
                              'teaching_method_versions':('id','version'),
                              'method_library_trash':('kind','method_id'),
                              'method_library_purged':('kind','method_id')}.get(table,(columns[0],))
                primary=tuple(row[1] for row in sorted(schema,key=lambda row:row[5]) if row[5])
                actual=tuple(row[1] for row in schema)
                if actual not in (columns,columns+derived.get(table,())) or any(row[6] for row in schema) or primary!=expected_key:
                    raise ValueError('这不是兼容的申论研习数据库。')
                if table=='question_bank':
                    unique_indexes=[row[1] for row in source.execute('PRAGMA index_list(question_bank)') if row[2] and not row[4]]
                    unique_columns=[tuple(row[2] for row in source.execute('PRAGMA index_info("'+index.replace('"','""')+'")')) for index in unique_indexes]
                    if ('fingerprint',) not in unique_columns:raise ValueError('备份题目唯一约束不正确。')
                if 'data' in columns:
                    if source.execute('SELECT 1 FROM '+table+' WHERE data IS NULL OR NOT json_valid(data) LIMIT 1').fetchone():
                        raise ValueError('备份含有不可读取的JSON记录。')
                    if table!='settings' and source.execute("SELECT 1 FROM "+table+" WHERE json_type(data)!='object' LIMIT 1").fetchone():
                        raise ValueError('备份记录结构不正确。')
            if any(source.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type!='table'",(name,)).fetchone() for name in optional):
                raise ValueError('备份的题库表结构不正确。')
            self._validate_practice_restore(source,tables)
            self._validate_practice_deletion_restore(source,tables)
            self._validate_assessment_restore(source,tables)
            self._validate_rubric_library_restore(source,tables)
            if 'reference_point_versions' in tables:
                if 'question_bank' not in tables:raise ValueError('参考要点备份缺少题库。')
                from .reference_points import validate_reference_version
                for qid,version,fingerprint,status,created_at,digest,payload in source.execute('SELECT * FROM reference_point_versions'):
                    question=source.execute('SELECT data FROM question_bank WHERE id=?',(qid,)).fetchone()
                    if not question:raise ValueError('参考要点备份引用未知题目。')
                    value=validate_reference_version(json.loads(payload),json.loads(question[0])['submission'])
                    if (qid,version,fingerprint,status,created_at,digest)!=(value['question_id'],value['version'],value['question_fingerprint'],value['status'],value['created_at'],value['content_hash']):
                        raise ValueError('参考要点备份索引与内容不一致。')
            self._validate_teaching_restore(source,tables)
            self._validate_method_library_restore(source,tables)
            self._validate_method_purge_restore(source,tables)
            self._validate_method_presets_restore(source,tables)
            from .evaluation_store import validate_teaching_history
            from .teaching import validate_teaching_output
            for row in source.execute('SELECT data FROM reviews'):
                review=json.loads(row[0]);teaching=review.get('teaching_snapshot')
                if teaching is not None:
                    submission=review.get('submission')
                    validate_teaching_history(source,tables,teaching,submission)
                    assessment=review.get('assessment')
                    if not isinstance(assessment,dict):raise ValueError('教学方法报告缺少评价内容。')
                    validate_teaching_output(assessment.get('teaching_analysis'),teaching,submission)
            selection=source.execute("SELECT data FROM settings WHERE key='teaching_selection'").fetchone()
            if selection:
                from .teaching import normalize_teaching_selection
                normalize_teaching_selection(json.loads(selection[0]))
            self._validate_evaluation_restore(source,tables)
            self._validate_method_comparisons_restore(source,tables)
            if source.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():raise ValueError('备份包含不受支持的数据库触发器。')
            return source
        except Exception:
            source.close();raise

    def _validate_practice_restore(self,source,tables):
        """Validate optional 0.4 payloads before touching the active database; 0.3 stays compatible."""
        for table in ('practice_workspace','question_drafts','workspace_history'):
            if table in tables:
                for row in source.execute('SELECT data FROM '+table):self._workspace_snapshot(json.loads(row[0]))
        if 'question_bank' in tables:
            for identifier,fingerprint,payload in source.execute('SELECT id,fingerprint,data FROM question_bank'):
                value=json.loads(payload)
                if value.get('id')!=identifier or not isinstance(value.get('submission'),dict):raise ValueError('备份题目关联不正确。')
                fields=normalize_question(value['submission'])
                if question_fingerprint(fields)!=fingerprint:raise ValueError('备份题目内容与标识不一致。')
                for key in ('title','created_at','updated_at','source'):
                    if not isinstance(value.get(key),str):raise ValueError('备份题目字段不正确。')
                tags=value.get('tags')
                if 'review_plan' in value:
                    plan = normalize_review_plan(value['review_plan'])
                    if plan['version']<1 or (plan['status']!='已解决') != value.get('needs_retry'):
                        raise ValueError('备份复习计划版本或状态不正确。')
                    from .review_goals import validate_goal_sources
                    validate_goal_sources(source,plan.get('goals',[]))
                if not isinstance(tags,list) or any(not isinstance(tag,str) for tag in tags) or type(value.get('needs_retry')) is not bool:
                    raise ValueError('备份题目标签或状态不正确。')
        if 'human_scores' in tables:
            for identifier,payload in source.execute('SELECT review_id,data FROM human_scores'):
                value=json.loads(payload)
                if value.get('review_id')!=identifier or not isinstance(value.get('basis'),str):raise ValueError('备份人工评分关联不正确。')
                finite_number(value.get('score'),'人工分数',0,1000)
                if type(value.get('independent')) is not bool or any(not isinstance(value.get(key),str) for key in ('reason','reviewer','updated_at')):
                    raise ValueError('备份人工评分字段不正确。')

    def restore_from(self,path):
        with self.assessment_restore_guard():
            return self._restore_from_guarded(path)

    def _restore_from_guarded(self,path):
        try:source=self._validated_restore_source(path)
        except (sqlite3.Error,OSError) as exc:raise ValueError('备份无法读取：'+str(exc)) from None
        safety=self.path.parent/('恢复前备份-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.db')
        try:
            self.backup_to(safety,backup_kind='safety')
            try:
                with closing(sqlite3.connect(self.path,timeout=30)) as destination:source.backup(destination)
                # A safety copy already exists, so no extra pre-migration backup.
                self.__init__(self.path, migrate_backup=False)
                with self._connection() as conn:
                    self._invalidate_assessment_claims(conn)
            except Exception:
                with closing(sqlite3.connect(safety)) as original:
                    with closing(sqlite3.connect(self.path,timeout=30)) as destination:original.backup(destination)
                self.__init__(self.path, migrate_backup=False)
                with self._connection() as conn:
                    self._invalidate_assessment_claims(conn)
                raise
        finally:source.close()
        return safety
