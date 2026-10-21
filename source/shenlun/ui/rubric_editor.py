"""Visual presentation over the existing authoritative raw JSON draft."""
import copy
import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..domain import validate_rubric
from .widgets import button


class _JsonNumber(str):
    """Exact numeric token, constructed only by json.loads numeric callbacks."""


def _load_draft_json(text):
    # The parser validates the entire document and numeric grammar before creating
    # tokens. An edited cell such as '1, "injected": 2' can never become a token.
    return json.loads(text, parse_float=_JsonNumber, parse_int=_JsonNumber)


def _dump_draft_json(value, level=0):
    """Emit parser-produced numbers exactly; let json escape all ordinary text."""
    if isinstance(value, _JsonNumber):
        return str(value)
    if isinstance(value, (dict, list)):
        if not value:
            return '{}' if isinstance(value, dict) else '[]'
        padding = '  ' * (level + 1)
        if isinstance(value, dict):
            items = [json.dumps(key, ensure_ascii=False) + ': ' + _dump_draft_json(item, level + 1)
                     for key, item in value.items()]
            opening, closing = '{', '}'
        else:
            items = [_dump_draft_json(item, level + 1) for item in value]
            opening, closing = '[', ']'
        return opening + '\n' + ',\n'.join(padding + item for item in items) + '\n' + '  ' * level + closing
    return json.dumps(value, ensure_ascii=False)


class RubricJsonDialog(QDialog):
    def __init__(self, text, commit, parent=None):
        super().__init__(parent)
        self.setObjectName('rubric_json_dialog')
        self.setWindowTitle('高级 JSON · 训练量表')
        self.resize(740, 540)
        self._commit = commit
        layout = QVBoxLayout(self)
        self.json_edit = QPlainTextEdit()
        self.json_edit.setObjectName('rubric_json_buffer')
        self.json_edit.setPlainText(text)
        layout.addWidget(self.json_edit, 1)
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        text = self.json_edit.toPlainText()
        try:
            validate_rubric(json.loads(text))
        except (ValueError, TypeError) as exc:
            self.error_label.setText(str(exc))
            return
        self._commit(text)
        super().accept()


class RubricEditor(QWidget):
    """Rendering never writes raw text; only explicit user edits do so."""
    def __init__(self, raw_edit, template, copy_template, restore_template, parent=None):
        super().__init__(parent)
        self.raw_edit = raw_edit
        self.template = template
        self._rows = None
        self._editable = False
        self._writing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        self.copy_button = button('编辑为自定义', copy_template, name='rubric_copy_button')
        self.restore_button = button('恢复模板…', restore_template, name='rubric_restore_button')
        self.advanced_button = button('高级 JSON…', self.show_json, name='rubric_advanced_button')
        controls.addWidget(self.copy_button)
        controls.addWidget(self.restore_button)
        self.add_button = button('添加', self.add_row, name='rubric_add_button')
        self.delete_button = button('删除', self.delete_rows, name='rubric_delete_button')
        self.add_button.setToolTip('添加维度（最多 20 个）')
        self.delete_button.setToolTip('删除所选维度')
        controls.addWidget(self.add_button)
        controls.addWidget(self.delete_button)
        controls.addStretch()
        controls.addWidget(self.advanced_button)
        layout.addLayout(controls)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName('rubric_table')
        self.table.setAccessibleName('训练量表维度')
        self.table.setHorizontalHeaderLabels(['维度名称', '权重 %', '说明'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setMinimumSize(260, 100)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.verticalHeader().setMinimumSectionSize(36)
        header = self.table.horizontalHeader()
        header.setStyleSheet('QHeaderView::section { padding: 4px 8px; }')
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(60)
        header.resizeSection(0, 110)
        header.resizeSection(1, 85)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.table, 1)
        self.total_label = QLabel()
        self.total_label.setTextFormat(Qt.TextFormat.PlainText)
        self.total_label.setObjectName('rubric_total')

    def refresh(self):
        raw = self.raw_edit.toPlainText()
        custom = bool(raw.strip())
        error = None
        try:
            rows = _load_draft_json(raw) if custom else self.template()['rubric']
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('请在高级 JSON 中修复量表结构')
        except (ValueError, TypeError) as exc:
            rows = None
            error = str(exc)
        self._editable = custom and rows is not None
        self.restore_button.setEnabled(custom)
        if not self._writing:
            self._rows = copy.deepcopy(rows)
            selected = self.table.currentRow()
            self.table.blockSignals(True)
            try:
                self.table.setRowCount(len(rows) if rows is not None else 0)
                for index, dimension in enumerate(rows or []):
                    for column, key in enumerate(('name', 'weight', 'description')):
                        value = dimension.get(key, '')
                        item = QTableWidgetItem(value if isinstance(value, str) else str(value))
                        item.setToolTip(item.text())
                        if column == 0:
                            item.setData(Qt.ItemDataRole.UserRole, dimension.get('id'))
                        self.table.setItem(index, column, item)
                if 0 <= selected < self.table.rowCount():
                    self.table.setCurrentCell(selected, 0)
            finally:
                self.table.blockSignals(False)
        triggers = (QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                    | QAbstractItemView.EditTrigger.AnyKeyPressed) if self._editable else QAbstractItemView.EditTrigger.NoEditTriggers
        self.table.setEditTriggers(triggers)
        total = None
        try:
            if rows is not None:
                # Keep validation identical to submission. Exact tokens belong
                # only to the editor model, never the domain/service contract.
                validate_rubric(json.loads(raw) if custom else rows)
                total = sum((Decimal(str(row.get('weight'))) for row in rows), Decimal(0))
                if not total.is_finite():
                    total = None
        except (ValueError, TypeError, InvalidOperation) as exc:
            error = str(exc)
        display = format(total.normalize(), 'f') + '%' if total is not None else ''
        self.total_label.setText((('合计 ' + display + ' · ' if display else '') + '未完成')
                                 if error else '合计 ' + display)
        self.total_label.setToolTip(error or '')
        self._update_actions()

    def _update_actions(self):
        count = len(self._rows) if self._rows is not None else 0
        self.add_button.setEnabled(self._editable and count < 20)
        self.delete_button.setEnabled(self._editable and bool(self.table.selectionModel().selectedRows()))

    def _write_rows(self):
        self._writing = True
        try:
            self.raw_edit.setPlainText(_dump_draft_json(self._rows))
        finally:
            self._writing = False

    def _cell_changed(self, item):
        if not self._editable or self._rows is None:
            return
        key = ('name', 'weight', 'description')[item.column()]
        value = item.text()
        if key == 'weight':
            try:
                parsed = _load_draft_json(value)
                if isinstance(parsed, (_JsonNumber, int, float)) and not isinstance(parsed, bool):
                    value = parsed
            except ValueError:
                pass  # Unfinished numeric cells remain an autosaved invalid draft.
        self._rows[item.row()][key] = value
        self.table.blockSignals(True)
        try:
            item.setToolTip(item.text())
        finally:
            self.table.blockSignals(False)
        self._write_rows()

    def add_row(self):
        if not self._editable or len(self._rows) >= 20:
            return
        self._rows.append({'id':'custom_' + uuid4().hex, 'name':'新维度', 'weight':0, 'description':''})
        self._write_rows()
        self.refresh()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.scrollToBottom()

    def delete_rows(self):
        if not self._editable:
            return
        for index in sorted((item.row() for item in self.table.selectionModel().selectedRows()), reverse=True):
            del self._rows[index]
        self._write_rows()
        self.refresh()

    def build_json_dialog(self):
        raw = self.raw_edit.toPlainText()
        if not raw.strip():
            try:
                raw = json.dumps(self.template()['rubric'], ensure_ascii=False, indent=2)
            except (ValueError, TypeError):
                pass
        return RubricJsonDialog(raw, self.raw_edit.setPlainText, self)

    def show_json(self):
        dialog = self.build_json_dialog()
        dialog.exec()
        dialog.deleteLater()
