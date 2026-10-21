"""Local input review: reading never changes an exercise until confirmation."""

from PySide6.QtWidgets import QComboBox, QPlainTextEdit



def local_mode_combo():
    result = QComboBox()
    result.addItem('本机增强（默认）', 'enhanced')
    result.addItem('标准识别', 'standard')
    return result


def columns_combo():
    result = QComboBox()
    result.addItem('单栏顺序', 1)
    result.addItem('双栏：先左后右', 2)
    return result


TARGETS = [('给定材料', 'materials'), ('题干', 'question'), ('我的原答', 'answer'), ('参考答案', 'reference')]


def target_combo(target):
    result = QComboBox()
    for label, key in TARGETS:
        result.addItem(label, key)
    result.setCurrentIndex(max(0, result.findData(target)))
    return result


class IntakeEditor(QPlainTextEdit):
    """Keep ordinary paste native; route image/file payloads through review."""
    def __init__(self, receive, parent=None):
        super().__init__(parent)
        self.receive = receive

    @staticmethod
    def needs_review(mime):
        return mime.hasUrls() or mime.hasImage()

    def canInsertFromMimeData(self, mime):
        return self.needs_review(mime) or super().canInsertFromMimeData(mime)

    def insertFromMimeData(self, mime):
        if self.needs_review(mime):
            self.receive(mime)
        else:
            super().insertFromMimeData(mime)

    def dragEnterEvent(self, event):
        if self.needs_review(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.needs_review(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.needs_review(event.mimeData()):
            self.receive(event.mimeData())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
