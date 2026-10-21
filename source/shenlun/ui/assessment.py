"""Submission editor, saved report and Unicode-safe annotation navigation."""
import copy
import json
from html import escape
from pathlib import Path

from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextCursor, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QSpinBox, QSplitter, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget)

from ..domain import prepare_submission, rubric_fingerprint, validate_rubric
from ..rubrics import PROFILE_CHOICES, resolve_template, submission_rubric
from ..reports import export_review, review_html
from .widgets import badge, button, card, editor, open_publisher, page, row, section_label
from .intake import IntakeMixin
from .teaching import TeachingUiMixin
from .method_presets import MethodPresetUiMixin
from .review_learning import ReviewLearningMixin
from .recovery import AssessmentRecoveryMixin
from .rubric_editor import RubricEditor
from .rubric_library import RubricLibraryUiMixin
from .review_goals import ReviewGoalsUiMixin


class AssessmentMixin(ReviewGoalsUiMixin, RubricLibraryUiMixin, AssessmentRecoveryMixin, IntakeMixin, TeachingUiMixin, MethodPresetUiMixin, ReviewLearningMixin):
    def build_assessment(self):
        widget, layout = page('训练批改', '审清题意，核对材料，保留自己的原答。每次训练都可保存与回看。')
        settings_card, settings_layout = card()
        self.exercise_settings_card = settings_card
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.question_type = QComboBox()
        self.question_type.addItems(['归纳概括', '综合分析', '提出对策', '应用写作', '文章写作'])
        self.exam = QComboBox()
        self.exam.setEditable(True)
        self.exam.addItems(['江苏省考', '国考', '通用训练'])
        self.year = QLineEdit()
        self.year.setPlaceholderText('年份')
        self.year.setMaximumWidth(95)
        self.category = QLineEdit()
        self.category.setPlaceholderText('类别 / 卷别')
        self.max_score = QSpinBox()
        self.max_score.setRange(1, 1000)
        self.max_score.setValue(20)
        self.max_score.setPrefix('满分 ')
        self.word_limit = QSpinBox()
        self.word_limit.setRange(0, 10000)
        self.word_limit.setSpecialValueText('字数不限')
        self.word_limit.setSuffix(' 字')
        for control, label in ((self.question_type, '题型'), (self.exam, '考试'),
                               (self.year, '年份'), (self.category, '类别 / 卷别'),
                               (self.max_score, '题目满分'), (self.word_limit, '字数上限')):
            control.setAccessibleName(label)
            control.setToolTip(label)
        self.question_type.setMinimumWidth(108)
        self.exam.setMinimumWidth(118)
        self.category.setMaximumWidth(140)
        for control in (self.question_type, self.exam, self.year, self.category, self.max_score, self.word_limit):
            controls.addWidget(control)
        settings_layout.addLayout(controls)
        self.teaching_summary = QLabel(settings_card)
        self.teaching_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.teaching_summary.setWordWrap(True)
        self.teaching_summary.hide()  # Retain legacy state for callers; visible summary is shared below.
        self.teaching_select_button = button('方法辅助…', self.show_teaching_selection, name='teaching_select_button')
        self.question_type.currentTextChanged.connect(self.refresh_teaching_summary)
        self.refresh_teaching_summary()
        self.init_method_presets(settings_layout)
        layout.addWidget(settings_card)
        self.assessment_tabs = QTabWidget()
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(14, 14, 14, 12)
        input_layout.setSpacing(10)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        self.question_edit = self.intake_editor('question', 'question_edit', '请输入或粘贴内容…')
        self.question_edit.setMaximumHeight(110)
        self.question_edit.setMinimumHeight(70)
        self.materials_edit = self.intake_editor('materials', 'materials_edit', '请输入或粘贴内容…')
        left_layout.addWidget(section_label('01  题干与作答要求'))
        left_layout.addWidget(self.question_edit)
        left_layout.addWidget(section_label('02  给定材料'))
        left_layout.addWidget(self.materials_edit, 1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        self.answer_edit = self.intake_editor('answer', 'answer_edit', '请输入或粘贴内容…')
        self.word_count = QLabel('原答 0 字 · 含标点，不含空白')
        self.answer_edit.textChanged.connect(lambda: self.word_count.setText(f'原答 {sum(not c.isspace() for c in self.answer_edit.toPlainText())} 字 · 含标点，不含空白'))
        right_layout.addWidget(section_label('03  我的原答'))
        right_layout.addWidget(self.answer_edit, 1)
        right_layout.addWidget(self.word_count)
        self.word_count.setObjectName('muted')
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([500, 570])
        input_layout.addWidget(split, 1)
        toolbar = QHBoxLayout()
        toolbar.addWidget(button('导入题目 / 材料', lambda: self.load_input_file('materials')))
        toolbar.addWidget(button('导入原答', lambda: self.load_input_file('answer')))
        toolbar.addWidget(button('剪贴板', self.import_clipboard))
        toolbar.addStretch()
        toolbar.addWidget(button('保存草稿', self.save_draft))
        toolbar.addWidget(button('恢复草稿', self.recover_workspace))
        toolbar.addWidget(button('未完成任务…', self.show_assessment_recovery, name='assessment_recovery_button'))
        toolbar.addWidget(button('练习示例', self.load_example))
        input_layout.addLayout(toolbar)
        self.assessment_tabs.addTab(input_widget, '01 题目与原答')
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(16, 14, 16, 14)
        advanced_layout.setSpacing(8)
        advanced_split = QSplitter(Qt.Orientation.Horizontal)
        reference_panel = QWidget()
        reference_layout = QVBoxLayout(reference_panel)
        reference_layout.setContentsMargins(0, 0, 6, 0)
        reference_layout.setSpacing(8)
        reference_layout.addWidget(section_label('参考答案 · 可选'))
        self.reference_edit = self.intake_editor('reference', placeholder='请输入或粘贴内容…')
        reference_layout.addWidget(self.reference_edit, 1)
        rubric_panel = QWidget()
        rubric_layout = QVBoxLayout(rubric_panel)
        rubric_layout.setContentsMargins(6, 0, 0, 0)
        rubric_layout.setSpacing(8)
        rubric_title = QHBoxLayout()
        rubric_title.addWidget(section_label('训练量表'))
        rubric_title.addStretch()
        rubric_layout.addLayout(rubric_title)
        rubric_controls = QHBoxLayout()
        self.rubric_profile = QComboBox()
        self.rubric_profile.setObjectName('rubric_profile')
        self.rubric_profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.rubric_profile.setMinimumContentsLength(12)
        self.rubric_profile.setAccessibleName('考试与题型量表模板')
        self.rubric_profile.setToolTip('自动按考试、类别和题型匹配；自定义量表始终优先。')
        for key, label in PROFILE_CHOICES:
            self.rubric_profile.addItem(label, key)
        self.rubric_profile.setCurrentIndex(self.rubric_profile.findData('auto'))
        rubric_controls.addWidget(self.rubric_profile, 1)
        self.rubric_preview_button = button('预览实际量表', self.show_rubric_preview, name='rubric_preview_button')
        rubric_controls.addWidget(self.rubric_preview_button)
        self.rubric_library_button = button('模板库…', self.show_rubric_library, name='rubric_library_button')
        rubric_controls.addWidget(self.rubric_library_button)
        rubric_layout.addLayout(rubric_controls)
        self.rubric_summary = QLabel(rubric_panel)
        self.rubric_summary.setObjectName('rubric_summary')
        self.rubric_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.rubric_summary.setWordWrap(True)
        self.rubric_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rubric_summary.hide()  # Keep preview state without a persistent explanatory label.
        self.rubric_edit = editor(placeholder='[{"id":"…","name":"…","weight":100,"description":"…"}]')
        self.rubric_edit.setParent(rubric_panel)
        self.rubric_edit.hide()
        self.rubric_editor = RubricEditor(self.rubric_edit, self.selected_rubric_template,
            self.copy_rubric_template, self.restore_rubric_template, rubric_panel)
        self.rubric_table = self.rubric_editor.table
        rubric_title.addWidget(self.rubric_editor.total_label)
        self.rubric_copy_button = self.rubric_editor.copy_button
        self.rubric_copy_button.setToolTip('复制后按自定义量表使用；有现成文字时会先确认覆盖。')
        rubric_layout.addWidget(self.rubric_editor, 1)
        advanced_split.addWidget(reference_panel)
        advanced_split.addWidget(rubric_panel)
        advanced_split.setStretchFactor(0, 1)
        advanced_split.setStretchFactor(1, 2)
        advanced_split.setSizes([260, 500])
        advanced_layout.addWidget(advanced_split)
        for control in (self.exam, self.question_type):
            control.currentTextChanged.connect(self.update_rubric_preview)
        for control in (self.year, self.category):
            control.textChanged.connect(self.update_rubric_preview)
        self.rubric_profile.currentIndexChanged.connect(self.update_rubric_preview)
        self.rubric_edit.textChanged.connect(self.update_rubric_preview)
        self.update_rubric_preview()
        self.assessment_tabs.addTab(advanced, '02 参考与量表')
        self.result_widget = self.build_result()
        self.assessment_tabs.addTab(self.result_widget, '03 批改与对照')
        layout.addWidget(self.assessment_tabs, 1)
        self.scope_label = QLabel('主动点击才调用模型：发送本题内容及至多 8 个本地参考段落。训练估评未经真实阅卷评分校准。')
        self.scope_label.setObjectName('muted')
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.assess_button = button('开始训练批改', self.begin_assessment, True, 'assess_button')
        self.cancel_button = button('取消任务', self.cancel_jobs, name='cancel_button')
        self.cancel_button.setEnabled(False)
        self.assessment_actions = QWidget()
        actions = QHBoxLayout(self.assessment_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.assess_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(button('我的题库', lambda:self.navigation.setCurrentRow(5)))
        actions.addStretch()
        self.autosave_label = QLabel('本机草稿 · 尚无改动')
        self.autosave_label.setObjectName('muted')
        actions.addWidget(self.autosave_label)
        actions.addWidget(button('重试保存', self.save_draft))
        layout.addWidget(self.assessment_actions)
        self.assessment_tabs.currentChanged.connect(self.update_assessment_mode)
        self.update_assessment_mode(self.assessment_tabs.currentIndex())
        return widget

    def update_assessment_mode(self, index):
        editing = index != 2
        self.exercise_settings_card.setVisible(editing)
        self.scope_label.setVisible(editing)
        self.assessment_actions.setVisible(editing)

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
        annotated = QSplitter()
        self.annotated_result = annotated
        self.result_original = editor(readonly=True)
        self.result_original.setPlaceholderText('批改完成后，在这里保留提交时的原答。选择右侧批注可定位对应文字。')
        self.annotation_list = QListWidget()
        self.annotation_list.setMaximumWidth(310)
        self.annotation_list.currentRowChanged.connect(self.select_annotation)
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
        layout.addWidget(self.revision_status)
        self.export_report_button = button('导出 HTML / TXT / JSON', self.export_current)
        self.export_pdf_button = button('导出 PDF', self.export_pdf)
        self.export_report_button.setEnabled(False)
        self.export_pdf_button.setEnabled(False)
        exports = row(self.export_report_button, self.export_pdf_button)
        self.add_review_goals_button = button('加入复习…',self.show_review_goals)
        self.add_review_goals_button.setEnabled(False)
        self.add_review_goals_button.setToolTip('请先保存并载入报告。')
        exports.addWidget(self.add_review_goals_button)
        exports.addStretch()
        self.return_to_question_button = button('返回题目', lambda: self.assessment_tabs.setCurrentIndex(0))
        exports.addWidget(self.return_to_question_button)
        layout.addLayout(exports)
        return widget

    def raw_submission(self):
        rubric = self.rubric_edit.toPlainText().strip()
        value = {'question':self.question_edit.toPlainText(), 'materials':self.materials_edit.toPlainText(),
                 'answer':self.answer_edit.toPlainText(), 'question_type':self.question_type.currentText(),
                 'exam':self.exam.currentText(), 'year':self.year.text(), 'category':self.category.text(),
                 'rubric_selection':self.rubric_profile.currentData(),
                 'max_score':self.max_score.value(), 'word_limit':self.word_limit.value(),
                 'reference':self.reference_edit.toPlainText()}
        if rubric:
            value['rubric'] = validate_rubric(json.loads(rubric))
        return value

    def submission(self):
        return prepare_submission(self.raw_submission())

    def begin_assessment(self):
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        try:
            submission = self.submission()
            self.preflight_teaching(submission)
            self.refresh_teaching_summary()
            from ..method_presets import normalize_method_choice
            choice=normalize_method_choice(self.store.get_setting('method_choice'))
            context=None
            if choice['mode']!='auto':
                from ..service import AppService,ASSESSMENT_SYSTEM,PROMPT_VERSION
                from ..evaluation import canonical_hash
                offline=AppService(self.store,dict(self.config),'')
                _,payload,sources,methods,points=offline.prepare_grade(submission,method_choice=choice)
                context={'payload':payload,'sources':sources,'method_snapshot':methods,
                    'reference_points_snapshot':points,'prompt_version':PROMPT_VERSION,
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM),'method_choice_snapshot':offline._last_method_choice}
                title=offline._last_method_choice.get('name','关闭方法')
                names=['教学：'+m['name'][:45]+'（'+m['id']+'，v'+str(m['version'])+'）'
                    for m in (payload.get('teaching_guidance') or {}).get('methods',[])]
                names+=['文章：'+m['name'][:45]+'（'+m['id']+'）' for m in methods]
                message='本次选择：'+title+'\n'+('\n'.join(names) if names else '本次不使用教学方法或文章提炼方法。')
                message+='\n\n将按上述冻结名单批改；本题、材料、原答和所选依据会发送给已配置模型。是否开始？'
                if QMessageBox.question(self,'确认本次实际使用方法',message,
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
                if self.workers or getattr(self,'_closing',False):return
            if context is None:
                from ..service import AppService, ASSESSMENT_SYSTEM, PROMPT_VERSION
                from ..evaluation import canonical_hash
                offline = AppService(self.store, dict(self.config), '')
                _, payload, sources, methods, points = offline.prepare_grade(submission, method_choice=choice)
                context = {'payload':payload, 'sources':sources, 'method_snapshot':methods,
                    'reference_points_snapshot':points, 'prompt_version':PROMPT_VERSION,
                    'system_hash':canonical_hash(ASSESSMENT_SYSTEM), 'method_choice_snapshot':offline._last_method_choice}
            if not self.flush_workspace(): return
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))
            return
        self.launch_assessment_snapshot(copy.deepcopy(submission), copy.deepcopy(context))

    def launch_assessment_snapshot(self, submission, context):
        if self.workers or self._closing: return
        try:
            task_id = self.store.create_assessment_task(submission, context)
        except (ValueError, TypeError, OSError) as exc:
            self.show_task_failure('保存任务快照', str(exc), None)
            return
        self.launch_assessment_task(task_id)

    def show_review(self, review, navigate=True):
        self.clear_task_feedback()
        self.current_review = review
        self.refresh_review_goals_entry()
        self.refresh_revision_diff()
        from ..review_learning import summary_html
        assessment = review.get('assessment',{})
        total = assessment.get('total_score')
        score = '表达与结构诊断' if total is None else f'训练估分 {total} / {assessment.get("max_score",20)}'
        self.result_summary.setHtml('<h3>'+escape(str(review.get('title','训练复盘')))+'</h3><p>'+escape(score)+'</p>'+summary_html(review))
        self.result_tabs.setCurrentIndex(0)
        self.export_report_button.setEnabled(True)
        self.export_pdf_button.setEnabled(True)
        self.result_report.setHtml(review_html(review))
        submission = review.get('submission', {})
        self.result_original.setPlainText(submission.get('answer', ''))
        revisions = review.get('revisions', {})
        self.result_minimal.setPlainText(revisions.get('minimal', ''))
        self.result_model.setPlainText(revisions.get('model', ''))
        self.revision_status.setText(revisions.get('status', ''))
        self.annotation_list.clear()
        for index, annotation in enumerate(review.get('assessment', {}).get('annotations', []), 1):
            self.annotation_list.addItem(f'{index}. {annotation.get("issue", "批注")}\n{annotation.get("suggestion", "")}')
        if navigate:
            self.pages.setCurrentIndex(0)
            self.navigation.setCurrentRow(0)
        self.assessment_tabs.setCurrentIndex(2)
        self.refresh_history()
        if str(revisions.get('status','')).startswith('修改未完成'):
            frozen = copy.deepcopy(review)
            task_id = self.store.assessment_task_for_review(review['id'])
            self.show_task_failure('修改稿生成（原答评分已保存）', revisions['status'],
                (lambda:self.resume_assessment_task(task_id)) if task_id else lambda:self.retry_revision_snapshot(frozen))

    def retry_revision_snapshot(self, review):
        task_id = self.store.assessment_task_for_review(review['id'])
        if task_id:
            return self.resume_assessment_task(task_id)
        if self.workers or self._closing: return
        if QMessageBox.question(self, '重试生成修改稿',
            '原答评分已保存；本次只重新生成两种修改稿，不重复评分。\n'
            '使用该训练记录的原答和评分快照，以及当前已保存的模型设置，可能再次产生费用。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        try:
            from ..model_diagnostics import preflight_request
            from ..service import AppService, REVISION_SYSTEM
            preflight_request(self.config, REVISION_SYSTEM, AppService.revision_payload(review), output_limit=self.declared_output_limit(self.config))
            service = self.get_service(require_config=True)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.show_task_failure('修改稿发送前检查', str(exc), lambda:self.retry_revision_snapshot(review))
            return
        self.start_job('重试修改稿',
            lambda progress,cancel:service.retry_revisions(review['id'], progress, cancel, expected_review=copy.deepcopy(review)),
            self.show_review, retry=lambda:self.retry_revision_snapshot(review),
            domains=('history', 'usage'))

    def select_annotation(self, index):
        if not self.current_review or index < 0:
            return
        annotations = self.current_review.get('assessment', {}).get('annotations', [])
        if index >= len(annotations):
            return
        annotation = annotations[index]
        answer = self.current_review['submission']['answer']
        start, end = annotation.get('start', 0), annotation.get('end', 0)
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(answer)):
            return
        cursor = self.result_original.textCursor()
        cursor.setPosition(len(answer[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(len(answer[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
        self.result_original.setTextCursor(cursor)
        self.result_original.ensureCursorVisible()

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
