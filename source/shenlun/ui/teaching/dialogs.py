"""Local teaching-method editing, provenance review and deliberate selection."""
import json
import sqlite3

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget)

from ...teaching import normalize_teaching_selection
from ..widgets import editor

from .constants import MODES, dialog_buttons, label


class TeachingReviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('审核并启用'); self.resize(540, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(label('核对来源、引文和适用范围。'))
        form = QFormLayout(); self.reviewer = QLineEdit(); self.reviewer.setMaxLength(200)
        self.note = editor(placeholder='填写核对说明…')
        form.addRow('审核人', self.reviewer); form.addRow('审核说明', self.note)
        layout.addLayout(form); self.message = label('请填写审核人和说明。'); layout.addWidget(self.message)
        dialog_buttons(self, layout, '继续')

    def accept(self):
        if not self.reviewer.text().strip() or not self.note.toPlainText().strip():
            self.message.setText('请填写审核人和说明。'); return
        if len(self.note.toPlainText()) > 4000:
            self.message.setText('审核说明不能超过 4000 字。'); return
        super().accept()


class TeachingJsonPreviewDialog(QDialog):
    def __init__(self, records, filename, parent=None):
        super().__init__(parent); self.setWindowTitle('导入预览'); self.resize(740, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f'{filename} · {len(records)} 条；导入后为停用草稿。'))
        preview = editor(readonly=True); preview.setPlainText(json.dumps(records, ensure_ascii=False, indent=2))
        layout.addWidget(preview, 1); dialog_buttons(self, layout, '导入')


class TeachingSourcePreviewDialog(QDialog):
    def __init__(self, batch, parent=None):
        super().__init__(parent); self.setWindowTitle('来源片段'); self.resize(760, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(label('请核对识别文字，最多 4000 字。'))
        sources = '\n'.join(f'{p.source} · 第 {p.number} 页 · {p.method}' for p in batch.pages)
        self.provenance = editor(readonly=True); self.provenance.setMaximumHeight(95)
        self.provenance.setPlainText(sources + '\n' + '\n'.join(batch.errors)); layout.addWidget(self.provenance)
        self.text = editor(); self.text.setPlainText('\n\n'.join(p.text for p in batch.pages if p.text))
        layout.addWidget(self.text, 1); self.message = label('取消不会修改当前内容。'); layout.addWidget(self.message)
        dialog_buttons(self, layout, '使用此片段')

    def accept(self):
        if not self.text.toPlainText().strip() or len(self.text.toPlainText()) > 4000:
            self.message.setText('请选择 1–4000 字的来源片段后确认。'); return
        super().accept()


class TeachingSelectionDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent); self.store = store; self.host = parent
        self.setWindowTitle('批改中的方法辅助'); self.resize(600, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(label('仅使用已启用且适合本题的方法，最多 6 项。'))
        self.mode = QComboBox()
        for text, value in MODES: self.mode.addItem(text, value)
        layout.addWidget(self.mode)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); owners_layout = QVBoxLayout(body)
        self.owner_checks = {}
        counts = {}
        for row in store.list_teaching_methods(True): counts[row['owner_id']] = counts.get(row['owner_id'], 0) + 1
        for owner in store.list_teaching_owners():
            check = QCheckBox(owner['name'])
            check.setToolTip(f'{owner["description"]}\n已启用 {counts.get(owner["id"], 0)}')
            self.owner_checks[owner['id']] = check; owners_layout.addWidget(check)
        scroll.setWidget(body); layout.addWidget(scroll, 1)
        self.message = label('指定 1 项；比较 2–3 项。'); layout.addWidget(self.message)
        try: selected = normalize_teaching_selection(store.get_setting('teaching_selection'))
        except ValueError: selected = normalize_teaching_selection(None)
        self.mode.setCurrentIndex(self.mode.findData(selected['mode']))
        if any(identity not in self.owner_checks for identity in selected['owner_ids']):
            selected = normalize_teaching_selection(None)
            self.mode.setCurrentIndex(self.mode.findData(selected['mode']))
        for identity in selected['owner_ids']: self.owner_checks[identity].setChecked(True)
        self.mode.currentIndexChanged.connect(self.update_mode); self.update_mode()
        dialog_buttons(self, layout, '保存本机选择')

    def update_mode(self):
        explicit = self.mode.currentData() in ('source', 'compare')
        for check in self.owner_checks.values(): check.setEnabled(explicit)

    def accept(self):
        if getattr(self.host, 'workers', set()): self.message.setText('请先完成或取消正在运行的任务。'); return
        try:
            selection = normalize_teaching_selection({'mode': self.mode.currentData(),
                'owner_ids': [key for key, check in self.owner_checks.items() if check.isChecked()]})
            live = {o['id'] for o in self.store.list_teaching_owners()}
            if any(identity not in live for identity in selection['owner_ids']):
                raise ValueError('主体已变化，请重新打开选择窗口。')
            self.store.set_setting('teaching_selection', selection)
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc: self.message.setText(str(exc)); return
        super().accept()
