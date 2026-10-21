"""Thin local snapshot manager over the authoritative raw rubric editor."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ..domain import RUBRICS, validate_rubric
from ..rubric_library import _record
from ..state_write import state_write
from .widgets import button
from .workspace_guard import ModalWorkspaceGuard


class RubricLibraryDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.store = host.store
        # Freeze epoch before any database or editor context is read.
        self.generation = self.store.assessment_generation()
        self.workspace_guard = ModalWorkspaceGuard(host, self.generation)
        try:
            self._initialize(host)
        except Exception:
            self.workspace_guard.release()
            raise

    def _initialize(self, host):
        with state_write(self.store, self.generation) as conn:
            row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
            self.workspace_guard.workspace = json.loads(row[0]) if row else None
        self.selected = None
        self.setObjectName('rubric_library_dialog')
        self.setWindowTitle('量表模板库')
        self.resize(900, 620)
        self.setMinimumSize(680, 460)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText('搜索模板名称')
        self.filter_type = QComboBox(); self.filter_type.addItem('全部题型', '')
        for kind in RUBRICS: self.filter_type.addItem(kind, kind)
        filters.addWidget(self.search_edit, 1); filters.addWidget(self.filter_type)
        layout.addLayout(filters)
        split = QSplitter()
        self.list_widget = QListWidget(); self.list_widget.setMinimumWidth(190)
        split.addWidget(self.list_widget)
        pane = QWidget(); detail = QVBoxLayout(pane); detail.setContentsMargins(8, 0, 0, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText('模板名称（1—60 字）')
        self.type_edit = QComboBox(); self.type_edit.addItems(list(RUBRICS))
        self.type_edit.setCurrentText(host.question_type.currentText())
        form.addRow('名称', self.name_edit); form.addRow('适用题型', self.type_edit)
        detail.addLayout(form)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setAccessibleName('所选模板原始 JSON')
        detail.addWidget(self.preview, 1)
        split.addWidget(pane); split.setSizes([260, 620])
        layout.addWidget(split, 1)
        self.action_buttons = []
        for actions in ((('保存当前…', self.save_current), ('载入', self.load_selected), ('复制', self.copy_selected)),
                        (('重命名', self.rename_selected), ('更新为当前内容…', self.update_selected), ('删除…', self.delete_selected))):
            line = QHBoxLayout()
            for label, callback in actions:
                control = button(label, callback)
                self.action_buttons.append(control); line.addWidget(control)
            line.addStretch(); layout.addLayout(line)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        close.rejected.connect(self.reject); layout.addWidget(close)
        self.workspace_guard.report = self.status.setText
        self.list_widget.currentItemChanged.connect(self.select_row)
        self.search_edit.textChanged.connect(self.refresh)
        self.filter_type.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def _error(self, exc):
        self.workspace_guard.protect()
        self.status.setText('操作未完成，输入已保留：' + str(exc))

    def refresh(self, *args, select_id=None):
        try:
            with state_write(self.store, self.generation):
                records = self.store.list_rubric_presets(self.search_edit.text(), self.filter_type.currentData())
            identity = select_id or (self.selected['id'] if self.selected else None)
            self.list_widget.blockSignals(True)
            try:
                self.list_widget.clear()
                selected_index = 0
                for index, record in enumerate(records):
                    item = QListWidgetItem(record['name'] + ' · ' + record['question_type'])
                    item.setData(Qt.ItemDataRole.UserRole, record)
                    self.list_widget.addItem(item)
                    if record['id'] == identity: selected_index = index
                self.list_widget.setCurrentRow(selected_index if records else -1)
            finally:
                self.list_widget.blockSignals(False)
            self.select_row(self.list_widget.currentItem())
        except Exception as exc:
            self._error(exc)

    def select_row(self, item, previous=None):
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.preview.setPlainText(self.selected['raw_json'] if self.selected else '')
        if self.selected:
            self.name_edit.setText(self.selected['name'])
            self.type_edit.setCurrentText(self.selected['question_type'])
        for control in self.action_buttons[1:]: control.setEnabled(self.selected is not None)

    def _fresh(self, conn):
        if self.selected is None:
            raise ValueError('请先选择模板。')
        row = _record(conn.execute('SELECT * FROM rubric_presets WHERE id=?', (self.selected['id'],)).fetchone())
        if row != self.selected:
            raise ValueError('模板已更新或删除，请保留输入并重新打开模板库。')
        return row

    def _current_raw(self):
        raw = self.host.rubric_edit.toPlainText()
        if not raw.strip():
            raw = json.dumps(self.host.selected_rubric_template()['rubric'], ensure_ascii=False, indent=2)
        validate_rubric(json.loads(raw))
        return raw

    def _confirm(self, title, message):
        return QMessageBox.question(self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _confirm_selected(self, title, message):
        selected = self.selected
        if selected is None:
            raise ValueError('请先选择模板。')
        if not self._confirm(title, message):
            return False
        if self.selected != selected:
            raise ValueError('确认期间所选模板已变化，请重新核对。')
        return True

    def save_current(self):
        try:
            saved = self.store.save_rubric_preset({'name':self.name_edit.text(),
                'question_type':self.type_edit.currentText(), 'raw_json':self._current_raw()},
                expected_generation=self.generation)
            self.status.clear(); self.refresh(select_id=saved['id'])
        except Exception as exc:
            self._error(exc)

    def load_selected(self):
        try:
            before = self.host.workspace_snapshot()
            if before['submission']['rubric_text'].strip() and not self._confirm_selected('覆盖自定义量表？',
                    '载入会替换当前自定义量表文字（包括未完成内容）。是否覆盖？'):
                return
            with state_write(self.store, self.generation) as conn:
                selected = self._fresh(conn)
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                current = json.loads(row[0]) if row else None
                if (self.host.workspace_snapshot() != before or current != self.workspace_guard.workspace
                        or self.host._autosave_paused or self.host._closing):
                    raise ValueError('草稿已变化或暂不可写，请保留输入并重新载入。')
                # Ordinary editor change signals own dirty state and autosave.
                self.host.rubric_edit.setPlainText(selected['raw_json'])
            self.accept()
        except Exception as exc:
            self._error(exc)

    def copy_selected(self):
        try:
            with state_write(self.store, self.generation) as conn:
                selected = self._fresh(conn)
                saved = self.store._save_rubric_preset(conn, {'name':self.name_edit.text(),
                    'question_type':self.type_edit.currentText(), 'raw_json':selected['raw_json']})
            self.status.clear(); self.refresh(select_id=saved['id'])
        except Exception as exc:
            self._error(exc)

    def rename_selected(self):
        try:
            if self.selected is None: raise ValueError('请先选择模板。')
            saved = self.store.save_rubric_preset({'name':self.name_edit.text(),
                'question_type':self.selected['question_type'], 'raw_json':self.selected['raw_json']},
                preset_id=self.selected['id'], expected_version=self.selected['version'], expected_generation=self.generation)
            self.status.clear(); self.refresh(select_id=saved['id'])
        except Exception as exc:
            self._error(exc)

    def update_selected(self):
        try:
            if self.selected is None: raise ValueError('请先选择模板。')
            before = self.host.workspace_snapshot()
            classification = self.type_edit.currentText()
            if not self._confirm_selected('更新模板内容？', '将所选模板更新为当前训练量表。已载入副本和旧报告保留。是否继续？'):
                return
            if self.host.workspace_snapshot() != before or self.type_edit.currentText() != classification:
                raise ValueError('确认期间编辑器已变化，请重新核对。')
            saved = self.store.save_rubric_preset({'name':self.selected['name'],
                'question_type':self.type_edit.currentText(), 'raw_json':self._current_raw()},
                preset_id=self.selected['id'], expected_version=self.selected['version'], expected_generation=self.generation)
            self.status.clear(); self.refresh(select_id=saved['id'])
        except Exception as exc:
            self._error(exc)

    def delete_selected(self):
        try:
            if self.selected is None: raise ValueError('请先选择模板。')
            if not self._confirm_selected('删除模板？', '仅删除库中所选模板，已载入副本和旧报告保留。是否删除？'):
                return
            self.store.delete_rubric_preset(self.selected['id'], expected_version=self.selected['version'], expected_generation=self.generation)
            self.status.clear(); self.refresh()
        except Exception as exc:
            self._error(exc)

    def done(self, result):
        self.workspace_guard.release()
        super().done(result)


class RubricLibraryUiMixin:
    def replace_rubric_draft(self, replacement, title, message):
        """Use the same guarded consent boundary for existing builtin actions."""
        guard = None
        try:
            generation = self.store.assessment_generation()
            guard = ModalWorkspaceGuard(self, generation)
            with state_write(self.store, generation) as conn:
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                workspace = json.loads(row[0]) if row else None
                guard.workspace = workspace
            before = self.workspace_snapshot()
            raw = replacement()
            if before['submission']['rubric_text'].strip() and QMessageBox.question(self,
                    title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            with state_write(self.store, generation) as conn:
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                current = json.loads(row[0]) if row else None
                if self.workspace_snapshot() != before or current != workspace or self._autosave_paused or self._closing:
                    raise ValueError('确认期间草稿或模板选择已变化，请保留输入并重新核对。')
                self.rubric_edit.setPlainText(raw)
        except Exception as exc:
            self.notify('量表未替换，原输入已保留：' + str(exc))
        finally:
            if guard is not None:
                guard.release()

    def build_rubric_library_dialog(self):
        return RubricLibraryDialog(self)

    def show_rubric_library(self):
        try:
            dialog = self.build_rubric_library_dialog()
            dialog.exec()
            dialog.deleteLater()
        except Exception as exc:
            self.notify('模板库无法打开：' + str(exc))
