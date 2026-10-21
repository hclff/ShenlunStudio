"""Local article browsing, deliberate imports and evidence-backed methods."""
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget)

from ..widgets import button, card, editor, empty_state, page, row, table
from ..teaching import TeachingPanel
from ..article_method_editor import ArticleMethodEditorDialog, MethodTrashDialog
from ..method_filters import MethodFilterBar


class MethodsMixin:
    def build_methods(self):
        widget, outer = page('写作方法库', '分别核对文章提炼与教学来源；启用前须检查证据及适用边界。')
        self.methods_tabs = QTabWidget()
        self.teaching_panel = TeachingPanel(self)
        self.methods_tabs.addTab(self.teaching_panel, '教学方法 · 名师来源')
        publication = QWidget()
        layout = QVBoxLayout(publication)
        layout.addWidget(QLabel('文章提炼方法须有至少两篇不同正文支持，沿用原有核验规则。'))
        self.methods_tabs.addTab(publication, '文章提炼方法')
        if self.store.list_cards():
            self.methods_tabs.setCurrentIndex(1)
        outer.addWidget(self.methods_tabs, 1)
        summary = QHBoxLayout()
        self.method_status = QLabel()
        self.method_status.setObjectName('muted')
        summary.addWidget(self.method_status)
        summary.addStretch()
        self.new_method_button = button('新建方法', self.new_article_method, True)
        self.deleted_methods_button = button('已删除…', self.show_deleted_article_methods)
        summary.addWidget(self.new_method_button)
        summary.addWidget(self.deleted_methods_button)
        summary.addWidget(button('刷新方法', self.refresh_methods))
        layout.addLayout(summary)
        self.article_method_filters = MethodFilterBar()
        self.article_method_search = self.article_method_filters.search
        self.article_method_type_filter = self.article_method_filters.question_type
        self.article_method_status_filter = self.article_method_filters.enabled
        self._article_filter_state = self.article_method_filters.state()
        self.article_method_filters.changed.connect(self.change_article_method_filter)
        layout.addWidget(self.article_method_filters)
        self.method_library_button = button('去文章资料库提炼', lambda: self.navigation.setCurrentRow(1), True)
        self.method_empty = empty_state('从原文中积累方法', '可点击上方新建方法保存草稿，或选择文章完成提炼，再核对原文证据与适用范围。', self.method_library_button)
        layout.addWidget(self.method_empty, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        self.method_split = split
        self.method_table = table(['方法名称', '适用题型', '证据篇数', '状态'])
        self.method_table.set_empty_text('暂无方法卡', '真实文章提炼后，候选方法会显示在这里。')
        self.method_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.method_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.method_table.itemSelectionChanged.connect(self.show_method)
        self.method_detail = editor(readonly=True)
        self.method_detail.setPlaceholderText('选择方法后，核对操作步骤、适用边界与原文证据。')
        self.method_refs = QComboBox()
        split.addWidget(self.method_table)
        split.addWidget(self.method_detail)
        self.init_article_batch(layout)
        layout.addWidget(split, 1)
        self.enable_method_button = button('启用所选方法', lambda: self.set_method_enabled(True), True)
        self.disable_method_button = button('停用所选方法', lambda: self.set_method_enabled(False))
        self.edit_method_button = button('编辑', self.edit_article_method)
        self.delete_method_button = button('删除', self.delete_article_method)
        self.method_evidence_button = button('查看证据原文', self.view_method_evidence)
        self.enable_method_button.setToolTip('启用前仍会检查证据数量、来源去重与方法有效性。')
        self.method_controls = QWidget()
        action_layout = QVBoxLayout(self.method_controls)
        action_layout.setContentsMargins(0, 0, 0, 0)
        actions = row(self.enable_method_button, self.disable_method_button, self.edit_method_button, self.delete_method_button)
        actions.addStretch()
        action_layout.addLayout(actions)
        evidence = row(self.method_refs, self.method_evidence_button)
        evidence.setStretch(0, 1)
        self.method_refs.setPlaceholderText('选择方法后查看可定位的原文证据')
        action_layout.addLayout(evidence)
        layout.addWidget(self.method_controls)
        self.method_refs.currentIndexChanged.connect(self.update_method_actions)
        return widget

    def change_article_method_filter(self):
        if self.method_crud_guarded():
            self.article_method_filters.restore(self._article_filter_state)
            return
        self.check_article_methods(False)
        self._article_filter_state = self.article_method_filters.state()
        self.refresh_methods()

    def refresh_methods(self):
        if hasattr(self, 'teaching_panel'):
            self.teaching_panel.refresh()
        selected = self.selected_card() if hasattr(self, 'cards') else None
        checked={r['id'] for r in self.checked_article_methods()}
        all_cards = self.store.list_cards()
        titles = {}
        if self.article_method_search.text().strip():
            identities = sorted({identity for card in all_cards
                                 for identity in card.get('source_article_ids', [])})
            titles = self.store.article_titles(identities)
        self.cards = [card for card in all_cards if self.article_method_filters.matches(card,
            source_titles=[titles.get(identity, '') for identity in card.get('source_article_ids', [])])]
        self.method_table.blockSignals(True)
        self.method_table.setRowCount(len(self.cards))
        self.method_table.clearSelection()
        self.method_table.setCurrentCell(-1, -1)
        for r, card in enumerate(self.cards):
            values = [card.get('name', ''), '、'.join(card.get('applicable_types', [])),
                      str(len(set(card.get('source_fingerprints', card.get('source_article_ids', []))))),
                      '已启用' if card.get('enabled') else card.get('status', '停用')]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, card.get('id'))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if card.get('id') in checked else Qt.CheckState.Unchecked)
                self.method_table.setItem(r, c, item)
            if selected and selected.get('id') == card.get('id'):
                self.method_table.selectRow(r)
        self.method_table.blockSignals(False)
        self.method_empty.setVisible(not all_cards)
        self.method_split.setVisible(bool(all_cards))
        self.method_controls.setVisible(bool(self.cards))
        self.method_status.setText(f'显示 {len(self.cards)} / {len(all_cards)} 个方法 · 已启用 {sum(bool(card.get("enabled")) for card in self.cards)} 个')
        self.show_method()

    def selected_card(self):
        r = self.method_table.currentRow()
        if r < 0 or r >= len(self.cards) or not self.method_table.selectionModel().hasSelection():
            return None
        item = self.method_table.item(r, 0)
        return next((c for c in self.cards if item and c.get('id') == item.data(Qt.ItemDataRole.UserRole)), None)

    def show_method(self):
        card = self.selected_card()
        self.method_refs.clear()
        if not card:
            self.method_detail.clear()
            self.update_method_actions()
            return
        steps = '\n'.join(f'{i}. {s}' for i, s in enumerate(card.get('steps', []), 1))
        refs = '\n'.join(f'[{r.get("id", "")}] {r.get("quote", "")}' for r in card.get('refs', []))
        self.method_detail.setPlainText(f'{card.get("name", "")}\n\n作用：{card.get("purpose", "")}\n\n步骤\n{steps}\n\n不适用情形：{card.get("not_applicable", "")}\n\n原文证据\n{refs}')
        for ref in card.get('refs', []):
            self.method_refs.addItem(ref.get('id', '') + '　' + ref.get('quote', '')[:30], ref.get('id', ''))
        if self.method_refs.count():
            self.method_refs.setCurrentIndex(0)
        self.update_method_actions()

    def update_method_actions(self):
        self.update_article_batch_actions()
        card = self.selected_card()
        idle = not bool(self.workers)
        self.new_method_button.setEnabled(idle)
        self.deleted_methods_button.setEnabled(idle)
        self.edit_method_button.setEnabled(bool(idle and card))
        self.delete_method_button.setEnabled(bool(idle and card))
        self.enable_method_button.setEnabled(bool(idle and card and not card.get('enabled')))
        self.disable_method_button.setEnabled(bool(idle and card and card.get('enabled')))
        paragraph_id = self.method_refs.currentData()
        self.method_refs.setEnabled(bool(card and self.method_refs.count()))
        self.method_evidence_button.setEnabled(bool(card and paragraph_id and ':p' in paragraph_id))

    def method_crud_guarded(self):
        if self.workers or getattr(self, '_closing', False):
            self.notify('请先完成或取消正在运行的任务。')
            return True
        return False

    def new_article_method(self):
        self.edit_article_method(new=True)

    def edit_article_method(self, new=False):
        if self.method_crud_guarded():
            return
        value = None if new else self.selected_card()
        if not new and not value:
            self.notify('请先选择方法。')
            return
        dialog = ArticleMethodEditorDialog(self.store, value, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.method_crud_guarded():
                return
            self.refresh_methods()
            identity = (dialog.saved_card or {}).get('id')
            for index, card in enumerate(self.cards):
                if card.get('id') == identity:
                    self.method_table.selectRow(index)
                    break
            self.notify('方法已保存并停用。')

    def delete_article_method(self):
        if self.method_crud_guarded():
            return
        value = self.selected_card()
        if not value:
            self.notify('请先选择方法。')
            return
        answer = QMessageBox.question(self, '删除文章提炼方法？',
            f'将“{value.get("name", "")}”移入已删除方法，可随时恢复。历史批改与快照不受影响。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes or self.method_crud_guarded():
            return
        try:
            self.store.delete_method('article', value['id'])
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.notify(str(exc))
            return
        self.refresh_methods()
        self.notify('方法已移入已删除，可恢复后重新核验。')

    def show_deleted_article_methods(self):
        if self.method_crud_guarded():
            return
        dialog = MethodTrashDialog(self.store, 'article', self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.method_crud_guarded():
                return
            self.refresh_methods()
            self.notify('方法已彻底删除，不能从已删除列表恢复。' if dialog.action=='purged' else '方法已恢复并停用。')

    def set_method_enabled(self, enabled):
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        card = self.selected_card()
        if not card:
            self.notify('请先选择方法。')
            return
        try:
            self.get_service().enable_card(card['id'], enabled)
            self.refresh_methods()
            self.notify('方法已启用。' if enabled else '方法已停用。')
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def view_method_evidence(self):
        paragraph_id = self.method_refs.currentData()
        if not paragraph_id or ':p' not in paragraph_id:
            self.notify('该方法没有可定位的原文证据。')
            return
        article_id = paragraph_id.rsplit(':p', 1)[0]
        self.navigation.setCurrentRow(1)
        self.show_article(article_id, paragraph_id)
