"""Local article browsing, deliberate imports and evidence-backed methods."""
import json
from datetime import date

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget)

from ..table_models import ArticleSummaryModel
from ..widgets import (
    button,
    card,
    editor,
    model_table,
    open_publisher,
    page,
    publisher_url,
    row,
    section_label)


class CorpusMixin:
    def build_corpus(self):
        widget, layout = page('文章资料库', '原文留在本机。按主题阅读、核对段落，再选择需要提炼的文章。')
        self.corpus_stats = QLabel()
        self.corpus_stats.setWordWrap(True)
        self.corpus_stats.hide()  # Preserve the complete summary for existing callers.
        stats_card, stats_layout = card()
        stats_layout.setContentsMargins(16, 10, 16, 10)
        stats_row = QHBoxLayout()
        self.corpus_stat_values = {}
        for key, label in [('articles', '文章条目'), ('available', '来源可访问'),
                           ('unavailable', '缺失条目'), ('unique_bodies', '去重正文'),
                           ('text_bearing', '含文本文章'), ('analyses', '已完成提炼')]:
            metric = QVBoxLayout()
            metric.setSpacing(2)
            value = QLabel('—')
            value.setObjectName('stat_value')
            value.setAccessibleName(label)
            caption = QLabel(label)
            caption.setObjectName('stat_label')
            metric.addWidget(value)
            metric.addWidget(caption)
            stats_row.addLayout(metric, 1)
            self.corpus_stat_values[key] = value
        stats_layout.addLayout(stats_row)
        layout.addWidget(stats_card)
        scope = self.store.get_setting('bundled_corpus_scope', {})
        dates = '、'.join(scope.get('people_daily_sample_dates', []))
        scope_text = ('求是、红旗文稿：' + scope['qstheory'] + '。' if scope.get('qstheory') else '内置资料保留已知来源与缺失条目。')
        scope_text += ('人民日报抽样日期：' + dates + '；' if dates else '人民日报为抽样文章；') + '不代表近三年完整覆盖，可按日期范围继续导入。'
        self.scope_note = QLabel('收录范围：求是、红旗文稿及人民日报抽样资料，不代表完整覆盖。悬停查看日期与来源范围。')
        self.scope_note.setToolTip(scope_text)
        self.scope_note.setAccessibleDescription(scope_text)
        self.scope_note.setObjectName('muted')
        self.scope_note.setWordWrap(True)
        layout.addWidget(self.scope_note)
        self.corpus_search = QLineEdit()
        self.corpus_search.setObjectName('corpus_search')
        self.corpus_search.setPlaceholderText('搜索标题、作者或正文关键词…')
        self.journal_filter = QComboBox()
        self.journal_filter.addItems(['全部来源', '求是', '红旗文稿', '人民日报'])
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(['全部类型', '评论', '理论', '导读', '推荐', '统计', '人物', '其他'])
        self.year_filter = QComboBox()
        self.year_filter.addItems(['全部年份'] + [str(y) for y in range(date.today().year, 1948, -1)])
        self.corpus_search.setClearButtonEnabled(True)
        self.corpus_search.setMinimumWidth(190)
        filters = row(self.corpus_search, self.journal_filter, self.kind_filter, self.year_filter)
        filters.setStretch(0, 1)
        layout.addLayout(filters)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh_corpus)
        self.corpus_search.textChanged.connect(lambda: self.search_timer.start())
        for combo in (self.journal_filter, self.kind_filter, self.year_filter):
            combo.currentIndexChanged.connect(self.refresh_corpus)
        split = QSplitter(Qt.Orientation.Vertical)
        self.corpus_model = ArticleSummaryModel()
        self.corpus_table = model_table(self.corpus_model)
        self.corpus_table.setMinimumHeight(122)
        self.corpus_table.set_empty_text('资料库中还没有文章', '导入本地文件或官方来源，建立自己的训练资料库。')
        self.corpus_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.corpus_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.corpus_table.selectionModel().selectionChanged.connect(self.show_article_selection)
        split.addWidget(self.corpus_table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.article_heading = section_label('选择文章，阅读原文与段落编号')
        self.article_heading.setWordWrap(True)
        self.article_heading.setMaximumHeight(44)
        self.article_tabs = QTabWidget()
        self.corpus_body = editor(readonly=True)
        self.corpus_analysis = editor(readonly=True)
        self.corpus_body.setMinimumHeight(50)
        self.corpus_analysis.setMinimumHeight(50)
        self.corpus_body.setPlaceholderText('选择上方文章后查看原文。支持 Ctrl 多选文章进行批量提炼。')
        self.corpus_analysis.setPlaceholderText('提炼完成后，在这里核对分析与原文证据。')
        self.article_tabs.addTab(self.corpus_body, '原文 · 段落编号')
        self.article_tabs.addTab(self.corpus_analysis, '提炼分析')
        detail_layout.addWidget(self.article_tabs)
        source_row = QHBoxLayout()
        source_row.addWidget(self.article_heading, 1)
        self.article_source_button = button('打开官方原文', self.open_article_url)
        self.article_source_button.setEnabled(False)
        self.article_source_button.setToolTip('选择带有官方来源链接的文章后可打开。')
        source_row.addWidget(self.article_source_button)
        self.article_selection_label = QLabel('尚未选择文章')
        self.article_selection_label.setObjectName('muted')
        source_row.addWidget(self.article_selection_label)
        detail_layout.insertLayout(0, source_row)
        split.addWidget(detail)
        split.setChildrenCollapsible(False)
        split.setSizes([260, 300])
        layout.addWidget(split, 1)
        imports = QHBoxLayout()
        imports.addWidget(button('本地文件', self.import_local_dialog))
        imports.addWidget(button('官方链接', self.import_url_dialog))
        imports.addWidget(button('已有清单', self.import_manifest_dialog))
        imports.addWidget(button('人民日报日期范围', self.import_people_dialog))
        imports.addStretch()
        imports.addWidget(button('刷新', self.refresh_corpus))
        self.extract_button = button('提炼所选文章…', self.extract_selected, True)
        self.extract_button.setEnabled(False)
        self.extract_button.setToolTip('选择至少一篇含正文的可访问文章，先估算再确认。')
        imports.addWidget(self.extract_button)
        layout.addLayout(imports)
        return widget

    def selected_article_ids(self):
        return self.corpus_table.selected_ids()

    def refresh_corpus(self):
        selected = set(self.selected_article_ids())
        stats = self.store.stats()
        self.corpus_stats.setText(f'总条目 {stats["articles"]:,}　可访问 {stats["available"]:,}　缺失 {stats["unavailable"]:,}　去重正文 {stats["unique_bodies"]:,}　含文本 {stats["text_bearing"]:,}　已提炼 {stats["analyses"]:,}')
        for key, label in self.corpus_stat_values.items():
            value = stats.get(key)
            label.setText(f'{value:,}' if isinstance(value, int) else '—')
        self.corpus_table.set_empty_text(
            '没有匹配的文章' if stats['articles'] else '资料库中还没有文章',
            '试试其他关键词，或清除来源、类型与年份筛选。' if stats['articles'] else '从下方导入本地文件或官方来源，开始积累训练资料。')
        # Only scalar summaries are read here; bodies load when a row is opened.
        summaries = self.store.list_article_summaries(
            query=self.corpus_search.text().strip(),
            journal='' if self.journal_filter.currentIndex() == 0 else self.journal_filter.currentText(),
            kind='' if self.kind_filter.currentIndex() == 0 else self.kind_filter.currentText(),
            year=self.year_filter.currentText() if self.year_filter.currentIndex() else '',
            limit=100000)
        self.corpus_model.set_rows(summaries)
        selection = self.corpus_table.selectionModel()
        selection.blockSignals(True)
        selection.clearSelection()
        for article_id in selected:
            row_index = self.corpus_model.index_of(article_id)
            if row_index >= 0:
                selection.select(self.corpus_model.index(row_index, 0),
                                 QItemSelectionModel.SelectionFlag.Select
                                 | QItemSelectionModel.SelectionFlag.Rows)
        selection.blockSignals(False)
        self.show_article_selection()

    def show_article_selection(self):
        rows = self.corpus_table.selected_rows()
        ids = [row['id'] for row in rows]
        self.extract_button.setEnabled(any(
            row['status'] == 'available' and row['has_body'] for row in rows))
        self.article_selection_label.setText(f'已选 {len(ids)} 篇' if ids else '尚未选择文章')
        self.article_selection_label.setToolTip('可按 Ctrl 多选文章进行批量提炼。')
        if ids:
            self.show_article(ids[0])
        else:
            self.selected_article = None
            self.article_heading.setText('选择文章查看原文与段落编号')
            self.corpus_body.clear()
            self.corpus_analysis.clear()
            self.article_source_button.setEnabled(False)

    def show_article(self, article_id, paragraph_id=None):
        article = self.store.get_article(article_id)
        self.selected_article = article
        self.article_source_button.setEnabled(bool(article and publisher_url(article.get('url', ''))))
        if not article:
            return
        self.article_heading.setText(f'{article["title"]} · {article.get("journal", "")} {article.get("publication", "")}')
        self.article_heading.setToolTip(self.article_heading.text())
        if article.get('body', '').strip():
            self.corpus_body.setPlainText('\n\n'.join(f'[{p["id"]}]\n{p["text"]}' for p in article.get('paragraphs', [])))
        else:
            self.corpus_body.setPlainText('图片内容尚无可提取正文。请点击官方原文链接阅读。' if article.get('status') == 'available' else '此条目保留来源信息，当前缺少可用原文。')
        analysis = self.store.get_analysis(article_id)
        self.corpus_analysis.setPlainText(json.dumps(analysis, ensure_ascii=False, indent=2) if analysis else '尚未提炼。选择有文本的文章后，可先估算再开始模型分析。')
        if paragraph_id:
            self.article_tabs.setCurrentIndex(0)
            self.corpus_body.moveCursor(self.corpus_body.textCursor().MoveOperation.Start)
            self.corpus_body.find('[' + paragraph_id + ']')

    def open_article_url(self):
        article = getattr(self, 'selected_article', None)
        if article and article.get('url'):
            open_publisher(article['url'])
        else:
            self.notify('当前文章没有可用的官方来源链接。')
