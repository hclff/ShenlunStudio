"""Compact task failures with opt-in plain-text details and deliberate retries."""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ..settings import get_api_key
from .widgets import button, row


class TaskFeedbackMixin:
    def build_task_feedback(self):
        self._retry_task = None
        self.failure_panel = QWidget()
        layout = QVBoxLayout(self.failure_panel)
        layout.setContentsMargins(24, 6, 24, 6)
        layout.setSpacing(6)
        self.failure_summary = QLabel()
        self.failure_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.failure_summary.setWordWrap(True)
        layout.addWidget(self.failure_summary)
        self.retry_task_button = button('重试本次任务', self.retry_failed_task)
        self.failure_details_button = button('查看详情', self.toggle_failure_details)
        layout.addLayout(row(self.retry_task_button, self.failure_details_button,
                             button('收起', self.failure_panel.hide)))
        self.failure_details = QPlainTextEdit()
        self.failure_details.setReadOnly(True)
        self.failure_details.setMaximumHeight(95)
        self.failure_details.hide()
        layout.addWidget(self.failure_details)
        self.failure_panel.hide()
        return self.failure_panel

    def clear_task_feedback(self):
        self._retry_task = None
        self.failure_panel.hide()
        self.failure_details.hide()
        self.failure_details_button.setText('查看详情')

    def show_task_failure(self, stage, message, retry=None):
        # Never render upstream text as HTML or expose credentials in error details.
        text = str(message)[:4000]
        key = get_api_key()
        if key: text = text.replace(key, '[密钥已隐藏]')
        text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [已隐藏]', text)
        advice = '请核对设置或稍后重试。'
        if '预算' in text: advice = '请提高任务预算或降低单次输出上限。'
        elif '字符' in text: advice = '请缩小材料或提高输入字符上限。'
        elif 'JSON' in text or '截断' in text: advice = '请检测 JSON 兼容性，并核对单次输出上限。'
        elif '401' in text or '403' in text: advice = '请核对密钥、模型权限及账户状态。'
        self.failure_summary.setText('失败环节：'+str(stage)[:120]+'　'+advice+' 当前编辑内容保留。')
        self.failure_details.setPlainText(text)
        self.failure_details.hide()
        self.failure_details_button.setText('查看详情')
        self._retry_task = retry
        self.retry_task_button.setVisible(retry is not None)
        self.retry_task_button.setEnabled(not self.workers)
        self.failure_panel.show()
        self.notify(self.failure_summary.text())
        self.set_task_state('error')

    def toggle_failure_details(self):
        show = self.failure_details.isHidden()
        self.failure_details.setVisible(show)
        self.failure_details_button.setText('隐藏详情' if show else '查看详情')

    def retry_failed_task(self):
        if self.workers or self._closing:
            self.notify('请先完成或取消正在运行的任务。')
            return
        if self._retry_task is not None:
            self._retry_task()

    def job_progress(self, job, message):
        job.last_stage = str(message)
        self.notify(message)
