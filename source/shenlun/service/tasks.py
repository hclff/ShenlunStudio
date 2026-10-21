from __future__ import annotations

import copy
import re

from ..domain import require_text
from ..model import CancelledError, ModelError, check_cancel

from .prompts import REVISION_SYSTEM
from .text import _notify


class TaskMixin:
    def run_assessment_task(self, task_id, progress=None, cancel=None, *, expected_generation=None):
        """Explicit durable run; an already saved grade is never sent again."""
        if expected_generation is None:
            expected_generation = self.store.assessment_generation()
        task = self.store.get_assessment_task(task_id)
        if not task:
            raise ValueError('任务不存在。')
        if task.get('error'):
            raise ValueError(task['error'])
        if task['state'] == 'completed':
            return self.store.get_review(task['review_id'])
        with self.store.claim_assessment_task(task_id, expected_generation=expected_generation) as token:
            try:
                check_cancel(cancel)
                task = self.store.get_assessment_task(task_id)
                if task['review_id']:
                    review = self.store.get_review(task['review_id'])
                    expected = copy.deepcopy(review)
                    if review.get('revisions', {}).get('minimal') and review.get('revisions', {}).get('model'):
                        self.store.save_assessment_review(task_id, token, review, state='completed', expected_review=expected)
                        return review
                    review['revision_config_snapshot'] = dict(self.config)
                else:
                    context = task['context']
                    review = self._grade(task['submission'], progress, cancel,
                                         context.get('reference_points_snapshot'), context)
                    review['revisions']['status'] = '原答已保存，准备生成修改稿'
                    review['id'] = self.store.save_assessment_review(task_id, token, review, state='scored')
                    expected = copy.deepcopy(review)
                self.store.update_assessment_task(task_id, token, 'revising')
                return self._revise_review(review, progress, cancel, expected_review=expected, task_claim=(task_id, token))
            except CancelledError:
                self.store.update_assessment_task(task_id, token, 'cancelled')
                raise
            except Exception:
                self.store.update_assessment_task(task_id, token, 'failed')
                raise

    @staticmethod
    def revision_payload(review):
        s = review['submission']
        payload = {'submission':s, 'assessment':review['assessment'], 'word_limit':s['word_limit'],
                   'output_schema':{'minimal':'修改正文','model':'示范正文'}}
        if review.get('teaching_snapshot'): payload['teaching_guidance'] = copy.deepcopy(review['teaching_snapshot'])
        return payload

    def retry_revisions(self, review_id, progress=None, cancel=None, *, expected_review=None):
        check_cancel(cancel)
        current = self.store.get_review(review_id)
        if current is None: raise ValueError('训练记录不存在或已删除。')
        if expected_review is not None and current != expected_review:
            raise ValueError('训练记录已被其他窗口更新，请重新载入后再试。')
        if current.get('revisions',{}).get('minimal') and current.get('revisions',{}).get('model'):
            raise ValueError('修改稿已经完成；无需再次重试。')
        from ..model_diagnostics import preflight_request
        preflight_request(self.config, REVISION_SYSTEM, self.revision_payload(current))
        review = copy.deepcopy(current)
        review['revision_config_snapshot'] = dict(self.config)
        return self._revise_review(review, progress, cancel, expected_review=current)

    def _revise_review(self, review, progress, cancel, *, expected_review=None, task_claim=None):
        expected = copy.deepcopy(expected_review if expected_review is not None else review)
        s = review['submission']
        _notify(progress,'原答评分与证据已保存，单独生成两种修改稿…')
        try:
            revision_payload=self.revision_payload(review)
            revised=self._complete(REVISION_SYSTEM,revision_payload,cancel)
            minimal=require_text(revised['data'].get('minimal'),'修改正文')
            model=require_text(revised['data'].get('model'),'示范正文')
            check_cancel(cancel)
            review['revisions']={'minimal':minimal,'model':model,'status':'完成（独立生成，原答评分未改变）'}
            if s['word_limit']:
                for key,label in (('minimal','修改稿'),('model','示范稿')):
                    count=len(re.sub(r'\s','',review['revisions'][key]))
                    if count>s['word_limit']:
                        review['revisions']['status']+=f'；{label}{count}字，超出{s["word_limit"]}字，请压缩'
            for key in ('input_tokens','output_tokens','total_tokens'):
                review['usage'][key]=review['usage'].get(key,0)+revised['usage'].get(key,0)
        except CancelledError:
            review['revisions']['status']='修改已取消；原答评价已保存'
            if task_claim:
                self.store.save_assessment_review(*task_claim, review, state='cancelled', expected_review=expected)
            else:
                self.store.save_review(review, expected_review=expected)
            raise
        except (ModelError,ValueError) as exc:
            review['revisions']['status']=('修改未完成；原答评价已保存' if task_claim else '修改未完成：'+str(exc)+'；原答评价已保存')
        if task_claim:
            state = 'completed' if review['revisions']['minimal'] and review['revisions']['model'] else 'failed'
            self.store.save_assessment_review(*task_claim, review, state=state, expected_review=expected)
        else:
            self.store.save_review(review, expected_review=expected)
        _notify(progress,'训练批改已保存，可以对照原答、材料依据与修改稿。')
        return review
