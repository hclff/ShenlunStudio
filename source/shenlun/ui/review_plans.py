"""Explicit local review-plan dialogs; no model requests or implicit resolution."""
import re
import json
import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QFormLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from ..review_plans import STATUSES, question_plan
from ..review_goals import review_goal_basis
from ..state_write import state_write
from .widgets import button, row
from .workspace_guard import ModalWorkspaceGuard


def goal_text(goal):
    parts = [goal['title'],goal['action']]
    for ref in goal['refs']: parts.append('材料 ['+ref['id']+']：'+ref['quote'])
    if goal['annotation']: parts.append('原答批注摘录：'+goal['annotation']['quote'])
    return '\n'.join(parts)


class ReviewPlanDialog(QDialog):
    def __init__(self, store, question_id, parent=None, *, review_context=False):
        super().__init__(parent)
        self.store, self.question_id = store, question_id
        self.generation = store.assessment_generation()
        self.host = parent if hasattr(parent,'autosave_timer') else getattr(parent,'host',None)
        self.workspace_guard = None
        if self.host:
            # Stop a pending autosave before even the first context read can fail.
            self.workspace_guard = ModalWorkspaceGuard(self.host,self.generation)
        try:
            self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True)
            if self.workspace_guard: self.workspace_guard.report = self.message.setText
            displayed = (self.host.current_review or {}) if review_context else {}
            with state_write(store,self.generation) as conn:
                workspace = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                workspace = json.loads(workspace[0]) if workspace else None
                if self.workspace_guard: self.workspace_guard.workspace = workspace
                if review_context:
                    if not displayed.get('id'): raise ValueError('请先保存并载入一份训练报告。')
                    source = conn.execute('SELECT r.data,q.question_id FROM reviews r JOIN review_questions q '
                        'ON r.id=q.review_id WHERE r.id=?',(displayed['id'],)).fetchone()
                    if not source or not source[1]: raise ValueError('来源报告没有有效关联题目，无法加入复习。')
                    self.review = json.loads(source[0]); self.question_id = source[1]
                    if review_goal_basis(self.review) != review_goal_basis(displayed):
                        raise ValueError('当前显示报告已变化，请重新载入。')
                source = conn.execute('SELECT data FROM question_bank WHERE id=?',(self.question_id,)).fetchone()
                question = json.loads(source[0]) if source else None
                if not question: raise ValueError('题目不存在或已删除。')
                self.plan = question_plan(question)
            self._initialize_plan_ui(question)
        except Exception:
            if self.workspace_guard: self.workspace_guard.release()
            raise

    def _initialize_plan_ui(self, question):
        self.retained_goals = self.plan.get('goals',[]).copy()
        self.setWindowTitle('安排复习'); self.resize(680,600); self.setMinimumSize(560,460)
        layout = QVBoxLayout(self)
        title = QLabel(question['title']); title.setTextFormat(Qt.TextFormat.PlainText); title.setWordWrap(True)
        layout.addWidget(title)
        form = QFormLayout()
        self.status = QComboBox(); self.status.addItems(STATUSES); self.status.setCurrentText(self.plan['status'])
        form.addRow('复习状态', self.status)
        self.scheduled = QCheckBox('安排复习日期'); self.scheduled.setChecked(bool(self.plan['due_date']))
        self.due_date = QDateEdit(); self.due_date.setCalendarPopup(True); self.due_date.setDisplayFormat('yyyy-MM-dd')
        self.due_date.setDate(QDate.fromString(self.plan['due_date'],'yyyy-MM-dd') if self.plan['due_date'] else QDate.currentDate())
        self.due_date.setEnabled(self.scheduled.isChecked()); self.scheduled.toggled.connect(self.due_date.setEnabled)
        form.addRow(self.scheduled,self.due_date)
        self.tags = QLineEdit('、'.join(self.plan['tags'])); self.tags.setPlaceholderText('如：遗漏要点、概括过细、逻辑混乱')
        form.addRow('问题标签',self.tags); layout.addLayout(form)
        note = QLabel('状态由你确认；重练或分数变化不会自动标记已解决。'); note.setWordWrap(True); note.setObjectName('muted'); layout.addWidget(note)
        self.goals = QListWidget(); self.goals.setMaximumHeight(100)
        self.goal_detail = QPlainTextEdit(); self.goal_detail.setReadOnly(True)
        self.goal_detail.setMinimumHeight(85)
        self.goal_panel = QWidget(); self.goal_section = QVBoxLayout(self.goal_panel)
        self.goal_section.setContentsMargins(0,0,0,0)
        self.goal_section.addWidget(QLabel('已保存训练目标'))
        self.goal_section.addWidget(self.goals); self.goal_section.addWidget(self.goal_detail,1)
        self.open_source_button = button('打开来源报告',self.open_goal_source)
        self.remove_goal_button = button('移除所选目标',self.remove_goal)
        self.goal_section.addLayout(row(self.open_source_button,self.remove_goal_button))
        layout.addWidget(self.goal_panel,1)
        self.refresh_goals()
        self.goals.currentRowChanged.connect(self.show_goal_detail)
        layout.addWidget(self.message)
        layout.addStretch(); layout.addLayout(row(button('保存',self.save,True),button('取消',self.reject)))
        self._baseline = self.value()
        self._baseline_goals = self.retained_goals.copy()

    def refresh_goals(self):
        self.goals.clear()
        for goal in self.retained_goals: self.goals.addItem(goal['title'])
        if self.goals.count(): self.goals.setCurrentRow(0)
        else: self.goal_detail.setPlainText('暂无训练目标。')
        self.remove_goal_button.setEnabled(bool(self.retained_goals))
        self.open_source_button.setEnabled(False)
        self.show_goal_detail()

    def selected_goal(self):
        index = self.goals.currentRow()
        return self.retained_goals[index] if 0 <= index < len(self.retained_goals) else None

    def show_goal_detail(self, *_):
        goal = self.selected_goal()
        if not goal: return
        report = self.store.get_review(goal['review_id'])
        matched = bool(report and review_goal_basis(report) == goal['basis'])
        status = '来源报告可核对' if matched else '来源报告已删除' if report is None else '来源评分依据不再匹配（保留当时摘录）'
        self.goal_detail.setPlainText(goal_text(goal)+'\n\n'+status)
        self.open_source_button.setEnabled(matched and self.host is not None)

    def open_goal_source(self):
        goal = self.selected_goal()
        if not goal: return
        try:
            with state_write(self.store,self.generation) as conn:
                source = conn.execute('SELECT data FROM reviews WHERE id=?',(goal['review_id'],)).fetchone()
                report = json.loads(source[0]) if source else None
                if not report or review_goal_basis(report) != goal['basis']:
                    raise ValueError('来源报告已删除或评分依据不再匹配。')
            if self.host: self.host.show_review(report)
        except (ValueError,TypeError) as exc: self.message.setText(str(exc))
        self.show_goal_detail()

    def remove_goal(self):
        goal = self.selected_goal()
        if goal:
            self.retained_goals.remove(goal); self.refresh_goals()

    def value(self):
        return {'status':self.status.currentText(),
                'due_date':self.due_date.date().toString('yyyy-MM-dd') if self.scheduled.isChecked() else '',
                'tags':[v.strip() for v in re.split('[、，,]',self.tags.text()) if v.strip()]}

    def save(self):
        try:
            value = self.value()
            if 'goals' in self.plan or self.retained_goals: value['goals'] = self.retained_goals
            self.store.save_review_plan(self.question_id,value,expected_version=self.plan['version'],
                expected_generation=self.generation)
            self.accept()
        except (ValueError,TypeError,sqlite3.Error,OSError) as exc:
            self.message.setText(str(exc)+' 当前输入保留。')
            if self.workspace_guard: self.workspace_guard.protect()

    def has_changes(self):
        return self.value()!=self._baseline or self.retained_goals != self._baseline_goals

    def reject(self):
        if self.has_changes():
            answer = QMessageBox.question(self,'放弃未保存的复习计划','尚未保存，是否放弃本次修改？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No, defaultButton=QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:return
        super().reject()

    def done(self, result):
        if self.workspace_guard: self.workspace_guard.release()
        super().done(result)


class ReviewListDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host; self.store = host.store
        self.setWindowTitle('复习清单'); self.resize(820,570)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索题名、题干、来源或问题标签')
        self.status_filter = QComboBox(); self.status_filter.addItems(['全部状态',*STATUSES])
        self.due_filter = QComboBox(); self.due_filter.addItems(['全部日期','今天及逾期','未安排日期'])
        layout.addLayout(row(self.search,self.status_filter,self.due_filter))
        self.items = QListWidget(); self.items.setWordWrap(True); layout.addWidget(self.items,1)
        self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True); layout.addWidget(self.message)
        layout.addLayout(row(button('编辑计划…',self.edit,True),button('开始重练',self.practice),button('关闭',self.reject)))
        self.search.textChanged.connect(self.refresh); self.status_filter.currentTextChanged.connect(self.refresh)
        self.due_filter.currentIndexChanged.connect(self.refresh); self.items.itemDoubleClicked.connect(lambda _:self.edit())
        self.refresh()

    def refresh(self, *args):
        selected = self.selected_id(); self.items.clear()
        values = self.store.list_review_plans(query=self.search.text(),
            status='' if self.status_filter.currentIndex()==0 else self.status_filter.currentText(),
            due=('','due','unscheduled')[self.due_filter.currentIndex()])
        for value in values:
            plan = value['review_plan']
            item = QListWidgetItem(value['title']+'\n'+plan['status']+' · '+(plan['due_date'] or '未安排日期')+' · '+'、'.join(plan['tags']))
            item.setData(Qt.ItemDataRole.UserRole,value['id']); self.items.addItem(item)
            if value['id']==selected:self.items.setCurrentItem(item)
        if self.items.currentRow()<0 and self.items.count():self.items.setCurrentRow(0)
        self.message.setText(f'共 {len(values)} 题。已有“待重练”题目也在清单中；可编辑计划补充日期与标签。')

    def selected_id(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def edit(self):
        qid = self.selected_id()
        if not qid:return
        try:
            if ReviewPlanDialog(self.store,qid,self).exec()==QDialog.DialogCode.Accepted:
                self.refresh(); self.host.refresh_questions()
        except (ValueError,TypeError) as exc:self.message.setText(str(exc))

    def practice(self):
        qid = self.selected_id()
        if qid and self.host.new_question_attempt(qid):self.accept()


class ReviewPlanUiMixin:
    def review_plan_guard(self):
        if self.workers:
            self.notify('请先完成或取消当前任务。'); return False
        qid = self.selected_question_id()
        value = self.store.get_question(qid) if qid else None
        if value:
            tags = [v.strip() for v in re.split('[、，,]',self.question_tags.text()) if v.strip()]
            if (self.question_title.text(),self.question_source.text(),tags)!=(value['title'],value.get('source',''),value.get('tags',[])):
                self.notify('题目说明尚未保存，请先保存后再管理复习计划。'); return False
        return True

    def edit_selected_review_plan(self):
        if not self.review_plan_guard():return
        qid = self.selected_question_id()
        if not qid:self.notify('请先保存并选择一道题。');return
        try:
            if ReviewPlanDialog(self.store,qid,self).exec()==QDialog.DialogCode.Accepted:
                self.refresh_questions();self.notify('复习计划已保存。')
        except (ValueError,TypeError) as exc:self.notify(str(exc))

    def open_review_list(self):
        if not self.review_plan_guard():return
        try:ReviewListDialog(self).exec()
        except (ValueError,TypeError) as exc:self.notify(str(exc))
