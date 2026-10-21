"""Submission editor, saved report and Unicode-safe annotation navigation."""
import copy
import json
from html import escape

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMessageBox

from ...domain import prepare_submission, validate_rubric
from ...reports import review_html


class WorkflowMixin:
    def raw_submission(self):
        rubric = self.rubric_edit.toPlainText().strip()
        value = {'question':self.question_edit.toPlainText(), 'materials':self.materials_edit.toPlainText(),
                 'answer':self.answer_edit.toPlainText(), 'question_type':self.question_type.currentText(),
                 'exam':self.exam.currentText(), 'year':self.year.text(), 'category':self.category.text(),
                 'rubric_selection':self.rubric_profile.currentData(),
                 'max_score':self.max_score.value(), 'word_limit':self.word_limit.value(),
                 'reference':self.reference_edit.toPlainText()}
        if rubric:
            value['rubric'] = validate_rubric(json.loads(rubric))
        return value

    def submission(self):
        return prepare_submission(self.raw_submission())

    def begin_assessment(self):
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        try:
            submission = self.submission()
            self.preflight_teaching(submission)
            self.refresh_teaching_summary()
            from ...method_presets import normalize_method_choice
            choice=normalize_method_choice(self.store.get_setting('method_choice'))
            context=None
            if choice['mode']!='auto':
                from ...service import AppService,ASSESSMENT_SYSTEM,PROMPT_VERSION
                from ...evaluation import canonical_hash
                offline=AppService(self.store,dict(self.config),'')
                _,payload,sources,methods,points=offline.prepare_grade(submission,method_choice=choice)
                context={'payload':payload,'sources':sources,'method_snapshot':methods,
                    'reference_points_snapshot':points,'prompt_version':PROMPT_VERSION,
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM),'method_choice_snapshot':offline._last_method_choice}
                title=offline._last_method_choice.get('name','关闭方法')
                names=['教学：'+m['name'][:45]+'（'+m['id']+'，v'+str(m['version'])+'）'
                    for m in (payload.get('teaching_guidance') or {}).get('methods',[])]
                names+=['文章：'+m['name'][:45]+'（'+m['id']+'）' for m in methods]
                message='本次选择：'+title+'\n'+('\n'.join(names) if names else '本次不使用教学方法或文章提炼方法。')
                message+='\n\n将按上述冻结名单批改；本题、材料、原答和所选依据会发送给已配置模型。是否开始？'
                if QMessageBox.question(self,'确认本次实际使用方法',message,
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
                if self.workers or getattr(self,'_closing',False):return
            if context is None:
                from ...service import AppService, ASSESSMENT_SYSTEM, PROMPT_VERSION
                from ...evaluation import canonical_hash
                offline = AppService(self.store, dict(self.config), '')
                _, payload, sources, methods, points = offline.prepare_grade(submission, method_choice=choice)
                context = {'payload':payload, 'sources':sources, 'method_snapshot':methods,
                    'reference_points_snapshot':points, 'prompt_version':PROMPT_VERSION,
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM), 'method_choice_snapshot':offline._last_method_choice}
            if not self.flush_workspace(): return
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))
            return
        self.launch_assessment_snapshot(copy.deepcopy(submission), copy.deepcopy(context))

    def launch_assessment_snapshot(self, submission, context):
        if self.workers or self._closing: return
        try:
            task_id = self.store.create_assessment_task(submission, context)
        except (ValueError, TypeError, OSError) as exc:
            self.show_task_failure('保存任务快照', str(exc), None)
            return
        self.launch_assessment_task(task_id)

    def show_review(self, review, navigate=True):
        self.clear_task_feedback()
        self.current_review = review
        self.refresh_review_goals_entry()
        self.refresh_revision_diff()
        from ...review_learning import summary_html
        assessment = review.get('assessment',{})
        total = assessment.get('total_score')
        score = '表达与结构诊断' if total is None else f'训练估分 {total} / {assessment.get("max_score",20)}'
        self.result_summary.setHtml('<h3>'+escape(str(review.get('title','训练复盘')))+'</h3><p>'+escape(score)+'</p>'+summary_html(review))
        self.result_tabs.setCurrentIndex(0)
        self.export_report_button.setEnabled(True)
        self.export_pdf_button.setEnabled(True)
        self.result_report.setHtml(review_html(review))
        submission = review.get('submission', {})
        self.result_original.setPlainText(submission.get('answer', ''))
        revisions = review.get('revisions', {})
        self.result_minimal.setPlainText(revisions.get('minimal', ''))
        self.result_model.setPlainText(revisions.get('model', ''))
        self.revision_status.setText(revisions.get('status', ''))
        self.annotation_list.clear()
        for index, annotation in enumerate(review.get('assessment', {}).get('annotations', []), 1):
            self.annotation_list.addItem(f'{index}. {annotation.get("issue", "批注")}\n{annotation.get("suggestion", "")}')
        if navigate:
            self.pages.setCurrentIndex(0)
            self.navigation.setCurrentRow(0)
        self.assessment_tabs.setCurrentIndex(2)
        self.refresh_history()
        if str(revisions.get('status','')).startswith('修改未完成'):
            frozen = copy.deepcopy(review)
            task_id = self.store.assessment_task_for_review(review['id'])
            self.show_task_failure('修改稿生成（原答评分已保存）', revisions['status'],
                (lambda:self.resume_assessment_task(task_id)) if task_id else lambda:self.retry_revision_snapshot(frozen))

    def retry_revision_snapshot(self, review):
        task_id = self.store.assessment_task_for_review(review['id'])
        if task_id:
            return self.resume_assessment_task(task_id)
        if self.workers or self._closing: return
        if QMessageBox.question(self, '重试生成修改稿',
            '原答评分已保存；本次只重新生成两种修改稿，不重复评分。\n'
            '使用该训练记录的原答和评分快照，以及当前已保存的模型设置，可能再次产生费用。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        try:
            from ...model_diagnostics import preflight_request
            from ...service import AppService, REVISION_SYSTEM
            preflight_request(self.config, REVISION_SYSTEM, AppService.revision_payload(review), output_limit=self.declared_output_limit(self.config))
            service = self.get_service(require_config=True)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('修改稿发送前检查', str(exc), lambda:self.retry_revision_snapshot(review))
            return
        self.start_job('重试修改稿',
            lambda progress,cancel:service.retry_revisions(review['id'], progress, cancel, expected_review=copy.deepcopy(review)),
            self.show_review, retry=lambda:self.retry_revision_snapshot(review),
            domains=('history', 'usage'))

    def select_annotation(self, index):
        if not self.current_review or index < 0:
            return
        annotations = self.current_review.get('assessment', {}).get('annotations', [])
        if index >= len(annotations):
            return
        annotation = annotations[index]
        answer = self.current_review['submission']['answer']
        start, end = annotation.get('start', 0), annotation.get('end', 0)
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(answer)):
            return
        cursor = self.result_original.textCursor()
        cursor.setPosition(len(answer[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(len(answer[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
        self.result_original.setTextCursor(cursor)
        self.result_original.ensureCursorVisible()
