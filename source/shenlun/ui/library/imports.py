"""Local article browsing, deliberate imports and evidence-backed methods."""
import json
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMessageBox)

from ...importers import import_file, import_manifest, import_url
from ..common.dialogs import confirm_billable


class ImportMixin:
    def import_manifest_dialog(self):
        default = str(Path(__file__).resolve().parents[3] / 'tmp' / 'pdfs' / 'qstheory-source')
        path, _ = QFileDialog.getOpenFileName(self, '选择原始文章清单', default, 'JSON (*.json)')
        if path:
            self.start_import('导入清单', lambda progress, cancel: import_manifest(Path(path), self.store, progress=progress, cancel=cancel))

    def import_local_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地文章', '', '文章文件 (*.txt *.md *.pdf *.json)')
        if path:
            self.start_import('导入本地文章', lambda progress, cancel: import_file(Path(path), self.store, progress=progress, cancel=cancel))

    def import_url_dialog(self):
        value, ok = QInputDialog.getText(self, '导入官方原文', '求是网或人民网文章 HTTP(S) 地址：')
        if ok and value.strip():
            self.start_import('导入官方原文', lambda progress, cancel: import_url(value.strip(), self.store, progress=progress, cancel=cancel))

    def import_people_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入人民日报评论 / 理论版')
        layout = QFormLayout(dialog)
        layout.addRow(QLabel('按日期导入评论与理论版，已有日期会续传；可随时取消。'))
        start = QDateEdit(QDate.currentDate().addDays(-6))
        end = QDateEdit(QDate.currentDate())
        for control in (start, end):
            control.setCalendarPopup(True)
            control.setDisplayFormat('yyyy-MM-dd')
        layout.addRow('开始日期', start)
        layout.addRow('结束日期', end)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            first, last = start.date().toPython(), end.date().toPython()
            if first > last:
                self.notify('开始日期不能晚于结束日期。')
                return
            self.start_import('导入人民日报日期范围', lambda progress, cancel: self.import_people_chunks(first, last, progress, cancel))

    def import_people_chunks(self, first, last, progress, cancel):
        # Read through the package so a host that swaps the importer is honoured.
        from . import import_people_range as fetch_range
        totals = {}
        current = first
        while current <= last and not cancel.is_set():
            finish = min(current + timedelta(days=30), last)
            progress(f'导入 {current.isoformat()} 至 {finish.isoformat()}…')
            result = fetch_range(current.isoformat(), finish.isoformat(), self.store, progress=progress, cancel=cancel)
            for key, value in result.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
                elif isinstance(value, list):
                    totals.setdefault(key, []).extend(value)
            current = finish + timedelta(days=1)
        return totals

    def start_import(self, title, operation):
        def done(result):
            self.notify(title + '完成：' + json.dumps(result, ensure_ascii=False))
        # Imports only add corpus rows; other pages stay untouched.
        self.start_job(title, operation, done, domains=('corpus',))

    def extract_selected(self):
        ids = self.selected_article_ids()
        if not ids:
            self.notify('请先在表格中选择需要提炼的文章，可按 Ctrl 多选。')
            return
        try:
            service = self.get_service(require_config=True)
            estimate = service.estimate_extraction(ids)
            if not estimate['articles']:
                self.notify('所选条目没有可提取正文；缺失或仅图片的条目会跳过。')
                return
            budget = self.config['session_token_budget']
            batches = estimate.get('estimated_batches', estimate['articles'])
            message = (f'将发送所选 {estimate["articles"]} 篇文章正文，共 {estimate["characters"]:,} 字符。\n'
                       f'预计输入约 {estimate["estimated_input_tokens"]:,} tokens，输出上限约 {batches * self.config["max_output_tokens"]:,} tokens。\n'
                       f'本次任务 token预算：{budget:,}。估算不等于费用上限。\n已完成的提炼会跳过；取消后可再次选择续传。是否开始？')
            # Billable upload: default No, so Enter cannot start a paid run.
            if not confirm_billable(self, '确认模型批量提炼', message):
                return
            def done(result):
                self.notify('提炼完成：' + json.dumps(result, ensure_ascii=False))
            # Extraction stores analyses, candidate methods and token usage.
            self.start_job('提炼所选文章', lambda progress, cancel: service.extract(ids, progress=progress, cancel=cancel),
                           done, domains=('corpus', 'methods', 'usage'))
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))
