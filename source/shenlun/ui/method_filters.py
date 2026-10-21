"""Shared local method search and compact, reversible filter controls."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

from ..domain import RUBRICS


def method_matches(record, *, query='', question_type=None, enabled=None,
                   owner_id=None, owner_names=None, source_titles=()):
    if question_type and question_type not in record.get('applicable_types', []):
        return False
    if enabled is not None and bool(record.get('enabled')) != enabled:
        return False
    if owner_id and record.get('owner_id') != owner_id:
        return False
    terms = query.casefold().split()
    if not terms:
        return True
    fields = [record.get('name', ''), record.get('purpose', ''),
              *(record.get('steps') or []), *(record.get('checks') or []),
              *(source.get('title', '') for source in record.get('sources', [])),
              *source_titles, (owner_names or {}).get(record.get('owner_id'), '')]
    text = '\n'.join(str(value) for value in fields).casefold()
    return all(term in text for term in terms)


class MethodFilterBar(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        controls = QHBoxLayout(self)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索方法、步骤、检查或来源…')
        self.search.setAccessibleName('搜索方法')
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(140)
        self.question_type = QComboBox()
        self.question_type.setAccessibleName('方法适用题型')
        self.question_type.addItem('全部题型', None)
        for question_type in RUBRICS:
            self.question_type.addItem(question_type, question_type)
        self.enabled = QComboBox()
        self.enabled.setAccessibleName('方法启用状态')
        for label, value in [('全部状态', None), ('已启用', True), ('未启用', False)]:
            self.enabled.addItem(label, value)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.question_type)
        controls.addWidget(self.enabled)
        self.search.textChanged.connect(self.changed)
        self.question_type.currentIndexChanged.connect(self.changed)
        self.enabled.currentIndexChanged.connect(self.changed)

    def state(self):
        return self.search.text(), self.question_type.currentData(), self.enabled.currentData()

    def restore(self, state):
        # Block child signals too: callers must be able to cancel a dirty-editor change.
        for widget in (self.search, self.question_type, self.enabled):
            widget.blockSignals(True)
        self.search.setText(state[0])
        self.question_type.setCurrentIndex(self.question_type.findData(state[1]))
        self.enabled.setCurrentIndex(self.enabled.findData(state[2]))
        for widget in (self.search, self.question_type, self.enabled):
            widget.blockSignals(False)

    def matches(self, record, **context):
        query, question_type, enabled = self.state()
        return method_matches(record, query=query, question_type=question_type,
                              enabled=enabled, **context)
