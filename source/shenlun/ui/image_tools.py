"""Explicit image selection and non-destructive transcription review."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget)


class RoiView(QGraphicsView):
    selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(260, 220)
        self._start = None
        self._rectangle = None
        self._bounds = QRectF()
        self._fit = True

    def set_image(self, data, crop=None):
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            raise ValueError('无法显示原图')
        self.scene().clear()
        self._rectangle = None
        self.scene().addPixmap(pixmap)
        self._bounds = QRectF(pixmap.rect())
        self.setSceneRect(self._bounds)
        if crop:
            l, t, r, b = crop
            self._draw(QRectF(l * pixmap.width(), t * pixmap.height(),
                             (r-l) * pixmap.width(), (b-t) * pixmap.height()))
        self.fit_image()

    def _draw(self, rect):
        if self._rectangle is None:
            pen = QPen(QColor('#168487'), 2)
            pen.setCosmetic(True)
            self._rectangle = self.scene().addRect(rect, pen, QColor(22, 132, 135, 45))
        else:
            self._rectangle.setRect(rect)

    def fit_image(self):
        self._fit = True
        self.fitInView(self._bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor):
        self._fit = False
        scale = self.transform().m11() * factor
        if .02 <= scale <= 8:
            self.scale(factor, factor)

    def original_size(self):
        self._fit = False
        self.resetTransform()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()

    def _point(self, point):
        point = self.mapToScene(point)
        point.setX(min(self._bounds.right(), max(0, point.x())))
        point.setY(min(self._bounds.bottom(), max(0, point.y())))
        return point

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._bounds.contains(self.mapToScene(event.position().toPoint())):
            self._start = self._point(event.position().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None:
            self._draw(QRectF(self._start, self._point(event.position().toPoint())).normalized())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._start is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = QRectF(self._start, self._point(event.position().toPoint())).normalized()
            self._start = None
            if rect.width() >= 2 and rect.height() >= 2:
                self._draw(rect)
                self.selected.emit((rect.left()/self._bounds.width(), rect.top()/self._bounds.height(),
                                    rect.right()/self._bounds.width(), rect.bottom()/self._bounds.height()))
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ImageToolsDialog(QDialog):
    def __init__(self, source, rotation=0, crop=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.rotation = rotation
        self.crop = crop
        self.setWindowTitle('旋转与选区 · 仅处理当前页')
        self.resize(900, 620)
        self.setMinimumSize(700, 480)
        layout = QVBoxLayout(self)
        notice = QLabel('在左侧原图上拖出矩形选区；滚动条可查看放大的原图。旋转后请重新框选。原图始终保留。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        tools = QHBoxLayout()
        self.roi_view = RoiView()
        for text, action in [('适应', self.roi_view.fit_image), ('100%', self.roi_view.original_size),
                             ('−', lambda: self.roi_view.zoom(.8)), ('＋', lambda: self.roi_view.zoom(1.25)),
                             ('顺时针90°', self.rotate_image), ('清除选区', self.clear_crop)]:
            control = QPushButton(text)
            control.clicked.connect(action)
            tools.addWidget(control)
        layout.addLayout(tools)
        split = QSplitter()
        split.addWidget(self.roi_view)
        selected_pane = QWidget()
        selected_layout = QVBoxLayout(selected_pane)
        selected_layout.addWidget(QLabel('本次识别范围'))
        self.selected_preview = QLabel()
        self.selected_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_preview.setMinimumWidth(180)
        selected_layout.addWidget(self.selected_preview, 1)
        self.region_label = QLabel()
        self.region_label.setWordWrap(True)
        selected_layout.addWidget(self.region_label)
        split.addWidget(selected_pane)
        split.setSizes([600, 240])
        layout.addWidget(split, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('使用此范围')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.roi_view.selected.connect(self.select_crop)
        self.refresh_image()

    def refresh_image(self):
        from ..image_ocr import transform_image
        self.roi_view.set_image(transform_image(self.source, rotation=self.rotation), self.crop)
        self.refresh_selection()

    def refresh_selection(self):
        from ..image_ocr import transform_image
        selected = transform_image(self.source, rotation=self.rotation, crop=self.crop)
        pixmap = QPixmap()
        pixmap.loadFromData(selected)
        self.selected_preview.setPixmap(pixmap.scaled(210, 340, Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))
        self.region_label.setText(f'顺时针 {self.rotation}° · ' +
                                 ('选区 ' + ', '.join(f'{v:.1%}' for v in self.crop) if self.crop else '整页') +
                                 f'\n{pixmap.width()} × {pixmap.height()} 像素')

    def select_crop(self, crop):
        self.crop = crop
        self.refresh_selection()

    def rotate_image(self):
        self.rotation = (self.rotation + 90) % 360
        self.crop = None
        self.refresh_image()

    def clear_crop(self):
        self.crop = None
        self.refresh_image()


class CandidateDialog(QDialog):
    def __init__(self, old, result, region, parent=None):
        super().__init__(parent)
        self.setWindowTitle('核对候选文字')
        self.resize(850, 570)
        self.setMinimumSize(700, 450)
        layout = QVBoxLayout(self)
        notice = QLabel('候选仅来自' + region + '。采用会替换本页全部文字；也可从候选中复制所需内容。关闭此窗保留当前文字，采用后可撤销。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        split = QSplitter()
        self.old_text = QPlainTextEdit()
        self.new_text = QPlainTextEdit()
        for title, editor, text in [('当前文字（含人工修订）', self.old_text, old),
                                    ('新候选（尚未采用）', self.new_text, result['text'])]:
            pane = QWidget()
            form = QVBoxLayout(pane)
            form.addWidget(QLabel(title))
            editor.setPlainText(text)
            editor.setReadOnly(True)
            form.addWidget(editor, 1)
            split.addWidget(pane)
        layout.addWidget(split, 1)
        warnings = list(result.get('warnings', []))
        warnings.extend('需核对：' + item for item in result.get('uncertainties', []))
        self.warnings = QPlainTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setMaximumHeight(105)
        self.warnings.setPlainText('\n'.join(warnings) or '请对照原图逐字核对；没有字符置信度数据。')
        layout.addWidget(self.warnings)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('采用候选 · 替换本页文字')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('保留当前文字')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
