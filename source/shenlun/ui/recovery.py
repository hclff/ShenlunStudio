"""Explicit recovery UI; merely opening it never creates a provider client."""
import copy
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QTextBrowser, QVBoxLayout

from .widgets import button

STAGE_LABELS = {'pending':'待开始', 'grading':'评分中断', 'scored':'原答评分已保存',
                'revising':'修改稿中断', 'completed':'已完成', 'failed':'失败',
                'cancelled':'已取消', 'abandoned':'已放弃'}


class AssessmentRecoveryMixin:
    def build_assessment_recovery_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('未完成任务')
        dialog.setObjectName('assessment_recovery_dialog')
        dialog.resize(790, 580)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        listing.setObjectName('assessment_recovery_list')
        dialog.task_list = listing
        layout.addWidget(listing, 1)
        details = QTextBrowser()
        details.setOpenLinks(False)
        details.setOpenExternalLinks(False)
        details.setObjectName('assessment_recovery_snapshot')
        layout.addWidget(details, 1)
        actions = QHBoxLayout()
        resume = button('继续', lambda: continue_selected())
        abandon = button('放弃…', lambda: abandon_selected())
        refresh = button('刷新', lambda: reload_tasks())
        actions.addWidget(resume)
        actions.addWidget(abandon)
        actions.addWidget(refresh)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def selected():
            item = listing.currentItem()
            return self.store.get_assessment_task(item.data(Qt.ItemDataRole.UserRole)) if item else None

        def show_snapshot():
            task = selected()
            resume.setEnabled(bool(task and not task.get('error') and not task['active']
                                   and task['state'] != 'abandoned'))
            abandon.setEnabled(bool(task and not task['active']))
            resume.setText('打开报告' if task and task['state'] == 'completed' else '继续')
            if not task:
                details.setPlainText('暂无任务。')
                return
            if task.get('error'):
                details.setPlainText(task['error'])
                return
            s = task['submission']
            text = '\n\n'.join(label+'\n'+s.get(key, '') for key,label in
                               [('question','题目'), ('materials','材料'), ('answer','提交原答'), ('reference','参考答案')])
            text += '\n\n实际量表\n' + json.dumps(s['rubric'], ensure_ascii=False, indent=2)
            context = task['context']
            text += '\n\n方法与参考依据\n' + json.dumps({k:context.get(k) for k in
                ('method_choice_snapshot', 'method_snapshot', 'reference_points_snapshot', 'sources')}, ensure_ascii=False, indent=2)
            if context['payload'].get('teaching_guidance'):
                text += '\n教学方法\n' + json.dumps(context['payload']['teaching_guidance'], ensure_ascii=False, indent=2)
            details.setPlainText(text)

        def reload_tasks():
            current = listing.currentItem()
            previous = current.data(Qt.ItemDataRole.UserRole) if current else None
            listing.clear()
            for task in self.store.list_assessment_tasks():
                stage = '不可继续' if task.get('error') else '运行中' if task['active'] else STAGE_LABELS[task['state']]
                item = QListWidgetItem(stage+' · '+task['updated_at'][:19].replace('T',' ')+' · '+task['title'])
                item.setData(Qt.ItemDataRole.UserRole, task['id'])
                listing.addItem(item)
                if previous == task['id']:
                    listing.setCurrentItem(item)
            if listing.currentRow() < 0 and listing.count():
                listing.setCurrentRow(0)
            show_snapshot()

        def continue_selected():
            task = selected()
            if task:
                if self.resume_assessment_task(task['id']):
                    dialog.accept()
                else:
                    reload_tasks()

        def abandon_selected():
            task = selected()
            if not task:
                return
            if QMessageBox.question(dialog, '放弃任务', '放弃后不能继续此任务；已保存的报告仍会保留。是否放弃？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.abandon_assessment_task(task['id'])
            except ValueError as exc:
                self.notify(str(exc))
            reload_tasks()

        listing.currentRowChanged.connect(show_snapshot)
        reload_tasks()
        return dialog

    def show_assessment_recovery(self):
        dialog = self.build_assessment_recovery_dialog()
        dialog.exec()
        dialog.deleteLater()

    def resume_assessment_task(self, task_id):
        if self.workers or self._closing:
            return False
        generation = self.store.assessment_generation()
        task = self.store.get_assessment_task(task_id)
        if not task or task.get('error') or task['state'] == 'abandoned':
            self.notify((task or {}).get('error', '任务不存在或已放弃，无法继续。'))
            return False
        if task['state'] == 'completed':
            self.show_review(self.store.get_review(task['review_id']))
            return True
        if task['active'] or self.store.assessment_tasks_active():
            self.notify('其他窗口有批改任务正在运行，请等待完成后再试。')
            return False
        message = ('原答评分已保存；本次只继续生成修改稿。' if task['review_id'] else
                   '上次评分请求的服务端执行状态未知；确认后将从评分阶段重试。')
        message += ('\n使用当时的题目、原答、量表和方法依据快照，以及当前已保存的模型设置。'
                    '\n可能再次产生费用；无法保证服务端不会重复收费。\n题目：' + task['title'] + '\n是否继续？')
        if QMessageBox.question(self, '继续批改任务', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        return self.launch_assessment_task(task_id, expected_generation=generation)

    def launch_assessment_task(self, task_id, *, expected_generation=None):
        if self.workers or self._closing:
            return False
        try:
            from ..model_diagnostics import preflight_request
            from ..service import AppService, ASSESSMENT_SYSTEM, REVISION_SYSTEM
            from ..settings import load_config
            # Recovery must keep the generation of the task the user confirmed;
            # a new assessment captures its generation at launch instead.
            generation = expected_generation if expected_generation is not None else self.store.assessment_generation()
            if generation != self.store.assessment_generation():
                self.notify('数据库已恢复，刚才的确认已失效；请重新选择任务并确认后继续。')
                return False
            task = self.store.get_assessment_task(task_id)
            if not task or task.get('error') or task['state'] in {'completed', 'abandoned'}:
                raise ValueError((task or {}).get('error', '任务状态已改变，请重新载入。'))
            if self.store.assessment_tasks_active():
                raise ValueError('其他窗口有批改任务正在运行，请等待完成后再试。')
            self.config = load_config(self.store)
            if task['review_id']:
                payload = AppService.revision_payload(self.store.get_review(task['review_id']))
                system = REVISION_SYSTEM
            else:
                payload, system = task['context']['payload'], ASSESSMENT_SYSTEM
            preflight_request(self.config, system, payload, output_limit=self.declared_output_limit(self.config))
            service = self.get_service(require_config=True)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('发送前检查', str(exc), lambda:self.resume_assessment_task(task_id))
            return False

        def show_result(review):
            current = self.store.get_assessment_task(task_id)
            if (generation != self.store.assessment_generation() or not current or current.get('error')
                    or current['review_id'] != review['id'] or self.store.get_review(review['id']) != review):
                self.notify('任务结果已失效，请重新打开训练记录。')
                return
            self.show_review(review)

        self.start_job('训练批改', lambda progress,cancel:service.assess(
            copy.deepcopy(task['submission']), progress=progress, cancel=cancel,
            grade_context=copy.deepcopy(task['context']), task_id=task_id, expected_generation=generation),
            show_result, retry=lambda:self.resume_assessment_task(task_id))
        return True
