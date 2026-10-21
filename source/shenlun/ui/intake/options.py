"""Local input review: reading never changes an exercise until confirmation."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout)

from ..widgets import button, card, section_label

from .controls import columns_combo, local_mode_combo, target_combo


class IntakeOptionsDialog(QDialog):
    def __init__(self, parent, target='materials', pdf=False):
        super().__init__(parent)
        self.setWindowTitle('读取前选择')
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(section_label('选择读取方式'))
        notice = QLabel('读取完成后会先显示预览，确认后才写入。图片识别在本机运行。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        options, options_layout = card()
        form = QFormLayout()
        form.setSpacing(12)
        self.target_combo = target_combo(target)
        form.addRow('写入位置', self.target_combo)
        self.ocr_mode = local_mode_combo()
        self.ocr_columns = columns_combo()
        form.addRow('图片识别', self.ocr_mode)
        form.addRow('图片栏序', self.ocr_columns)
        self.page_spec = QLineEdit()
        self.page_spec.setPlaceholderText('留空读取全部；例如 1-3,5；对每份 PDF 分别生效')
        self.page_spec.setEnabled(pdf)
        form.addRow('PDF 页码', self.page_spec)
        self.force_ocr = QCheckBox('PDF 全部使用本机 OCR（适合乱码或文字层不准）')
        self.force_ocr.setEnabled(pdf)
        form.addRow(self.force_ocr)
        options_layout.addLayout(form)
        layout.addWidget(options)
        limits = QLabel('最多 20 个文件、单个 25 MB；每份 PDF 最多 40 页；每批最多 80 页、25 万字。')
        limits.setWordWrap(True)
        limits.setObjectName('muted')
        layout.addWidget(limits)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始读取')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
