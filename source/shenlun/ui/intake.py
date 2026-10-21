"""Local input review: reading never changes an exercise until confirmation."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget)

from .widgets import badge, button, card, section_label
from .workers import Job
from .image_tools import CandidateDialog, ImageToolsDialog


def local_mode_combo():
    result = QComboBox()
    result.addItem('本机增强（默认）', 'enhanced')
    result.addItem('标准识别', 'standard')
    return result


def columns_combo():
    result = QComboBox()
    result.addItem('单栏顺序', 1)
    result.addItem('双栏：先左后右', 2)
    return result


TARGETS = [('给定材料', 'materials'), ('题干', 'question'), ('我的原答', 'answer'), ('参考答案', 'reference')]


def target_combo(target):
    result = QComboBox()
    for label, key in TARGETS:
        result.addItem(label, key)
    result.setCurrentIndex(max(0, result.findData(target)))
    return result


class IntakeEditor(QPlainTextEdit):
    """Keep ordinary paste native; route image/file payloads through review."""
    def __init__(self, receive, parent=None):
        super().__init__(parent)
        self.receive = receive

    @staticmethod
    def needs_review(mime):
        return mime.hasUrls() or mime.hasImage()

    def canInsertFromMimeData(self, mime):
        return self.needs_review(mime) or super().canInsertFromMimeData(mime)

    def insertFromMimeData(self, mime):
        if self.needs_review(mime):
            self.receive(mime)
        else:
            super().insertFromMimeData(mime)

    def dragEnterEvent(self, event):
        if self.needs_review(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.needs_review(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.needs_review(event.mimeData()):
            self.receive(event.mimeData())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class IntakeOptionsDialog(QDialog):
    def __init__(self, parent, target='materials', pdf=False):
        super().__init__(parent)
        self.setWindowTitle('读取前选择')
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(section_label('选择读取方式'))
        notice = QLabel('读取完成后会先显示预览，确认后才写入。图片识别在本机运行。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        options, options_layout = card()
        form = QFormLayout()
        form.setSpacing(12)
        self.target_combo = target_combo(target)
        form.addRow('写入位置', self.target_combo)
        self.ocr_mode = local_mode_combo()
        self.ocr_columns = columns_combo()
        form.addRow('图片识别', self.ocr_mode)
        form.addRow('图片栏序', self.ocr_columns)
        self.page_spec = QLineEdit()
        self.page_spec.setPlaceholderText('留空读取全部；例如 1-3,5；对每份 PDF 分别生效')
        self.page_spec.setEnabled(pdf)
        form.addRow('PDF 页码', self.page_spec)
        self.force_ocr = QCheckBox('PDF 全部使用本机 OCR（适合乱码或文字层不准）')
        self.force_ocr.setEnabled(pdf)
        form.addRow(self.force_ocr)
        options_layout.addLayout(form)
        layout.addWidget(options)
        limits = QLabel('最多 20 个文件、单个 25 MB；每份 PDF 最多 40 页；每批最多 80 页、25 万字。')
        limits.setWordWrap(True)
        limits.setObjectName('muted')
        layout.addWidget(limits)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始读取')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SourcePreview(QLabel):
    """Full source with fit width or scrollable pixel zoom."""
    def __init__(self):
        super().__init__('此页无原图')
        self.original = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip('适应宽度，或放大后使用滚动条查看原图。')

    def set_source(self, pixmap=None):
        self.original = pixmap or QPixmap()
        self.setMinimumHeight(0)
        if self.original.isNull():
            self.clear()
            self.setText('此页无原图')
        else:
            self.fit_source()

    def fit_source(self):
        self._fit = True
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if not self.original.isNull():
            width = self.parentWidget().width() if self.parentWidget() else self.width()
            # Fit may shrink the retained source, never enlarge a narrow/tall page.
            target_width = min(self.original.width(), max(1, width - 8))
            scaled = self.original.scaledToWidth(target_width, Qt.TransformationMode.SmoothTransformation)
            self.setPixmap(scaled)
            self.setMinimumHeight(scaled.height())

    def set_zoom(self, factor):
        if self.original.isNull():
            return
        self._fit = False
        max_zoom = min(4, (48000000 / (self.original.width()*self.original.height()))**.5)
        self._zoom = min(max_zoom, max(.05, factor))
        scaled = self.original.scaledToWidth(max(1, round(self.original.width()*self._zoom)),
                                             Qt.TransformationMode.SmoothTransformation)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumSize(scaled.size())
        self.setPixmap(scaled)

    def zoom(self, factor):
        current = (self.pixmap().width()/self.original.width()) if self._fit and not self.original.isNull() else self._zoom
        self.set_zoom(current*factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_source()


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
        from .intake_routing import IntakeRoutingDialog
        from .workspace_guard import ModalWorkspaceGuard
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
        from ..image_ocr import transform_image
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
        from ..image_ocr import transform_image
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
        from ..image_ocr import recognize_image
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
        from ..model import DEFAULT_CONFIG
        from ..vision_ocr import estimate_image
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
            answer = QMessageBox.question(self, '确认上传当前图片 / 选区', message,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          defaultButton=QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
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


class IntakeMixin:
    def intake_editor(self, target, name='', placeholder=''):
        result = IntakeEditor(lambda mime: self.intake_from_mime(mime, target))
        result.setObjectName(name)
        result.setPlaceholderText(placeholder)
        return result

    def current_intake_target(self):
        for _, key in TARGETS:
            if getattr(self, key + '_edit').hasFocus():
                return key
        return 'materials'

    def load_input_file(self, target='materials'):
        from ..intake import FILE_FILTER
        paths, _ = QFileDialog.getOpenFileNames(self, '导入题目 / 材料 / 原答', '', FILE_FILTER)
        if paths:
            self.import_paths(paths, target)

    def import_paths(self, paths, target='materials', ask_options=True):
        from ..intake import read_inputs
        if self.workers:
            self.notify('已有任务正在运行，请完成或取消后再导入。')
            return
        page_spec, force_ocr = '', False
        ocr_options = {'mode': 'enhanced', 'columns': 1}
        if ask_options:
            options = IntakeOptionsDialog(self, target, any(Path(path).suffix.lower() == '.pdf' for path in paths))
            if options.exec() != QDialog.DialogCode.Accepted:
                return
            target = options.target_combo.currentData()
            page_spec, force_ocr = options.page_spec.text(), options.force_ocr.isChecked()
            ocr_options = {'mode': options.ocr_mode.currentData(), 'columns': options.ocr_columns.currentData()}
        self.start_job('读取本机文件', lambda progress, cancel: read_inputs(paths, progress=progress, cancel=cancel,
                       force_ocr=force_ocr, page_spec=page_spec, ocr_options=ocr_options, retain_failed=True), lambda batch: self.preview_intake(batch, target),
                       domains=())

    def import_clipboard(self):
        self.intake_from_mime(QApplication.clipboard().mimeData(), self.current_intake_target())

    def intake_from_mime(self, mime, target=None):
        from ..intake import ImportBatch, InputPage, read_image_bytes
        target = target or 'materials'
        if self.workers:
            self.notify('已有任务正在运行，请完成或取消后再导入。')
            return
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()] if mime.hasUrls() else []
        if paths:
            self.import_paths(paths, target)
        elif mime.hasImage():
            # Clipboard/QMimeData and image copying are GUI-thread only.
            image = mime.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if not isinstance(image, QImage) or image.isNull():
                self.notify('剪贴板图片不可读取，请另存图片后导入。')
                return
            if image.sizeInBytes() > 100 * 1024 * 1024:
                self.notify('图片过大，请缩小后导入。')
                return
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            ok = image.copy().save(buffer, 'PNG')
            buffer.close()
            if not ok or data.size() > 25 * 1024 * 1024:
                self.notify('图片无法编码或超过 25 MB，请缩小后导入。')
                return
            payload = bytes(data)
            options = IntakeOptionsDialog(self, target)
            if options.exec() != QDialog.DialogCode.Accepted:
                return
            target = options.target_combo.currentData()
            ocr_options = {'mode': options.ocr_mode.currentData(), 'columns': options.ocr_columns.currentData()}
            self.start_job('本机图片文字识别', lambda progress, cancel: read_image_bytes(payload, progress=progress, cancel=cancel, ocr_options=ocr_options, retain_failed=True),
                           lambda batch: self.preview_intake(batch, target), domains=())
        elif mime.hasUrls():
            self.notify('请拖入本机文件；网页链接不作为题目导入。')
        elif mime.hasText() and mime.text().strip():
            text = mime.text()
            if len(text) > 250000:
                self.notify('剪贴板文字超过 25 万字，请分批导入。')
                return
            self.preview_intake(ImportBatch(pages=[InputPage(source='剪贴板文字', number=1, text=text, method='文本')]), target)
        else:
            self.notify('剪贴板中没有可导入的文字、图片或本机文件。')

    def preview_intake(self, batch, target='materials'):
        if self._closing:
            return
        dialog = IntakePreviewDialog(batch, self, target=target)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_intake_selection(dialog.selection())

    def apply_intake_selection(self, selection):
        if selection.get('mode') == 'routing':
            if selection.get('committed'):
                self.assessment_tabs.setCurrentIndex(0)
                self.notify('已写入核对后的内容。')
            return
        if selection['fields'] is not None:
            if self.load_submission(selection['fields']) is False:
                return
        else:
            if not self.flush_workspace(archive_previous=True):
                return
            edit = getattr(self, selection['target'] + '_edit')
            old = edit.toPlainText()
            text = selection['text']
            if old and text and not selection['replace']:
                text = old + '\n\n' + text
            elif old and not text and not selection['replace']:
                text = old
            edit.setPlainText(text)
            if not self.flush_workspace():
                return
        self.assessment_tabs.setCurrentIndex(0)
        self.notify('已写入核对后的内容。')

    def reload_stale_workspace(self):
        """Explicit recovery for editors protected after a stale modal context."""
        from ..state_write import state_write
        try:
            generation = self.store.assessment_generation()
            opening = self.store.load_workspace()
            if QMessageBox.question(self, '重新载入当前草稿',
                    '将重新载入当前数据库草稿，并放弃保留的编辑内容。是否继续？',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
            with state_write(self.store, generation) as conn:
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                current = json.loads(row[0]) if row else None
                if current != opening:
                    raise ValueError('确认期间草稿又有变化，请重新载入后核对。')
                value = current or {'submission': {}, 'question_id': None}
                self.store._workspace_snapshot(value)
                self.apply_workspace(value, persist=False)
            self._workspace_context_stale = False
            self.set_workspace_paused(False)
            self.autosave_label.setText('已重新载入 · 本机草稿')
            self.notify('已载入当前数据库草稿。请重新打开相关对话框并核对。')
            return True
        except Exception as exc:
            self.notify('重新载入失败，原输入和暂停状态已保留：' + str(exc))
            return False
