"""Local article-method drafts with original-text evidence and reversible removal."""
from copy import deepcopy
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..domain import RUBRICS
from .widgets import button, editor


def note(text):
    value = QLabel(text)
    value.setWordWrap(True)
    value.setTextFormat(Qt.TextFormat.PlainText)
    return value


def busy(parent):
    host = getattr(parent, 'host', parent)
    return bool(getattr(host, 'workers', ()) or getattr(host, '_closing', False))


class ArticleMethodEditorDialog(QDialog):
    """Saving stays within the dialog so validation errors retain every field."""
    def __init__(self, store, value=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.host = parent
        self.original = deepcopy(value or {})
        self.refs = deepcopy(self.original.get('refs', []))
        self.saved_card = None
        self.setWindowTitle('编辑文章提炼方法' if value else '新建文章提炼方法')
        self.resize(800, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(note('保存后为停用草稿；启用前须有两篇正文引文。'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        form = self.form_tab('方法与适用范围')
        self.name = QLineEdit(self.original.get('name', ''))
        self.name.setMaxLength(100)
        self.purpose = self.small_editor(self.original.get('purpose', ''), 65)
        self.steps = self.small_editor('\n'.join(self.original.get('steps', [])), 110)
        self.boundary = self.small_editor(self.original.get('not_applicable', ''), 70)
        form.addRow('方法名称', self.name)
        form.addRow('作用', self.purpose)
        form.addRow('步骤（每行一条）', self.steps)
        types = QWidget()
        types_layout = QVBoxLayout(types)
        types_layout.setContentsMargins(0, 0, 0, 0)
        self.type_checks = {}
        for kind in RUBRICS:
            check = QCheckBox(kind)
            check.setChecked(kind in self.original.get('applicable_types', []))
            self.type_checks[kind] = check
            types_layout.addWidget(check)
        form.addRow('适用题型（可多选）', types)
        form.addRow('不适用情形与边界', self.boundary)
        form = self.form_tab('原文证据')
        self.article_search = QLineEdit()
        self.article_search.setPlaceholderText('搜索文章标题或正文…')
        self.article_search.setClearButtonEnabled(True)
        search = QHBoxLayout()
        search.addWidget(self.article_search, 1)
        search.addWidget(button('搜索', self.search_articles))
        form.addRow(search)
        self.article_list = QListWidget()
        self.article_list.setMinimumHeight(85)
        self.article_list.setMaximumHeight(120)
        form.addRow('选择本机文章', self.article_list)
        self.paragraph_select = QComboBox()
        form.addRow('选择原文段落', self.paragraph_select)
        self.paragraph_text = self.small_editor('', 105, readonly=True)
        form.addRow('原文（可选中文字）', self.paragraph_text)
        form.addRow(button('剪取选中文字为引文', self.use_selected_quote))
        self.quote = self.small_editor('', 65)
        self.quote.setPlaceholderText('引文须与上方原文一致。')
        form.addRow('精确引文', self.quote)
        form.addRow(button('添加证据', self.add_evidence))
        self.evidence_list = QListWidget()
        self.evidence_list.setMinimumHeight(85)
        self.evidence_list.setMaximumHeight(130)
        form.addRow('已添加证据', self.evidence_list)
        form.addRow(button('移除所选证据', self.remove_evidence))
        self.message = note('内容仅保存在本机。')
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存草稿')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.article_search.returnPressed.connect(self.search_articles)
        self.article_list.currentItemChanged.connect(self.select_article)
        self.paragraph_select.currentIndexChanged.connect(self.select_paragraph)
        self.refresh_evidence()
        self.search_articles()

    @staticmethod
    def small_editor(text, height, readonly=False):
        field = editor(readonly=readonly)
        field.setPlainText(text)
        field.setMinimumHeight(height)
        field.setMaximumHeight(height)
        return field

    def form_tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body)
        self.tabs.addTab(scroll, title)
        return form

    def guarded(self):
        if busy(self.host):
            self.message.setText('请先完成或取消正在运行的任务。编辑内容已保留。')
            return True
        return False

    def search_articles(self):
        try:
            articles = self.store.list_articles(query=self.article_search.text().strip(), limit=200)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
            return
        self.article_list.clear()
        for article in articles:
            if (not article.get('body', '').strip() or article.get('status') != 'available'
                    or article.get('training_eligible') is False):
                continue
            item = QListWidgetItem(article['title'])
            item.setData(Qt.ItemDataRole.UserRole, article['id'])
            self.article_list.addItem(item)
        if not self.article_list.count():
            self.message.setText('没有匹配的正文文章。')
        else:
            self.message.setText('结果过多，请缩小搜索范围。')

    def select_article(self, current=None, previous=None):
        self.paragraph_select.clear()
        self.paragraph_text.clear()
        if current is None:
            return
        try:
            article = self.store.get_article(current.data(Qt.ItemDataRole.UserRole))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
            return
        for paragraph in (article or {}).get('paragraphs', []):
            self.paragraph_select.addItem(paragraph['id'] + '　' + paragraph['text'][:50], paragraph)

    def select_paragraph(self):
        paragraph = self.paragraph_select.currentData()
        self.paragraph_text.setPlainText(paragraph.get('text', '') if paragraph else '')
        self.quote.clear()

    def use_selected_quote(self):
        selected = self.paragraph_text.textCursor().selectedText().replace('\u2029', '\n')
        if not selected:
            self.message.setText('请先在原文段落中选中需要引用的文字。')
            return
        self.quote.setPlainText(selected)

    def add_evidence(self):
        if self.guarded():
            return
        paragraph = self.paragraph_select.currentData()
        quote = self.quote.toPlainText().strip()
        if not paragraph or not quote or quote not in paragraph['text']:
            self.message.setText('请选择原文段落，并填写原样位于该段落中的精确引文。')
            return
        ref = {'id': paragraph['id'], 'quote': quote}
        if ref not in self.refs:
            self.refs.append(ref)
            self.refresh_evidence()
        self.message.setText('已添加原文证据；保存后仍需单独核验启用。')

    def refresh_evidence(self):
        self.evidence_list.clear()
        for ref in self.refs:
            self.evidence_list.addItem(ref.get('id', '') + '　' + ref.get('quote', ''))

    def remove_evidence(self):
        if self.guarded():
            return
        index = self.evidence_list.currentRow()
        if 0 <= index < len(self.refs):
            self.refs.pop(index)
            self.refresh_evidence()

    def accept(self):
        if self.guarded():
            return
        value = {'name': self.name.text().strip(), 'purpose': self.purpose.toPlainText().strip(),
                 'steps': [step.strip() for step in self.steps.toPlainText().splitlines() if step.strip()],
                 'applicable_types': [kind for kind, check in self.type_checks.items() if check.isChecked()],
                 'not_applicable': self.boundary.toPlainText().strip(), 'refs': deepcopy(self.refs)}
        try:
            self.saved_card = self.store.save_manual_article_method(value, identity=self.original.get('id'),
                expected_card=self.original if self.original.get('id') else None)
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
            return
        super().accept()


class MethodTrashDialog(QDialog):
    """Restore a deleted method or permanently remove one confirmed local record."""
    def __init__(self, store, kind, parent=None):
        super().__init__(parent)
        self.store, self.kind, self.host = store, kind, parent
        self.action = None
        self.setWindowTitle('已删除方法')
        self.resize(630, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(note('已删除方法保留在本机；恢复后保持停用。'))
        self.method_list = QListWidget()
        layout.addWidget(self.method_list, 1)
        self.message = note('选择需要恢复或彻底删除的方法。')
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        self.restore_button = button('恢复所选方法', self.restore_selected, True)
        self.restore_button.setEnabled(False)
        actions.addWidget(self.restore_button)
        self.purge_button = button('彻底删除…', self.purge_selected)
        self.purge_button.setEnabled(False)
        actions.addWidget(self.purge_button)
        actions.addStretch()
        actions.addWidget(button('关闭', self.reject))
        layout.addLayout(actions)
        self.method_list.currentRowChanged.connect(self.update_actions)
        try:
            for record in self.store.list_deleted_methods(kind):
                item = QListWidgetItem(record['name'] + '　' + str(record.get('deleted_at', '')))
                item.setData(Qt.ItemDataRole.UserRole, record['id'])
                item.setData(Qt.ItemDataRole.UserRole + 1, deepcopy(record))
                self.method_list.addItem(item)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
        else:
            if not self.method_list.count():
                self.message.setText('暂无已删除方法。')

    def update_actions(self):
        selected = self.method_list.currentRow() >= 0 and not busy(self.host)
        self.restore_button.setEnabled(selected)
        self.purge_button.setEnabled(selected)

    def purge_selected(self):
        if busy(self.host):
            self.message.setText('请先完成或取消正在运行的任务。')
            return
        item = self.method_list.currentItem()
        if item is None:
            return
        record = item.data(Qt.ItemDataRole.UserRole + 1)
        answer = QMessageBox.question(self, '彻底删除方法？',
            f'确定彻底删除“{record["name"]}”？\n删除后不能从已删除列表恢复。'
            '\n历史批改 / 冻结评测保留已使用快照。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if busy(self.host):
            self.message.setText('请先完成或取消正在运行的任务。')
            return
        try:
            self.store.purge_method(self.kind, record['id'], expected_deleted_at=record['deleted_at'])
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
            return
        self.method_list.takeItem(self.method_list.row(item))
        self.update_actions()
        self.action = 'purged'
        super().accept()

    def restore_selected(self):
        if busy(self.host):
            self.message.setText('请先完成或取消正在运行的任务。')
            return
        item = self.method_list.currentItem()
        if item is None:
            return
        try:
            self.store.restore_method(self.kind, item.data(Qt.ItemDataRole.UserRole))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.message.setText(str(exc))
            return
        self.action = 'restored'
        super().accept()
