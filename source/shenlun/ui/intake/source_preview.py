"""Local input review: reading never changes an exercise until confirmation."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy



class SourcePreview(QLabel):
    """Full source with fit width or scrollable pixel zoom."""
    def __init__(self):
        super().__init__('此页无原图')
        self.original = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip('适应宽度，或放大后使用滚动条查看原图。')

    def set_source(self, pixmap=None):
        self.original = pixmap or QPixmap()
        self.setMinimumHeight(0)
        if self.original.isNull():
            self.clear()
            self.setText('此页无原图')
        else:
            self.fit_source()

    def fit_source(self):
        self._fit = True
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if not self.original.isNull():
            width = self.parentWidget().width() if self.parentWidget() else self.width()
            # Fit may shrink the retained source, never enlarge a narrow/tall page.
            target_width = min(self.original.width(), max(1, width - 8))
            scaled = self.original.scaledToWidth(target_width, Qt.TransformationMode.SmoothTransformation)
            self.setPixmap(scaled)
            self.setMinimumHeight(scaled.height())

    def set_zoom(self, factor):
        if self.original.isNull():
            return
        self._fit = False
        max_zoom = min(4, (48000000 / (self.original.width()*self.original.height()))**.5)
        self._zoom = min(max_zoom, max(.05, factor))
        scaled = self.original.scaledToWidth(max(1, round(self.original.width()*self._zoom)),
                                             Qt.TransformationMode.SmoothTransformation)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumSize(scaled.size())
        self.setPixmap(scaled)

    def zoom(self, factor):
        current = (self.pixmap().width()/self.original.width()) if self._fit and not self.original.isNull() else self._zoom
        self.set_zoom(current*factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_source()
