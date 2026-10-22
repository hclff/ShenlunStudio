"""Submission editor, saved report and Unicode-safe annotation navigation."""
import json
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QListWidget,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QVBoxLayout,
    QWidget)

from ...rubrics import PROFILE_CHOICES, resolve_template, submission_rubric
from ..widgets import button, editor, row


class RubricMixin:
    def selected_rubric_template(self):
        return resolve_template(self.question_type.currentText(), self.exam.currentText(),
                                self.category.text(), self.year.text(), self.rubric_profile.currentData())

    def rubric_preview(self):
        """Describe the exact current dimensions; incomplete custom text never falls back."""
        return submission_rubric(self.raw_submission())

    def update_rubric_preview(self, *args):
        # Called during bulk restore as well as editing; it only updates presentation.
        if not hasattr(self, 'rubric_summary') or not hasattr(self, 'rubric_edit'):
            return
        supported = self.rubric_profile.currentData() in dict(PROFILE_CHOICES)
        self.rubric_copy_button.setEnabled(supported and not getattr(self, '_autosave_paused', False))
        self.rubric_editor.refresh()
        if not supported:
            self.rubric_summary.setText('保存的量表选项不支持；原值仍保留，请选择有效模板后批改。')
            self.rubric_summary.setToolTip(str(self.rubric_profile.currentData()))
            return
        try:
            value = self.rubric_preview()
            metadata = value['metadata']
            if metadata['kind'] == 'custom':
                summary = metadata['name'] + ' · JSON 优先于所选模板；非官方评分规则。'
            else:
                summary = metadata['name'] + ' · 软件自定权重'
                if metadata.get('source_year'):
                    summary += '\n能力参考：' + str(metadata['source_year']) + ' 年公开大纲；'
                    summary += ('本题年份对应此能力来源。' if metadata.get('annual_verified') else
                                (self.year.text().strip() + ' 年度未核验，仅作训练参考。'
                                 if self.year.text().strip() else '未填写年份，仅作训练参考。'))
                else:
                    summary += '\n通用题型训练，无地区或年度专属能力来源。'
                for warning in metadata.get('warnings', []):
                    if '未匹配' in warning:
                        summary += '\n考试 / 类别未匹配；请补充类别或手动选择模板。'
                    elif '不一致' in warning:
                        summary += '\n手动模板与考试 / 类别不一致，请核对适用性。'
            self.rubric_summary.setText(summary)
            self.rubric_summary.setToolTip('\n'.join([metadata.get('notice', ''), *metadata.get('warnings', [])]))
        except (ValueError, TypeError) as exc:
            summary = '自定义 JSON 未完成或无效；请修正后批改。当前文字仍会自动保存。'
            self.rubric_summary.setText(summary)
            self.rubric_summary.setToolTip(str(exc))

    def build_rubric_preview_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName('rubric_preview_dialog')
        dialog.setWindowTitle('实际训练量表 · 权重与来源')
        dialog.resize(780, 600)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setObjectName('rubric_preview_browser')
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser, 1)
        try:
            value = self.rubric_preview()
            metadata = value['metadata']
            html = '<h2>' + escape(str(metadata['name'])) + '</h2>'
            html += '<p>版本：' + escape(str(metadata['version'])) + '</p>'
            html += '<p>' + escape(str(metadata.get('notice', ''))) + '</p>'
            html += '<h3>能力侧重</h3><p>' + escape(str(metadata.get('focus', ''))) + '</p>'
            if metadata.get('source_title'):
                html += '<h3>能力要求来源</h3><p>' + escape(str(metadata['source_title'])) + '</p>'
                html += '<p>来源年度：' + escape(str(metadata.get('source_year', ''))) + '；本题年份：' + escape(self.year.text() or '未填写') + '</p>'
                html += '<p>' + escape(str(metadata.get('source_access', ''))) + '</p>'
                html += '<p>来源地址（可选择复制）：<br>' + escape(str(metadata.get('source_url', ''))) + '</p>'
            else:
                html += '<p>来源：用户自定义，无官方能力来源绑定。</p>' if metadata['kind'] == 'custom' else '<p>来源：通用训练设计，无地区或年度专属能力来源。</p>'
            for warning in metadata.get('warnings', []):
                html += '<p><b>适用边界：</b>' + escape(str(warning)) + '</p>'
            html += '<h3>实际维度与权重（合计 100%）</h3>'
            for dimension in value['rubric']:
                html += '<h4>' + escape(str(dimension['name'])) + ' · ' + escape(str(dimension['weight'])) + '%</h4>'
                html += '<p>' + escape(str(dimension.get('description', '未填写维度说明'))) + '</p>'
            browser.setHtml(html)
        except (ValueError, TypeError) as exc:
            message = ('保存的量表选项不支持，请选择有效模板后批改。' if self.rubric_profile.currentData() not in dict(PROFILE_CHOICES)
                       else '自定义 JSON 未完成或无效，当前没有可用于批改的实际量表。\n请先修正 JSON。')
            browser.setPlainText(message + '\n原始草稿仍保留并自动保存。\n\n' + str(exc))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def show_rubric_preview(self):
        dialog = self.build_rubric_preview_dialog()
        dialog.exec()
        dialog.deleteLater()

    def copy_rubric_template(self):
        if self.rubric_profile.currentData() not in dict(PROFILE_CHOICES):
            return
        self.replace_rubric_draft(
            lambda: json.dumps(self.selected_rubric_template()['rubric'], ensure_ascii=False, indent=2),
            '覆盖自定义量表？', '将所选模板复制到 JSON 会替换当前量表文字（包括未完成内容）。是否覆盖？')

    def restore_rubric_template(self):
        if not self.rubric_edit.toPlainText().strip():
            return
        self.replace_rubric_draft(lambda: '', '恢复所选模板？',
            '恢复模板会替换当前自定义量表（包括未完成内容）。是否覆盖？')

    def build_rubric_json_dialog(self):
        return self.rubric_editor.build_json_dialog()

    def build_result(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.result_tabs = QTabWidget()
        self.result_summary = QTextBrowser()
        self.result_summary.setOpenLinks(False); self.result_summary.setOpenExternalLinks(False)
        self.result_summary.anchorClicked.connect(self.open_result_link)
        self.result_summary.setPlainText('完成批改后，这里显示优先改进项。')
        self.result_tabs.addTab(self.result_summary, '复盘重点')
        self.result_report = QTextBrowser()
        self.result_report.setOpenLinks(False)
        self.result_report.setOpenExternalLinks(False)
        self.result_report.anchorClicked.connect(self.open_result_link)
        self.result_report.setHtml('<h2>从一份原答开始</h2><p>完成批改后，这里会显示审题、分项评价、材料证据和下一步训练建议。</p><p>缺少题干或材料时，只诊断表达与结构，不生成训练总分。</p>')
        self.result_tabs.addTab(self.result_report, '训练报告')
        self.result_trace = QTreeWidget()
        self.result_trace.setHeaderLabels(['维度 / 证据', '依据与建议', '核实'])
        self.result_trace.setWordWrap(True)
        self.result_trace.setUniformRowHeights(False)
        self.result_trace.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.result_trace.setAlternatingRowColors(True)
        header = self.result_trace.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.result_trace.setColumnWidth(0, 240)
        self.result_trace.setColumnWidth(2, 190)
        self.result_trace.itemDoubleClicked.connect(lambda item, _column: self.open_trace_item(item))
        self.result_trace.setToolTip('按维度说明分数来源；双击证据行回到原答对应位置，分数不因单条批注单独调整。')
        self.result_tabs.addTab(self.result_trace, '评分依据')
        self.result_tabs.currentChanged.connect(lambda _index: self.fit_trace_rows())
        annotated = QSplitter()
        self.annotated_result = annotated
        self.result_original = editor(readonly=True)
        self.result_original.setPlaceholderText('批改完成后，在这里保留提交时的原答。选择右侧批注可定位对应文字。')
        self.annotation_list = QListWidget()
        self.annotation_list.setMaximumWidth(310)
        self.annotation_list.currentRowChanged.connect(self.select_annotation)
        self.annotation_list.itemChanged.connect(lambda _item: self.update_recheck_actions())
        annotated.addWidget(self.result_original)
        annotated.addWidget(self.annotation_list)
        self.result_tabs.addTab(annotated, '原答与批注')
        self.result_minimal = editor(readonly=True)
        self.result_model = editor(readonly=True)
        self.result_minimal.setPlaceholderText('完成训练批改后，这里显示保留原意的修改建议。')
        self.result_model.setPlaceholderText('完成训练批改后，这里显示按题目要求重新组织的训练参考。')
        self.result_tabs.addTab(self.result_minimal, '保留原意修改')
        self.result_tabs.addTab(self.result_model, '按题目重新组织')
        self.result_tabs.addTab(self.build_revision_diff(), '逐段修改对照')
        layout.addWidget(self.result_tabs, 1)
        self.revision_status = QLabel('暂无批改记录')
        self.revision_status.setWordWrap(True)
        self.generate_revision_button = button('生成修改稿（不重新评分）', self.generate_revision)
        self.generate_revision_button.setToolTip('仅调用改写模型，沿用已保存的原答与评分；不会重新评分或改动已有批注。')
        self.generate_revision_button.setVisible(False)
        status_row = row(self.revision_status, self.generate_revision_button)
        status_row.setStretch(0, 1)
        layout.addLayout(status_row)
        self.export_report_button = button('导出 HTML / TXT / JSON', self.export_current)
        self.export_pdf_button = button('导出 PDF', self.export_pdf)
        self.export_report_button.setEnabled(False)
        self.export_pdf_button.setEnabled(False)
        exports = row(self.export_report_button, self.export_pdf_button)
        self.recheck_button = button('复核所选批注…', self.recheck_selected)
        self.recheck_button.setEnabled(False)
        self.recheck_button.setToolTip('勾选「原答与批注」中的条目后可用；只复核所选批注，不改动原报告分数、批注文本与修改稿。')
        exports.addWidget(self.recheck_button)
        self.add_review_goals_button = button('加入复习…',self.show_review_goals)
        self.add_review_goals_button.setEnabled(False)
        self.add_review_goals_button.setToolTip('请先保存并载入报告。')
        exports.addWidget(self.add_review_goals_button)
        exports.addStretch()
        self.return_to_question_button = button('返回题目', lambda: self.assessment_tabs.setCurrentIndex(0))
        exports.addWidget(self.return_to_question_button)
        layout.addLayout(exports)
        return widget
