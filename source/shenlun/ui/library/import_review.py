"""Review dialog for local imports: parsed rows are checked before anything is written."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout)

from ..widgets import button

TAG_SPLIT = re.compile(r'[、,，;；\s]+')


class ImportReviewDialog(QDialog):
    """Editable preview of parsed files; nothing is written until ``accept``."""
    HEADERS = ('导入', '标题', '作者', '来源', '日期', '类型', '标签', '字符', '状态')
    FIELDS = {1: 'title', 2: 'author', 3: 'journal', 4: 'publication', 5: 'kind', 6: 'tags'}
    KIND_OPTIONS = ('评论', '理论', '讲话', '政策', '案例', '其他')

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle('确认导入内容')
        self.resize(1180, 620)
        self._items = list(preview.get('items') or ())
        self._failed = list(preview.get('failed') or ())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('勾选要入库的文章，可直接修改标题、作者、来源、日期、类型与标签。'
                                '重复正文默认跳过，仍可手动勾选覆盖。'))
        layout.addLayout(self._bulk_row())
        layout.addWidget(self._table(), 1)
        self._summary = QLabel()
        layout.addWidget(self._summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('导入所选')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_summary()

    def _bulk_row(self):
        row = QHBoxLayout()
        row.addWidget(button('全选', lambda: self._check_all(True)))
        row.addWidget(button('全不选', lambda: self._check_all(False)))
        self._journal = QLineEdit()
        self._journal.setPlaceholderText('来源，如 人民日报')
        row.addWidget(self._journal, 1)
        row.addWidget(button('套用来源', lambda: self._apply(3, self._journal.text())))
        self._kind = QComboBox()
        self._kind.addItems(('',) + self.KIND_OPTIONS)
        row.addWidget(self._kind)
        row.addWidget(button('套用类型', lambda: self._apply(5, self._kind.currentText())))
        self._tags = QLineEdit()
        self._tags.setPlaceholderText('标签，用顿号分隔')
        row.addWidget(self._tags, 1)
        row.addWidget(button('套用标签', lambda: self._apply(6, self._tags.text())))
        return row

    def _table(self):
        self.table = QTableWidget(len(self._items), len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        for row, item in enumerate(self._items):
            check = QTableWidgetItem('')
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked if item.get('duplicate') else Qt.CheckState.Checked)
            self.table.setItem(row, 0, check)
            values = {'title': item.get('title', ''), 'author': item.get('author', ''),
                      'journal': item.get('journal', ''), 'publication': item.get('publication', ''),
                      'kind': item.get('kind', ''), 'tags': '、'.join(item.get('tags') or [])}
            for column, key in self.FIELDS.items():
                cell = QTableWidgetItem(str(values.get(key, '')))
                cell.setToolTip(item.get('path', ''))
                self.table.setItem(row, column, cell)
            self.table.setItem(row, 7, QTableWidgetItem(str(item.get('characters') or 0)))
            status = '重复' if item.get('duplicate') else '可导入'
            state = QTableWidgetItem(status)
            state.setForeground(QColor('#b45309' if item.get('duplicate') else '#15803d'))
            state.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 8, state)
            if item.get('duplicate'):
                for column in range(len(self.HEADERS)):
                    self.table.item(row, column).setBackground(QColor('#fef3c7'))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(lambda *_: self._refresh_summary())
        return self.table

    def _check_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def _apply(self, column, value):
        value = str(value or '').strip()
        if not value:
            return
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                self.table.item(row, column).setText(value)

    def _refresh_summary(self):
        checked = sum(1 for row in range(self.table.rowCount())
                      if self.table.item(row, 0).checkState() == Qt.CheckState.Checked)
        duplicates = sum(1 for item in self._items if item.get('duplicate'))
        text = f'共 {len(self._items)} 条，已勾选 {checked} 条，其中重复正文 {duplicates} 条'
        if self._failed:
            reasons = '；'.join(f"{item.get('name') or item.get('path')}：{item.get('error')}"
                               for item in self._failed[:3])
            text += f'；读取失败 {len(self._failed)} 条（{reasons}）'
        self._summary.setText(text)

    def items(self):
        """Checked rows with the values currently shown in the table."""
        result = []
        for row, source in enumerate(self._items):
            if self.table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            item = dict(source)
            for column, key in self.FIELDS.items():
                item[key] = self.table.item(row, column).text().strip()
            item['tags'] = [tag for tag in TAG_SPLIT.split(item['tags']) if tag]
            result.append(item)
        return result
