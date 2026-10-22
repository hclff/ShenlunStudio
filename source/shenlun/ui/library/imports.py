"""Local article browsing, deliberate imports and evidence-backed methods."""
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel)

from ...importers import commit_imports, import_manifest, import_url, preview_files
from ...service.extraction import estimate_method_extraction as estimate_methods_local
from ..common.dialogs import confirm_billable
from .import_review import ImportReviewDialog

FILE_FILTER = '文章文件 (*.txt *.md *.pdf *.docx *.epub *.json)'


SKIPPED_TITLE_LIMIT = 3


def _title_note(titles, total):
    """Name the skipped articles; a bare count rarely tells the user what happened."""
    names = [str(item) for item in titles if str(item).strip()]
    if not names:
        return ''
    shown = '、'.join(names[:SKIPPED_TITLE_LIMIT])
    if max(int(total or 0), len(names)) > SKIPPED_TITLE_LIMIT:
        shown += f' 等 {max(int(total or 0), len(names))} 篇'
    return f'（{shown}）'


def import_summary(title, result):
    """Readable one-line summary; raw JSON is unhelpful in the status bar."""
    labels = (('成功', 'imported'), ('可用', 'available'), ('暂缺', 'unavailable'),
              ('重复', 'duplicates'), ('跳过已完成', 'skipped'), ('仅图片', 'image_only'))
    parts = []
    for label, key in labels:
        value = result.get(key)
        if not value:
            continue
        parts.append(f'{label} {value} 篇'
                     + _title_note(result.get(key + '_titles') or [], value))
    if result.get('processed') and 'imported' not in result:
        parts.insert(0, f"处理 {result['processed']} 篇")
    if result.get('batches'):
        parts.append(f"批次 {result['batches']}")
    failed = result.get('failed') or []
    if failed:
        detail = '；'.join(f"{item.get('path') or item.get('name') or ''}：{item.get('error')}"
                          for item in failed[:3])
        parts.append(f'失败 {len(failed)} 条（{detail}）')
    return title + '完成：' + ('，'.join(parts) if parts else '没有新增内容')


def method_extraction_summary(result):
    """Report what the paid pass produced and what still needs review."""
    parts = []
    if result.get('processed'):
        parts.append(f'提取 {result["processed"]} 篇')
    if result.get('skipped'):
        parts.append(f'复用缓存 {result["skipped"]} 篇'
                     + _title_note(result.get('skipped_titles') or [], result['skipped']))
    for label, key in (('暂缺', 'unavailable'), ('仅图片', 'image_only')):
        if result.get(key):
            parts.append(f'{label} {result[key]} 篇'
                         + _title_note(result.get(key + '_titles') or [], result[key]))
    if result.get('candidates'):
        parts.append(f'候选方法 {result["candidates"]} 条'
                     f'（新增 {result.get("created", 0)}，补充证据 {result.get("updated", 0)}）')
        if result.get('eligible'):
            parts.append(f'其中 {result["eligible"]} 条可启用')
    else:
        parts.append('没有可迁移的方法')
    failed = result.get('failed') or []
    if failed:
        detail = '；'.join(f"{item.get('title') or item.get('id') or ''}：{item.get('error')}"
                          for item in failed[:3])
        parts.append(f'失败 {len(failed)} 篇（{detail}）')
    return '提取写作方法完成：' + '，'.join(parts)


class ImportMixin:
    def import_manifest_dialog(self):
        default = str(Path(__file__).resolve().parents[3] / 'tmp' / 'pdfs' / 'qstheory-source')
        path, _ = QFileDialog.getOpenFileName(self, '选择原始文章清单', default, 'JSON (*.json)')
        if path:
            self.start_import('导入清单', lambda progress, cancel: import_manifest(Path(path), self.store, progress=progress, cancel=cancel))

    def import_local_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地文章', '', FILE_FILTER)
        if path:
            self.preview_import([path])

    def import_batch_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '批量导入本地文章', '', FILE_FILTER)
        if paths:
            self.preview_import(paths)

    def import_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, '选择要递归导入的文件夹')
        if folder:
            self.preview_import([folder])

    def preview_import(self, paths):
        """Parse first: nothing is written until the review dialog is accepted."""
        def done(result):
            # The job that produced this result is still registered while the
            # callback runs, so defer the dialog and the follow-up job.
            QTimer.singleShot(0, lambda: self.review_import(result))
        self.start_job('读取本地文件',
                       lambda progress, cancel: preview_files(paths, self.store,
                                                              progress=progress, cancel=cancel),
                       done, domains=())

    def review_import(self, preview):
        if not preview.get('items'):
            failed = preview.get('failed') or []
            detail = '；'.join(f"{item.get('name') or item.get('path')}：{item.get('error')}"
                              for item in failed[:5])
            self.notify('没有可导入的文章。' + detail)
            return
        dialog = ImportReviewDialog(preview, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        items = dialog.items()
        if not items:
            self.notify('没有勾选任何文章。')
            return
        self.start_import('导入文章', lambda progress, cancel: commit_imports(items, self.store, progress=progress, cancel=cancel))

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
            self.notify(import_summary(title, result))
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
                self._attach_skipped_titles(result)
                self.notify(import_summary('提炼所选文章', result))
            # Extraction stores analyses, candidate methods and token usage.
            self.start_job('提炼所选文章', lambda progress, cancel: service.extract(ids, progress=progress, cancel=cancel),
                           done, domains=('corpus', 'methods', 'usage'))
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def _attach_skipped_titles(self, result):
        for key in ('skipped', 'unavailable', 'image_only'):
            ids = result.get(key + '_ids') or []
            if ids:
                names = self.store.article_titles(ids[:SKIPPED_TITLE_LIMIT])
                result[key + '_titles'] = [names.get(item) or item for item in ids[:SKIPPED_TITLE_LIMIT]]
        return result

    def extract_methods_filtered(self):
        """Method-only pass over the current filter; results stay disabled candidates."""
        filters = dict(self._current_filters())
        filters.pop('order', None)
        ids = self.store.list_article_ids(**filters)
        if not ids:
            self.notify('当前筛选没有文章；请调整关键词、标签或来源后再试。')
            return
        try:
            # Estimate from local data first: declining must not read the API key.
            estimate = estimate_methods_local(self.store, ids)
            if not estimate['articles']:
                self.notify(f'当前筛选 {len(ids)} 篇，但没有可提取正文的文章；缺失或仅图片的条目会跳过。')
                return
            batches = estimate.get('estimated_batches', estimate['articles'])
            message = (f'当前筛选 {len(ids)} 篇，其中 {estimate["articles"]} 篇有正文可提取，'
                       f'共 {estimate["characters"]:,} 字符。\n'
                       f'预计输入约 {estimate["estimated_input_tokens"]:,} tokens，'
                       f'输出上限约 {batches * self.config["max_output_tokens"]:,} tokens。\n'
                       f'本次任务 token预算：{self.config["session_token_budget"]:,}。估算不等于费用上限。\n'
                       '只提取可迁移的写作方法；结果以停用候选进入方法库，不会自动启用。\n'
                       '已提取过的文章会复用缓存；取消后可再次执行续传。是否开始？')
            # Same billable-upload guard as full extraction: default No.
            if not confirm_billable(self, '确认批量提取写作方法', message):
                return
            service = self.get_service(require_config=True)
            def done(result):
                self._attach_skipped_titles(result)
                self.notify(method_extraction_summary(result))
                if result.get('candidates') and not getattr(self, '_closing', False):
                    QTimer.singleShot(0, self.open_candidate_pool)
            self.start_job('批量提取写作方法',
                           lambda progress, cancel: service.extract_methods(ids, progress=progress, cancel=cancel),
                           done, domains=('methods', 'usage'))
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))
