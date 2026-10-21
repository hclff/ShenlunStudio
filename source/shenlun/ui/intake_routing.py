"""Buffered, explicit paragraph allocation and atomic workspace adoption."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from ..intake_routing import FIELDS, compose_routed_fields, split_routing_pages
from .workspace_guard import ModalWorkspaceGuard
from .widgets import button

LABELS = {'question': '题干与要求', 'materials': '给定材料', 'answer': '我的原答', 'reference': '参考答案'}


def route_combo():
    combo = QComboBox()
    combo.addItem('不导入', '')
    for field, label in LABELS.items():
        combo.addItem(label, field)
    return combo


class IntakeRoutingDialog(QDialog):
    def __init__(self, preview, context, *, _workspace_guard=None):
        super().__init__(preview)
        self.preview = preview
        self.host = preview.parentWidget()
        self.workspace_guard = _workspace_guard or ModalWorkspaceGuard(
            self.host, context['generation'], context['workspace'], preview.report_message)
        try:
            self._initialize(context)
        except Exception:
            # The entry retains ownership until this constructor returns.
            if _workspace_guard is None:
                self.workspace_guard.release()
            raise

    def _initialize(self, context):
        preview = self.preview
        self.context = deepcopy(context)
        self.source = preview.routing_source_snapshot()
        self.segments = split_routing_pages(preview.routing_pages())
        self.saved = None
        self._adopting = False
        self.setWindowTitle('分配导入内容')
        self.resize(1050, 720)
        self.setMinimumSize(760, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('按原文顺序分配段落'))
        batch = QHBoxLayout()
        self.batch_combo = route_combo()
        batch.addWidget(QLabel('勾选段落统一分配到'))
        batch.addWidget(self.batch_combo)
        batch.addWidget(button('应用到勾选段落', self.assign_checked))
        batch.addStretch()
        layout.addLayout(batch)
        splitter = QSplitter()
        self.table = QTableWidget(len(self.segments), 3)
        self.table.setHorizontalHeaderLabels(['来源 / 页码', '原文段落', '写入位置'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(2, 145)
        self.target_combos = []
        for row, segment in enumerate(self.segments):
            source = QTableWidgetItem(f'{Path(segment["source"]).name}\n第 {segment["number"]} 页')
            source.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            source.setCheckState(Qt.CheckState.Unchecked)
            source.setToolTip(segment['source'])
            self.table.setItem(row, 0, source)
            text = QPlainTextEdit(segment['text'])
            text.setReadOnly(True)
            self.table.setCellWidget(row, 1, text)
            combo = route_combo()
            self.target_combos.append(combo)
            self.table.setCellWidget(row, 2, combo)
            combo.currentIndexChanged.connect(self.refresh_preview)
            self.table.setRowHeight(row, 100)
        splitter.addWidget(self.table)
        pane = QWidget()
        right = QVBoxLayout(pane)
        right.setContentsMargins(8, 0, 0, 0)
        right.addWidget(QLabel('四栏最终内容预览'))
        tabs = QTabWidget()
        self.previews = {}
        self.replace_checks = {}
        for field in FIELDS:
            tab = QWidget(); tab_layout = QVBoxLayout(tab)
            check = QCheckBox('替换已有内容（默认追加）')
            self.replace_checks[field] = check
            check.toggled.connect(self.refresh_preview)
            tab_layout.addWidget(check)
            edit = QPlainTextEdit(); edit.setReadOnly(True)
            self.previews[field] = edit
            tab_layout.addWidget(edit)
            tabs.addTab(tab, LABELS[field])
        right.addWidget(tabs)
        splitter.addWidget(pane)
        splitter.setSizes([620, 410])
        layout.addWidget(splitter, 1)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认写入')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('返回核对')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.refresh_preview()

    def assignments(self):
        return {segment['id']: combo.currentData() for segment, combo in zip(self.segments, self.target_combos)}

    def replacements(self):
        return {field for field, check in self.replace_checks.items() if check.isChecked()}

    def composed(self):
        before = self.context['before']['submission']
        return compose_routed_fields(self.segments, self.assignments(),
                                     {field: before[field] for field in FIELDS}, self.replacements())

    def assign_checked(self):
        for row, combo in enumerate(self.target_combos):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                combo.setCurrentIndex(combo.findData(self.batch_combo.currentData()))

    def refresh_preview(self, *args):
        assigned = set(self.assignments().values())
        for field, check in self.replace_checks.items():
            check.setEnabled(field in assigned)
        try:
            values = self.composed()
            for field, edit in self.previews.items():
                edit.setPlainText(values[field])
            self.status.clear()
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(assigned - {''}))
        except ValueError as exc:
            self.status.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def validate_context(self):
        if self.preview._busy or self.preview._reviewing or self.source != self.preview.routing_source_snapshot():
            raise ValueError('来源已改变，请返回核对并重新分配。')
        if self.host.workspace_snapshot() != self.context['before']:
            raise ValueError('编辑器已改变，请保留当前输入并重新载入后分配。')
        if self.host.store.assessment_generation() != self.context['generation']:
            raise ValueError('数据库已恢复，请重新载入后确认。')
        if getattr(self.host, '_autosave_paused', False) or getattr(self.host, '_closing', False):
            raise ValueError('草稿暂不可写，请重新载入后确认。')

    def protect_stale_workspace(self):
        """Do not let cancellation resume legacy autosave over a newer row."""
        self.workspace_guard.protect()

    def accept(self):
        if self.saved is not None or self._adopting:
            return
        self._adopting = True
        try:
            self.validate_context()
            values = self.composed()
            assigned = set(self.assignments().values()) - {''}
            if not assigned:
                raise ValueError('请至少分配一个段落。')
            after = deepcopy(self.context['before'])
            after['submission'].update(values)
            # Full workspace capacity includes settings and unfinished rubric text.
            self.host.store._workspace_snapshot(after)
            replaced = [LABELS[field] for field in FIELDS if field in self.replacements()
                        and field in assigned and self.context['before']['submission'][field]]
            if replaced and QMessageBox.question(self, '确认替换内容',
                    '将替换以下已有内容：\n' + '\n'.join(replaced),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            # Confirmation runs a nested event loop. Never refresh the frozen
            # generation or baselines after it: they are the user's consent context.
            self.validate_context()
            saved = self.host.store.commit_routed_workspace(self.context['before'], after,
                expected_workspace=self.context['workspace'], expected_generation=self.context['generation'])
            self.host.apply_workspace(saved, persist=False)
            self.host.autosave_label.setText('已保存 · 本机草稿')
            self.saved = saved
            self.preview._routed_saved = saved
            super().accept()
        except Exception as exc:
            self.protect_stale_workspace()
            self.status.setText('写入失败，分配内容已保留：' + str(exc))
        finally:
            self._adopting = False

    def done(self, result):
        self.workspace_guard.release(committed=self.saved is not None)
        super().done(result)
