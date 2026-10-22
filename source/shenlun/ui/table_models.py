"""Read-only table models for long local lists.

Rows hold scalar summaries only, so a table can show thousands of entries
without deserializing article bodies or building one widget item per cell.

Rows may also arrive in pages: ``append_rows`` keeps the model alive while the
corpus loads the next slice, and ``index_of`` stays cheap through a dict.
"""
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

#: Extra role the title delegate reads; the model owns the matching rule so the
#: highlight can never disagree with what ``instr`` selected.
HighlightRole = Qt.ItemDataRole.UserRole + 1
_SPLIT = re.compile(r'[\s,，、;；]+')


def split_terms(text):
    """Same separators as ``Store`` so highlights match the rows that were found."""
    if isinstance(text, str):
        return tuple(term for term in _SPLIT.split(text.strip()) if term)
    return tuple(str(term).strip() for term in text if str(term).strip())


class ArticleSummaryModel(QAbstractTableModel):
    """One row per article summary as returned by ``Store.list_article_summaries``."""
    HEADERS = ('文章标题', '来源', '年份', '类型', '正文状态', '标签')

    def __init__(self, rows=None):
        super().__init__()
        self._rows = list(rows or ())
        self._positions = {}
        self._highlight = ()
        self._reindex()

    def _reindex(self):
        self._positions = {row['id']: position for position, row in enumerate(self._rows)}

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self._reindex()
        self.endResetModel()

    def append_rows(self, rows):
        rows = list(rows)
        if not rows:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        for offset, row in enumerate(rows):
            self._positions[row['id']] = start + offset
        self.endInsertRows()

    def set_highlight(self, terms):
        """Terms the title delegate should mark; a string is split like a query."""
        terms = split_terms(terms)
        if terms == self._highlight:
            return
        self._highlight = terms
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0),
                                  [HighlightRole])

    def highlight_terms(self):
        return self._highlight

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None

    @staticmethod
    def tag_text(row):
        return '、'.join(tag for tag in str(row.get('tags') or '').split('\n') if tag)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row['id']
        if role == HighlightRole:
            return self._highlight if index.column() == 0 and self._highlight else None
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == len(self.HEADERS) - 1:
            return self.tag_text(row) or None
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
        if column == len(self.HEADERS) - 1:
            return self.tag_text(row)
        if row.get('status') != 'available':
            return '缺失'
        return '有文本' if row.get('has_body') else '仅图片'

    def id_at(self, row):
        return self._rows[row]['id']

    def row_at(self, row):
        return self._rows[row]

    def index_of(self, article_id):
        return self._positions.get(article_id, -1)
