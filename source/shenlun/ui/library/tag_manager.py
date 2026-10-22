"""Tag housekeeping: rename, merge or drop a tag across the whole corpus."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout)

from ..common.dialogs import confirm_destructive, confirm_question
from ..widgets import button


class TagManagerDialog(QDialog):
    """Every tag with its article count, plus the corpus-wide edits it supports."""

    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle('管理标签')
        self.resize(520, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('重命名会把这个标签改到所有文章；合并会把所选标签并入另一个标签；'
                                '删除会从所有文章中移除该标签，文章本身保留。'
                                '每次改动前都会备份整个数据库。'))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['标签', '文章数'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemDoubleClicked.connect(lambda _: self.rename_tag())
        layout.addWidget(self.table, 1)
        self.status = QLabel('单击选中标签，双击即重命名。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.rename_button = button('重命名…', self.rename_tag)
        self.merge_button = button('合并到…', self.merge_tag)
        self.delete_button = button('删除', self.delete_tag)
        for widget in (self.rename_button, self.merge_button, self.delete_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload()

    def reload(self):
        tags = self.store.article_tags()
        self.table.setRowCount(len(tags))
        for row, (tag, count) in enumerate(tags):
            name = QTableWidgetItem(tag)
            name.setToolTip(f'{tag}：{count} 篇')
            self.table.setItem(row, 0, name)
            total = QTableWidgetItem(f'{count:,}')
            total.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 1, total)
        self._sync_buttons()

    def current_tag(self):
        row = self.table.currentRow()
        if row < 0:
            return ''
        item = self.table.item(row, 0)
        return item.text() if item else ''

    def _sync_buttons(self):
        enabled = bool(self.current_tag())
        for widget in (self.rename_button, self.merge_button, self.delete_button):
            widget.setEnabled(enabled)

    def rename_tag(self):
        tag = self.current_tag()
        if not tag:
            return
        value, ok = QInputDialog.getText(self, '重命名标签', f'把“{tag}”改成：', text=tag)
        value = str(value or '').strip()
        if not ok or not value or value == tag:
            return
        affected = len(self.store.article_ids_with_tag(tag))
        if not confirm_question(self, '确认重命名标签',
                                f'将把 {affected} 篇文章的标签“{tag}”改为“{value}”。\n'
                                '修改前会备份整个数据库。是否继续？'):
            return
        self._apply(lambda: self.store.rename_article_tag(tag, value),
                    f'已把标签“{tag}”重命名为“{value}”')

    def merge_tag(self):
        tag = self.current_tag()
        if not tag:
            return
        value, ok = QInputDialog.getText(self, '合并标签', f'把“{tag}”合并到：')
        target = str(value or '').strip()
        if not ok or not target or target == tag:
            return
        affected = len(self.store.article_ids_with_tag(tag))
        known = {name for name, _ in self.store.article_tags()}
        extra = '' if target in known else '\n目标标签还不存在，会新建。'
        if not confirm_question(self, '确认合并标签',
                                f'将把 {affected} 篇文章的标签“{tag}”并入“{target}”。{extra}\n'
                                '修改前会备份整个数据库。是否继续？'):
            return
        self._apply(lambda: self.store.rename_article_tag(tag, target),
                    f'已把标签“{tag}”合并到“{target}”')

    def delete_tag(self):
        tag = self.current_tag()
        if not tag:
            return
        affected = len(self.store.article_ids_with_tag(tag))
        if not confirm_destructive(self, '确认删除标签',
                                   f'将从 {affected} 篇文章中移除标签“{tag}”，文章本身保留。\n'
                                   '删除前自动备份整个数据库。是否继续？'):
            return
        self._apply(lambda: self.store.rename_article_tag(tag, None),
                    f'已删除标签“{tag}”')

    def _apply(self, operation, message):
        try:
            result = operation()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '标签操作失败', str(exc))
            return
        if not result.get('updated'):
            self.status.setText('没有文章使用该标签，未做改动。')
            return
        backup = Path(result['backup']).name if result.get('backup') else ''
        text = f'{message}，涉及 {result["updated"]} 篇'
        self.status.setText(text + (f'；备份 {backup}' if backup else ''))
        self.changed.emit()
        self.reload()
