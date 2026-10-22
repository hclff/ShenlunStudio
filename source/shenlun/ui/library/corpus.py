"""Local article browsing, deliberate imports and evidence-backed methods."""
import json
from collections import OrderedDict
from datetime import date
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget)

from ...article_export import export_articles
from ...storage import tag_list
from ..common.dialogs import confirm_destructive
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
    PAGE_SIZE = 300
    CACHE_PAGES = 12

    def build_corpus(self):
        widget, layout = page('文章资料库', '原文留在本机。检索、核对段落，再选择文章提炼。')
        self._cache = OrderedDict()
        self._loaded = 0
        self._total = 0
        self._filters = {}
        self._pending = set()
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
        # The scope sentence lives in the subtitle and on this tooltip: at the
        # minimum window height one extra line pushed the detail pane out of view.
        self.scope_note = QLabel('收录范围：求是、红旗文稿及人民日报抽样资料，不代表完整覆盖。悬停查看日期与来源范围。')
        self.scope_note.setToolTip(scope_text)
        self.scope_note.setAccessibleDescription(scope_text)
        self.scope_note.setObjectName('muted')
        self.scope_note.setWordWrap(True)
        self.scope_note.hide()
        stats_card.setToolTip(scope_text)
        stats_card.setAccessibleDescription(scope_text)
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
        self.match_filter = QComboBox()
        self.match_filter.addItems(['同时包含', '任一包含'])
        self.match_filter.setToolTip('多个关键词的关系：同时包含为 AND，任一包含为 OR。')
        self.tag_button = QToolButton()
        self.tag_button.setText('全部标签')
        self.tag_button.setToolTip('按标签精确筛选，可勾选多个叠加。')
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tag_menu = QMenu(self.tag_button)
        self.tag_button.setMenu(self._tag_menu)
        self._selected_tags = set()
        self.order_filter = QComboBox()
        self.order_filter.addItems(['默认排序', '相关度优先', '最新年份'])
        self.order_filter.setToolTip('相关度优先按标题、来源与正文命中加权排序。')
        filters = row(self.corpus_search, self.match_filter, self.tag_button,
                      self.journal_filter, self.kind_filter, self.year_filter, self.order_filter)
        filters.setStretch(0, 1)
        layout.addLayout(filters)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh_corpus)
        self.corpus_search.textChanged.connect(lambda: self.search_timer.start())
        self.corpus_search.returnPressed.connect(self.refresh_corpus)
        for combo in (self.journal_filter, self.kind_filter, self.year_filter,
                      self.match_filter, self.order_filter):
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
        # The heading repeated what the selected row and the tab label already
        # say; at the minimum window height that line pushed the editors out.
        self.article_heading = section_label('选择文章，阅读原文与段落编号')
        self.article_heading.setWordWrap(True)
        self.article_heading.setMaximumHeight(44)
        self.article_heading.hide()
        self.article_tabs = QTabWidget()
        self.corpus_body = editor(readonly=True)
        self.corpus_analysis = editor(readonly=True)
        self.corpus_body.setMinimumHeight(32)
        self.corpus_analysis.setMinimumHeight(32)
        self.corpus_body.setPlaceholderText('选择上方文章后查看原文。支持 Ctrl 多选文章进行批量提炼。')
        self.corpus_analysis.setPlaceholderText('提炼完成后，在这里核对分析与原文证据。')
        self.article_tabs.addTab(self.corpus_body, '原文 · 段落编号')
        self.article_tabs.addTab(self.corpus_analysis, '提炼分析')
        detail_layout.addWidget(self.article_tabs)
        split.addWidget(detail)
        split.setChildrenCollapsible(False)
        split.setSizes([260, 300])
        layout.addWidget(split, 1)
        self.corpus_count_label = QLabel()
        self.corpus_count_label.setObjectName('muted')
        self.load_more_button = button('加载更多', self.load_more_articles)
        self.load_more_button.hide()
        self.article_source_button = button('打开官方原文', self.open_article_url)
        self.article_source_button.setEnabled(False)
        self.article_source_button.setToolTip('选择带有官方来源链接的文章后可打开。')
        self.article_selection_label = QLabel('尚未选择文章')
        self.article_selection_label.setObjectName('muted')
        pager = row(self.corpus_count_label, self.load_more_button)
        pager.addStretch()
        pager.addWidget(self.article_selection_label)
        pager.addWidget(self.article_source_button)
        layout.addLayout(pager)
        # Twelve actions on one line overflow at the minimum window width, where
        # Qt shrinks the buttons until the labels turn into ellipses. Split the
        # import entry points from the batch actions so every label stays whole.
        import_row = QHBoxLayout()
        for text, slot in (('本地文件', self.import_local_dialog),
                           ('批量导入', self.import_batch_dialog),
                           ('导入文件夹', self.import_folder_dialog),
                           ('官方链接', self.import_url_dialog),
                           ('已有清单', self.import_manifest_dialog),
                           ('人民日报日期范围', self.import_people_dialog)):
            import_row.addWidget(self._toolbar_button(text, slot))
        import_row.addStretch()
        layout.addLayout(import_row)
        action_row = QHBoxLayout()
        self.bulk_button = QToolButton()
        self.bulk_button.setText('批量操作')
        self.bulk_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bulk_menu = QMenu(self.bulk_button)
        bulk_menu.addAction('设置来源…', lambda: self.bulk_update('journal'))
        bulk_menu.addAction('设置类型…', lambda: self.bulk_update('kind'))
        bulk_menu.addAction('设置年份…', lambda: self.bulk_update('year'))
        bulk_menu.addSeparator()
        bulk_menu.addAction('添加标签…', lambda: self.bulk_tags(add=True))
        bulk_menu.addAction('移除标签…', lambda: self.bulk_tags(add=False))
        bulk_menu.addAction('管理标签…', self.manage_tags)
        bulk_menu.addSeparator()
        bulk_menu.addAction('删除所选…', self.bulk_delete)
        self.bulk_button.setMenu(bulk_menu)
        action_row.addWidget(self.bulk_button)
        action_row.addWidget(self._toolbar_button('导出所选…', self.export_selected))
        action_row.addWidget(self._toolbar_button('新建方法卡…', self.new_method_from_article))
        self.extract_button = self._toolbar_button('提炼所选文章…', self.extract_selected, True)
        self.extract_button.setEnabled(False)
        self.extract_button.setToolTip('选择至少一篇含正文的可访问文章，先估算再确认。')
        action_row.addWidget(self.extract_button)
        self.filter_extract_button = self._toolbar_button('提取筛选方法…', self.extract_methods_filtered)
        self.filter_extract_button.setToolTip('对当前筛选结果提取可迁移写作方法；结果进入候选池，审核后才启用。')
        action_row.addWidget(self.filter_extract_button)
        action_row.addStretch()
        action_row.addWidget(self._toolbar_button('刷新', self.refresh_corpus_fresh))
        layout.addLayout(action_row)
        return widget

    @staticmethod
    def _toolbar_button(text, callback, primary=False):
        """A button that never narrows below its own label."""
        result = button(text, callback, primary)
        result.setMinimumWidth(result.fontMetrics().horizontalAdvance(text) + 34)
        return result

    def selected_article_ids(self):
        shown = self.corpus_table.selected_ids()
        seen = set(shown)
        return shown + [article_id for article_id in sorted(self._pending) if article_id not in seen]

    def refresh_corpus_fresh(self):
        """Reload after a write: cached pages may predate the change."""
        self._cache.clear()
        self.refresh_corpus()

    def _current_filters(self):
        return {
            'query': self.corpus_search.text().strip(),
            'match': 'or' if self.match_filter.currentIndex() == 1 else 'and',
            'tags': tuple(sorted(self._selected_tags)),
            'order': ('default', 'relevance', 'year')[max(0, self.order_filter.currentIndex())],
            'journal': '' if self.journal_filter.currentIndex() == 0 else self.journal_filter.currentText(),
            'kind': '' if self.kind_filter.currentIndex() == 0 else self.kind_filter.currentText(),
            'year': self.year_filter.currentText() if self.year_filter.currentIndex() else ''}

    def _rebuild_tag_menu(self):
        """Tag facets follow the data; ticks survive a rebuild."""
        self._tag_menu.clear()
        for tag, count in self.store.article_tags():
            action = self._tag_menu.addAction(f'{tag}（{count}）')
            action.setCheckable(True)
            action.setChecked(tag in self._selected_tags)
            action.toggled.connect(lambda checked, value=tag: self._toggle_tag(value, checked))
        if not self._tag_menu.actions():
            action = self._tag_menu.addAction('暂无标签')
            action.setEnabled(False)
        self._sync_tag_button()

    def _toggle_tag(self, tag, checked):
        if checked:
            self._selected_tags.add(tag)
        else:
            self._selected_tags.discard(tag)
        self._sync_tag_button()
        self.refresh_corpus_fresh()

    def _sync_tag_button(self):
        tags = sorted(self._selected_tags)
        if not tags:
            self.tag_button.setText('全部标签')
        elif len(tags) == 1:
            self.tag_button.setText('标签：' + tags[0])
        else:
            self.tag_button.setText(f'标签：{tags[0]} 等 {len(tags)} 个')
        self.tag_button.setToolTip('已选标签：' + ('、'.join(tags) if tags else '无'))

    def _page(self, filters, offset, reuse):
        """Read one page; only continuation pages may be served from cache.

        The first page is always re-queried so a refresh can never show rows that
        predate a write, while paging back and forth stays free.
        """
        key = (tuple(sorted(filters.items())), offset)
        if reuse:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        rows = self.store.list_article_summaries(**filters, limit=self.PAGE_SIZE, offset=offset)
        self._cache[key] = rows
        while len(self._cache) > self.CACHE_PAGES:
            self._cache.popitem(last=False)
        return rows

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
        # Only the first page of scalar summaries is read; bodies load when a
        # row is opened and later pages follow on demand.
        self._rebuild_tag_menu()
        self._filters = self._current_filters()
        self._total = self.store.count_articles(
            **{key: value for key, value in self._filters.items() if key != 'order'})
        summaries = self._page(self._filters, 0, reuse=False)
        self._loaded = len(summaries)
        self.corpus_model.set_rows(summaries)
        self.corpus_model.set_highlight(self._filters['query'])
        self._pending = selected
        self._restore_selection(clear=True, keep_missing=False)
        self._update_pager()

    def load_more_articles(self):
        if self._loaded >= self._total:
            return
        summaries = self._page(self._filters, self._loaded, reuse=True)
        if not summaries:
            self._total = self._loaded
            self._update_pager()
            return
        self.corpus_model.append_rows(summaries)
        self._loaded += len(summaries)
        self._restore_selection(clear=False, keep_missing=True)
        self._update_pager()

    def _restore_selection(self, clear, keep_missing):
        selection = self.corpus_table.selectionModel()
        selection.blockSignals(True)
        if clear:
            selection.clearSelection()
        remaining = set()
        for article_id in self._pending:
            row_index = self.corpus_model.index_of(article_id)
            if row_index < 0:
                if keep_missing:
                    remaining.add(article_id)
                continue
            selection.select(self.corpus_model.index(row_index, 0),
                             QItemSelectionModel.SelectionFlag.Select
                             | QItemSelectionModel.SelectionFlag.Rows)
        selection.blockSignals(False)
        self._pending = remaining
        self.show_article_selection()

    def _update_pager(self):
        remaining = max(0, self._total - self._loaded)
        self.load_more_button.setVisible(remaining > 0)
        self.load_more_button.setText(f'加载更多（剩余 {remaining:,} 篇）')
        if remaining:
            text = f'显示 {self._loaded:,} / 共 {self._total:,} 篇'
        elif self._total:
            text = f'共 {self._total:,} 篇'
        else:
            text = ''
        if self._pending:
            text += f'　另有 {len(self._pending):,} 篇已选中未显示'
        self.corpus_count_label.setText(text)

    def show_article_selection(self):
        rows = self.corpus_table.selected_rows()
        ids = self.selected_article_ids()
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

    def _selected_or_notify(self, action):
        ids = self.selected_article_ids()
        if not ids:
            self.notify(f'请先在表格中选择要{action}的文章，可按 Ctrl 多选。')
        return ids

    def bulk_update(self, field):
        """Batch metadata edit; the body, fingerprint and paragraphs stay untouched."""
        ids = self._selected_or_notify('修改')
        if not ids:
            return
        prompts = {'journal': ('设置来源', '来源，例如 人民日报'),
                   'kind': ('设置类型', '类型，例如 评论'),
                   'year': ('设置年份', '四位年份，例如 2024')}
        title, prompt = prompts[field]
        value, ok = QInputDialog.getText(self, title, f'将应用到所选 {len(ids)} 篇文章。\n{prompt}')
        value = str(value or '').strip()
        if not ok or not value:
            return
        if field == 'year' and not (len(value) == 4 and value.isdigit()):
            self.notify('年份必须是四位数字。')
            return
        fields = {'year': int(value)} if field == 'year' else {field: value}
        try:
            result = self.store.update_articles(ids, fields)
        except (ValueError, OSError) as exc:
            self.notify(str(exc))
            return
        self.notify(f'已更新 {result["updated"]} 篇文章的{title[2:]}。')
        self.refresh_corpus_fresh()

    def bulk_tags(self, add):
        ids = self._selected_or_notify('打标签')
        if not ids:
            return
        value, ok = QInputDialog.getText(
            self, '添加标签' if add else '移除标签',
            f'将应用到所选 {len(ids)} 篇文章。多个标签用顿号或逗号分隔：')
        tags = tag_list(value)
        if not ok or not tags:
            return
        result = (self.store.set_article_tags(ids, add=tags) if add
                  else self.store.set_article_tags(ids, remove=tags))
        self.notify(f'已{"添加" if add else "移除"}标签 {("、".join(tags))}，涉及 {result["updated"]} 篇。')
        self.refresh_corpus_fresh()

    def manage_tags(self):
        """Corpus-wide tag housekeeping; the dialog writes its own backups."""
        from .tag_manager import TagManagerDialog

        dialog = TagManagerDialog(self.store, self)
        dialog.changed.connect(self.refresh_corpus_fresh)
        dialog.exec()
        self.refresh_corpus_fresh()

    def bulk_delete(self):
        ids = self._selected_or_notify('删除')
        if not ids:
            return
        message = (f'将从资料库删除所选 {len(ids)} 篇文章，删除前自动备份整个数据库。\n'
                   '仍被方法卡或教学版本引用的文章会跳过并列出原因。\n确定继续？')
        if not confirm_destructive(self, '确认删除文章', message):
            return
        try:
            result = self.store.delete_articles(ids)
        except (ValueError, OSError) as exc:
            self.notify(str(exc))
            return
        text = f'已删除 {result["deleted"]} 篇'
        if result['blocked']:
            detail = '；'.join(f'{item["title"] or item["id"]}（{item["reason"]}）'
                              for item in result['blocked'][:3])
            text += f'，跳过 {len(result["blocked"])} 篇：{detail}'
        if result['backup']:
            text += f'；备份 {Path(result["backup"]).name}'
        self.notify(text)
        self._pending.clear()
        self.refresh_corpus_fresh()

    def export_selected(self):
        ids = self._selected_or_notify('导出')
        if not ids:
            return
        default = str(Path.home() / '申论文章导出.md')
        path, _ = QFileDialog.getSaveFileName(
            self, '导出所选文章', default,
            'Markdown (*.md);;JSON (*.json);;Word 文档 (*.docx)')
        if not path:
            return
        articles = [article for article in (self.store.get_article(item) for item in ids) if article]
        try:
            saved = export_articles(articles, path)
        except (ValueError, OSError) as exc:
            self.notify(str(exc))
            return
        self.notify(f'已导出 {len(articles)} 篇到 {saved}')

    def new_method_from_article(self):
        """Open the method editor with the selected article already in the evidence tab."""
        ids = self._selected_or_notify('新建方法卡')
        if not ids:
            return
        from ..article_method_editor import ArticleMethodEditorDialog

        dialog = ArticleMethodEditorDialog(self.store, None, self)
        dialog.preselect(ids[0])
        dialog.exec()
        self.refresh_corpus()
