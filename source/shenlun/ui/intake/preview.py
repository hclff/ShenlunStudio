"""Local input review: reading never changes an exercise until confirmation."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget)

from ..common.dialogs import confirm_billable
from ..widgets import badge, button, section_label
from ..workers import Job
from ..image_tools import CandidateDialog, ImageToolsDialog

from .controls import TARGETS, columns_combo, local_mode_combo, target_combo
from .source_preview import SourcePreview


class IntakePreviewDialog(QDialog):
    def __init__(self, batch, parent, target='materials'):
        super().__init__(parent)
        self.setWindowTitle('核对导入内容')
        self.resize(1050, 720)
        self.setMinimumSize(760, 520)
        self._current = None
        self._jobs = set()
        self._close_pending = False
        self._pending_candidate = None
        self._busy = False
        self._reviewing = False
        self._routing_attempted = False
        self._routing_context = None
        self._routed_saved = None
        self._target = target if target in dict((key, label) for label, key in TARGETS) else 'materials'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(section_label('核对文字，再写入练习'))
        heading.addStretch()
        heading.addWidget(badge('本机读取 · 待确认', 'neutral'))
        layout.addLayout(heading)
        notice = QLabel('勾选要写入的页面，可修改文字、调整顺序或移除。识别结果请对照原图核对；取消不会改动原文。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('写入位置'))
        self.target_combo = target_combo(self._target)
        controls.addWidget(self.target_combo)
        self.replace_check = QCheckBox('替换该栏已有内容（默认追加）')
        controls.addWidget(self.replace_check)
        self.routing_button = button('分配内容…', self.open_routing)
        controls.addWidget(self.routing_button)
        controls.addStretch()
        layout.addLayout(controls)
        split = QSplitter()
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.addWidget(section_label('导入页面'))
        self.page_list = QListWidget()
        self.page_list.setMinimumWidth(165)
        pane_layout.addWidget(self.page_list, 1)
        pane.setMinimumWidth(165)
        pane.setMaximumWidth(260)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.move_up_button = button('上移', lambda: self.move_page(-1))
        self.move_down_button = button('下移', lambda: self.move_page(1))
        self.remove_page_button = button('移除', self.remove_page)
        for action in (self.move_up_button, self.move_down_button, self.remove_page_button):
            actions.addWidget(action)
        pane_layout.addLayout(actions)
        split.addWidget(pane)
        text_pane = QWidget()
        text_layout = QVBoxLayout(text_pane)
        text_layout.setContentsMargins(10, 0, 10, 0)
        text_layout.addWidget(section_label('文字内容 · 可直接修订'))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText('当前页文字，可直接修订…')
        text_layout.addWidget(self.text_edit, 1)
        self.undo_button = button('撤销上次采用', self.undo_recognition)
        text_layout.addWidget(self.undo_button)
        split.addWidget(text_pane)
        self.source_view = SourcePreview()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.source_view)
        source_pane = QWidget()
        source_layout = QVBoxLayout(source_pane)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(section_label('来源原图 · 识别范围'))
        zoom = QHBoxLayout()
        for text, action in [('适应', self.source_view.fit_source), ('100%', lambda: self.source_view.set_zoom(1)),
                             ('−', lambda: self.source_view.zoom(.8)), ('＋', lambda: self.source_view.zoom(1.25))]:
            zoom.addWidget(button(text, action))
        source_layout.addLayout(zoom)
        source_layout.addWidget(scroll, 1)
        self.region_label = QLabel('整页 · 顺时针 0°')
        self.region_label.setWordWrap(True)
        source_layout.addWidget(self.region_label)
        self.image_tools_button = button('旋转 / 拖动选区…', self.edit_image_region)
        source_layout.addWidget(self.image_tools_button)
        self.ocr_mode = local_mode_combo()
        self.ocr_columns = columns_combo()
        choices = QHBoxLayout()
        choices.addWidget(self.ocr_mode)
        choices.addWidget(self.ocr_columns)
        source_layout.addLayout(choices)
        retry = QHBoxLayout()
        self.local_retry_button = button('本机重识别', self.retry_local)
        self.ai_button = button('AI 转写…', self.ai_recognize)
        self.ai_button.setToolTip('仅当前页/选区；每次上传前显示范围、模型、预算并请求确认。需支持图像的模型。')
        retry.addWidget(self.local_retry_button)
        retry.addWidget(self.ai_button)
        source_layout.addLayout(retry)
        self.operations_label = QLabel()
        self.operations_label.setWordWrap(True)
        self.operations_label.setObjectName('muted')
        self.operations_label.setMaximumHeight(65)
        source_layout.addWidget(self.operations_label)
        source_pane.setMinimumWidth(285)
        self.cancel_recognition_button = button('取消识别', self.cancel_recognition)
        self.cancel_recognition_button.hide()
        source_layout.addWidget(self.cancel_recognition_button)
        split.addWidget(source_pane)
        split.setSizes([180, 480, 340])
        layout.addWidget(split, 1)
        self.exercise_check = QCheckBox('整题替换：使用唯一勾选的 JSON 题目（缺失的原答会清空）')
        self.exercise_check.setToolTip('仅在勾选一条结构化题目记录时可用。将替换题干、材料、原答、参考及题目设置；默认只写入所选栏。')
        layout.addWidget(self.exercise_check)
        self.record_details = QPlainTextEdit()
        self.record_details.setReadOnly(True)
        self.record_details.setMaximumHeight(95)
        self.record_details.hide()
        layout.addWidget(self.record_details)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumHeight(80)
        messages = list(batch.errors)
        for source in batch.pages:
            messages.extend(f'{Path(source.source).name} · 第 {source.number} 页：{message}' for message in source.warnings)
            item = QListWidgetItem(f'{Path(source.source).name} · {source.number}\n{source.method}')
            # Independent copies ensure preview editing/cancelling never mutates reader output.
            item.setData(Qt.ItemDataRole.UserRole, {'text': source.text, 'fields': deepcopy(source.fields), 'png': source.preview_png,
                'source_name': source.source, 'page_number': source.number,
                'source': getattr(source, 'source_image', b'') or source.preview_png,
                'ocr_info': deepcopy(getattr(source, 'ocr_info', {})), 'rotation': 0, 'crop': None, 'history': []})
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.page_list.addItem(item)
        self.messages.setPlainText('\n'.join(messages))
        self.messages.setVisible(bool(messages))
        layout.addWidget(self.messages)
        self.page_summary = QLabel()
        self.page_summary.setObjectName('muted')
        layout.addWidget(self.page_summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认写入')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.page_list.currentItemChanged.connect(self.show_page)
        self.page_list.itemChanged.connect(self.update_selection_state)
        self.target_combo.currentIndexChanged.connect(self.change_target)
        self.exercise_check.toggled.connect(self.update_selection_state)
        self.text_edit.textChanged.connect(self.update_selection_state)
        self.page_list.setCurrentRow(0)
        self.update_selection_state()

    def save_page(self):
        if self._current is not None:
            value = self._current.data(Qt.ItemDataRole.UserRole)
            if value['fields']:
                value['fields'][self._target] = self.text_edit.toPlainText()
            else:
                value['text'] = self.text_edit.toPlainText()
            self._current.setData(Qt.ItemDataRole.UserRole, value)

    def show_page(self, item, previous=None):
        self.save_page()
        self._current = item
        if item is None:
            self.text_edit.clear()
            self.source_view.set_source()
            self.update_image_controls()
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        text = str(value['fields'].get(self._target, '')) if value['fields'] else value['text']
        self.text_edit.setPlainText(text)
        self.refresh_source()
        self.record_details.setVisible(bool(value['fields']))
        labels = TARGETS + [('考试', 'exam'), ('年份', 'year'), ('类别', 'category'),
                            ('题型', 'question_type'), ('满分', 'max_score'), ('字数上限', 'word_limit')]
        details = '\n'.join(f'{label}：{value["fields"].get(key, "")}' for label, key in labels)
        if value['fields'].get('rubric'):
            details += '\n自定量表：' + json.dumps(value['fields']['rubric'], ensure_ascii=False)
        self.record_details.setPlainText(details)
        self.update_image_controls()

    def change_target(self):
        self.save_page()
        self._target = self.target_combo.currentData()
        current = self._current
        self._current = None
        self.show_page(current)

    def included(self):
        return [self.page_list.item(i) for i in range(self.page_list.count())
                if self.page_list.item(i).checkState() == Qt.CheckState.Checked]

    def update_selection_state(self, *args):
        chosen = self.included()
        structured = len(chosen) == 1 and bool(chosen[0].data(Qt.ItemDataRole.UserRole)['fields'])
        self.exercise_check.setEnabled(structured)
        if not structured:
            self.exercise_check.setChecked(False)
        self.replace_check.setEnabled(not self.exercise_check.isChecked() and not self._busy and not self._reviewing)
        self.routing_button.setEnabled(bool(chosen) and not self.exercise_check.isChecked() and not self._busy and not self._reviewing)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(chosen) and not self._busy and not self._reviewing)
        current = self.page_list.currentRow()
        self.move_up_button.setEnabled(current > 0 and not self._busy)
        self.move_down_button.setEnabled(0 <= current < self.page_list.count() - 1 and not self._busy)
        self.remove_page_button.setEnabled(current >= 0 and not self._busy)
        self.text_edit.setEnabled(current >= 0 and not self._busy)
        self.update_image_controls()
        self.page_summary.setText(f'已勾选 {len(chosen)} / {self.page_list.count()} 页 · 确认后写入，默认追加到已有内容')

    def move_page(self, offset):
        if self._busy or self._reviewing:
            return
        row = self.page_list.currentRow()
        destination = row + offset
        if 0 <= destination < self.page_list.count():
            self.save_page()
            self._current = None
            item = self.page_list.takeItem(row)
            self.page_list.insertItem(destination, item)
            self.page_list.setCurrentItem(item)

    def remove_page(self):
        if self._busy or self._reviewing:
            return
        row = self.page_list.currentRow()
        if row >= 0:
            self._current = None
            self.page_list.takeItem(row)
            self.show_page(self.page_list.currentItem())
            self.update_selection_state()

    def selection(self):
        if self._routing_attempted:
            return {'mode': 'routing', 'committed': self._routed_saved is not None}
        self.save_page()
        values = [item.data(Qt.ItemDataRole.UserRole) for item in self.included()]
        text = '\n\n'.join(str(value['fields'].get(self._target, '')) if value['fields'] else value['text'] for value in values)
        fields = deepcopy(values[0]['fields']) if len(values) == 1 and self.exercise_check.isChecked() else None
        return {'target': self._target, 'text': text, 'replace': self.replace_check.isChecked(), 'fields': fields}

    def accept(self):
        if self._busy or self._reviewing:
            return
        if self._routing_attempted:
            self.report_message('已进入内容分配，请使用“分配内容…”确认，或取消后重新导入。')
            return
        selection = self.selection()
        length = len(selection['text'])
        if selection['fields'] is not None:
            length = sum(len(str(selection['fields'].get(key, ''))) for _, key in TARGETS)
        if length > 250000 or not self.included():
            self.messages.show()
            self.messages.setPlainText('请至少勾选一页，且合并文字不得超过 25 万字。')
            return
        super().accept()

    def routing_source_snapshot(self):
        values = []
        for index in range(self.page_list.count()):
            item = self.page_list.item(index)
            value = deepcopy(item.data(Qt.ItemDataRole.UserRole))
            if item is self._current:
                if value['fields']:
                    value['fields'][self._target] = self.text_edit.toPlainText()
                else:
                    value['text'] = self.text_edit.toPlainText()
            values.append({'checked': item.checkState() == Qt.CheckState.Checked,
                           'source': value['source_name'], 'number': value['page_number'],
                           'text': value['text'], 'fields': value['fields'], 'ocr_info': value['ocr_info']})
        return {'target': self._target, 'whole_question': self.exercise_check.isChecked(), 'pages': values}

    def routing_pages(self):
        return [{'source': page['source'], 'number': page['number'],
                 'text': str(page['fields'].get(self._target, '')) if page['fields'] else page['text']}
                for page in self.routing_source_snapshot()['pages'] if page['checked']]

    def create_routing_dialog(self):
        if self._busy or self._reviewing or self.exercise_check.isChecked() or not self.included():
            return None
        from ..intake_routing import IntakeRoutingDialog
        from ..workspace_guard import ModalWorkspaceGuard
        host = self.parentWidget()
        # Capture generation BEFORE reading any consent context, and own the
        # suspension until construction succeeds (including entry-context reads).
        generation = (host.store.assessment_generation() if self._routing_context is None
                      else self._routing_context['generation'])
        guard = ModalWorkspaceGuard(host, generation, report=self.report_message)
        try:
            if self._routing_context is None:
                workspace = deepcopy(host.store.load_workspace())
                guard.workspace = workspace
                self._routing_context = {'generation': generation, 'before': deepcopy(host.workspace_snapshot()),
                                         'workspace': workspace}
            else:
                guard.workspace = self._routing_context['workspace']
            self._routing_attempted = True
            return IntakeRoutingDialog(self, self._routing_context, _workspace_guard=guard)
        except Exception:
            guard.release()
            raise

    def open_routing(self):
        try:
            dialog = self.create_routing_dialog()
            if dialog is not None and dialog.exec() == QDialog.DialogCode.Accepted:
                super().accept()
        except Exception as exc:
            self.report_message('无法打开分配，请重新核对：' + str(exc))

    def current_image_value(self):
        return self._current.data(Qt.ItemDataRole.UserRole) if self._current else {}

    def selected_image(self):
        from ...image_ocr import transform_image
        value = self.current_image_value()
        if not value.get('source') or value.get('fields'):
            raise ValueError('此页没有可识别的图片。')
        return transform_image(value['source'], rotation=value['rotation'], crop=value['crop'])

    def refresh_source(self):
        value = self.current_image_value()
        pixmap = QPixmap()
        if value.get('source') and not value.get('fields'):
            try:
                pixmap.loadFromData(self.selected_image())
            except ValueError as exc:
                self.report_message(str(exc))
        self.source_view.set_source(pixmap)
        region = '选区' if value.get('crop') else '整页'
        self.region_label.setText(f'{region} · 顺时针 {value.get("rotation", 0)}°' +
                                 (f' · {pixmap.width()} × {pixmap.height()}' if not pixmap.isNull() else ''))
        info = value.get('ocr_info', {})
        operations = info.get('operations', [])
        detail = '；'.join(str(op) for op in operations)
        if info.get('segments'):
            detail += f' · {info["segments"]} 个识别分块'
        self.operations_label.setText(detail or '手动选区后重识别；结果先核对再采用。')
        self.operations_label.setToolTip(detail)

    def update_image_controls(self):
        value = self.current_image_value()
        enabled = bool(value.get('source')) and not value.get('fields') and not self._busy and not self._reviewing
        for control in (self.image_tools_button, self.ocr_mode, self.ocr_columns,
                        self.local_retry_button, self.ai_button):
            control.setEnabled(bool(enabled))
        self.undo_button.setEnabled(bool(value.get('history')) and not self._busy and not self._reviewing)

    def set_image_region(self, rotation, crop):
        if self._busy or self._reviewing:
            return
        from ...image_ocr import transform_image
        value = self.current_image_value()
        transform_image(value['source'], rotation=rotation, crop=crop)
        value.update(rotation=rotation, crop=crop)
        self._current.setData(Qt.ItemDataRole.UserRole, value)
        self.refresh_source()

    def edit_image_region(self):
        if self._busy or self._reviewing:
            return
        value = self.current_image_value()
        if not value.get('source') or value.get('fields'):
            return
        try:
            dialog = ImageToolsDialog(value['source'], value['rotation'], value['crop'], self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.set_image_region(dialog.rotation, dialog.crop)
        except ValueError as exc:
            self.report_message(str(exc))

    def report_message(self, message):
        self.messages.show()
        self.messages.setPlainText(str(message)[:12000])

    def retry_local(self):
        if self._busy or self._reviewing:
            return
        from ...image_ocr import recognize_image
        value = self.current_image_value()
        if not value.get('source') or value.get('fields'):
            return
        data = value['source']
        options = dict(mode=self.ocr_mode.currentData(), columns=self.ocr_columns.currentData(),
                       rotation=value['rotation'], crop=value['crop'])
        self.start_recognition('本机重识别', lambda progress, cancel:
                               recognize_image(data, progress=progress, cancel=cancel, **options))

    def ai_recognize(self):
        if self._busy or self._reviewing:
            return
        from ...model import DEFAULT_CONFIG
        from ...vision_ocr import estimate_image
        host = self.parentWidget()
        config = {**DEFAULT_CONFIG, **deepcopy(getattr(host, 'config', {}))}
        try:
            if not config.get('base_url') or not config.get('model'):
                raise ValueError('请先在设置中保存支持图像输入的模型及接口地址，然后重新打开预览。')
            # Pure transformation/estimate: no service construction or credential read here.
            data = self.selected_image()
            estimate = estimate_image(data, config)
            budget = config['session_token_budget']
            if type(budget) is not int or estimate['estimated_total_tokens'] > budget:
                raise ValueError(f'图像识别预估 {estimate["estimated_total_tokens"]} token，超过本次任务预算。请调整设置。')
            message = (f'仅上传当前所选页面：{self._current.text().splitlines()[0]}\n'
                       f'范围：{self.region_label.text()}\n'
                       f'模型：{config["model"]}\n接口：{config["base_url"]}\n'
                       f'上传：{estimate["width"]} × {estimate["height"]} 像素，'
                       f'{estimate["bytes"]/1024:.1f} KB（去除元数据）\n'
                       f'预估输入 {estimate["estimated_input_tokens"]} token，'
                       f'含输出上限共 {estimate["estimated_total_tokens"]} token；预算 {budget}。\n\n'
                       '当前文字可能含人工修订。结果只作为候选，采用前请核对；选区结果采用后会替换本页文字。\n'
                       '图片将发往上述服务，可能产生费用；取消后服务端仍可能处理和计费。'
                       '模型必须支持图像输入；不会自动切换到其他服务。\n' +
                       '\n'.join(estimate['warnings']) + '\n\n是否上传此次图片进行转写？')
            if not confirm_billable(self, '确认上传当前图片 / 选区', message):
                return
            # The explicit Yes above is the sole authorization for this one key read/call.
            service = host.get_service(require_config=True)
            self.start_recognition('AI 图像转写', lambda progress, cancel:
                                   service.recognize_image(data, progress=progress, cancel=cancel))
        except (ValueError, TypeError, RuntimeError) as exc:
            self.report_message(str(exc))

    def set_recognition_busy(self, busy):
        self._busy = busy
        for control in (self.page_list, self.target_combo, self.replace_check, self.exercise_check):
            control.setEnabled(not busy)
        self.cancel_recognition_button.setVisible(busy)
        self.cancel_recognition_button.setEnabled(busy)
        self.update_selection_state()

    def start_recognition(self, title, operation):
        if self._jobs or self._reviewing or self._close_pending:
            return
        self.save_page()
        self._pending_candidate = None
        self._recognition_item = self._current
        self._recognition_region = self.region_label.text()
        self._recognition_title = title
        job = Job(operation, self)
        self._jobs.add(job)
        # Also retain through parent shutdown, including a close requested by the OS.
        host = self.parentWidget()
        if hasattr(host, 'workers'):
            host.workers.add(job)
        job.progress.connect(self.recognition_progress)
        job.result.connect(self.recognition_result)
        job.failed.connect(self.recognition_failed)
        job.finished.connect(lambda: self.recognition_finished(job))
        self.set_recognition_busy(True)
        self.operations_label.setText(title + '… 当前文字保留。')
        job.start()

    def recognition_progress(self, message):
        if not self._close_pending:
            self.operations_label.setText(str(message)[:400])

    def recognition_result(self, result):
        if not self._close_pending and not any(job.cancel_event.is_set() for job in self._jobs):
            self._pending_candidate = result

    def recognition_failed(self, message):
        if not self._close_pending and not any(job.cancel_event.is_set() for job in self._jobs):
            self.report_message(str(message) + '\n识别失败，当前文字保持不变。')

    def recognition_finished(self, job):
        cancelled = job.cancel_event.is_set()
        self._jobs.discard(job)
        host = self.parentWidget()
        if hasattr(host, 'workers'):
            host.workers.discard(job)
        job.deleteLater()
        self.set_recognition_busy(False)
        if self._close_pending or getattr(host, '_closing', False):
            self._pending_candidate = None
            super().reject()
            if getattr(host, '_closing', False) and not host.workers:
                QTimer.singleShot(0, host.close)
            return
        self.refresh_source()
        result, self._pending_candidate = self._pending_candidate, None
        if cancelled:
            self.report_message('识别已取消，当前文字保持不变。服务端已收到的请求仍可能计费。')
        elif result is not None:
            self.review_candidate(result)

    def review_candidate(self, result):
        if self._current is not self._recognition_item or self._close_pending:
            return
        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or len(result['text']) > 250000:
            self.report_message('候选文字无效或超过25万字；当前文字保持不变。')
            return
        self._reviewing = True
        self.update_selection_state()
        try:
            old = self.text_edit.toPlainText()
            dialog = CandidateDialog(old, result, self._recognition_region, self)
            if dialog.exec() != QDialog.DialogCode.Accepted or self._close_pending:
                return
            value = self.current_image_value()
            value['history'] = (value['history'] + [{'text': old, 'ocr_info': value['ocr_info']}])[-5:]
            info = deepcopy(result.get('info', {}))
            if not info.get('operations'):
                info['operations'] = [self._recognition_title]
            value['ocr_info'] = info
            self._current.setData(Qt.ItemDataRole.UserRole, value)
            self.text_edit.setPlainText(result['text'])
            self.save_page()
            self.refresh_source()
        finally:
            self._reviewing = False
            self.update_selection_state()

    def undo_recognition(self):
        if self._busy or self._reviewing:
            return
        value = self.current_image_value()
        if not value.get('history'):
            return
        prior = value['history'].pop()
        value['ocr_info'] = prior['ocr_info']
        self._current.setData(Qt.ItemDataRole.UserRole, value)
        self.text_edit.setPlainText(prior['text'])
        self.save_page()
        self.refresh_source()
        self.update_image_controls()

    def cancel_recognition(self):
        self._pending_candidate = None
        for job in self._jobs:
            job.cancel()
        self.cancel_recognition_button.setEnabled(False)
        self.operations_label.setText('正在取消… 请等待当前处理结束。')

    def reject(self):
        if self._jobs:
            self._close_pending = True
            self.cancel_recognition()
            return
        self._close_pending = True
        super().reject()

    def closeEvent(self, event):
        if self._jobs:
            self.reject()
            event.ignore()
        else:
            self._close_pending = True
            super().closeEvent(event)
