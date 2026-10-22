"""Submission editor, saved report and Unicode-safe annotation navigation."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget)

from ...rubrics import PROFILE_CHOICES
from ..widgets import button, card, editor, page, section_label
from ..rubric_editor import RubricEditor


class EditorMixin:
    def build_assessment(self):
        widget, layout = page('训练批改', '审清题意，核对材料，保留自己的原答。')
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
        # Defer method resolution: changing type only invalidates the preview.
        # Resolving every enabled method here made each type switch wait on the
        # whole method library, even though the names are only needed on demand.
        self.question_type.currentTextChanged.connect(self.invalidate_method_context)
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
        self.word_count = QLabel('原答 0 字')
        self.word_count.setToolTip('按字符计数，含标点，不含空白。')
        self.answer_edit.textChanged.connect(lambda: self.word_count.setText(f'原答 {sum(not c.isspace() for c in self.answer_edit.toPlainText())} 字'))
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
        self.scope_label = QLabel('仅在你主动点击后调用模型；发送本题内容及至多 8 个本地参考段落。')
        self.scope_label.setObjectName('muted')
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.assess_button = button('开始训练批改', self.begin_assessment, True, 'assess_button')
        self.cancel_button = button('取消任务', self.cancel_jobs, name='cancel_button')
        self.cancel_button.setEnabled(False)
        self.assessment_actions = QWidget()
        actions = QHBoxLayout(self.assessment_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.revision_mode = QComboBox()
        self.revision_mode.setObjectName('revision_mode')
        self.revision_mode.setAccessibleName('本次输出')
        self.revision_mode.addItem('仅精批', 'grade_only')
        self.revision_mode.addItem('精批＋修改稿', 'with_revision')
        self.revision_mode.setToolTip('仅精批只生成评分与批注，不调用改写模型；需要修改稿时可在结果区单独生成，且不重新评分。')
        saved = self.store.get_setting('revision_mode')
        self.revision_mode.setCurrentIndex(max(0, self.revision_mode.findData(saved)))
        self.revision_mode.currentIndexChanged.connect(self.save_revision_mode)
        actions.addWidget(self.revision_mode)
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

    def save_revision_mode(self, *args):
        value = self.revision_mode.currentData()
        if value not in ('grade_only', 'with_revision'):
            return
        try:
            self.store.set_setting('revision_mode', value)
        except (ValueError, OSError) as exc:
            self.notify('本次输出选择未能保存：' + str(exc))

    def update_assessment_mode(self, index):
        editing = index != 2
        self.exercise_settings_card.setVisible(editing)
        self.scope_label.setVisible(editing)
        self.assessment_actions.setVisible(editing)
