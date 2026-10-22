"""Review extracted candidates before any of them reaches the active library."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidgetItem,
    QVBoxLayout)

from ...method_batch import set_methods_enabled
from ...method_library import merge_article_methods, validate_article_card
from ..article_method_editor import ArticleMethodEditorDialog
from ..method_batch import MethodBatchResultDialog, confirm_batch
from ..widgets import button, table

INELIGIBLE = QColor('#8a8a8a')


def candidate_cards(store):
    """Only extraction candidates: manual drafts and enabled cards stay out."""
    return [card for card in store.list_cards()
            if not card.get('enabled') and card.get('status') == '候选方法，尚未启用']


def evidence_count(card):
    fingerprints = card.get('source_fingerprints') or list((card.get('source_versions') or {}).values())
    return len(set(fingerprints)) or len(set(card.get('source_article_ids') or []))


def eligibility(store, card, cache=None):
    """Return ``(usable, reason)``; cached per dialog load to keep scrolling cheap."""
    key = card.get('id')
    if cache is not None and key in cache:
        return cache[key]
    try:
        validate_article_card(store, card)
        verdict = (True, '')
    except ValueError as exc:
        verdict = (False, str(exc))
    if cache is not None:
        cache[key] = verdict
    return verdict


class CandidatePoolDialog(QDialog):
    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle('候选方法池')
        self.resize(920, 580)
        self._cache = {}
        self.cards = []
        layout = QVBoxLayout(self)
        heading = QLabel('候选方法池')
        heading.setObjectName('section_title')
        layout.addWidget(heading)
        hint = QLabel('提炼出的方法默认停用。证据来自至少两篇不同正文才可启用；'
                      '相近方法可合并证据后再启用，合并不会补写步骤，请核对名称与步骤。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        self.only_eligible = QCheckBox('只看可启用')
        self.only_eligible.setToolTip('隐藏证据不足、暂时不能启用的候选；默认全部显示，便于合并相近方法。')
        self.only_eligible.toggled.connect(self.load)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索方法名称…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.load)
        controls.addWidget(self.only_eligible)
        controls.addWidget(self.search, 1)
        self.select_button = button('全选可启用', self.select_eligible)
        self.clear_button = button('清空勾选', self.clear_checks)
        controls.addWidget(self.select_button)
        controls.addWidget(self.clear_button)
        layout.addLayout(controls)
        self.table = table(['', '方法名称', '适用题型', '证据篇数', '可用性'])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.set_empty_text('候选池为空', '在文章资料库点击“提取筛选方法…”，候选方法会先进入这里。')
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 320)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.update_actions)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.merge_button = button('合并所选…', self.merge_selected)
        self.merge_button.setToolTip('以表格中第一张为保留项，合并其余勾选候选的原文证据。')
        self.edit_button = button('编辑', self.edit_selected)
        self.enable_button = button('批量启用…', self.enable_selected, True)
        self.enable_button.setToolTip('仍会逐项复核证据；不满足条件的保持停用并列出原因。')
        footer.addWidget(self.merge_button)
        footer.addWidget(self.edit_button)
        footer.addWidget(self.enable_button)
        footer.addWidget(button('关闭', self.accept))
        layout.addLayout(footer)
        self.load()

    def load(self):
        self._cache = {}
        needle = self.search.text().strip().lower()
        cards = [card for card in candidate_cards(self.store)
                 if not needle or needle in card.get('name', '').lower()]
        if self.only_eligible.isChecked():
            cards = [card for card in cards if self.verdict(card)[0]]
        cards.sort(key=lambda card: (not self.verdict(card)[0], card.get('name', '')))
        self.cards = cards
        self.table.blockSignals(True)
        self.table.setRowCount(len(cards))
        for row, card in enumerate(cards):
            usable, reason = self.verdict(card)
            check = QTableWidgetItem('')
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if usable else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, card['id'])
            self.table.setItem(row, 0, check)
            name = QTableWidgetItem(card.get('name', ''))
            name.setToolTip(reason or '证据充分，可启用。')
            if not usable:
                name.setForeground(QBrush(INELIGIBLE))
            self.table.setItem(row, 1, name)
            types = QTableWidgetItem('、'.join(card.get('applicable_types') or []))
            if not usable:
                types.setForeground(QBrush(INELIGIBLE))
            self.table.setItem(row, 2, types)
            count = QTableWidgetItem(str(evidence_count(card)))
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not usable:
                count.setForeground(QBrush(INELIGIBLE))
            self.table.setItem(row, 3, count)
            state = QTableWidgetItem('可启用' if usable else '证据不足')
            state.setToolTip(reason or '证据来自至少两篇不同正文。')
            if not usable:
                state.setForeground(QBrush(INELIGIBLE))
            self.table.setItem(row, 4, state)
        self.table.blockSignals(False)
        self.update_actions()

    def verdict(self, card):
        return eligibility(self.store, card, self._cache)

    def checked_cards(self):
        checked = {self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                   for row in range(self.table.rowCount())
                   if self.table.item(row, 0)
                   and self.table.item(row, 0).checkState() == Qt.CheckState.Checked}
        return [card for card in self.cards if card['id'] in checked]

    def checked_order(self):
        """Checked cards in visual order; the first one is the merge target."""
        order = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                card = next((card for card in self.cards if card['id'] == item.data(Qt.ItemDataRole.UserRole)), None)
                if card is not None:
                    order.append(card)
        return order

    def select_eligible(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and self.cards[row] and self.verdict(self.cards[row])[0]:
                item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self.update_actions()

    def clear_checks(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.update_actions()

    def update_actions(self, *args):
        checked = self.checked_cards()
        usable = sum(1 for card in self.cards if self.verdict(card)[0])
        self.status.setText(f'候选 {len(self.cards)} 张 · 可启用 {usable} 张 · 勾选 {len(checked)} 项')
        self.select_button.setEnabled(bool(self.cards))
        self.clear_button.setEnabled(bool(checked))
        self.enable_button.setEnabled(bool(checked))
        self.merge_button.setEnabled(len(checked) > 1)
        self.edit_button.setEnabled(0 <= self.table.currentRow() < len(self.cards))

    def enable_selected(self):
        records = self.checked_cards()
        if not records:
            self.status.setText('请先勾选需要启用的候选方法。')
            return
        if not confirm_batch(self, records, True):
            return
        results = set_methods_enabled(self.store, 'article', records, True)
        self.changed.emit()
        self.load()
        MethodBatchResultDialog(results, self).exec()

    def merge_selected(self):
        cards = self.checked_order()
        if len(cards) < 2:
            self.status.setText('请勾选至少两张候选方法，第一张作为保留项。')
            return
        target = cards[0]
        names = '\n'.join('• ' + card.get('name', card['id']) for card in cards)
        message = (f'将把勾选的 {len(cards)} 张候选方法合并为一张：\n{names}\n\n'
                   f'保留“{target.get("name", "")}”的名称与步骤，合并其余候选的原文证据；'
                   '其余方法移入已删除方法（可恢复）。步骤差异请合并后核对。是否继续？')
        from ..common.dialogs import confirm_destructive
        if not confirm_destructive(self, '确认合并候选方法', message):
            return
        try:
            result = merge_article_methods(self.store, target['id'], [card['id'] for card in cards[1:]])
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.changed.emit()
        self.load()
        note = f'已合并 {result["merged"]} 张候选，共 {result["refs"]} 条原文证据。'
        if result['failed']:
            note += f' 另有 {len(result["failed"])} 张未能移入已删除方法。'
        self.status.setText(note)

    def edit_selected(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.cards):
            return
        dialog = ArticleMethodEditorDialog(self.store, self.cards[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.changed.emit()
            self.load()
