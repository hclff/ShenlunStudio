"""Local article browsing, deliberate imports and evidence-backed methods."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QSplitter, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget)

from ..importers import import_file, import_manifest, import_people_range, import_url
from .table_models import ArticleSummaryModel
from .widgets import (button, card, editor, empty_state, model_table, open_publisher, page,
    publisher_url, row, section_label, table)
from .teaching import TeachingPanel
from .article_method_editor import ArticleMethodEditorDialog, MethodTrashDialog
from .method_batch import ArticleBatchMixin
from .method_filters import MethodFilterBar


class LibraryMixin(ArticleBatchMixin):
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

    def import_manifest_dialog(self):
        default = str(Path(__file__).resolve().parents[3] / 'tmp' / 'pdfs' / 'qstheory-source')
        path, _ = QFileDialog.getOpenFileName(self, '选择原始文章清单', default, 'JSON (*.json)')
        if path:
            self.start_import('导入清单', lambda progress, cancel: import_manifest(Path(path), self.store, progress=progress, cancel=cancel))

    def import_local_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地文章', '', '文章文件 (*.txt *.md *.pdf *.json)')
        if path:
            self.start_import('导入本地文章', lambda progress, cancel: import_file(Path(path), self.store, progress=progress, cancel=cancel))

    def import_url_dialog(self):
        value, ok = QInputDialog.getText(self, '导入官方原文', '求是网或人民网文章 HTTP(S) 地址：')
        if ok and value.strip():
            self.start_import('导入官方原文', lambda progress, cancel: import_url(value.strip(), self.store, progress=progress, cancel=cancel))

    def import_people_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('导入人民日报评论 / 理论版')
        layout = QFormLayout(dialog)
        layout.addRow(QLabel('按日期导入评论与理论版，已有日期会续传；可随时取消。'))
        start = QDateEdit(QDate.currentDate().addDays(-6))
        end = QDateEdit(QDate.currentDate())
        for control in (start, end):
            control.setCalendarPopup(True)
            control.setDisplayFormat('yyyy-MM-dd')
        layout.addRow('开始日期', start)
        layout.addRow('结束日期', end)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            first, last = start.date().toPython(), end.date().toPython()
            if first > last:
                self.notify('开始日期不能晚于结束日期。')
                return
            self.start_import('导入人民日报日期范围', lambda progress, cancel: self.import_people_chunks(first, last, progress, cancel))

    def import_people_chunks(self, first, last, progress, cancel):
        totals = {}
        current = first
        while current <= last and not cancel.is_set():
            finish = min(current + timedelta(days=30), last)
            progress(f'导入 {current.isoformat()} 至 {finish.isoformat()}…')
            result = import_people_range(current.isoformat(), finish.isoformat(), self.store, progress=progress, cancel=cancel)
            for key, value in result.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
                elif isinstance(value, list):
                    totals.setdefault(key, []).extend(value)
            current = finish + timedelta(days=1)
        return totals

    def start_import(self, title, operation):
        def done(result):
            self.notify(title + '完成：' + json.dumps(result, ensure_ascii=False))
        # Imports only add corpus rows; other pages stay untouched.
        self.start_job(title, operation, done, domains=('corpus',))

    def extract_selected(self):
        ids = self.selected_article_ids()
        if not ids:
            self.notify('请先在表格中选择需要提炼的文章，可按 Ctrl 多选。')
            return
        try:
            service = self.get_service(require_config=True)
            estimate = service.estimate_extraction(ids)
            if not estimate['articles']:
                self.notify('所选条目没有可提取正文；缺失或仅图片的条目会跳过。')
                return
            budget = self.config['session_token_budget']
            batches = estimate.get('estimated_batches', estimate['articles'])
            message = (f'将发送所选 {estimate["articles"]} 篇文章正文，共 {estimate["characters"]:,} 字符。\n'
                       f'预计输入约 {estimate["estimated_input_tokens"]:,} tokens，输出上限约 {batches * self.config["max_output_tokens"]:,} tokens。\n'
                       f'本次任务 token预算：{budget:,}。估算不等于费用上限。\n已完成的提炼会跳过；取消后可再次选择续传。是否开始？')
            if QMessageBox.question(self, '确认模型批量提炼', message) != QMessageBox.StandardButton.Yes:
                return
            def done(result):
                self.notify('提炼完成：' + json.dumps(result, ensure_ascii=False))
            # Extraction stores analyses, candidate methods and token usage.
            self.start_job('提炼所选文章', lambda progress, cancel: service.extract(ids, progress=progress, cancel=cancel),
                           done, domains=('corpus', 'methods', 'usage'))
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def build_methods(self):
        widget, outer = page('写作方法库', '分别核对文章提炼与教学来源；启用前须检查证据及适用边界。')
        self.methods_tabs = QTabWidget()
        self.teaching_panel = TeachingPanel(self)
        self.methods_tabs.addTab(self.teaching_panel, '教学方法 · 名师来源')
        publication = QWidget()
        layout = QVBoxLayout(publication)
        layout.addWidget(QLabel('文章提炼方法须有至少两篇不同正文支持，沿用原有核验规则。'))
        self.methods_tabs.addTab(publication, '文章提炼方法')
        if self.store.list_cards():
            self.methods_tabs.setCurrentIndex(1)
        outer.addWidget(self.methods_tabs, 1)
        summary = QHBoxLayout()
        self.method_status = QLabel()
        self.method_status.setObjectName('muted')
        summary.addWidget(self.method_status)
        summary.addStretch()
        self.new_method_button = button('新建方法', self.new_article_method, True)
        self.deleted_methods_button = button('已删除…', self.show_deleted_article_methods)
        summary.addWidget(self.new_method_button)
        summary.addWidget(self.deleted_methods_button)
        summary.addWidget(button('刷新方法', self.refresh_methods))
        layout.addLayout(summary)
        self.article_method_filters = MethodFilterBar()
        self.article_method_search = self.article_method_filters.search
        self.article_method_type_filter = self.article_method_filters.question_type
        self.article_method_status_filter = self.article_method_filters.enabled
        self._article_filter_state = self.article_method_filters.state()
        self.article_method_filters.changed.connect(self.change_article_method_filter)
        layout.addWidget(self.article_method_filters)
        self.method_library_button = button('去文章资料库提炼', lambda: self.navigation.setCurrentRow(1), True)
        self.method_empty = empty_state('从原文中积累方法', '可点击上方新建方法保存草稿，或选择文章完成提炼，再核对原文证据与适用范围。', self.method_library_button)
        layout.addWidget(self.method_empty, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        self.method_split = split
        self.method_table = table(['方法名称', '适用题型', '证据篇数', '状态'])
        self.method_table.set_empty_text('暂无方法卡', '真实文章提炼后，候选方法会显示在这里。')
        self.method_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.method_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.method_table.itemSelectionChanged.connect(self.show_method)
        self.method_detail = editor(readonly=True)
        self.method_detail.setPlaceholderText('选择方法后，核对操作步骤、适用边界与原文证据。')
        self.method_refs = QComboBox()
        split.addWidget(self.method_table)
        split.addWidget(self.method_detail)
        self.init_article_batch(layout)
        layout.addWidget(split, 1)
        self.enable_method_button = button('启用所选方法', lambda: self.set_method_enabled(True), True)
        self.disable_method_button = button('停用所选方法', lambda: self.set_method_enabled(False))
        self.edit_method_button = button('编辑', self.edit_article_method)
        self.delete_method_button = button('删除', self.delete_article_method)
        self.method_evidence_button = button('查看证据原文', self.view_method_evidence)
        self.enable_method_button.setToolTip('启用前仍会检查证据数量、来源去重与方法有效性。')
        self.method_controls = QWidget()
        action_layout = QVBoxLayout(self.method_controls)
        action_layout.setContentsMargins(0, 0, 0, 0)
        actions = row(self.enable_method_button, self.disable_method_button, self.edit_method_button, self.delete_method_button)
        actions.addStretch()
        action_layout.addLayout(actions)
        evidence = row(self.method_refs, self.method_evidence_button)
        evidence.setStretch(0, 1)
        self.method_refs.setPlaceholderText('选择方法后查看可定位的原文证据')
        action_layout.addLayout(evidence)
        layout.addWidget(self.method_controls)
        self.method_refs.currentIndexChanged.connect(self.update_method_actions)
        return widget

    def change_article_method_filter(self):
        if self.method_crud_guarded():
            self.article_method_filters.restore(self._article_filter_state)
            return
        self.check_article_methods(False)
        self._article_filter_state = self.article_method_filters.state()
        self.refresh_methods()

    def refresh_methods(self):
        if hasattr(self, 'teaching_panel'):
            self.teaching_panel.refresh()
        selected = self.selected_card() if hasattr(self, 'cards') else None
        checked={r['id'] for r in self.checked_article_methods()}
        all_cards = self.store.list_cards()
        titles = {}
        if self.article_method_search.text().strip():
            identities = sorted({identity for card in all_cards
                                 for identity in card.get('source_article_ids', [])})
            titles = self.store.article_titles(identities)
        self.cards = [card for card in all_cards if self.article_method_filters.matches(card,
            source_titles=[titles.get(identity, '') for identity in card.get('source_article_ids', [])])]
        self.method_table.blockSignals(True)
        self.method_table.setRowCount(len(self.cards))
        self.method_table.clearSelection()
        self.method_table.setCurrentCell(-1, -1)
        for r, card in enumerate(self.cards):
            values = [card.get('name', ''), '、'.join(card.get('applicable_types', [])),
                      str(len(set(card.get('source_fingerprints', card.get('source_article_ids', []))))),
                      '已启用' if card.get('enabled') else card.get('status', '停用')]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, card.get('id'))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if card.get('id') in checked else Qt.CheckState.Unchecked)
                self.method_table.setItem(r, c, item)
            if selected and selected.get('id') == card.get('id'):
                self.method_table.selectRow(r)
        self.method_table.blockSignals(False)
        self.method_empty.setVisible(not all_cards)
        self.method_split.setVisible(bool(all_cards))
        self.method_controls.setVisible(bool(self.cards))
        self.method_status.setText(f'显示 {len(self.cards)} / {len(all_cards)} 个方法 · 已启用 {sum(bool(card.get("enabled")) for card in self.cards)} 个')
        self.show_method()

    def selected_card(self):
        r = self.method_table.currentRow()
        if r < 0 or r >= len(self.cards) or not self.method_table.selectionModel().hasSelection():
            return None
        item = self.method_table.item(r, 0)
        return next((c for c in self.cards if item and c.get('id') == item.data(Qt.ItemDataRole.UserRole)), None)

    def show_method(self):
        card = self.selected_card()
        self.method_refs.clear()
        if not card:
            self.method_detail.clear()
            self.update_method_actions()
            return
        steps = '\n'.join(f'{i}. {s}' for i, s in enumerate(card.get('steps', []), 1))
        refs = '\n'.join(f'[{r.get("id", "")}] {r.get("quote", "")}' for r in card.get('refs', []))
        self.method_detail.setPlainText(f'{card.get("name", "")}\n\n作用：{card.get("purpose", "")}\n\n步骤\n{steps}\n\n不适用情形：{card.get("not_applicable", "")}\n\n原文证据\n{refs}')
        for ref in card.get('refs', []):
            self.method_refs.addItem(ref.get('id', '') + '　' + ref.get('quote', '')[:30], ref.get('id', ''))
        if self.method_refs.count():
            self.method_refs.setCurrentIndex(0)
        self.update_method_actions()

    def update_method_actions(self):
        self.update_article_batch_actions()
        card = self.selected_card()
        idle = not bool(self.workers)
        self.new_method_button.setEnabled(idle)
        self.deleted_methods_button.setEnabled(idle)
        self.edit_method_button.setEnabled(bool(idle and card))
        self.delete_method_button.setEnabled(bool(idle and card))
        self.enable_method_button.setEnabled(bool(idle and card and not card.get('enabled')))
        self.disable_method_button.setEnabled(bool(idle and card and card.get('enabled')))
        paragraph_id = self.method_refs.currentData()
        self.method_refs.setEnabled(bool(card and self.method_refs.count()))
        self.method_evidence_button.setEnabled(bool(card and paragraph_id and ':p' in paragraph_id))

    def method_crud_guarded(self):
        if self.workers or getattr(self, '_closing', False):
            self.notify('请先完成或取消正在运行的任务。')
            return True
        return False

    def new_article_method(self):
        self.edit_article_method(new=True)

    def edit_article_method(self, new=False):
        if self.method_crud_guarded():
            return
        value = None if new else self.selected_card()
        if not new and not value:
            self.notify('请先选择方法。')
            return
        dialog = ArticleMethodEditorDialog(self.store, value, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.method_crud_guarded():
                return
            self.refresh_methods()
            identity = (dialog.saved_card or {}).get('id')
            for index, card in enumerate(self.cards):
                if card.get('id') == identity:
                    self.method_table.selectRow(index)
                    break
            self.notify('方法已保存并停用。')

    def delete_article_method(self):
        if self.method_crud_guarded():
            return
        value = self.selected_card()
        if not value:
            self.notify('请先选择方法。')
            return
        answer = QMessageBox.question(self, '删除文章提炼方法？',
            f'将“{value.get("name", "")}”移入已删除方法，可随时恢复。历史批改与快照不受影响。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes or self.method_crud_guarded():
            return
        try:
            self.store.delete_method('article', value['id'])
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.notify(str(exc))
            return
        self.refresh_methods()
        self.notify('方法已移入已删除，可恢复后重新核验。')

    def show_deleted_article_methods(self):
        if self.method_crud_guarded():
            return
        dialog = MethodTrashDialog(self.store, 'article', self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.method_crud_guarded():
                return
            self.refresh_methods()
            self.notify('方法已彻底删除，不能从已删除列表恢复。' if dialog.action=='purged' else '方法已恢复并停用。')

    def set_method_enabled(self, enabled):
        if self.workers:
            self.notify('请先完成或取消正在运行的任务。')
            return
        card = self.selected_card()
        if not card:
            self.notify('请先选择方法。')
            return
        try:
            self.get_service().enable_card(card['id'], enabled)
            self.refresh_methods()
            self.notify('方法已启用。' if enabled else '方法已停用。')
        except (ValueError, TypeError) as exc:
            self.notify(str(exc))

    def view_method_evidence(self):
        paragraph_id = self.method_refs.currentData()
        if not paragraph_id or ':p' not in paragraph_id:
            self.notify('该方法没有可定位的原文证据。')
            return
        article_id = paragraph_id.rsplit(':p', 1)[0]
        self.navigation.setCurrentRow(1)
        self.show_article(article_id, paragraph_id)
