"""Local practice workspace, question bank and bound human observations."""
import math
from html import escape

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..practice import comparison, question_fingerprint
from .widgets import button, page, row


class HumanScoreDialog(QDialog):
    def __init__(self, review, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('记录人工评分'); self.resize(530, 360)
        form = QFormLayout(self)
        assessment = review.get('assessment', {})
        form.addRow(QLabel('绑定所选记录的原答；自行填写的分数不等于独立阅卷。'))
        self.score = QDoubleSpinBox(); self.score.setDecimals(2)
        self.score.setRange(0, float(assessment['max_score']))
        self.reason = QLineEdit(); self.reviewer = QLineEdit()
        self.independent = QCheckBox('我确认该评分来自独立人工阅卷（自行声明）')
        existing = existing or {}
        if existing.get('stale'):
            notice = QLabel('原答或评分条件已更改，需要重新评阅。旧人工分数已从误差统计中排除。')
            notice.setWordWrap(True); form.addRow(notice)
        self.score.setValue(float(existing.get('human', existing.get('score', 0))))
        self.reason.setText(existing.get('reason', '')); self.reviewer.setText(existing.get('reviewer', ''))
        self.independent.setChecked(bool(existing.get('independent', False)))
        form.addRow('人工分数', self.score); form.addRow('评分理由 / 来源', self.reason)
        form.addRow('评分人', self.reviewer); form.addRow(self.independent)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存评分')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)


from .review_plans import ReviewPlanUiMixin


class PracticeMixin(ReviewPlanUiMixin):
    def initialize_workspace(self):
        self.current_question_id = None
        self._workspace_bulk = False; self._workspace_dirty = False; self._autosave_paused = False
        self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(900); self.autosave_timer.timeout.connect(self.flush_workspace)
        for field in ('question', 'materials', 'answer', 'reference', 'rubric'):
            getattr(self, field + '_edit').textChanged.connect(self.workspace_changed)
        for field in ('question_type', 'exam'):
            getattr(self, field).currentTextChanged.connect(self.workspace_changed)
        self.year.textChanged.connect(self.workspace_changed); self.category.textChanged.connect(self.workspace_changed)
        self.max_score.valueChanged.connect(self.workspace_changed); self.word_limit.valueChanged.connect(self.workspace_changed)
        self.rubric_profile.currentIndexChanged.connect(self.workspace_changed)
        try:
            snapshot = self.store.load_workspace()
            if snapshot:
                self.apply_workspace(snapshot, persist=False)
                self.autosave_label.setText('已恢复本机草稿')
        except Exception as exc:
            # Do not permit a blank editor to overwrite a workspace that failed to load.
            self.set_workspace_paused(True)
            self.autosave_label.setText('草稿恢复失败')
            self.notify('草稿恢复失败，请先备份数据库后重试：' + str(exc))

    def set_workspace_paused(self, paused):
        self._autosave_paused = paused
        if paused: self.autosave_timer.stop()
        for field in ('question', 'materials', 'answer', 'reference', 'rubric'):
            getattr(self, field + '_edit').setEnabled(not paused)
        for field in ('question_type', 'exam', 'year', 'category', 'max_score', 'word_limit', 'rubric_profile', 'rubric_copy_button', 'rubric_editor'):
            getattr(self, field).setEnabled(not paused)
        self.update_rubric_preview()

    def workspace_snapshot(self):
        value = {field:getattr(self, field + '_edit').toPlainText()
                 for field in ('question', 'materials', 'answer', 'reference')}
        value.update(question_type=self.question_type.currentText(), exam=self.exam.currentText(),
                     year=self.year.text(), category=self.category.text(), max_score=self.max_score.value(),
                     word_limit=self.word_limit.value(), rubric_text=self.rubric_edit.toPlainText(),
                     rubric_selection=self.rubric_profile.currentData())
        return {'submission':value, 'question_id':self.current_question_id}

    def workspace_changed(self, *args):
        if not hasattr(self, 'autosave_timer') or self._workspace_bulk or self._autosave_paused:
            return
        self._workspace_dirty = True
        self.autosave_label.setText('待保存…')
        self.autosave_timer.start()

    def flush_workspace(self, archive_previous=False):
        self.autosave_timer.stop()
        if self._autosave_paused:
            if not self._workspace_dirty: return True
            self.notify('草稿保存已暂停，请先恢复本机数据。'); return False
        if not self._workspace_dirty and not archive_previous:
            return True
        self.autosave_label.setText('保存中…')
        try:
            snapshot = self.workspace_snapshot()
            if self.current_question_id:
                question = self.store.get_question(self.current_question_id)
                if not question or question_fingerprint(question['submission']) != question_fingerprint(snapshot['submission']):
                    snapshot['question_id'] = None
            if archive_previous and self._workspace_dirty:
                # Store archives the previous persisted row. Make that row contain
                # the latest editor text before asking it to archive a replacement.
                snapshot = self.store.save_workspace(snapshot)
                self.current_question_id = snapshot.get('question_id')
            saved = self.store.save_workspace(snapshot, archive_previous=archive_previous)
            self.current_question_id = saved.get('question_id')
            self._workspace_dirty = False
            self.autosave_label.setText('已保存 · 本机草稿')
            return True
        except Exception as exc:
            self._workspace_dirty = True
            self.autosave_label.setText('保存失败 · 点击重试')
            self.notify('草稿保存失败；内容仍在编辑器中，请重试后再切换或关闭：' + str(exc))
            return False

    def apply_workspace(self, snapshot, persist=True):
        self.autosave_timer.stop(); self._workspace_bulk = True
        try:
            value = snapshot.get('submission', {})
            self._set_submission_fields(value)
            self.current_question_id = snapshot.get('question_id')
        finally:
            self._workspace_bulk = False
        self._workspace_dirty = persist
        return self.flush_workspace() if persist else True

    def load_submission(self, value, question_id=None, archive_previous=True):
        if not self.flush_workspace(archive_previous=archive_previous):
            return False
        return self.apply_workspace({'submission':value, 'question_id':question_id})

    def save_draft(self):
        self._workspace_dirty = True
        if not self.flush_workspace():
            return False
        # Keep the previous public setting for old exports, without parsing incomplete JSON.
        value = self.workspace_snapshot()['submission']
        for field in ('question', 'materials', 'answer', 'reference'):
            value[field] = value[field].replace('\r\n', '\n').replace('\r', '\n').strip()
        try:
            self.store.set_setting('draft', value)
        except Exception as exc:
            self.notify('草稿已自动保存，但兼容副本写入失败：' + str(exc)); return False
        self.notify('题目和原答草稿已保存在本机。'); return True

    def load_draft(self):
        if getattr(self, '_workspace_context_stale', False):
            return self.reload_stale_workspace()
        # Capture the chosen data before flushing the currently edited workspace.
        value = self.store.load_workspace()
        if value and self.flush_workspace(archive_previous=True):
            if self.apply_workspace(value):
                self.assessment_tabs.setCurrentIndex(0); self.notify('本机草稿已载入。')
        elif not value:
            self.notify('尚无保存的草稿。')

    def recover_workspace(self):
        if not self.flush_workspace():
            return
        values = self.store.list_workspace_history(limit=20)
        if not values:
            self.notify('暂无可恢复的旧草稿；切题或开始新作答时会自动保留。'); return
        dialog = QDialog(self); dialog.setWindowTitle('恢复最近草稿'); dialog.resize(650, 420)
        layout = QVBoxLayout(dialog); listing = QListWidget(); layout.addWidget(listing)
        for snapshot in values:
            value = snapshot['submission']
            listing.addItem(str(snapshot.get('updated_at', ''))[:19] + '　' + (value.get('question') or value.get('materials') or '未命名草稿')[:55]
                            + '\n原答 ' + str(len(value.get('answer', ''))) + ' 字')
        listing.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText('恢复选中草稿')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentRow() >= 0:
            snapshot = values[listing.currentRow()]
            if self.flush_workspace(archive_previous=True) and self.apply_workspace(snapshot):
                self.navigation.setCurrentRow(0); self.assessment_tabs.setCurrentIndex(0)
                self.notify('已恢复选中的本机草稿。')

    def build_practice(self):
        widget, layout = page('我的题库', '保存题目与草稿，独立保留每次作答。')
        self.question_search = QLineEdit(); self.question_search.setPlaceholderText('搜索题名、题干、材料、标签或来源')
        self.question_status = QComboBox(); self.question_status.addItems(['全部状态', '未练', '已练', '待重练'])
        self.question_type_filter = QComboBox(); self.question_type_filter.addItems(['全部题型','归纳概括','综合分析','提出对策','应用写作','文章写作'])
        filters = row(self.question_search, self.question_status, self.question_type_filter, button('复习清单…',self.open_review_list)); layout.addLayout(filters)
        split = QSplitter(); self.question_list = QListWidget(); self.question_list.setMinimumWidth(210)
        self.question_list.setWordWrap(True)
        layout.addLayout(self.build_practice_delete_toolbar('questions'))
        split.addWidget(self.question_list)
        detail = QWidget(); details = QVBoxLayout(detail); details.setContentsMargins(8,0,0,0)
        self.question_title = QLineEdit(); self.question_title.setPlaceholderText('题名')
        self.question_source = QLineEdit(); self.question_source.setPlaceholderText('来源')
        self.question_tags = QLineEdit(); self.question_tags.setPlaceholderText('标签，以逗号分隔')
        details.addWidget(self.question_title); details.addLayout(row(self.question_source, self.question_tags))
        details.addLayout(row(button('保存题目说明', self.save_question_metadata), button('标记待重练', self.toggle_question_retry),button('安排复习…',self.edit_selected_review_plan)))
        self.reference_points_button = button('人工参考要点…', self.open_reference_points)
        self.evaluation_button = button('小规模评测…', self.open_evaluation)
        details.addLayout(row(self.reference_points_button, self.evaluation_button))
        self.question_tabs = QTabWidget()
        self.question_detail = QTextBrowser(); self.question_detail.setOpenExternalLinks(False)
        self.question_tabs.addTab(self.question_detail, '题目与来源')
        attempts = QWidget(); attempts_layout = QVBoxLayout(attempts)
        self.attempt_list = QListWidget(); self.attempt_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attempt_list.setToolTip('按住 Ctrl 选择两条已批改作答，再比较原答与评分条件')
        attempts_layout.addWidget(self.attempt_list)
        attempts_layout.addLayout(row(button('比较选中两次作答', self.compare_attempts), button('查看所选报告', self.open_attempt_report)))
        self.question_tabs.addTab(attempts, '历次作答')
        self.comparison_browser = QTextBrowser(); self.comparison_browser.setOpenExternalLinks(False)
        self.question_tabs.addTab(self.comparison_browser, '作答对比')
        details.addWidget(self.question_tabs, 1); split.addWidget(detail); split.setSizes([300,650]); layout.addWidget(split,1)
        self.question_summary = QLabel(''); self.question_summary.setWordWrap(True); layout.addWidget(self.question_summary)
        layout.addLayout(row(button('保存当前题目', self.save_current_question), button('打开 / 继续草稿', self.open_selected_question, True),
                             button('开始新作答（清空原答）', self.new_selected_attempt), button('恢复最近草稿', self.recover_workspace)))
        self.question_list.currentItemChanged.connect(self.select_question)
        self.question_search.textChanged.connect(self.refresh_questions)
        self.question_status.currentTextChanged.connect(self.refresh_questions)
        self.question_type_filter.currentTextChanged.connect(self.refresh_questions)
        return widget

    def selected_question_id(self):
        item = self.question_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def open_reference_points(self):
        from .reference_points import ReferencePointsDialog
        if self.workers:
            self.notify('请先完成或取消当前任务。'); return
        question_id = self.selected_question_id()
        if not question_id:
            self.notify('请先保存并选择一道题目，再管理人工参考要点。'); return
        try:
            ReferencePointsDialog(self.store, question_id, self).exec()
        except Exception as exc: self.notify(str(exc))

    def open_evaluation(self):
        from .evaluation import EvaluationDialog
        if self.workers:
            self.notify('请先完成或取消当前任务。'); return
        try:
            EvaluationDialog(self).exec()
        except Exception as exc: self.notify(str(exc))

    def refresh_questions(self, *args):
        selected = self.selected_question_id()
        filters = (self.question_search.text(), self.question_status.currentText(), self.question_type_filter.currentText())
        checked = set(self.checked_practice_ids('questions')) if getattr(self, '_question_delete_filters', None) == filters else set()
        self._question_delete_filters = filters
        self.question_list.blockSignals(True); self.question_list.clear()
        values = self.store.list_questions(query=self.question_search.text(),
            status='' if self.question_status.currentIndex() == 0 else self.question_status.currentText(),
            question_type='' if self.question_type_filter.currentIndex() == 0 else self.question_type_filter.currentText())
        for value in values:
            item = QListWidgetItem(f'{value["title"]}\n{value["status"]} · 已批改 {value["attempt_count"]} 次\n' + '、'.join(value.get('tags', [])))
            item.setData(Qt.ItemDataRole.UserRole, value['id']); self.question_list.addItem(item)
            item.setCheckState(Qt.CheckState.Checked if value['id'] in checked else Qt.CheckState.Unchecked)
            if value['id'] == selected: self.question_list.setCurrentItem(item)
        self.question_list.blockSignals(False)
        self.select_question(self.question_list.currentItem())
        summary = self.store.practice_summary()
        self.question_summary.setText(f'已保存 {summary["question_count"]} 题 · 已批改 {summary["attempt_count"]} 次 · 人工评分 {summary["human_count"]} 条')
        self.refresh_practice_delete_actions()

    def select_question(self, current, previous=None):
        self.comparison_browser.clear()
        self.question_tabs.setCurrentIndex(0)
        value = self.store.get_question(current.data(Qt.ItemDataRole.UserRole)) if current else None
        self.attempt_list.clear()
        self.question_title.setText(value['title'] if value else '')
        self.question_source.setText(value.get('source', '') if value else '')
        self.question_tags.setText('、'.join(value.get('tags', [])) if value else '')
        if not value:
            self.question_detail.setPlainText('保存当前题目后，可在这里接续草稿、开始新作答和比较历次原答。'); return
        submission = value['submission']
        self.question_detail.setPlainText(f'{submission.get("question_type", "")}　{value["status"]}\n来源：{value.get("source", "")}\n\n题干\n{submission.get("question", "")}\n\n给定材料\n{submission.get("materials", "")}\n\n参考答案\n{submission.get("reference", "")}')
        for index, review in enumerate(self.store.list_question_attempts(value['id']), 1):
            assessment = review.get('assessment', {}); score = assessment.get('total_score')
            item = QListWidgetItem(f'第 {index} 次 · {str(review.get("created_at", ""))[:16]} · ' +
                ('无训练总分' if score is None else f'{score} / {assessment.get("max_score", "?")}') + '\n模型 ' + str(review.get('model', '未记录')))
            item.setData(Qt.ItemDataRole.UserRole, review['id']); self.attempt_list.addItem(item)

    def save_current_question(self):
        if not self.flush_workspace(): return
        try:
            self.current_question_id = self.store.save_question(self.raw_submission())
            self._workspace_dirty = True; self.flush_workspace(); self.refresh_questions()
            for i in range(self.question_list.count()):
                if self.question_list.item(i).data(Qt.ItemDataRole.UserRole) == self.current_question_id:
                    self.question_list.setCurrentRow(i); break
            self.notify('题目已保存；原答只保存在草稿和训练记录中。')
        except Exception as exc: self.notify('题目未保存：' + str(exc))

    def save_question_metadata(self):
        qid = self.selected_question_id()
        if not qid: self.notify('请先选择一道题。'); return
        try:
            value = self.store.get_question(qid)
            tags = self.question_tags.text().replace('、', ',').replace('，', ',').split(',')
            self.store.save_question(value['submission'], title=self.question_title.text(), source=self.question_source.text(),
                                     tags=[tag.strip() for tag in tags if tag.strip()])
            self.refresh_questions(); self.notify('题名、来源与标签已保存。')
        except Exception as exc: self.notify(str(exc))

    def toggle_question_retry(self):
        qid = self.selected_question_id()
        if qid:
            value = self.store.get_question(qid); self.store.mark_question_retry(qid, not value['needs_retry']); self.refresh_questions()

    def open_selected_question(self):
        qid = self.selected_question_id()
        if qid: self.open_question(qid)
        else: self.notify('请先保存或选择一道题。')

    def open_question(self, qid):
        if not self.flush_workspace(): return False
        value = self.store.get_question(qid)
        if not value: self.notify('题目不存在，请刷新题库。'); return False
        draft = self.store.load_question_draft(qid) or {'submission':{**value['submission'], 'answer':''}, 'question_id':qid}
        if not self.flush_workspace(archive_previous=True) or not self.apply_workspace(draft): return False
        self.navigation.setCurrentRow(0); self.assessment_tabs.setCurrentIndex(0)
        self.notify('已打开本题草稿；已批改的历次作答保留在题库。'); return True

    def new_selected_attempt(self):
        qid = self.selected_question_id()
        if qid: self.new_question_attempt(qid)
        else: self.notify('请先保存或选择一道题。')

    def new_question_attempt(self, qid):
        if not self.flush_workspace(): return False
        # The target may own a long-lived draft outside the recovery-history cap.
        # Open it first so the same guarded archival path protects that draft too.
        if qid != self.current_question_id and not self.open_question(qid): return False
        if not self.flush_workspace(archive_previous=True): return False
        value = self.store.get_question(qid)
        if not value: return False
        if not self.apply_workspace({'submission':{**value['submission'], 'answer':''}, 'question_id':qid}): return False
        self.navigation.setCurrentRow(0); self.assessment_tabs.setCurrentIndex(0)
        self.notify('已开始新作答，原答已清空；旧草稿可恢复，已批改记录完整保留。'); return True

    def open_attempt_report(self):
        selected = self.attempt_list.selectedItems()
        if selected:
            self.show_review(self.store.get_review(selected[0].data(Qt.ItemDataRole.UserRole)))

    def compare_attempts(self):
        selected = self.attempt_list.selectedItems()
        if len(selected) != 2: self.notify('请按住 Ctrl 选择两次已批改的作答。'); return
        left, right = [self.store.get_review(item.data(Qt.ItemDataRole.UserRole)) for item in selected]
        try: result = comparison(left, right)
        except (ValueError, TypeError) as exc: self.notify(str(exc)); return
        def details(review):
            a = review.get('assessment', {}); s = review.get('submission', {})
            return escape(f'模型：{review.get("model", "未记录")}；提示版本：{review.get("prompt_version", "未记录")}；'
                f'量表：{a.get("rubric_fingerprint", "未记录")}；满分：{a.get("max_score", "未记录")}；字数上限：{s.get("word_limit", "未记录")}；模式：{a.get("mode", "未记录")}')
        def evidence(review):
            a = review.get('assessment', {})
            labels = {'covered':'已覆盖', 'partial':'部分覆盖', 'missing':'未覆盖', 'uncertain':'待确认'}
            points = a.get('material_points', [])
            content = '<p>材料要点：' + str(len(points)) + ' 条；批注：' + str(len(a.get('annotations', []))) + ' 条。AI 推断需人工核验。</p>'
            for point in points:
                content += '<p>' + escape(str(point.get('point', ''))) + '：' + labels.get(point.get('coverage'), '待确认') + '；原答对应：' + escape(str(point.get('answer_quote', ''))) + '</p>'
            for note in a.get('annotations', []):
                content += '<p>批注：' + escape(str(note.get('issue', ''))) + '；建议：' + escape(str(note.get('suggestion', ''))) + '</p>'
            return content
        label = ('评分条件一致 · 估分差不证明学习增益' if result['comparable']
                 else '评分条件不同 / 缺少版本信息，不能据此计算进步。')
        html = '<h3>' + label + '</h3><p>' + escape('；'.join(result.get('reasons', []))) + '</p>'
        html += '<p>左侧原答估分：' + escape(str(result.get('left_score'))) + '；右侧原答估分：' + escape(str(result.get('right_score'))) + '</p>'
        if result['comparable']: html += '<p>估分差：' + escape(str(result.get('delta'))) + '</p>'
        html += result['answer_html']
        html += '<h4>实际评分条件</h4><p>左侧条件：' + details(left) + '</p><p>右侧条件：' + details(right) + '</p>'
        html += '<h4>左侧材料覆盖与批注</h4>' + evidence(left) + '<h4>右侧材料覆盖与批注</h4>' + evidence(right)
        self.comparison_browser.setHtml(html); self.question_tabs.setCurrentIndex(2)

    def record_human_score(self):
        item = self.review_list.currentItem()
        review = self.store.get_review(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if not review or not review.get('id'): self.notify('请先选择已保存的训练记录。'); return
        assessment = review.get('assessment', {})
        score, maximum = assessment.get('total_score'), assessment.get('max_score')
        if (not isinstance(score, (int,float)) or isinstance(score,bool) or not math.isfinite(score)
                or not isinstance(maximum,(int,float)) or not math.isfinite(maximum) or maximum <= 0):
            self.notify('表达结构诊断没有训练总分，不能绑定人工评分。'); return
        dialog = HumanScoreDialog(review, self.store.get_human_score(review['id']), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.save_human_score(review['id'], dialog.score.value(), reason=dialog.reason.text(),
                    reviewer=dialog.reviewer.text(), independent=dialog.independent.isChecked())
                self.refresh_calibration(); self.refresh_questions(); self.notify('人工评分已绑定本条记录的原答；独立阅卷标记为自行声明。')
            except Exception as exc: self.notify(str(exc))
