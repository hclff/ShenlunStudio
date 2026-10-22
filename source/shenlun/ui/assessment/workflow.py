"""Submission editor, saved report and Unicode-safe annotation navigation."""
import copy
import json
from html import escape

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QListWidgetItem, QMessageBox, QTreeWidgetItem

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
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM),'method_choice_snapshot':offline._last_method_choice,
                    'revision_mode':self.revision_mode.currentData()}
                title=offline._last_method_choice.get('name','关闭方法')
                names=['教学：'+m['name'][:45]+'（'+m['id']+'，v'+str(m['version'])+'）'
                    for m in (payload.get('teaching_guidance') or {}).get('methods',[])]
                names+=['文章：'+m['name'][:45]+'（'+m['id']+'）' for m in methods]
                message='本次选择：'+title+'\n'+('\n'.join(names) if names else '本次不使用教学方法或文章提炼方法。')
                message+='\n本次输出：'+('仅精批（不调用改写模型）' if self.revision_mode.currentData()=='grade_only' else '精批＋修改稿')
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
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM), 'method_choice_snapshot':offline._last_method_choice,
                    'revision_mode':self.revision_mode.currentData()}
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
        from ...assessment_trace import DECISION_LABELS
        from ...review_learning import summary_html
        rechecks = self.current_rechecks(review)
        assessment = review.get('assessment',{})
        total = assessment.get('total_score')
        score = '表达与结构诊断' if total is None else f'训练估分 {total} / {assessment.get("max_score",20)}'
        self.result_summary.setHtml('<h3>'+escape(str(review.get('title','训练复盘')))+'</h3><p>'+escape(score)+'</p>'+summary_html(review, rechecks))
        self.result_tabs.setCurrentIndex(0)
        self.export_report_button.setEnabled(True)
        self.export_pdf_button.setEnabled(True)
        self.result_report.setHtml(review_html(review, rechecks))
        self.refresh_trace(review, rechecks)
        submission = review.get('submission', {})
        self.result_original.setPlainText(submission.get('answer', ''))
        revisions = review.get('revisions', {})
        self.result_minimal.setPlainText(revisions.get('minimal', ''))
        self.result_model.setPlainText(revisions.get('model', ''))
        self.revision_status.setText(revisions.get('status', ''))
        self.refresh_generate_revision(revisions)
        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        for index, annotation in enumerate(review.get('assessment', {}).get('annotations', []), 0):
            text = f'{index + 1}. {annotation.get("issue", "批注")}\n{annotation.get("suggestion", "")}'
            verdict = rechecks.get(index) if isinstance(rechecks, dict) else None
            label = DECISION_LABELS.get(verdict.get('decision')) if isinstance(verdict, dict) else None
            if label:
                text += '\n〔复核：' + label + '〕'
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if label:
                item.setToolTip('最近一次复核：' + label + '。' +
                                str(verdict.get('reason') or '') + '\n勾选可再次复核；原评分不会因此改动。')
            self.annotation_list.addItem(item)
        self.annotation_list.blockSignals(False)
        self.update_recheck_actions()
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

    def current_rechecks(self, review=None):
        """Latest verdict per annotation for the loaded review; empty without an id."""
        review = review if isinstance(review, dict) else self.current_review
        if not isinstance(review, dict):
            return {}
        identity = review.get('id')
        if not isinstance(identity, str) or not identity.strip():
            return {}
        try:
            return self.store.latest_recheck_map(identity)
        except (ValueError, OSError):
            return {}

    def refresh_generate_revision(self, revisions=None):
        revisions = revisions if isinstance(revisions, dict) else {}
        empty = not str(revisions.get('minimal') or '').strip() and not str(revisions.get('model') or '').strip()
        self.generate_revision_button.setVisible(empty and bool(self.current_review))
        self.generate_revision_button.setEnabled(True)

    def checked_annotations(self):
        result = []
        for row in range(self.annotation_list.count()):
            item = self.annotation_list.item(row)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(index, int) and index >= 0:
                result.append(index)
        return result

    def update_recheck_actions(self):
        selected = self.checked_annotations()
        self.recheck_button.setText(f'复核所选批注（{len(selected)}）…' if selected else '复核所选批注…')
        self.recheck_button.setEnabled(bool(selected) and isinstance(self.current_review, dict) and
                                       bool(self.current_review.get('id')))

    def recheck_selected(self):
        """One extra call for the checked annotations; the report stays read-only."""
        review = self.current_review
        if not isinstance(review, dict) or not review.get('id'):
            self.notify('复核只针对已保存的训练记录；请先完成批改或从历史记录载入报告。')
            return
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        indices = self.checked_annotations()
        if not indices:
            self.notify('请先在「原答与批注」中勾选需要复核的批注。')
            return
        try:
            from ...model_diagnostics import preflight_request
            from ...service import AppService, RECHECK_SYSTEM
            offline = AppService(self.store, dict(self.config), '')
            payload = offline.recheck_payload(review, indices)
            estimate = preflight_request(self.config, RECHECK_SYSTEM, payload,
                                         output_limit=self.declared_output_limit(self.config))
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('复核发送前检查', str(exc), lambda: self.recheck_selected())
            return
        output_cap = self.config.get('max_output_tokens', 0)
        budget = self.config.get('session_token_budget', 0)
        message = (f'将复核 {len(indices)} 条批注，预计输入约 {estimate["estimated_input_tokens"]:,} token，'
                   f'单次输出上限 {output_cap:,} token，任务预算 {budget:,} token。\n'
                   '本次只核对所选批注：原报告的分值、批注文本与修改稿一律不改动，复核结论另外保存。\n'
                   '复核属于额外模型调用，可能再次产生费用。是否继续？')
        from ..common import confirm_billable
        if not confirm_billable(self, '确认复核所选批注', message):
            return
        try:
            service = self.get_service(require_config=True)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('复核发送前检查', str(exc), lambda: self.recheck_selected())
            return
        frozen = copy.deepcopy(review)

        def operation(progress, cancel):
            result = service.recheck_annotations(frozen, indices, progress, cancel)
            service.save_recheck(result)
            return result

        self.start_job('复核所选批注', operation,
                       lambda result: self.recheck_done(frozen, result),
                       retry=lambda: self.recheck_selected(), domains=('history', 'usage'))

    def recheck_done(self, review, result):
        counts = {'uphold': 0, 'revise': 0, 'unclear': 0}
        verdicts = result.get('verdicts') if isinstance(result, dict) else {}
        for verdict in (verdicts or {}).values():
            decision = verdict.get('decision') if isinstance(verdict, dict) else None
            if decision in counts:
                counts[decision] += 1
        total = sum(counts.values())
        self.notify(f'复核完成：{total} 条（维持 {counts["uphold"]}，建议修正 {counts["revise"]}，'
                    f'存疑 {counts["unclear"]}）；原报告分数与批注未改动。')
        self.show_review(review)

    def generate_revision(self):
        """Generate revisions only; the stored score and annotations are reused."""
        review = self.current_review
        if not isinstance(review, dict) or not review.get('id'):
            self.notify('请先载入已保存的训练报告。')
            return
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        revisions = review.get('revisions') if isinstance(review.get('revisions'), dict) else {}
        if str(revisions.get('minimal') or '').strip() or str(revisions.get('model') or '').strip():
            self.notify('本次已有修改稿，无需重复生成。')
            return
        try:
            from ...model_diagnostics import preflight_request
            from ...service import AppService, REVISION_SYSTEM
            estimate = preflight_request(self.config, REVISION_SYSTEM, AppService.revision_payload(review),
                                         output_limit=self.declared_output_limit(self.config))
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('修改稿发送前检查', str(exc), lambda: self.generate_revision())
            return
        output_cap = self.config.get('max_output_tokens', 0)
        budget = self.config.get('session_token_budget', 0)
        message = (f'将生成两种修改稿，预计输入约 {estimate["estimated_input_tokens"]:,} token，'
                   f'单次输出上限 {output_cap:,} token，任务预算 {budget:,} token。\n'
                   '本次只调用改写模型：沿用已保存的原答与评分，不重新评分，原有分数与批注保持不变。\n'
                   '修改稿属于额外模型调用，可能再次产生费用。是否继续？')
        from ..common import confirm_billable
        if not confirm_billable(self, '确认生成修改稿', message):
            return
        try:
            service = self.get_service(require_config=True)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('修改稿发送前检查', str(exc), lambda: self.generate_revision())
            return
        self.start_job('生成修改稿',
                       lambda progress, cancel: service.retry_revisions(
                           review['id'], progress, cancel, expected_review=copy.deepcopy(review)),
                       self.show_review, retry=lambda: self.generate_revision(),
                       domains=('history', 'usage'))

    def fit_trace_rows(self):
        """Give each evidence row enough height for every wrapped column."""
        tree = getattr(self, 'result_trace', None)
        if tree is None or not tree.topLevelItemCount():
            return
        widths = [max(80, tree.columnWidth(column) - 18) for column in range(3)]
        if widths[1] < 120:
            widths[1] = max(180, tree.viewport().width() - widths[0] - widths[2] - 24)
        metrics = tree.fontMetrics()

        def cell_height(text, width):
            total = 0
            for line in str(text).split('\n'):
                rect = metrics.boundingRect(QRect(0, 0, width, 100000),
                                             Qt.TextFlag.TextWordWrap, line or ' ')
                total += max(metrics.height(), rect.height())
            return total

        def fit(item, depth=0):
            cell_widths = [max(60, widths[0] - depth * 18), widths[1], widths[2]]
            height = max(cell_height(item.text(column), cell_widths[column])
                         for column in range(3))
            item.setSizeHint(0, QSize(0, max(30, height + 10)))
            for index in range(item.childCount()):
                fit(item.child(index), depth + 1)

        for index in range(tree.topLevelItemCount()):
            fit(tree.topLevelItem(index))

    def refresh_trace(self, review, rechecks=None):
        """Fill the evidence tree from the same rows the report HTML uses."""
        from ...assessment_trace import dimension_score_text, trace_rows, verification_text
        rows = trace_rows(review, rechecks)
        tree = self.result_trace
        tree.clear()
        for row in rows:
            dimension = row['dimension']
            score = dimension_score_text(dimension).replace('得分 ', '得分').replace(' / ', '/')
            score = score.replace(' · ', '·').replace('权重 ', '权重')
            title = dimension['name'] + ('　' + score if score else '')
            if row['unassigned']:
                title += '（批注未对应本次量表维度）'
            parent = QTreeWidgetItem([title, '为什么是这个分：' + (dimension['reason'] or '未写明'), ''])
            parent.setToolTip(1, dimension['reason'] or '未写明评分理由')
            if row['pending']:
                parent.setText(2, '待核实')
                font = parent.font(2); font.setBold(True); parent.setFont(2, font)
            tree.addTopLevelItem(parent)
            for item in row['items']:
                refs = '；'.join(f'[{ref["id"]}] {ref["quote"]}' for ref in item['material_refs'])
                detail = item['issue_type_label'] + '：' + item['issue']
                if item['suggestion']:
                    detail += '\n建议：' + item['suggestion']
                if refs:
                    detail += '\n材料：' + refs
                child = QTreeWidgetItem([f'{item["annotation_index"] + 1}. {item["quote"]}', detail,
                                         '待核实' if item['needs_review'] and not item.get('recheck')
                                         else verification_text(item)])
                child.setData(0, Qt.ItemDataRole.UserRole, item['annotation_index'])
                child.setToolTip(0, item['quote'] + '\n双击可跳回原答对应位置。')
                child.setToolTip(1, detail)
                child.setToolTip(2, '；'.join(item['review_reasons']) or verification_text(item))
                if item['needs_review']:
                    font = child.font(2); font.setBold(True); child.setFont(2, font)
                parent.addChild(child)
            parent.setExpanded(True)
        self.fit_trace_rows()
        QTimer.singleShot(0, self.fit_trace_rows)

    def open_trace_item(self, item):
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0:
            return
        self.result_tabs.setCurrentWidget(self.annotated_result)
        self.annotation_list.setCurrentRow(index)

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
