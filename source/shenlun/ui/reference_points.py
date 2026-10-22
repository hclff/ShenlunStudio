"""Explicit, versioned local reference points with inspectable material evidence."""
from copy import deepcopy
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..reference_points import validate_reference_points
from .widgets import button, row


STATUS = {'draft': '草稿', 'confirmed': '人工已确认', 'withdrawn': '已撤回'}


class ReferencePointsDialog(QDialog):
    def __init__(self, store, question_id, parent=None):
        super().__init__(parent)
        self.store, self.question_id = store, question_id
        question = store.get_question(question_id)
        if not question:
            raise ValueError('请先保存并选择一道题目。')
        self.submission = deepcopy(question['submission'])
        self._current = None
        self.expected_version = 0
        self.setWindowTitle('人工参考要点 · ' + question['title'])
        self.resize(1000, 680); self.setMinimumSize(850, 580)
        layout = QVBoxLayout(self)
        notice = QLabel('要点用于训练复盘，并非官方评分标准；请引用右侧实际 M 段落原文。')
        notice.setWordWrap(True); layout.addWidget(notice)
        self.state_label = QLabel(); self.state_label.setTextFormat(Qt.TextFormat.PlainText); self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        tabs = QTabWidget(); layout.addWidget(tabs, 1)
        editor = QWidget(); editor_layout = QVBoxLayout(editor)
        split = QSplitter()
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0,0,0,0)
        self.point_list = QListWidget(); left_layout.addWidget(self.point_list)
        left_layout.addLayout(row(button('添加要点', self.add_point), button('移除要点', self.remove_point)))
        split.addWidget(left)
        center = QWidget(); center_layout = QVBoxLayout(center); center_layout.setContentsMargins(0,0,0,0)
        form = QFormLayout(); self.point_id = QLineEdit(); form.addRow('要点编号', self.point_id)
        center_layout.addLayout(form)
        center_layout.addWidget(QLabel('要点内容'))
        self.point_text = QPlainTextEdit(); self.point_text.setPlaceholderText('用自己的话归纳材料要点'); center_layout.addWidget(self.point_text, 1)
        center_layout.addWidget(QLabel('原文引用 · 每行一个段落及精确引文，可多条'))
        self.refs_table = QTableWidget(0, 2); self.refs_table.setHorizontalHeaderLabels(['M 段落', '材料原文'])
        self.refs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.refs_table.setColumnWidth(0, 75); center_layout.addWidget(self.refs_table, 1)
        center_layout.addLayout(row(button('添加引用', self.add_reference), button('移除引用', self.remove_reference)))
        split.addWidget(center)
        self.materials_view = QPlainTextEdit(); self.materials_view.setReadOnly(True)
        paragraphs = [p.strip() for p in self.submission.get('materials', '').split('\n') if p.strip()]
        self.materials_view.setPlainText('题干\n' + self.submission.get('question', '') + '\n\n' +
            '\n\n'.join(f'M{i}\n{text}' for i, text in enumerate(paragraphs, 1)))
        split.addWidget(self.materials_view); split.setSizes([170, 430, 330]); editor_layout.addWidget(split, 1)
        metadata = QFormLayout(); self.reviewer = QLineEdit(); self.reviewer.setMaxLength(200)
        self.note = QLineEdit(); self.note.setMaxLength(4000)
        metadata.addRow('确认 / 撤回人', self.reviewer); metadata.addRow('备注 / 撤回原因', self.note)
        editor_layout.addLayout(metadata)
        editor_layout.addLayout(row(button('保存草稿', self.save_points), button('明确确认当前要点', lambda: self.save_points(confirm=True)),
                                   button('撤回生效版本', self.withdraw_points)))
        tabs.addTab(editor, '编辑与材料')
        history = QWidget(); history_layout = QVBoxLayout(history)
        history_layout.addWidget(QLabel('只读历史；选择旧版不会改变当前编辑内容或生效版本。'))
        self.history_list = QListWidget(); self.history_list.setMaximumHeight(150)
        self.history_detail = QPlainTextEdit(); self.history_detail.setReadOnly(True)
        history_layout.addWidget(self.history_list); history_layout.addWidget(self.history_detail, 1)
        history_layout.addWidget(button('重新载入最新版本到编辑区…', self.reload_latest))
        tabs.addTab(history, '版本历史')
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message); layout.addLayout(row(button('关闭', self.reject)))
        self.point_list.currentItemChanged.connect(self.show_point)
        self.history_list.currentItemChanged.connect(self.inspect_history)
        self.load_latest()

    def load_latest(self):
        latest = self.store.get_reference_points(self.question_id)
        self.expected_version = latest['version'] if latest else 0
        self._current = None; self.point_list.clear()
        for point in latest['points'] if latest else []:
            item = QListWidgetItem(point['id'] + ' · ' + point['text'][:30])
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(point)); self.point_list.addItem(item)
        self.reviewer.setText(latest['reviewer'] if latest else '')
        self.note.setText(latest['note'] if latest else '')
        self.point_list.setCurrentRow(0); self.show_point(self.point_list.currentItem())
        self.refresh_history()

    def refresh_history(self):
        self.history_list.clear()
        for record in self.store.list_reference_versions(self.question_id):
            item = QListWidgetItem(f'v{record["version"]} · {STATUS[record["status"]]} · {record["reviewer"]} · {record["created_at"][:19]}')
            item.setData(Qt.ItemDataRole.UserRole, record); self.history_list.addItem(item)
        self.history_list.setCurrentRow(0)
        active = self.store.get_active_reference_points(self.question_id)
        self.state_label.setText(f'编辑基于 v{self.expected_version}；' +
            (f'当前生效：v{active["version"]}（{active["reviewer"]}）' if active else '当前没有生效的人工确认要点'))

    def inspect_history(self, item, previous=None):
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.history_detail.setPlainText(json.dumps(value, ensure_ascii=False, indent=2) if value else '尚无版本。')

    def save_current_point(self):
        if self._current is None:
            return
        refs = []
        for index in range(self.refs_table.rowCount()):
            refs.append({key: self.refs_table.item(index, col).text() if self.refs_table.item(index, col) else ''
                         for col, key in enumerate(('id', 'quote'))})
        value = {'id': self.point_id.text(), 'text': self.point_text.toPlainText(), 'material_refs': refs}
        self._current.setData(Qt.ItemDataRole.UserRole, value)
        self._current.setText(value['id'] + ' · ' + value['text'][:30])

    def show_point(self, item, previous=None):
        self.save_current_point(); self._current = item
        value = item.data(Qt.ItemDataRole.UserRole) if item else {}
        self.point_id.setText(value.get('id', '')); self.point_text.setPlainText(value.get('text', ''))
        self.refs_table.setRowCount(0)
        for ref in value.get('material_refs', []):
            self.add_reference(ref['id'], ref['quote'])
        for control in (self.point_id, self.point_text, self.refs_table):
            control.setEnabled(item is not None)

    def add_point(self):
        self.save_current_point()
        ids = {self.point_list.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(self.point_list.count())}
        number = 1
        while 'P' + str(number) in ids: number += 1
        item = QListWidgetItem('P' + str(number))
        item.setData(Qt.ItemDataRole.UserRole, {'id': 'P'+str(number), 'text':'', 'material_refs':[]})
        self.point_list.addItem(item); self.point_list.setCurrentItem(item)

    def remove_point(self):
        self._current = None; self.point_list.takeItem(self.point_list.currentRow())
        self.show_point(self.point_list.currentItem())

    def add_reference(self, source_id='M1', quote=''):
        if isinstance(source_id, bool): source_id = 'M1'
        index = self.refs_table.rowCount(); self.refs_table.insertRow(index)
        self.refs_table.setItem(index, 0, QTableWidgetItem(source_id)); self.refs_table.setItem(index, 1, QTableWidgetItem(quote))

    def remove_reference(self):
        self.refs_table.removeRow(self.refs_table.currentRow())

    def points(self):
        self.save_current_point()
        return [deepcopy(self.point_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.point_list.count())]

    def save_points(self, checked=False, *, confirm=False):
        try:
            points = validate_reference_points(self.points(), self.submission, allow_empty=not confirm)
            if confirm:
                if not self.reviewer.text().strip(): raise ValueError('明确确认前请填写确认人。')
                result = QMessageBox.question(self, '确认人工参考要点',
                    '确认人：' + self.reviewer.text() + '\n确认已核对每条材料原文，并将当前要点作为本题后续训练依据？\n这不是官方评分标准。',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, defaultButton=QMessageBox.StandardButton.No)
                if result != QMessageBox.StandardButton.Yes: return
            saved = self.store.save_reference_points(self.question_id, points, reviewer=self.reviewer.text(),
                note=self.note.text(), confirm=confirm, expected_version=self.expected_version)
            self.expected_version = saved['version']; self.refresh_history()
            self.message.setText('已保存 v' + str(saved['version']) + ' · ' + STATUS[saved['status']])
        except Exception as exc:
            self.message.setText(str(exc) + '\n当前编辑保留；可在历史页检查更新。')

    def withdraw_points(self):
        try:
            if not self.reviewer.text().strip() or not self.note.text().strip():
                raise ValueError('撤回请填写撤回人和原因。')
            if QMessageBox.question(self, '撤回人工参考要点', '撤回后本题将没有生效要点；历史版本仍保留。是否撤回？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
            saved = self.store.withdraw_reference_points(self.question_id, self.reviewer.text(), self.note.text(), expected_version=self.expected_version)
            self.expected_version = saved['version']; self.refresh_history(); self.message.setText('已撤回，历史保留。')
        except Exception as exc: self.message.setText(str(exc))

    def reload_latest(self):
        if QMessageBox.question(self, '重新载入最新版本', '将替换编辑区尚未保存的文字。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes: self.load_latest()
