"""Checkbox batch controls; detail selection and unsaved editors stay independent."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QHBoxLayout,QLabel,QMessageBox,QVBoxLayout

from ..method_batch import set_methods_enabled
from .widgets import button,editor


def batch_summary(results):
    counts={key:sum(r['status']==key for r in results) for key in ('success','skipped','failed')}
    return f'批量处理完成：成功 {counts["success"]} 项，跳过 {counts["skipped"]} 项，失败 {counts["failed"]} 项。'


class MethodBatchResultDialog(QDialog):
    def __init__(self,results,parent=None):
        super().__init__(parent);self.setWindowTitle('批量处理结果');self.resize(700,450)
        layout=QVBoxLayout(self);summary=QLabel(batch_summary(results));summary.setWordWrap(True);layout.addWidget(summary)
        details=editor(readonly=True)
        statuses={'success':'成功','skipped':'跳过','failed':'失败'}
        details.setPlainText('\n\n'.join(f'【{statuses[r["status"]]}】{r["name"]}（{r["id"]}）\n{r["reason"]}' for r in results))
        layout.addWidget(details,1);layout.addWidget(button('关闭',self.accept,True))


def confirm_batch(parent,records,enabled,reviewer='',note=''):
    action='启用' if enabled else '停用'
    names='\n'.join('• '+r.get('name',r['id']) for r in records[:12])
    if len(records)>12:names+=f'\n另有 {len(records)-12} 项，以当前勾选列表为准。'
    message=f'将逐项{action}勾选的 {len(records)} 个方法：\n{names}\n'
    if reviewer:
        message+=f'\n本地审核人：{reviewer}\n审核说明：{note}\n'
        message+='后续批改会发送匹配的方法与来源。\n'
    if enabled:
        message+='\n每项仍须通过来源核验；不符合条件的项目保持原状态并列出原因。启用方法会按题型用于后续批改。'
    else:message+='\n只影响后续批改；历史报告和冻结评测保留当时的方法快照。'
    return QMessageBox.question(parent,f'确认批量{action}',message,
        QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
        defaultButton=QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes


class ArticleBatchMixin:
    def init_article_batch(self,layout):
        controls=QHBoxLayout()
        self.article_batch_select_all_button=button('全选当前列表',lambda:self.check_article_methods(True))
        self.article_batch_clear_button=button('清空选择',lambda:self.check_article_methods(False))
        self.article_batch_count=QLabel('已勾选 0 项')
        self.article_batch_enable_button=button('批量启用…',lambda:self.set_article_batch_enabled(True))
        self.article_batch_disable_button=button('批量停用…',lambda:self.set_article_batch_enabled(False))
        for control in (self.article_batch_select_all_button,self.article_batch_clear_button,self.article_batch_count,
                        self.article_batch_enable_button,self.article_batch_disable_button):controls.addWidget(control)
        controls.addStretch();layout.addLayout(controls)
        self.method_table.itemChanged.connect(self.update_article_batch_actions)
        self.last_method_batch_result=[]
        self.update_article_batch_actions()

    def checked_article_methods(self):
        checked={self.method_table.item(i,0).data(Qt.ItemDataRole.UserRole) for i in range(self.method_table.rowCount())
                 if self.method_table.item(i,0) and self.method_table.item(i,0).checkState()==Qt.CheckState.Checked}
        return [r for r in getattr(self,'cards',[]) if r['id'] in checked]

    def check_article_methods(self,checked):
        if self.method_crud_guarded():return
        self.method_table.blockSignals(True)
        for i in range(self.method_table.rowCount()):
            self.method_table.item(i,0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.method_table.blockSignals(False);self.update_article_batch_actions()

    def update_article_batch_actions(self,*args):
        if not hasattr(self,'article_batch_count'):return
        selected=self.checked_article_methods();idle=not self.workers and not getattr(self,'_closing',False)
        self.article_batch_count.setText(f'已勾选 {len(selected)} 项')
        self.article_batch_select_all_button.setEnabled(bool(idle and self.method_table.rowCount()))
        self.article_batch_clear_button.setEnabled(bool(idle and selected))
        self.article_batch_enable_button.setEnabled(bool(idle and any(not r.get('enabled') for r in selected)))
        self.article_batch_disable_button.setEnabled(bool(idle and any(r.get('enabled') for r in selected)))

    def set_article_batch_enabled(self,enabled):
        if self.method_crud_guarded():return
        records=deepcopy(self.checked_article_methods())
        if not records:self.notify('请先勾选需要批量处理的方法。');return
        if not confirm_batch(self,records,enabled) or self.method_crud_guarded():return
        self.last_method_batch_result=set_methods_enabled(self.store,'article',records,enabled)
        self.refresh_methods();self.notify(batch_summary(self.last_method_batch_result))
        MethodBatchResultDialog(self.last_method_batch_result,self).exec()


class TeachingBatchMixin:
    def init_teaching_batch(self,layout):
        controls=QHBoxLayout()
        self.batch_select_all_button=button('全选当前列表',lambda:self.check_methods(True))
        self.batch_clear_button=button('清空选择',lambda:self.check_methods(False))
        self.batch_count=QLabel('已勾选 0 项')
        self.batch_enable_button=button('批量审核启用…',lambda:self.set_batch_enabled(True))
        self.batch_disable_button=button('批量停用…',lambda:self.set_batch_enabled(False))
        for control in (self.batch_select_all_button,self.batch_clear_button,self.batch_count,
                        self.batch_enable_button,self.batch_disable_button):controls.addWidget(control)
        controls.addStretch();layout.addLayout(controls)
        self.last_batch_result=[]

    def checked_methods(self):
        checked={item.data(Qt.ItemDataRole.UserRole) for item in (
            self.method_list.item(i) for i in range(self.method_list.count())) if item.checkState()==Qt.CheckState.Checked}
        return [r for r in self.records if r['id'] in checked]

    def check_methods(self,checked):
        if self.guarded():return
        self.method_list.blockSignals(True)
        for i in range(self.method_list.count()):
            self.method_list.item(i).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.method_list.blockSignals(False);self.update_batch_actions()

    def update_batch_actions(self,*args):
        selected=self.checked_methods();idle=not self.host.workers and not getattr(self.host,'_closing',False)
        self.batch_count.setText(f'已勾选 {len(selected)} 项')
        self.batch_select_all_button.setEnabled(bool(idle and self.method_list.count()))
        self.batch_clear_button.setEnabled(bool(idle and selected))
        self.batch_enable_button.setEnabled(bool(idle and any(not r.get('enabled') for r in selected)))
        self.batch_disable_button.setEnabled(bool(idle and any(r.get('enabled') for r in selected)))

    def set_batch_enabled(self,enabled):
        if self.guarded():return
        if self.dirty:self.say('请先保存或明确放弃当前修改，再批量处理已保存版本。');return
        records=deepcopy(self.checked_methods())
        if not records:self.say('请先勾选需要批量处理的方法。');return
        reviewer=note=''
        if enabled:
            from .teaching import TeachingReviewDialog
            review=TeachingReviewDialog(self)
            if review.exec()!=QDialog.DialogCode.Accepted:return
            reviewer=review.reviewer.text().strip();note=review.note.toPlainText().strip()
            if not reviewer or not note:self.say('须填写本地审核人姓名和说明。');return
        if self.guarded() or self.dirty:return
        if not confirm_batch(self,records,enabled,reviewer,note) or self.guarded() or self.dirty:return
        self.last_batch_result=set_methods_enabled(self.store,'teaching',records,enabled,reviewer=reviewer,note=note)
        self.refresh();self.say(batch_summary(self.last_batch_result))
        MethodBatchResultDialog(self.last_batch_result,self).exec()
