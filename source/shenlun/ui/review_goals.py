"""Explicit local selection of existing summary evidence into the existing plan."""
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from ..review_goals import review_goal_basis, review_goal_candidates
from .review_plans import ReviewPlanDialog, goal_text


class ReviewGoalsDialog(ReviewPlanDialog):
    def __init__(self, host):
        super().__init__(host.store,None,host,review_context=True)
        try:
            self.candidate_values = review_goal_candidates(self.review)
            if not self.candidate_values: raise ValueError('本报告暂无有效优先改进项。')
            self.setWindowTitle('加入复习'); self.resize(760,620); self.setMinimumSize(640,520)
            self.candidates = QListWidget(); self.candidates.setMaximumHeight(100)
            self.candidate_detail = QPlainTextEdit(); self.candidate_detail.setReadOnly(True)
            self.candidate_detail.setMinimumHeight(85)
            self.goal_tabs = QTabWidget()
            panel = QWidget(); candidate_layout = QVBoxLayout(panel)
            candidate_layout.addWidget(self.candidates); candidate_layout.addWidget(self.candidate_detail,1)
            self.layout().replaceWidget(self.goal_panel,self.goal_tabs)
            self.goal_tabs.addTab(panel,'勾选本次训练目标')
            self.goal_tabs.addTab(self.goal_panel,'已保存目标 ('+str(len(self.retained_goals))+')')
            for goal in self.candidate_values:
                item = QListWidgetItem(goal['title']); item.setData(Qt.ItemDataRole.UserRole,goal['id'])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked); self.candidates.addItem(item)
            self.candidates.currentRowChanged.connect(self.show_candidate)
            self.candidates.setCurrentRow(0)
            # Existing goals are maintained by the atomic append; removals belong to plan editing.
            self.remove_goal_button.setVisible(False)
        except Exception:
            if self.workspace_guard: self.workspace_guard.release()
            raise

    def show_candidate(self,index):
        if 0 <= index < len(self.candidate_values):
            self.candidate_detail.setPlainText(goal_text(self.candidate_values[index]))

    def has_changes(self):
        return super().has_changes() or (hasattr(self,'candidates') and any(
            self.candidates.item(i).checkState() == Qt.CheckState.Checked for i in range(self.candidates.count())))

    def save(self):
        selected = [self.candidates.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.candidates.count())
                    if self.candidates.item(i).checkState() == Qt.CheckState.Checked]
        try:
            self.store.add_review_goals(self.review['id'],selected,self.value(),expected_version=self.plan['version'],
                expected_basis=review_goal_basis(self.review),expected_generation=self.generation,
                expected_question_id=self.question_id)
            self.accept()
        except (ValueError,TypeError,sqlite3.Error,OSError) as exc:
            self.message.setText(str(exc)+' 当前输入保留。')
            if self.workspace_guard: self.workspace_guard.protect()


class ReviewGoalsUiMixin:
    def refresh_history(self):
        super().refresh_history()
        if hasattr(self,'add_review_goals_button'): self.refresh_review_goals_entry()

    def refresh_review_goals_entry(self):
        reason = ''
        try:
            displayed = self.current_review or {}
            rid = displayed.get('id')
            report = self.store.get_review(rid) if rid else None
            qid = self.store.question_for_review(rid) if report else None
            if not report: reason = '请先保存并载入报告。'
            elif not qid or not self.store.get_question(qid): reason = '本报告没有有效关联题目。'
            elif review_goal_basis(displayed) != review_goal_basis(report): reason = '报告已变化，请重新载入。'
            elif not review_goal_candidates(report): reason = '本报告暂无有效优先改进项。'
        except (ValueError,TypeError): reason = '报告无法核对，请重新载入。'
        self.add_review_goals_button.setEnabled(not reason)
        self.add_review_goals_button.setToolTip(reason)

    def build_review_goals_dialog(self):
        return ReviewGoalsDialog(self)

    def show_review_goals(self):
        if self.workers:
            self.notify('请先完成或取消当前任务。'); return
        try:
            dialog = self.build_review_goals_dialog()
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.refresh_questions(); self.notify('训练目标已加入复习计划。')
            dialog.deleteLater()
        except (ValueError,TypeError) as exc: self.notify(str(exc))
        self.refresh_review_goals_entry()
