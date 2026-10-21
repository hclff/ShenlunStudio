"""Local teaching-method editing, provenance review and deliberate selection."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QLabel

from ..widgets import button


SCOPES = [('正文片段 · full_text', 'full_text'), ('索引正文 · index_text', 'index_text'),
          ('目录 · 不可启用', 'catalog'), ('学员笔记 · 不可启用', 'student_note')]
MODES = [('关闭', 'off'), ('综合已审核方法', 'combined'), ('指定一个来源', 'source'), ('比较 2–3 个来源', 'compare')]
STATUS = {'draft': '草稿 · 待本地审核', 'enabled': '已启用', 'disabled': '已停用 · 待重新审核'}


def label(text):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def dialog_buttons(dialog, layout, text='确认'):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(text)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
    buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return buttons
