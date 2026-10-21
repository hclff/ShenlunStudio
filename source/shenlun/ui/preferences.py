"""Explicit model configuration and transparent saved training statistics."""
import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..reports import calibration_metrics
from ..model import MAX_CONFIG_LIMIT
from ..model_diagnostics import config_guidance, connection_identity
from ..settings import get_api_key, load_config, save_config, set_api_key
from .widgets import button, card, editor, empty_state, page, row


class PreferencesMixin:
    def build_settings(self):
        widget, layout = page('设置', '选择模型服务，管理调用预算。保存后，仅在主动操作时连接。')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(16)
        group = QGroupBox('01  模型连接')
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(12)
        self.model_url = QLineEdit(self.config['base_url'])
        self.model_url.setObjectName('model_url')
        self.model_url.setPlaceholderText('https://你的服务地址/v1')
        self.model_name = QLineEdit(self.config['model'])
        self.model_name.setObjectName('model_name')
        self.model_name.setPlaceholderText('服务商提供的模型名称')
        form.addRow('API 地址', self.model_url)
        form.addRow('模型名称', self.model_name)
        connection_note = QLabel('兼容 OpenAI 接口。远程服务使用 HTTPS；本机服务可以免密钥。')
        connection_note.setObjectName('muted')
        connection_note.setWordWrap(True)
        form.addRow(connection_note)
        content_layout.addWidget(group)
        security = QGroupBox('02  密钥与隐私')
        security_form = QFormLayout(security)
        security_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        security_form.setSpacing(12)
        self.api_key = QLineEdit()
        self.api_key.setObjectName('api_key')
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('输入新密钥；留空保留已有密钥')
        security_form.addRow('API 密钥', self.api_key)
        self.key_status = QLabel('如已保存密钥，将在调用时从系统凭据库读取。')
        self.key_status.setWordWrap(True)
        self.key_status.setObjectName('muted')
        security_form.addRow('', self.key_status)
        clear_key_button = button('清除已保存密钥', self.clear_key)
        clear_key_button.setMaximumWidth(180)
        security_form.addRow('', clear_key_button)
        privacy = QLabel('密钥保存在系统凭据库或当前会话，不写入文章与训练数据库。文章、原答和训练记录保存在本机；批改与提炼时会发送相应内容。')
        privacy.setObjectName('muted')
        privacy.setWordWrap(True)
        security_form.addRow(privacy)
        content_layout.addWidget(security)
        advanced = QGroupBox('03  调用预算与服务兼容')
        advanced_form = QFormLayout(advanced)
        advanced_form.setSpacing(12)
        self.config_numbers = {}
        for key, label, maximum, minimum in [
            ('timeout', '单次请求超时（秒）', 300, 1), ('max_output_tokens', '单次输出 token 上限', MAX_CONFIG_LIMIT, 128),
            ('session_token_budget', '任务总 token 预算（输入＋输出）', MAX_CONFIG_LIMIT, 128), ('max_input_chars', '输入字符上限（非 token）', MAX_CONFIG_LIMIT, 1)]:
            control = QSpinBox()
            control.setRange(minimum, maximum)
            control.setValue(int(self.config[key]))
            control.setMaximumWidth(240)
            self.config_numbers[key] = control
            advanced_form.addRow(label, control)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(.1)
        self.temperature.setValue(self.config['temperature'])
        self.temperature.setMaximumWidth(240)
        advanced_form.addRow('temperature', self.temperature)
        self.json_mode = QCheckBox('要求 JSON 对象输出（服务需支持）')
        self.json_mode.setChecked(self.config.get('json_mode', True))
        advanced_form.addRow('', self.json_mode)
        self.token_parameter = QComboBox()
        self.token_parameter.addItems(['max_tokens', 'max_completion_tokens'])
        self.token_parameter.setCurrentText(self.config.get('token_parameter', 'max_tokens'))
        self.token_parameter.setMaximumWidth(240)
        advanced_form.addRow('输出上限参数名', self.token_parameter)
        self.send_temperature = QCheckBox('发送 temperature（部分推理模型需取消勾选）')
        self.send_temperature.setChecked(self.config.get('send_temperature', True))
        advanced_form.addRow('', self.send_temperature)
        self.limit_guidance = QLabel()
        self.limit_guidance.setWordWrap(True)
        self.limit_guidance.setObjectName('muted')
        advanced_form.addRow(self.limit_guidance)
        for control in self.config_numbers.values():
            control.valueChanged.connect(self.refresh_limit_guidance)
        self.json_mode.toggled.connect(self.refresh_limit_guidance)
        self.refresh_limit_guidance()
        for control in (self.model_url, self.model_name):
            control.textChanged.connect(self.refresh_limit_guidance)
        self.token_parameter.currentTextChanged.connect(self.refresh_limit_guidance)
        self.send_temperature.toggled.connect(self.refresh_limit_guidance)
        self.temperature.valueChanged.connect(self.refresh_limit_guidance)
        note = QLabel('预算按每次批改或提炼任务计算，按实际返回或估算的 token 记账；不是保证不超出的金额上限。远程服务须使用 HTTPS，本机服务可以免密钥。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        advanced_form.addRow(note)
        content_layout.addWidget(advanced)
        diagnostic, diagnostic_layout = card('模型兼容性')
        self.compatibility_summary = QLabel('尚未检测；小额测试不代表批改效果或模型极限。')
        self.compatibility_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.compatibility_summary.setWordWrap(True)
        diagnostic_layout.addWidget(self.compatibility_summary)
        self.compatibility_details = editor('compatibility_details', readonly=True)
        self.compatibility_details.setMaximumHeight(150)
        self.compatibility_details.hide()
        diagnostic_layout.addWidget(self.compatibility_details)
        content_layout.addWidget(diagnostic)
        local, local_layout = card('本机数据与用量')
        self.usage_label = QLabel()
        self.usage_label.setWordWrap(True)
        local_layout.addWidget(self.usage_label)
        location = QLabel('本机数据库：' + str(self.store.path.resolve()))
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        location.setWordWrap(True)
        location.setObjectName('muted')
        local_layout.addWidget(location)
        backup_note = QLabel('数据库备份包含题库、草稿、训练记录、文章和设置，不包含系统凭据库密钥。恢复将替换当前本机数据库，并先创建安全备份。')
        backup_note.setWordWrap(True); backup_note.setObjectName('muted'); local_layout.addWidget(backup_note)
        local_layout.addLayout(row(button('备份数据库…', self.backup_database), button('从备份恢复…', self.restore_database)))
        local_layout.addWidget(self.build_backup_controls())
        content_layout.addWidget(local)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        actions = row(button('保存设置', self.save_preferences, True), button('一键检测兼容性', self.test_connection))
        actions.addStretch()
        layout.addLayout(actions)
        return widget

    def refresh_limit_guidance(self, *args):
        value = self.read_preferences()
        self.limit_guidance.setText(config_guidance(value, self.declared_output_limit(value)))
        tested = getattr(self, '_compatibility_tested_config', None)
        if tested is not None:
            suffix = '（设置已变更，请重新检测）' if tested != value else ''
            self.compatibility_summary.setText(self._compatibility_summary_text + suffix)

    def declared_output_limit(self, config=None):
        declared = getattr(self, '_declared_limit', None)
        value = config if config is not None else self.read_preferences()
        return declared[1] if declared is not None and declared[0] == connection_identity(value) else None

    def remember_output_limit(self, config, parameter, upper):
        if parameter == config.get('token_parameter', 'max_tokens') and type(upper) is int and 1 <= upper <= MAX_CONFIG_LIMIT:
            self._declared_limit = (connection_identity(config), upper)
            self.refresh_limit_guidance()

    def read_preferences(self):
        return {**self.config, 'base_url':self.model_url.text().strip(), 'model':self.model_name.text().strip(),
                **{k:v.value() for k, v in self.config_numbers.items()}, 'temperature':self.temperature.value(),
                'json_mode':self.json_mode.isChecked(), 'token_parameter':self.token_parameter.currentText(),
                'send_temperature':self.send_temperature.isChecked()}

    def save_preferences(self):
        try:
            value = self.read_preferences()
            self.save_backup_preferences()
            save_config(self.store, value)
            self.config = load_config(self.store)
            if self.api_key.text():
                self._declared_limit = None
                vault = set_api_key(self.api_key.text())
                self.api_key.clear()
                self.key_status.setText('新密钥已存入系统凭据库。' if vault else '系统凭据库不可用；新密钥仅留在当前会话，关闭后需重新输入。')
            self.refresh_connection_label()
            self.refresh_limit_guidance()
            self.notify('设置已保存。连接测试与批改均需主动点击。')
            return True
        except (ValueError, TypeError, OSError) as exc:
            self.notify(str(exc))
            return False

    def clear_key(self):
        self._declared_limit = None
        vault = set_api_key('')
        self.api_key.clear()
        self.key_status.setText('密钥已清除。' if vault else '当前会话密钥已清除；系统凭据库不可用。')
        self.notify(self.key_status.text())
        self.refresh_limit_guidance()

    def get_service(self, require_config=False):
        if require_config:
            if not self.config.get('base_url') or not self.config.get('model'):
                raise ValueError('请先在“设置”中填写并保存 API 地址与模型名称；本地资料库与训练记录可离线使用。')
            host = urlsplit(self.config['base_url']).hostname
            key = get_api_key()
            if not key and host not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('请先在“设置”中保存模型 API 密钥。')
        else:
            key = get_api_key()
        return self.service_factory(dict(self.config), key)

    def test_connection(self):
        if self.workers:
            self.notify('请先完成或取消当前任务。'); return
        if QMessageBox.question(self, '检测模型兼容性',
            '将保存当前设置并进行连接、JSON 样例和参数三项小额测试。\n'
            '只发送固定测试内容，不发送题目、材料或原答。每项输出额度为 128 token，可能产生少量费用。\n'
            '检测不会修改你的输出上限，也不能验证模型极限或保证实际批改成功。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        if not self.save_preferences():
            return
        try:
            service = self.get_service(require_config=True)
            tested_config = self.read_preferences()
            self.start_job('模型兼容性检测', lambda progress, cancel: service.check_compatibility(progress=progress,cancel=cancel),
                lambda result:self.show_compatibility_result(result, tested_config))
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def show_compatibility_result(self, result, tested_config):
        labels = {'passed':'通过', 'failed':'未通过', 'skipped':'未检测', 'inconclusive':'未确认'}
        checks = result['checks']
        summary = '　'.join(c['label']+'：'+labels[c['status']] for c in checks)
        self._compatibility_tested_config = dict(tested_config)
        self._compatibility_summary_text = summary
        if result.get('output_limit') is not None:
            self.remember_output_limit(tested_config, tested_config.get('token_parameter','max_tokens'), result['output_limit'])
        self.refresh_limit_guidance()
        details = ['模型：'+tested_config['model'],
                   *[c['label']+'：'+labels[c['status']]+'\n'+c['detail'] for c in checks],
                   '仅验证 128 token 的小额样例；填写的单次输出额度未作极限测试。',
                   'JSON 样例通过不等于支持 JSON Schema 或保证后续结果完整。',
                   '模型实际输出上限：'+(str(result['output_limit']) if result.get('output_limit') is not None else '未知'),
                   '以上对应检测时设置；更改接口、模型或参数后请重新检测。用量已计入统计。']
        self.compatibility_details.setPlainText('\n\n'.join(details))
        self.compatibility_details.show()
        self.notify(summary)

    def refresh_usage(self):
        usage = self.store.usage_summary()
        self.usage_label.setText(f'累计调用 {usage["requests"]:,} 次　输入 {usage["input_tokens"]:,}　输出 {usage["output_tokens"]:,}　合计 {usage["total_tokens"]:,} tokens')

    def backup_database(self):
        if self.workers or self._backup_job is not None:
            self.notify('请先完成或取消当前任务，再备份数据库。'); return
        if not self.flush_workspace(): return
        path, _ = QFileDialog.getSaveFileName(self, '备份本机数据库', '申论研习备份.db', 'SQLite 数据库 (*.db)')
        if not path: return
        if self.workers or self._backup_job is not None:
            self.notify('其他任务或备份正在运行，请完成后再试。'); return
        self._start_backup_operation('manual', lambda: self.store.backup_to(Path(path), overwrite=True),
            lambda saved: self.backup_status.setText('数据库已备份：' + str(saved)))

    def restore_database(self):
        if self.workers or self._backup_job is not None:
            self.notify('请先完成或取消当前任务，再恢复数据库。'); return
        path, _ = QFileDialog.getOpenFileName(self, '选择要恢复的数据库', '', 'SQLite 数据库 (*.db)')
        if not path: return
        return self.restore_backup_path(path)

    def restore_backup_path(self, path):
        if self.workers or self._backup_job is not None:
            self.notify('请先完成或取消当前任务，再恢复数据库。'); return False
        answer = QMessageBox.question(self, '确认替换本机数据',
            '将使用以下备份替换当前数据库。\n\n备份：' + str(Path(path).resolve()) + '\n当前数据库：' + str(self.store.path.resolve()) +
            '\n\n题库、草稿、训练记录、文章和模型设置将一并恢复。系统凭据库密钥不受影响。恢复前会在当前数据库旁保留安全备份。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return False
        if self.workers or self._backup_job is not None:
            self.notify('任务或备份正在运行，恢复已取消。'); return False
        if not self.flush_workspace(): return False
        self.autosave_timer.stop(); self._autosave_paused = True
        return self._start_backup_operation('restore', lambda: self.store.restore_from(Path(path)),
            self._restore_database_completed, modal=True)

    def _restore_database_completed(self, safety):
        try:
            self.clear_task_feedback()
            self._declared_limit = None
            self._compatibility_tested_config = None
            self.compatibility_summary.setText('尚未检测；小额测试不代表批改效果或模型极限。')
            self.compatibility_details.clear(); self.compatibility_details.hide()
            backup_settings = self.backup_manager.settings()
            self.auto_backup_enabled.setChecked(backup_settings['enabled'])
            self.backup_retention.setValue(backup_settings['retention'])
            self.backup_directory.setText(backup_settings['directory'])
            self.config = load_config(self.store)
            self.model_url.setText(self.config['base_url']); self.model_name.setText(self.config['model'])
            for key, control in self.config_numbers.items(): control.setValue(int(self.config[key]))
            self.temperature.setValue(self.config['temperature'])
            self.json_mode.setChecked(self.config.get('json_mode', True))
            self.token_parameter.setCurrentText(self.config.get('token_parameter','max_tokens'))
            self.send_temperature.setChecked(self.config.get('send_temperature', True))
            self.api_key.clear()
            self.current_review = None
            self.result_report.setPlainText('数据库已恢复，请选择训练记录查看。')
            self.result_original.clear(); self.result_minimal.clear(); self.result_model.clear(); self.annotation_list.clear()
            self.export_report_button.setEnabled(False); self.export_pdf_button.setEnabled(False)
            self.apply_workspace(self.store.load_workspace() or {'submission':{}, 'question_id':None}, persist=False)
            self.set_workspace_paused(False); self.autosave_label.setText('已恢复 · 本机草稿')
            self.refresh_connection_label(); self.refresh_corpus(); self.refresh_methods(); self.refresh_history()
            self.refresh_calibration(); self.refresh_usage(); self.refresh_questions()
            self.notify('数据库已恢复。恢复前安全备份：' + str(safety))
        except Exception as exc:
            # Keep autosave paused until restored data can be read safely.
            self.set_workspace_paused(True)
            self.notify('数据库已恢复，但界面读取失败；自动保存已暂停，请重新打开应用：' + str(exc))

    def build_history(self):
        widget, layout = page('训练记录', '保留每次提交的原答、训练评分与修改稿，回看具体进步。')
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.review_list = QListWidget()
        self.review_list.setObjectName('review_list')
        self.review_list.setMinimumWidth(260)
        self.review_list.currentItemChanged.connect(self.open_history_item)
        self.review_list.itemSelectionChanged.connect(self.update_history_actions)
        layout.addLayout(self.build_practice_delete_toolbar('reviews'))
        split.addWidget(self.review_list)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        self.history_start_button = button('开始一次训练', self.start_history_training, True)
        self.history_empty = empty_state('你的训练轨迹，从这里开始', '完成一次训练批改后，原题、原答、报告和修改稿会保存在本机，方便随时复盘。', self.history_start_button)
        detail_layout.addWidget(self.history_empty, 1)
        self.history_explanation = editor(readonly=True)
        self.history_explanation.setPlainText('选择左侧记录，打开完整训练报告与原答对照。\n\n原答与修改稿分别保存，修改稿不会改变原答得分。\n\n可载入原题再次练习，也可导出 HTML、纯文本、JSON 或 PDF 报告。')
        detail_layout.addWidget(self.history_explanation, 1)
        split.addWidget(detail)
        split.setSizes([440, 540])
        layout.addWidget(split, 2)
        self.reopen_history_button = button('载入原题再次练习', self.reopen_submission)
        self.export_history_button = button('导出所选报告', self.export_history)
        self.open_history_button = button('查看完整报告', self.open_selected_report, True)
        actions = row(button('刷新记录', self.refresh_history), self.open_history_button, self.reopen_history_button, self.export_history_button)
        actions.addStretch()
        layout.addLayout(actions)
        group = QGroupBox('人工分数对照 · 未校准')
        group_layout = QVBoxLayout(group)
        comparison_note = QLabel('统一按百分制统计人工分数与训练估分的误差；这些对照不代表模型已校准。')
        comparison_note.setWordWrap(True)
        comparison_note.setObjectName('muted')
        group_layout.addWidget(comparison_note)
        self.calibration_label = editor(readonly=True)
        self.calibration_label.setMaximumHeight(105)
        group_layout.addWidget(self.calibration_label)
        format_note = QLabel('CSV / JSON：human, ai, max_score；可选 answer_id、version。导入将替换当前对照数据。')
        format_note.setWordWrap(True)
        format_note.setObjectName('muted')
        group_layout.addWidget(format_note)
        group_layout.addLayout(row(button('记录所选原答的人工评分', self.record_human_score), button('导入人工对照 CSV / JSON', self.import_calibration)))
        layout.addWidget(group)
        return widget

    def refresh_history(self):
        # A current-item signal still owns its C++ item until every slot returns.
        # Rebuilding here from show_review can destroy a checkable item while Qt
        # is processing selection, so opening an existing row needs no refresh.
        if getattr(self, '_opening_history_item', False):
            return
        selected = self.review_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        checked = set(self.checked_practice_ids('reviews'))
        self.review_list.blockSignals(True)
        self.review_list.clear()
        for review in self.store.list_reviews(limit=1000):
            assessment = review.get('assessment', {})
            total = assessment.get('total_score')
            score = '表达结构诊断' if total is None else f'{total} / {assessment.get("max_score", 100)}'
            item = QListWidgetItem(f'{review.get("title", "申论练习")}\n{str(review.get("created_at", ""))[:16]}　{score}\n量表 {assessment.get("rubric_fingerprint", "旧记录未标识")}')
            item.setData(Qt.ItemDataRole.UserRole, review['id'])
            item.setCheckState(Qt.CheckState.Checked if review['id'] in checked else Qt.CheckState.Unchecked)
            self.review_list.addItem(item)
            if review['id'] == selected_id:
                self.review_list.setCurrentItem(item)
        self.review_list.blockSignals(False)
        has_records = bool(self.review_list.count())
        self.review_list.setVisible(has_records)
        self.history_empty.setVisible(not has_records)
        self.history_explanation.setVisible(has_records)
        if not self.review_list.currentItem():
            self.history_explanation.setPlainText('选择左侧记录，打开完整训练报告与原答对照。')
        if (self.current_review or {}).get('id') and not self.store.get_review(self.current_review['id']):
            self.clear_deleted_review_display()
        self.update_history_actions()
        self.refresh_practice_delete_actions()

    def update_history_actions(self):
        selected = bool(self.review_list.selectedItems())
        self.open_history_button.setEnabled(selected)
        self.reopen_history_button.setEnabled(selected)
        self.export_history_button.setEnabled(selected)

    def start_history_training(self):
        self.navigation.setCurrentRow(0)
        self.assessment_tabs.setCurrentIndex(0)

    def open_selected_report(self):
        review = self.selected_review()
        if review:
            self.show_review(review)

    def open_history_item(self, current, previous=None):
        self.update_history_actions()
        if current:
            review = self.store.get_review(current.data(Qt.ItemDataRole.UserRole))
            if review:
                self._opening_history_item = True
                try:
                    self.show_review(review, navigate=False)
                finally:
                    self._opening_history_item = False
                assessment = review.get('assessment', {})
                score = assessment.get('total_score')
                score_text = '表达结构诊断 · 无训练总分' if score is None else f'训练估分 {score} / {assessment.get("max_score", 100)}'
                self.history_explanation.setPlainText(
                    f'{review.get("title", "申论练习")}\n\n'
                    f'保存时间：{str(review.get("created_at", ""))[:16]}\n{score_text}\n\n'
                    f'题型：{review.get("submission", {}).get("question_type", "未记录")}\n'
                    f'量表：{assessment.get("rubric_fingerprint", "旧记录未标识")}\n\n'
                    '点击“查看完整报告”阅读评价与原答批注，或载入原题再次练习。\n\n原答与修改稿分别保存；训练估分需人工复核。')

    def selected_review(self):
        item = self.review_list.currentItem()
        return self.store.get_review(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def reopen_submission(self):
        review = self.selected_review()
        if not review:
            self.notify('请先选择训练记录。')
            return
        if not self.load_submission(review.get('submission', {}), question_id=self.store.question_for_review(review['id'])):
            return
        self.navigation.setCurrentRow(0)
        self.assessment_tabs.setCurrentIndex(0)
        self.notify('已载入历史原题与原答，可修改后重新练习。')

    def export_history(self):
        review = self.selected_review()
        if not review:
            self.notify('请先选择已保存的训练记录。')
            return
        self.current_review = review
        self.export_current()

    def import_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入人工对照数据', '', '对照数据 (*.csv *.json)')
        if path:
            try:
                if Path(path).stat().st_size > 25 * 1024 * 1024:
                    raise ValueError('对照文件不能超过25MB。')
                with open(path, encoding='utf-8-sig', newline='') as stream:
                    if Path(path).suffix.lower() == '.json':
                        pairs = json.load(stream)
                    else:
                        pairs = list(csv.DictReader(stream))
                        for pair in pairs:
                            for key in ('human', 'ai', 'max_score'):
                                pair[key] = float(pair.get(key) or (100 if key == 'max_score' else ''))
                calibration_metrics(pairs)
                self.store.set_setting('calibration_pairs', pairs)
                self.refresh_calibration()
                self.notify('人工对照数据已保存至本机。')
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.notify(str(exc))

    def refresh_calibration(self):
        try:
            summary = self.store.practice_summary()
            prefix = f'绑定人工评分 {summary["human_count"]} 条 · 自行声明独立阅卷 {summary["independent_human_count"]} 条\n'
            metrics = calibration_metrics(self.store.get_setting('calibration_pairs', []) + self.store.human_calibration_pairs())
            if not metrics['n']:
                self.calibration_label.setPlainText(prefix + '尚无有效人工分数对照。当前训练估评未校准。\n同一原答同一评分版本重复估分组：' + str(len(summary['repeat_groups'])))
                return
            def value(key):
                v = metrics.get(key)
                return '暂无' if v is None else str(v)
            self.calibration_label.setPlainText(prefix + f'样本 n = {metrics["n"]}　MAE = {value("mae")}　偏差 bias = {value("bias")}　RMSE = {value("rmse")}\n'
                f'Spearman = {value("spearman")}\n重复作答分数波动：{json.dumps(metrics.get("repeat_spread", {}), ensure_ascii=False)}\n'
                f'版本样本数：{json.dumps(metrics.get("versions", {}), ensure_ascii=False)}\n'
                f'同一原答同一评分版本重复估分：{json.dumps(summary["repeat_groups"], ensure_ascii=False)}\n仅人工对照统计，不代表已校准。')
        except (ValueError, TypeError) as exc:
            self.calibration_label.setPlainText('对照数据无效：' + str(exc))
