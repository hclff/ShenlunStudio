"""Read-only table models for long local lists.

Rows hold scalar summaries only, so a table can show thousands of entries
without deserializing article bodies or building one widget item per cell.
"""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class ArticleSummaryModel(QAbstractTableModel):
    """One row per article summary as returned by ``Store.list_article_summaries``."""
    HEADERS = ('文章标题', '来源', '年份', '类型', '正文状态')

    def __init__(self, rows=None):
        super().__init__()
        self._rows = list(rows or ())
        self._ids = [row['id'] for row in self._rows]

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self._ids = [row['id'] for row in self._rows]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row['id']
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        if column == 0:
            return row['title']
        if column == 1:
            return row.get('journal', '')
        if column == 2:
            return str(row.get('year') or '')
        if column == 3:
            return row.get('kind', '')
        if row.get('status') != 'available':
            return '缺失'
        return '有文本' if row.get('has_body') else '仅图片'

    def id_at(self, row):
        return self._ids[row]

    def row_at(self, row):
        return self._rows[row]

    def index_of(self, article_id):
        try:
            return self._ids.index(article_id)
        except ValueError:
            return -1
