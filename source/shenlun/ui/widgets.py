"""Shared, deliberately small presentation helpers."""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QColor,QDesktopServices, QFont, QFontDatabase,QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTableView, QTableWidget, QVBoxLayout, QWidget,QSizePolicy)


STYLE = r'''
QWidget{font-family:"Microsoft YaHei UI","Microsoft YaHei";font-size:13px;color:#334155;}
QMainWindow,QDialog{background:#f3f5f9;}
QLabel{background:transparent;}
QWidget#page{background:transparent;}
QLabel#heading{font-size:25px;font-weight:700;color:#152d45;}
QLabel#muted,QLabel[role="muted"]{color:#718096;font-size:12px;}
QLabel#section_label{font-size:14px;font-weight:600;color:#263f56;}
QWidget#card,QGroupBox{background:white;border:1px solid #e0e7ef;border-radius:10px;}
QLabel#card_title{font-size:15px;font-weight:600;color:#223b53;}
QLabel#stat_value{font-size:23px;font-weight:700;color:#233e56;}
QLabel#stat_label{font-size:11px;color:#7a8da3;}
QLabel#empty_title{font-size:16px;font-weight:600;color:#40536b;}
QLabel#empty_description{color:#8492a6;font-size:12px;}
QWidget#sidebar{background:#132b43;border:none;}
QLabel#brand{font-size:20px;font-weight:700;color:#f5f9ff;}
QLabel#brand_tagline{font-size:11px;color:#90a7bf;}
QLabel#nav_section{font-size:11px;color:#7d96b1;padding:16px 14px 4px;}
QListWidget#navigation{background:transparent;border:none;color:#b6c7d9;outline:0;font-size:14px;}
QListWidget#navigation::item{padding:13px 12px;margin:3px 6px;border:0;border-radius:7px;}
QListWidget#navigation::item:hover{background:#203b56;color:white;}
QListWidget#navigation::item:selected{background:#244c64;color:#f3fffd;border-left:3px solid #42c9b0;}
QWidget#sidebar_model{background:#1c3851;border:1px solid #29465d;border-radius:8px;}
QLabel#connection_label{color:#d1dfed;font-size:12px;}
QLabel#sidebar_note{color:#849eb8;font-size:11px;}
QPushButton#sidebar_help{background:transparent;border:none;color:#b5c7d8;text-align:left;}
QPushButton#sidebar_help:hover{background:#203b56;color:white;}
QWidget#topbar{background:white;border-bottom:1px solid #e3e9f0;}
QLabel#breadcrumb{color:#63748a;font-size:12px;}
QLabel#badge{border-radius:5px;padding:5px 9px;color:#64748b;background:#eff3f8;font-size:11px;}
QLabel#badge[tone="success"]{background:#e5f5ef;color:#16745f;}
QLabel#badge[tone="warning"]{background:#fff5df;color:#956516;}
QWidget#statusbar{background:white;border-top:1px solid #e3e9f0;}
QLabel#status_symbol{color:#0d9488;font-size:16px;}
QLabel#status_label{color:#63748a;font-size:11px;}
QLabel#status_label[tone="error"],QLabel#status_symbol[tone="error"]{color:#b64a47;}
QLabel#status_label[tone="success"],QLabel#status_symbol[tone="success"]{color:#16806d;}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QDateEdit{background:#fff;border:1px solid #dce4ee;border-radius:6px;padding:7px 9px;selection-background-color:#c8eae4;min-height:18px;}
QLineEdit:hover,QComboBox:hover,QSpinBox:hover{border-color:#b6c9d8;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus,QSpinBox:focus,QTextEdit:focus{border:1px solid #3aa998;}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled{background:#f4f6f9;color:#9ba8b7;}
QComboBox::drop-down{border:none;width:24px;}
QComboBox::down-arrow{image:url("ICON_ROOT/chevron-down.svg");width:14px;height:14px;}
QSpinBox,QDoubleSpinBox{padding-right:24px;}
QSpinBox::up-button,QDoubleSpinBox::up-button{width:20px;subcontrol-origin:border;subcontrol-position:top right;border-left:1px solid #e0e7ef;}
QSpinBox::down-button,QDoubleSpinBox::down-button{width:20px;subcontrol-origin:border;subcontrol-position:bottom right;border-left:1px solid #e0e7ef;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{image:url("ICON_ROOT/chevron-up.svg");width:11px;height:11px;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{image:url("ICON_ROOT/chevron-down.svg");width:11px;height:11px;}
QTextEdit,QPlainTextEdit,QTextBrowser{background:white;border:1px solid #e0e7ef;border-radius:7px;padding:10px;selection-background-color:#c6e8e1;font-size:14px;}
QPlainTextEdit[readOnly="true"],QTextBrowser{background:#fcfdff;}
QPushButton{background:white;border:1px solid #d8e2ec;border-radius:6px;padding:8px 13px;min-height:17px;color:#3b5269;}
QPushButton:hover{background:#f0f6f8;border-color:#9ebdc6;color:#1f6170;}
QPushButton:pressed{background:#e4eff0;}
QPushButton[role="primary"]{background:#148779;border:1px solid #148779;color:white;font-weight:600;}
QPushButton[role="primary"]:hover{background:#107566;border-color:#107566;color:white;}
QPushButton[role="quiet"]{border:none;background:transparent;color:#677d92;}
QPushButton[role="quiet"]:hover{background:#edf3f7;color:#1e566f;}
QPushButton:disabled{color:#a0adbb;background:#f1f4f8;border-color:#e6ebf1;}
QTabWidget::pane{border:1px solid #e0e7ef;border-radius:8px;background:white;top:-1px;}
QTabBar::tab{background:transparent;color:#7d8da1;padding:11px 16px;border-bottom:2px solid transparent;margin-right:4px;}
QTabBar::tab:selected{color:#118372;border-bottom:2px solid #199b87;font-weight:600;}
QTabBar::tab:hover{color:#147d72;background:#edf6f3;}
QTableWidget,QTableView,QListWidget,QListView{background:white;border:1px solid #e0e7ef;border-radius:7px;alternate-background-color:#f8fafc;selection-background-color:#e3f3f0;selection-color:#23594f;outline:none;}
QTableWidget::item,QTableView::item{padding:5px 8px;border-bottom:1px solid #f0f3f7;}
QTableWidget::item:selected,QTableView::item:selected{background:#e3f3f0;color:#205c50;}
QTableWidget::item:hover,QTableView::item:hover{background:#f0f7f6;}
QListWidget::item,QListView::item{padding:9px;border-bottom:1px solid #f1f4f8;}
QListWidget::item:selected,QListView::item:selected{background:#e3f3f0;color:#205c50;}
QHeaderView::section{background:#f7f9fc;border:none;border-bottom:1px solid #e3e9f1;padding:10px 8px;color:#6a7b90;font-size:12px;font-weight:600;}
QTableCornerButton::section{background:#f7f9fc;border:0;}
QSplitter::handle{background:transparent;width:8px;height:8px;}
QSplitter::handle:hover{background:#d9e9e5;}
QScrollArea{border:none;background:transparent;}
QScrollBar:vertical{background:transparent;width:9px;margin:2px;}
QScrollBar::handle:vertical{background:#cbd5e1;border-radius:3px;min-height:30px;}
QScrollBar::handle:vertical:hover{background:#9bafc2;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
QScrollBar:horizontal{background:transparent;height:9px;margin:2px;}
QScrollBar::handle:horizontal{background:#cbd5e1;border-radius:3px;min-width:30px;}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}
QGroupBox{font-weight:600;margin-top:16px;padding:20px 14px 12px;}
QGroupBox::title{subcontrol-origin:margin;left:16px;padding:0 5px;color:#354c62;}
QProgressBar{border:0;background:#e8f1ee;border-radius:0;height:3px;}
QProgressBar::chunk{background:#24ad96;}
QMenuBar{background:white;border-bottom:1px solid #e6ebf1;padding:2px 8px;font-size:12px;}
QMenuBar::item{padding:5px 12px;}
QMenuBar::item:selected,QMenu::item:selected{background:#e7f3f0;color:#167e6c;}
QMenu{background:white;border:1px solid #dbe4ee;padding:6px;}
QMenu::item{padding:8px 28px 8px 12px;}
QToolTip{background:#203b52;color:white;border:none;padding:7px;font-size:12px;}
'''
STYLE=STYLE.replace('ICON_ROOT',(Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))/'assets'/'ui').as_posix())


def ensure_fonts():
    """Qt offscreen on Windows does not enumerate installed fonts by itself."""
    app=QApplication.instance()
    if app and not app.property('workbench_style'):
        app.setStyle('Fusion')
        palette=app.palette()
        for role,color in ((QPalette.ColorRole.Window,'#f3f5f9'),(QPalette.ColorRole.WindowText,'#334155'),
                           (QPalette.ColorRole.Base,'#ffffff'),(QPalette.ColorRole.Text,'#334155'),
                           (QPalette.ColorRole.Button,'#ffffff'),(QPalette.ColorRole.ButtonText,'#334155'),
                           (QPalette.ColorRole.Highlight,'#d9eee8'),(QPalette.ColorRole.HighlightedText,'#205c50')):
            palette.setColor(role,QColor(color))
        app.setPalette(palette);app.setProperty('workbench_style',True)
    if os.name == 'nt' and 'Microsoft YaHei' not in QFontDatabase.families():
        directory = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
            path = directory / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    if 'Microsoft YaHei' in QFontDatabase.families():
        QApplication.instance().setFont(QFont('Microsoft YaHei', 10))


def page(title, subtitle):
    widget = QWidget()
    widget.setObjectName('page')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(24, 20, 24, 16)
    layout.setSpacing(12)
    heading = QLabel(title)
    heading.setObjectName('heading')
    layout.addWidget(heading)
    desc = QLabel(subtitle)
    desc.setObjectName('muted')
    desc.setWordWrap(True)
    layout.addWidget(desc)
    return widget, layout


def button(text, callback, primary=False, name=''):
    result = QPushButton(text)
    result.setObjectName(name or ('primary' if primary else ''))
    result.setProperty('role','primary' if primary else 'secondary')
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.clicked.connect(callback)
    return result


def editor(name='', placeholder='', readonly=False):
    result = QPlainTextEdit()
    result.setObjectName(name)
    result.setPlaceholderText(placeholder)
    result.setReadOnly(readonly)
    return result


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(9)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


class WorkbenchTable(QTableWidget):
    """Empty-state copy lives in the viewport and never creates fake data rows."""
    def __init__(self,columns):
        super().__init__(0,columns)
        self.empty_hint=QLabel(self.viewport())
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_hint.setStyleSheet('color:#8492a6;background:transparent;font-size:13px;padding:24px;')
        self.set_empty_text('暂无内容','导入资料或调整筛选条件后，内容会显示在这里。')

    def set_empty_text(self,title,description=''):
        self.empty_hint.setText(title+'\n\n'+description)
        self.update_empty()

    def update_empty(self):
        self.empty_hint.setGeometry(self.viewport().rect())
        self.empty_hint.setVisible(self.rowCount()==0)

    def setRowCount(self,count):
        super().setRowCount(count)
        if hasattr(self,'empty_hint'):self.update_empty()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'empty_hint'):self.update_empty()

    def showEvent(self,event):
        super().showEvent(event);self.update_empty()


def table(headers):
    result = WorkbenchTable(len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.setAlternatingRowColors(True)
    result.verticalHeader().hide()
    result.verticalHeader().setDefaultSectionSize(40)
    result.setShowGrid(False)
    result.setWordWrap(False)
    result.setTextElideMode(Qt.TextElideMode.ElideRight)
    result.setMouseTracking(True)
    result.horizontalHeader().setStretchLastSection(True)
    return result


class ModelTable(QTableView):
    """Model-backed table for long lists; only visible rows are laid out.

    Shares the QTableWidget styling and the empty-state hint so switching a
    table to a model cannot change what the workspace looks like.
    """
    def __init__(self, model):
        super().__init__()
        self.setModel(model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(40)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setMouseTracking(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.empty_hint = QLabel(self.viewport())
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_hint.setStyleSheet('color:#8492a6;background:transparent;font-size:13px;padding:24px;')
        self.set_empty_text('暂无内容', '导入资料或调整筛选条件后，内容会显示在这里。')
        model.modelReset.connect(self.update_empty)
        model.rowsInserted.connect(lambda *_: self.update_empty())
        model.rowsRemoved.connect(lambda *_: self.update_empty())

    def set_empty_text(self, title, description=''):
        self.empty_hint.setText(title + '\n\n' + description)
        self.update_empty()

    def update_empty(self):
        self.empty_hint.setGeometry(self.viewport().rect())
        model = self.model()
        self.empty_hint.setVisible(model is None or model.rowCount() == 0)

    def rowCount(self):
        model = self.model()
        return model.rowCount() if model is not None else 0

    def selected_ids(self, role=Qt.ItemDataRole.UserRole):
        model = self.model()
        if model is None or not hasattr(model, 'id_at'):
            return []
        return [model.id_at(index.row()) for index in self.selectionModel().selectedRows()]

    def selected_rows(self):
        model = self.model()
        if model is None or not hasattr(model, 'row_at'):
            return []
        return [model.row_at(index.row()) for index in self.selectionModel().selectedRows()]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_empty()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_empty()


def model_table(model):
    return ModelTable(model)


def card(title='',subtitle=''):
    widget=QWidget();widget.setObjectName('card')
    layout=QVBoxLayout(widget);layout.setContentsMargins(16,14,16,14);layout.setSpacing(10)
    if title:
        label=QLabel(title);label.setObjectName('card_title');layout.addWidget(label)
    if subtitle:
        label=QLabel(subtitle);label.setObjectName('muted');label.setWordWrap(True);layout.addWidget(label)
    return widget,layout


def badge(text,tone='neutral'):
    label=QLabel(text);label.setObjectName('badge');label.setProperty('tone',tone)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
    return label


def section_label(text):
    label=QLabel(text);label.setObjectName('section_label');return label


def empty_state(title,description,action=None):
    widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(24,20,24,20);layout.setSpacing(12)
    heading=QLabel(title);heading.setObjectName('empty_title');heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    desc=QLabel(description);desc.setObjectName('empty_description');desc.setWordWrap(True);desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addStretch();layout.addWidget(heading);layout.addWidget(desc)
    if action:layout.addWidget(action,0,Qt.AlignmentFlag.AlignCenter)
    layout.addStretch();return widget


def publisher_url(value):
    """Only user-clicked HTTP(S) links to supported publishers and outline sources."""
    try:
        parsed = urlsplit(str(value))
        host = (parsed.hostname or '').lower()
        allowed = any(host == domain or host.endswith('.' + domain) for domain in ('people.com.cn', 'qstheory.cn', 'jsxf.gov.cn', 'forestry.gov.cn'))
        return parsed.scheme in ('http', 'https') and allowed and not parsed.username and not parsed.password
    except ValueError:
        return False


def open_publisher(url):
    value = url.toString() if isinstance(url, QUrl) else str(url)
    if publisher_url(value):
        QDesktopServices.openUrl(QUrl(value))
