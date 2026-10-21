"""Versioned local review schedules embedded in existing question records."""
import json
from datetime import date, datetime, timezone

from .review_goals import normalize_goals, review_goal_basis, review_goal_candidates
from .state_write import state_write

STATUSES = ('待复习','复习中','已解决')


def normalize_review_plan(value):
    if not isinstance(value,dict) or value.get('status') not in STATUSES:
        raise ValueError('复习状态须为待复习、复习中或已解决。')
    due = value.get('due_date','')
    if not isinstance(due,str): raise ValueError('复习日期不正确。')
    if due:
        try: valid = date.fromisoformat(due)
        except ValueError: raise ValueError('复习日期不正确。') from None
        if valid.isoformat()!=due: raise ValueError('复习日期须为 YYYY-MM-DD。')
    tags = value.get('tags',[])
    if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or len(t)>40 for t in tags):
        raise ValueError('问题标签最多20个，每个不超过40字。')
    version = value.get('version',0)
    if type(version) is not int or not 0 <= version < 2**31: raise ValueError('复习计划版本不正确。')
    result = {'version':version,'status':value['status'],'due_date':due,'tags':list(dict.fromkeys(t.strip() for t in tags if t.strip()))}
    if 'goals' in value: result['goals'] = normalize_goals(value['goals'])
    return result


def question_plan(question):
    return normalize_review_plan(question['review_plan']) if 'review_plan' in question else {
        'version':0,'status':'待复习','due_date':'','tags':[]}


class ReviewPlanStoreMixin:
    def _write_review_plan(self, conn, question_id, plan, expected_version, *, append_goals=None):
        if type(expected_version) is not int or expected_version < 0: raise ValueError('请先载入复习计划版本。')
        row = conn.execute('SELECT data FROM question_bank WHERE id=?',(question_id,)).fetchone()
        if not row: raise ValueError('题目不存在或已删除；未重新创建。')
        question = json.loads(row[0]); current = question_plan(question)
        if current['version'] != expected_version: raise ValueError('复习计划已被其他窗口更新，请重新载入。')
        existing = current.get('goals',[])
        if append_goals is not None:
            plan = normalize_review_plan({**current,**plan})
            plan['goals'] = normalize_goals(existing+[g for g in append_goals if g not in existing])
        elif 'goals' in plan:
            if any(g not in existing for g in plan['goals']):
                raise ValueError('普通计划编辑只能保留或移除已有训练目标，请从报告勾选添加。')
        elif 'goals' in current:
            plan['goals'] = existing
        plan['version'] = current['version']+1
        question['review_plan'] = plan
        question['needs_retry'] = plan['status']!='已解决'
        timestamp = datetime.now(timezone.utc).isoformat()
        question['updated_at'] = timestamp
        conn.execute('UPDATE question_bank SET updated_at=?,data=? WHERE id=?',
                     (timestamp,json.dumps(question,ensure_ascii=False,allow_nan=False,separators=(',',':')),question_id))
        return plan

    def save_review_plan(self, question_id, value, *, expected_version, expected_generation=None):
        plan = normalize_review_plan(value)
        generation = self.assessment_generation() if expected_generation is None else expected_generation
        with state_write(self, generation) as conn:
            return self._write_review_plan(conn,question_id,plan,expected_version)

    def add_review_goals(self, review_id, selected_goal_ids, plan_value, *, expected_version,
                         expected_basis, expected_generation, expected_question_id):
        if (not isinstance(selected_goal_ids,list) or not selected_goal_ids or len(selected_goal_ids)>1000
                or any(not isinstance(identity,str) for identity in selected_goal_ids)):
            raise ValueError('请勾选至少一项有效训练目标。')
        if not isinstance(plan_value,dict) or 'goals' in plan_value:
            raise ValueError('新增目标须由所选报告条目生成。')
        normalize_review_plan(plan_value)
        plan = dict(plan_value)
        with state_write(self, expected_generation) as conn:
            row = conn.execute('SELECT r.data,q.question_id FROM reviews r JOIN review_questions q '
                               'ON r.id=q.review_id WHERE r.id=?',(review_id,)).fetchone()
            if not row or not row[1]: raise ValueError('来源报告或关联题目不存在或已删除；未重新创建。')
            if row[1] != expected_question_id:
                raise ValueError('来源报告关联题目已变化，请重新载入。')
            review = json.loads(row[0])
            if review_goal_basis(review) != expected_basis:
                raise ValueError('来源报告评分依据已变化，请重新载入。')
            candidates = {g['id']:g for g in review_goal_candidates(review)}
            if any(identity not in candidates for identity in selected_goal_ids):
                raise ValueError('所选目标已失效，请重新载入报告。')
            selected = [candidates[identity] for identity in dict.fromkeys(selected_goal_ids)]
            return self._write_review_plan(conn,row[1],plan,expected_version,append_goals=selected)

    def list_review_plans(self, query='', status='', due='', today=None):
        if status and status not in STATUSES: raise ValueError('复习筛选状态不正确。')
        if due not in ('','due','unscheduled'): raise ValueError('复习日期筛选不正确。')
        today = date.fromisoformat(today).isoformat() if today is not None else date.today().isoformat()
        results = []
        with self._connection() as conn:
            rows = conn.execute('SELECT data FROM question_bank').fetchall()
            for row in rows:
                question = json.loads(row[0])
                if 'review_plan' not in question and not question.get('needs_retry'): continue
                plan = question_plan(question)
                if status and plan['status']!=status: continue
                if due=='due' and (not plan['due_date'] or plan['due_date']>today or plan['status']=='已解决'): continue
                if due=='unscheduled' and plan['due_date']: continue
                if query and str(query) not in '\n'.join([question['title'],question.get('source',''),
                    question['submission'].get('question',''),*plan['tags']]): continue
                question['review_plan'] = plan
                results.append(self._decorate_question(conn,question))
        return sorted(results,key=lambda q:(q['review_plan']['status']=='已解决',q['review_plan']['due_date'] or '9999-12-31',q['title'],q['id']))
