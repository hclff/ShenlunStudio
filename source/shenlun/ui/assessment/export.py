"""Submission editor, saved report and Unicode-safe annotation navigation."""
import json
from pathlib import Path

from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QFileDialog

from ...domain import rubric_fingerprint, validate_rubric
from ...rubrics import resolve_template
from ...reports import export_review, review_html


class ExportMixin:
    def save_draft(self):
        try:
            value = self.raw_submission()
            for key in ('question', 'materials', 'answer', 'reference'):
                value[key] = value[key].replace('\r\n', '\n').replace('\r', '\n').strip()
            self.store.set_setting('draft', value)
            self.notify('题目和原答草稿已保存在本机。')
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def _set_submission_fields(self, value):
        for field in ('question', 'materials', 'answer', 'reference'):
            getattr(self, field + '_edit').setPlainText(value.get(field, ''))
        self.question_type.setCurrentText(value.get('question_type', '归纳概括'))
        self.exam.setCurrentText(value.get('exam', '通用训练'))
        self.year.setText(str(value.get('year', '')))
        self.category.setText(value.get('category', ''))
        selection = value.get('rubric_selection', 'auto')
        selection_index = self.rubric_profile.findData(selection)
        if selection_index < 0:
            self.rubric_profile.addItem('不支持的保存选项：' + str(selection)[:24], selection)
            selection_index = self.rubric_profile.count() - 1
            self.rubric_profile.setItemData(selection_index, str(selection), Qt.ItemDataRole.ToolTipRole)
        self.rubric_profile.setCurrentIndex(selection_index)
        self.max_score.setValue(int(value.get('max_score', 20)))
        self.word_limit.setValue(int(value.get('word_limit', 0)))
        self.rubric_edit.setPlainText(self.restored_rubric_text(value))

    def restored_rubric_text(self, value):
        """Only an exact current builtin can be represented by the picker alone."""
        raw_text = value.get('rubric_text', '')
        if raw_text:
            return raw_text
        if 'rubric' not in value:
            return raw_text
        actual = value['rubric']
        try:
            resolved = resolve_template(value.get('question_type', '归纳概括'), value.get('exam', ''),
                                        value.get('category', ''), value.get('year', ''), value.get('rubric_selection', 'auto'))
            if (value.get('rubric_template') == resolved['metadata']
                    and rubric_fingerprint(validate_rubric(actual)) == rubric_fingerprint(resolved['rubric'])):
                return ''
        except (ValueError, TypeError):
            pass
        return json.dumps(actual, ensure_ascii=False, indent=2)

    def load_draft(self):
        value = self.store.get_setting('draft')
        if value:
            self.load_submission(value)
            self.assessment_tabs.setCurrentIndex(0)
            self.notify('本机草稿已载入。')
        else:
            self.notify('尚无保存的草稿。')

    def load_example(self):
        if not self.load_submission({'question':'【自编练习，非真题】根据材料，概括便民服务站的做法与成效。不超过150字。',
            'materials':'某社区便民服务站开通电话报修渠道，为不会使用智能手机的老人登记需求。工作人员将需求派发给维修志愿者，并在服务后电话回访。\n服务站发现夜间照明报修较多，便与物业协商定期巡检，减少同类故障。',
            'answer':'社区开通电话报修渠道，方便老人反映需求；工作人员派单、回访，形成服务闭环；协同物业定期巡检，由事后维修转向主动预防。',
            'question_type':'归纳概括','exam':'通用训练','max_score':20,'word_limit':150}):
            return
        self.assessment_tabs.setCurrentIndex(0)
        self.notify('已载入自编练习示例；点击批改才会调用已配置的模型。')

    def export_current(self):
        if not self.current_review:
            self.notify('请先选择一条训练记录。')
            return
        path, selected = QFileDialog.getSaveFileName(self, '导出训练报告', '申论训练报告.html', 'HTML (*.html);;纯文本 (*.txt);;JSON (*.json)')
        if path:
            try:
                if not Path(path).suffix:
                    path += '.json' if 'JSON' in selected else '.txt' if '文本' in selected else '.html'
                export_review(self.current_review, Path(path))
                self.notify('报告已导出：' + path)
            except (ValueError, OSError) as exc:
                self.notify(str(exc))

    def export_pdf(self):
        if not self.current_review:
            self.notify('请先选择一条训练记录。')
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出 PDF', '申论训练报告.pdf', 'PDF (*.pdf)')
        if path:
            try:
                self.write_pdf(Path(path))
                self.notify('PDF 已导出：' + path)
            except (OSError, ValueError) as exc:
                self.notify(str(exc))

    def write_pdf(self, path):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                        QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter))
        document = QTextDocument()
        document.setHtml(review_html(self.current_review))
        document.print_(printer)
        if not Path(path).exists() or Path(path).stat().st_size == 0:
            raise OSError('PDF 未能写入，请检查路径。')
