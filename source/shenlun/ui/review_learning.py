"""Local paragraph comparisons, keeping the original report and answer intact."""
from html import escape

from PySide6.QtWidgets import QComboBox, QListWidget, QSplitter, QTextBrowser, QVBoxLayout, QWidget

from ..review_learning import paragraph_diff
from .widgets import open_publisher


class ReviewLearningMixin:
    def build_revision_diff(self):
        widget = QWidget(); layout = QVBoxLayout(widget)
        self.revision_diff_kind = QComboBox()
        self.revision_diff_kind.addItem('保留原意修改', 'minimal')
        self.revision_diff_kind.addItem('按题目重新组织', 'model')
        layout.addWidget(self.revision_diff_kind)
        split = QSplitter()
        self.revision_diff_list = QListWidget(); self.revision_diff_list.setWordWrap(True)
        self.revision_diff_browser = QTextBrowser()
        self.revision_diff_browser.setOpenLinks(False); self.revision_diff_browser.setOpenExternalLinks(False)
        split.addWidget(self.revision_diff_list); split.addWidget(self.revision_diff_browser)
        split.setSizes([220,700]); layout.addWidget(split,1)
        self.revision_diff_kind.currentIndexChanged.connect(self.refresh_revision_diff)
        self.revision_diff_list.currentRowChanged.connect(self.show_revision_diff_row)
        self._revision_diff_rows = []
        self.refresh_revision_diff()
        return widget

    def refresh_revision_diff(self, *args):
        self._revision_diff_rows = paragraph_diff(self.current_review or {}, self.revision_diff_kind.currentData())
        self.revision_diff_list.blockSignals(True); self.revision_diff_list.clear()
        labels = {'equal':'未改','replace':'改写','delete':'删除','insert':'新增'}
        for row in self._revision_diff_rows:
            before = str(row['before_index']+1) if row['before_index'] is not None else '—'
            after = str(row['after_index']+1) if row['after_index'] is not None else '—'
            self.revision_diff_list.addItem(f'{labels[row["kind"]]} · 原段 {before} → 新段 {after}\n'+(row['after'] or row['before'])[:55])
        self.revision_diff_list.blockSignals(False)
        if self._revision_diff_rows:
            self.revision_diff_list.setCurrentRow(0)
        else:
            self.revision_diff_browser.setPlainText('修改稿尚未生成；原答和已有报告保留。')

    def show_revision_diff_row(self, index):
        if not 0 <= index < len(self._revision_diff_rows): return
        row = self._revision_diff_rows[index]
        parts = ['<h3>逐段修改对照</h3><p style="color:#718096">按文本顺序对齐，不代表语义一一对应；绿色为新增，红色为删除。</p>',
            '<table width="100%" cellspacing="0" cellpadding="10" border="1"><tr><th>提交时原答</th><th>所选修改稿</th></tr><tr><td width="50%">',
            row['before_html'] or '（无原段）','</td><td width="50%">',row['after_html'] or '（本段已删除）','</td></tr></table>']
        if row['coarse']: parts.append('<p>长段落仅显示整段差异，正文未截断。</p>')
        parts.append('<h3>原段相关批注与依据</h3>')
        if row['annotations']:
            parts.append('<p style="color:#718096">来自本次原答批注，不等于每处改写的原因。</p>')
            for item in row['annotations']:
                parts.extend(['<p><b>',escape(item['issue']),'</b><br>',escape(item['suggestion']),'</p>'])
                for ref in item['refs']:
                    parts.append('<p>材料依据 ['+escape(ref['id'])+']：'+escape(ref['quote'])+'</p>')
                if not item['refs']: parts.append('<p>本条批注暂无可核对的材料引用。</p>')
        else:
            parts.append('<p>本段暂无对应批注或材料依据；仅展示文本差异，不推断修改原因。</p>')
        self.revision_diff_browser.setHtml(''.join(parts))

    def open_result_link(self, url):
        fragment = url.fragment()
        prefix = 'learning-annotation-'
        if not url.scheme() and not url.host() and fragment.startswith(prefix):
            raw = fragment[len(prefix):]
            if raw.isascii() and raw.isdigit():
                index = int(raw)
                if 0 <= index < self.annotation_list.count():
                    self.result_tabs.setCurrentWidget(self.annotated_result)
                    self.annotation_list.setCurrentRow(index)
                    self.select_annotation(index)
            return
        open_publisher(url)
