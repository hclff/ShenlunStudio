"""Controlled evaluation UI reusing the existing cancellable job/consent lifecycle."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox,QFileDialog,QLabel,QListWidget,QListWidgetItem,QMessageBox,QPlainTextEdit,QVBoxLayout,QWidget

from ..method_comparison import (plan_method_comparison,run_method_comparison,method_comparison_report,
    method_comparison_text,method_comparison_html)
from .widgets import button,row
from .workers import Job


class MethodComparisonUiMixin:
    def build_method_comparisons(self):
        page=QWidget();layout=QVBoxLayout(page)
        notice=QLabel('使用同一批作答对比“关闭方法”和指定组合；分数变化仅供参考。')
        notice.setWordWrap(True);layout.addWidget(notice)
        self.comparison_preset=QComboBox()
        self.comparison_run_button=button('估算并运行两组对照…',self.start_method_comparison,True)
        self.comparison_cancel_button=button('取消对照',self.cancel_run);self.comparison_cancel_button.hide()
        layout.addLayout(row(QLabel('指定方法组合'),self.comparison_preset,self.comparison_run_button,self.comparison_cancel_button))
        self.comparison_list=QListWidget();self.comparison_list.setMaximumHeight(100);layout.addWidget(self.comparison_list)
        self.comparison_detail=QPlainTextEdit();self.comparison_detail.setReadOnly(True);layout.addWidget(self.comparison_detail,1)
        self.comparison_json_button=button('导出对照 JSON…',lambda:self.export_method_comparison())
        self.comparison_html_button=button('导出对照 HTML…',lambda:self.export_method_comparison(html=True))
        self.comparison_refresh_button=button('刷新组合与对照',self.refresh_method_comparisons)
        layout.addLayout(row(self.comparison_json_button,self.comparison_html_button,self.comparison_refresh_button))
        self.comparison_list.currentItemChanged.connect(self.show_method_comparison)
        self.tabs.addTab(page,'方法对照评测')

    def refresh_method_comparisons(self,*args,selected=None):
        if not hasattr(self,'comparison_list'):return
        preset_id=self.comparison_preset.currentData();self.comparison_preset.blockSignals(True);self.comparison_preset.clear()
        self.comparison_preset.addItem('请选择已保存的方法组合',None)
        for preset in self.store.list_method_presets():
            self.comparison_preset.addItem(preset['name']+' · '+preset['question_type'],preset['id'])
        index=self.comparison_preset.findData(preset_id)
        self.comparison_preset.setCurrentIndex(max(0,index));self.comparison_preset.blockSignals(False)
        current=self.comparison_list.currentItem()
        identity=selected or (current.data(Qt.ItemDataRole.UserRole) if current else None)
        self.comparison_list.blockSignals(True);self.comparison_list.clear()
        for group in self.store.list_method_comparisons(self.set_id()):
            item=QListWidgetItem(group['preset_snapshot']['name']+' · '+group['created_at'])
            item.setData(Qt.ItemDataRole.UserRole,group['id']);self.comparison_list.addItem(item)
            if group['id']==identity:self.comparison_list.setCurrentItem(item)
        if self.comparison_list.currentRow()<0:self.comparison_list.setCurrentRow(0)
        self.comparison_list.blockSignals(False);self.show_method_comparison()

    def show_method_comparison(self,*args):
        item=self.comparison_list.currentItem()
        available=item is not None
        self.comparison_json_button.setEnabled(available and not self._busy)
        self.comparison_html_button.setEnabled(available and not self._busy)
        if not item:
            self.comparison_detail.setPlainText('尚无对照。请先保存适用的方法组合。');return
        try:
            report=method_comparison_report(self.store,item.data(Qt.ItemDataRole.UserRole))
            self.comparison_detail.setPlainText(method_comparison_text(report))
        except (ValueError,TypeError,KeyError) as exc:self.comparison_detail.setPlainText(str(exc))

    def set_method_comparison_busy(self,busy):
        if not hasattr(self,'comparison_list'):return
        for control in (self.comparison_preset,self.comparison_run_button,self.comparison_list,
                        self.comparison_json_button,self.comparison_html_button,self.comparison_refresh_button):
            control.setEnabled(not busy)
        self.comparison_cancel_button.setVisible(busy);self.comparison_cancel_button.setEnabled(busy)

    def start_method_comparison(self):
        if self._jobs or self._busy or self._close_pending or self._reviewing:return
        if getattr(self.host,'workers',set()):self.say('请先完成或取消其他运行中的任务。');return
        try:
            identifiers=self.selected_case_ids();repeats=self.repeats.value();target=self.set_id();preset_id=self.comparison_preset.currentData()
            config=deepcopy(self.host.config)
            plan=plan_method_comparison(self.store,target,identifiers,repeats,config,preset_id)
            text=(f'接口：{plan["baseline"]["config"]["base_url"]}\n模型：{plan["baseline"]["config"]["model"]}\n'
                f'固定 {len(identifiers)} 份原答，两组各重复 {repeats} 次，共 {plan["calls"]} 次模型评分调用。\n'
                f'两组：关闭方法 / {plan["preset_snapshot"]["name"]}（v{plan["preset_snapshot"]["version"]}）。\n'
                f'两组合计估算上限 {plan["estimated_total_tokens"]} token；任务预算 {plan["baseline"]["config"]["session_token_budget"]}。\n'
                '完整题干、材料、原答与冻结评分依据会发送至上述服务，可能产生费用。\n'
                '取消停止后续调用；服务端已收到请求仍可能处理并计费。不会生成改写。\n'
                '没有独立人工评分时，只比较分数与波动，不计算准确率。是否明确上传并执行两组对照？')
            self._reviewing=True
            try:
                accepted=QMessageBox.question(self,'确认上传与费用 · 方法对照',text,
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    defaultButton=QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
            finally:self._reviewing=False
            if not accepted or self._close_pending or self._jobs or getattr(self.host,'workers',set()):return
            service=self.host.get_service(require_config=True)
            group=self.store.create_method_comparison(target,identifiers,repeats,config,preset_id,expected_plan_hash=plan['plan_hash'])
            self._run_id=group['baseline_run_id'];self.refresh_method_comparisons(selected=group['id'])
            job=Job(lambda progress,cancel:run_method_comparison(self.store,service,group['id'],progress=progress,cancel=cancel),self)
            self._jobs.add(job);self.host.workers.add(job)
            job.progress.connect(self.job_progress);job.failed.connect(self.job_failed);job.finished.connect(lambda:self.job_finished(job))
            self.set_busy(True);self.tabs.setCurrentIndex(3);self.say('正在执行关闭方法与指定组合两组对照…');job.start()
        except Exception as exc:self.say(exc)

    def export_method_comparison(self,path=None,*,html=False):
        if self._busy:return
        item=self.comparison_list.currentItem()
        if not item:self.say('请先选择一个已保存的方法对照。');return
        try:
            report=method_comparison_report(self.store,item.data(Qt.ItemDataRole.UserRole))
            if not path:
                path,_=QFileDialog.getSaveFileName(self,'导出方法对照','方法对照.'+('html' if html else 'json'),
                    'HTML (*.html)' if html else 'JSON (*.json)')
            if not path:return
            Path(path).write_text(method_comparison_html(report) if html else json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            self.say('已导出两组完整快照、逐次结果、配对统计及所用人工评分版本。')
        except Exception as exc:self.say(exc)
