from __future__ import annotations

import copy

from ..domain import prepare_submission, validate_assessment
from ..model import DEFAULT_CONFIG, check_cancel

from .prompts import ASSESSMENT_SCHEMA, ASSESSMENT_SYSTEM, PROMPT_VERSION, _USE_ACTIVE_POINTS
from .text import _answer_segments, _notify, _now


class GradingMixin:
    def prepare_grade(self,raw_submission,reference_points_snapshot=_USE_ACTIVE_POINTS,retire_invalid=False,method_choice=None):
        """Read-only payload construction by default, shared with exact budget estimates."""
        s=prepare_submission(raw_submission)
        contexts,sources=self._retrieve(s)
        from ..method_presets import resolve_method_choice
        selected=resolve_method_choice(self.store,s,method_choice,retire_invalid=retire_invalid)
        enabled=selected['article_methods']
        self._last_method_choice=copy.deepcopy(selected['preset'] or selected['choice'])
        payload={'submission':s,'answer_segments':_answer_segments(s['answer']), 'corpus_context':contexts,
                 'method_guidance':[{k:c.get(k) for k in ('name','purpose','steps','not_applicable','applicable_types')} for c in enabled],
                 'output_schema':copy.deepcopy(ASSESSMENT_SCHEMA),'training_notice':'训练量表，不是官方采分细则；参考答案来源未独立核验'}
        if reference_points_snapshot is _USE_ACTIVE_POINTS:
            reference_points_snapshot=self.store.reference_points_for_submission(s)
        snapshot=None
        if reference_points_snapshot is not None:
            from ..reference_points import validate_reference_snapshot
            snapshot=validate_reference_snapshot(reference_points_snapshot,s)
            payload['confirmed_reference_points']=snapshot
            payload['output_schema']['reference_point_coverage']=[{'id':'快照中的P标识','coverage':'covered/partial/missing/uncertain',
                                                                 'answer_quote':'原答原样摘录，未覆盖可为空','reason':'对应依据或待确认原因'}]
        teaching=selected['teaching_snapshot']
        if teaching is not None:
            payload['teaching_guidance']=teaching
            payload['output_schema']['teaching_analysis']={
                'applications':[{'method_id':'实际方法卡id','status':'applied/not_applicable/uncertain','observation':'方法在本题的具体应用或不适用原因',
                                 'material_refs':[{'id':'M1','quote':'给定材料原样短引用'}],'answer_quote':'原答原样引用，可为空'}],
                'consensus':['本题方法共识'],'differences':['本题组织或推断的差异与证据边界']}
        return s,payload,sources,enabled,snapshot

    def _grade(self,raw_submission,progress=None,cancel=None,reference_points_snapshot=_USE_ACTIVE_POINTS,grade_context=None,*,retry_json=True):
        check_cancel(cancel)
        _notify(progress,'核对题干、材料与字数，检索本地写作参考…')
        if grade_context is None:
            s,payload,sources,enabled,snapshot=self.prepare_grade(raw_submission,reference_points_snapshot,retire_invalid=True)
            method_choice_snapshot=copy.deepcopy(self._last_method_choice)
        else:
            from ..evaluation import canonical_hash
            from ..reference_points import validate_reference_snapshot
            s=prepare_submission(raw_submission)
            context=copy.deepcopy(grade_context)
            if (not isinstance(context,dict) or context.get('prompt_version')!=PROMPT_VERSION
                    or context.get('system_hash')!=canonical_hash(ASSESSMENT_SYSTEM)):
                raise ValueError('冻结评测上下文的提示版本无效。')
            payload=context.get('payload')
            if not isinstance(payload,dict) or payload.get('submission')!=s:
                raise ValueError('冻结评测内容与本次题目原答不一致。')
            snapshot=context.get('reference_points_snapshot')
            if snapshot!=reference_points_snapshot:raise ValueError('冻结参考要点版本不一致。')
            if snapshot is not None:snapshot=validate_reference_snapshot(snapshot,s)
            if payload.get('confirmed_reference_points')!=snapshot:raise ValueError('冻结参考要点输入不一致。')
            sources=context.get('sources');enabled=context.get('method_snapshot')
            method_choice_snapshot=copy.deepcopy(context.get('method_choice_snapshot'))
            if not isinstance(sources,list) or not isinstance(enabled,list):raise ValueError('冻结语料上下文格式无效。')
            expected=[paragraph for source in sources for paragraph in source.get('paragraphs',[])]
            if expected!=payload.get('corpus_context'):
                # Retrieval ranks can interleave articles: compare exact id/text maps.
                if {p['id']:p['text'] for p in expected}!={p['id']:p['text'] for p in payload.get('corpus_context',[])}:
                    raise ValueError('冻结语料证据与实际模型输入不一致。')
        teaching=payload.get('teaching_guidance')
        if teaching is not None:
            from ..teaching import validate_teaching_snapshot
            teaching=validate_teaching_snapshot(teaching,s)
            payload['teaching_guidance']=teaching
        _notify(progress,'正在分析原答；发送本题、已选方法及至多8个参考段落…')
        response=self._complete(ASSESSMENT_SYSTEM,payload,cancel,retry_json=retry_json)
        _notify(progress,'核验模型评分、材料引用与结构…')
        assessment=validate_assessment(response['data'],s,sources)
        if snapshot:
            from ..reference_points import validate_point_coverage
            assessment['reference_point_coverage']=validate_point_coverage(response['data'].get('reference_point_coverage'),snapshot,s['answer'])
        else:assessment.pop('reference_point_coverage',None)
        if teaching is not None:
            from ..teaching import validate_teaching_output
            assessment['teaching_analysis']=validate_teaching_output(response['data'].get('teaching_analysis'),teaching,s)
        else:assessment.pop('teaching_analysis',None)
        check_cancel(cancel)
        title=(s['question'].split('\n')[0] or '表达与结构诊断')[:60]
        review={'title':title,'submission':s,'assessment':assessment,
                'revisions':{'minimal':'','model':'','status':'评测仅批改，未生成修改稿'},
                'sources':sources,'method_snapshot':copy.deepcopy(enabled),'model':response.get('model',self.config['model']),
                'config_snapshot':{k:self.config[k] for k in DEFAULT_CONFIG},
                'prompt_version':PROMPT_VERSION,'usage':dict(response['usage']),'created_at':_now()}
        if method_choice_snapshot is not None:review['method_choice_snapshot']=method_choice_snapshot
        if snapshot:review['reference_points_snapshot']=copy.deepcopy(snapshot)
        if teaching is not None:review['teaching_snapshot']=copy.deepcopy(teaching)
        return review

    def evaluate_submission(self,raw_submission,reference_points_snapshot=None,progress=None,cancel=None,grade_context=None):
        """One frozen grade only; usage persists, ordinary attempts/revisions do not."""
        return self._grade(raw_submission,progress,cancel,reference_points_snapshot,grade_context,retry_json=False)

    def assess(self,raw_submission,progress=None,cancel=None,*,grade_context=None,task_id=None,expected_generation=None):
        if task_id is not None:
            return self.run_assessment_task(task_id, progress, cancel, expected_generation=expected_generation)
        points=grade_context.get('reference_points_snapshot') if grade_context is not None else _USE_ACTIVE_POINTS
        review=self._grade(raw_submission,progress,cancel,points,grade_context)
        s=review['submission'];assessment=review['assessment']
        review['revisions']['status']='原答已保存，准备生成修改稿'
        _notify(progress,'保存原答评分与证据…')
        review['id']=self.store.save_review(review)
        return self._revise_review(review, progress, cancel)
