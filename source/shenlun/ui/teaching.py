"""Local teaching-method editing, provenance review and deliberate selection."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QInputDialog, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..domain import RUBRICS
from ..intake import FILE_FILTER, read_inputs
from ..teaching import (SOURCE_FIELDS, normalize_teaching_selection,
    resolve_teaching_guidance, validate_teaching_method)
from .widgets import button, editor
from .method_batch import TeachingBatchMixin
from .method_filters import MethodFilterBar


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


class TeachingReviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('审核并启用'); self.resize(540, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(label('核对来源、引文和适用范围。'))
        form = QFormLayout(); self.reviewer = QLineEdit(); self.reviewer.setMaxLength(200)
        self.note = editor(placeholder='填写核对说明…')
        form.addRow('审核人', self.reviewer); form.addRow('审核说明', self.note)
        layout.addLayout(form); self.message = label('请填写审核人和说明。'); layout.addWidget(self.message)
        dialog_buttons(self, layout, '继续')

    def accept(self):
        if not self.reviewer.text().strip() or not self.note.toPlainText().strip():
            self.message.setText('请填写审核人和说明。'); return
        if len(self.note.toPlainText()) > 4000:
            self.message.setText('审核说明不能超过 4000 字。'); return
        super().accept()


class TeachingJsonPreviewDialog(QDialog):
    def __init__(self, records, filename, parent=None):
        super().__init__(parent); self.setWindowTitle('导入预览'); self.resize(740, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f'{filename} · {len(records)} 条；导入后为停用草稿。'))
        preview = editor(readonly=True); preview.setPlainText(json.dumps(records, ensure_ascii=False, indent=2))
        layout.addWidget(preview, 1); dialog_buttons(self, layout, '导入')


class TeachingSourcePreviewDialog(QDialog):
    def __init__(self, batch, parent=None):
        super().__init__(parent); self.setWindowTitle('来源片段'); self.resize(760, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(label('请核对识别文字，最多 4000 字。'))
        sources = '\n'.join(f'{p.source} · 第 {p.number} 页 · {p.method}' for p in batch.pages)
        self.provenance = editor(readonly=True); self.provenance.setMaximumHeight(95)
        self.provenance.setPlainText(sources + '\n' + '\n'.join(batch.errors)); layout.addWidget(self.provenance)
        self.text = editor(); self.text.setPlainText('\n\n'.join(p.text for p in batch.pages if p.text))
        layout.addWidget(self.text, 1); self.message = label('取消不会修改当前内容。'); layout.addWidget(self.message)
        dialog_buttons(self, layout, '使用此片段')

    def accept(self):
        if not self.text.toPlainText().strip() or len(self.text.toPlainText()) > 4000:
            self.message.setText('请选择 1–4000 字的来源片段后确认。'); return
        super().accept()


class TeachingSelectionDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent); self.store = store; self.host = parent
        self.setWindowTitle('批改中的方法辅助'); self.resize(600, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(label('仅使用已启用且适合本题的方法，最多 6 项。'))
        self.mode = QComboBox()
        for text, value in MODES: self.mode.addItem(text, value)
        layout.addWidget(self.mode)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); owners_layout = QVBoxLayout(body)
        self.owner_checks = {}
        counts = {}
        for row in store.list_teaching_methods(True): counts[row['owner_id']] = counts.get(row['owner_id'], 0) + 1
        for owner in store.list_teaching_owners():
            check = QCheckBox(owner['name'])
            check.setToolTip(f'{owner["description"]}\n已启用 {counts.get(owner["id"], 0)}')
            self.owner_checks[owner['id']] = check; owners_layout.addWidget(check)
        scroll.setWidget(body); layout.addWidget(scroll, 1)
        self.message = label('指定 1 项；比较 2–3 项。'); layout.addWidget(self.message)
        try: selected = normalize_teaching_selection(store.get_setting('teaching_selection'))
        except ValueError: selected = normalize_teaching_selection(None)
        self.mode.setCurrentIndex(self.mode.findData(selected['mode']))
        if any(identity not in self.owner_checks for identity in selected['owner_ids']):
            selected = normalize_teaching_selection(None)
            self.mode.setCurrentIndex(self.mode.findData(selected['mode']))
        for identity in selected['owner_ids']: self.owner_checks[identity].setChecked(True)
        self.mode.currentIndexChanged.connect(self.update_mode); self.update_mode()
        dialog_buttons(self, layout, '保存本机选择')

    def update_mode(self):
        explicit = self.mode.currentData() in ('source', 'compare')
        for check in self.owner_checks.values(): check.setEnabled(explicit)

    def accept(self):
        if getattr(self.host, 'workers', set()): self.message.setText('请先完成或取消正在运行的任务。'); return
        try:
            selection = normalize_teaching_selection({'mode': self.mode.currentData(),
                'owner_ids': [key for key, check in self.owner_checks.items() if check.isChecked()]})
            live = {o['id'] for o in self.store.list_teaching_owners()}
            if any(identity not in live for identity in selection['owner_ids']):
                raise ValueError('主体已变化，请重新打开选择窗口。')
            self.store.set_setting('teaching_selection', selection)
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc: self.message.setText(str(exc)); return
        super().accept()


class TeachingPanel(TeachingBatchMixin,QWidget):
    """An in-workspace editor; refresh/import never silently discard dirty fields."""
    def __init__(self, host):
        super().__init__(host); self.host = host; self.store = host.store
        self.current = None; self.records = []; self.dirty = False; self._loading = False
        self._sources = []; self._source_index = -1; self._filter_id = None
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 10, 10, 8); layout.setSpacing(6)
        self.summary = label(''); layout.addWidget(self.summary)
        self.owner_filter = QComboBox(); self.owner_filter.addItem(f'全部教学主体（{len(self.store.list_teaching_owners())}）', None)
        for owner in self.store.list_teaching_owners():
            self.owner_filter.addItem(owner['name'], owner['id'])
        self.method_filters = MethodFilterBar()
        self.method_search = self.method_filters.search
        self.question_type_filter = self.method_filters.question_type
        self.enabled_filter = self.method_filters.enabled
        self._method_filter_state = self.method_filters.state()
        self.owner_filter.setAccessibleName('教学主体筛选')
        self.owner_filter.setMaximumWidth(205)
        self.method_filters.layout().insertWidget(0, self.owner_filter)
        layout.addWidget(self.method_filters)
        owner_actions = QHBoxLayout()
        self.add_owner_button = button('添加主体',self.add_owner)
        self.delete_owner_button = button('删除主体',self.delete_owner)
        owner_actions.addWidget(self.add_owner_button);owner_actions.addWidget(self.delete_owner_button)
        owner_actions.addStretch();layout.addLayout(owner_actions)
        self.owner_notice = label(''); layout.addWidget(self.owner_notice)
        self.init_teaching_batch(layout)
        split = QSplitter(Qt.Orientation.Horizontal); split.setChildrenCollapsible(False)
        self.method_list = QListWidget(); self.method_list.setMinimumWidth(170); self.method_list.setMaximumWidth(275)
        split.addWidget(self.method_list); self.tabs = QTabWidget(); split.addWidget(self.tabs)
        self.tabs.setMinimumWidth(300); split.setSizes([210, 530]); layout.addWidget(split, 1)
        form = self.form_tab('方法与适用范围')
        self.owner_edit = QComboBox()
        for owner in self.store.list_teaching_owners(): self.owner_edit.addItem(owner['name'], owner['id'])
        self.name = QLineEdit(); self.name.setMaxLength(100)
        self.purpose = self.small_editor(65); self.steps = self.small_editor(110); self.checks = self.small_editor(90)
        self.boundary = self.small_editor(90)
        form.addRow('教学主体', self.owner_edit); form.addRow('名称', self.name); form.addRow('目的', self.purpose)
        form.addRow('步骤（每行一条）', self.steps); form.addRow('检查项（每行一条）', self.checks)
        self.type_checks = {}
        types = QWidget(); types_layout = QVBoxLayout(types); types_layout.setContentsMargins(0, 0, 0, 0)
        for value in RUBRICS:
            check = QCheckBox(value); self.type_checks[value] = check; types_layout.addWidget(check); check.toggled.connect(self.mark_dirty)
        form.addRow('适用题型', types); form.addRow('不适用情形与边界', self.boundary)
        form = self.form_tab('来源与精确引文')
        self.source_select = QComboBox(); form.addRow('当前来源（至多 8 项）', self.source_select)
        source_actions = QWidget(); source_row = QHBoxLayout(source_actions); source_row.setContentsMargins(0, 0, 0, 0)
        source_row.addWidget(button('添加来源', self.add_source)); source_row.addWidget(button('移除来源', self.remove_source)); form.addRow(source_actions)
        self.source_fields = {}
        for key, title in [('title','来源标题'), ('url','公开 HTTP(S) 地址'), ('author','作者 / 机构'),
                           ('published_at','发布日期'), ('accessed_at','核对日期'), ('locator','精确定位 / 页码 / 锚点')]:
            field = QLineEdit(); field.setMaxLength(4000); self.source_fields[key] = field; form.addRow(title, field)
        self.source_scope = QComboBox()
        for text, value in SCOPES: self.source_scope.addItem(text, value)
        form.addRow('可访问范围', self.source_scope)
        self.source_excerpt = self.small_editor(150); self.source_quote = self.small_editor(85)
        form.addRow('本机留存的来源片段', self.source_excerpt); form.addRow('精确引文（须原样位于片段）', self.source_quote)
        self.intake_button = button('读取本地文档 / 图片并预览…', self.read_source); form.addRow(self.intake_button)
        form.addRow(label('启用前须提供可核对的来源和引文。'))
        self.history = editor(readonly=True); self.tabs.addTab(self.history, '版本与审核历史')
        self.state = label(''); layout.addWidget(self.state)
        actions = QHBoxLayout(); self.new_button = button('新建', self.new_method)
        self.save_button = button('保存草稿', self.save_current, True)
        self.enable_button = button('审核并启用…', self.enable_current); self.disable_button = button('停用', self.disable_current)
        self.delete_button = button('删除', self.delete_current)
        for control in (self.new_button, self.save_button, self.enable_button, self.disable_button, self.delete_button): actions.addWidget(control)
        actions.addStretch(); layout.addLayout(actions)
        files = QHBoxLayout(); files.addWidget(button('JSON 导入预览…', self.import_file)); files.addWidget(button('导出方法 JSON…', self.export_file))
        self.trash_button = button('已删除…', self.show_deleted)
        files.addWidget(self.trash_button); files.addWidget(button('刷新', self.refresh)); files.addStretch(); layout.addLayout(files)
        self.message = label('修改后需重新审核。')
        self.message.setMaximumHeight(48); layout.addWidget(self.message)
        for field in (self.name, *self.source_fields.values()): field.textChanged.connect(self.mark_dirty)
        for field in (self.purpose, self.steps, self.checks, self.boundary, self.source_excerpt, self.source_quote): field.textChanged.connect(self.mark_dirty)
        self.owner_edit.currentIndexChanged.connect(self.mark_dirty); self.source_scope.currentIndexChanged.connect(self.mark_dirty)
        self.source_select.currentIndexChanged.connect(self.change_source)
        self.owner_filter.currentIndexChanged.connect(self.change_filter)
        self.method_filters.changed.connect(self.change_filter)
        self.method_list.currentItemChanged.connect(self.change_method)
        self.method_list.itemChanged.connect(self.update_batch_actions)
        self.refresh(); self.new_method(initial=True)

    @staticmethod
    def small_editor(height):
        field = editor(); field.setMinimumHeight(height); field.setMaximumHeight(height); return field

    def form_tab(self, title):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body); self.tabs.addTab(scroll, title); return form

    def say(self, value):
        self.message.setText(str(value)); self.message.setToolTip(str(value))

    def guarded(self):
        if self.host.workers or getattr(self.host, '_closing', False):
            self.say('请先完成或取消正在运行的任务。'); return True
        return False

    def refresh_owners(self):
        owners = self.store.list_teaching_owners()
        identities = {o['id'] for o in owners}
        previous = self.owner_edit.currentData()
        if self._filter_id not in identities:self._filter_id = None
        self.owner_filter.blockSignals(True);self.owner_edit.blockSignals(True)
        self.owner_filter.clear();self.owner_edit.clear()
        self.owner_filter.addItem(f'全部教学主体（{len(owners)}）',None)
        for owner in owners:
            self.owner_filter.addItem(owner['name'],owner['id'])
            self.owner_edit.addItem(owner['name'],owner['id'])
        self.owner_filter.setCurrentIndex(self.owner_filter.findData(self._filter_id))
        self.owner_edit.setCurrentIndex(self.owner_edit.findData(previous) if previous in identities else (0 if owners else -1))
        self.owner_filter.blockSignals(False);self.owner_edit.blockSignals(False)
        self.delete_owner_button.setEnabled(self._filter_id is not None)
        self.save_button.setEnabled(bool(owners))

    def add_owner(self):
        if self.guarded() or not self.confirm_discard():return
        name,accepted = QInputDialog.getText(self,'添加教学主体','主体名称')
        if not accepted or self.guarded():return
        try:
            owner = self.store.add_teaching_owner(name)
            self.current=None;self.dirty=False;self._filter_id=owner['id']
            self.refresh();self.new_method(initial=True)
            self.say('主体已添加。')
        except (ValueError,TypeError,OSError,sqlite3.Error) as exc:self.say(str(exc))

    def delete_owner(self):
        if self.guarded() or not self.confirm_discard():return
        identity = self.owner_filter.currentData()
        if identity is None:self.say('请先选择要删除的主体。');return
        methods = [r for r in self.store.list_teaching_methods() if r['owner_id']==identity]
        text=(f'删除主体“{self.owner_filter.currentText()}”？\n'
              f'其下 {len(methods)} 条方法一并移入“已删除”；历史报告保留。')
        if QMessageBox.question(self,'删除教学主体',text,
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        if self.guarded():return
        try:
            count = self.store.delete_teaching_owner(identity,expected_methods=methods)
            self.current=None;self.dirty=False;self._filter_id=None
            self.refresh();self.new_method(initial=True)
            self.say(f'主体已删除，{count} 条方法已移入“已删除”。')
        except (ValueError,TypeError,OSError,sqlite3.Error) as exc:self.say(str(exc))

    def mark_dirty(self, *args):
        if not self._loading:
            self.dirty = True; self.state.setText('未保存的修改 · 保存会停用当前方法，保留全部历史。')

    def confirm_discard(self):
        if not self.dirty: return True
        return QMessageBox.question(self, '放弃方法编辑？', '当前方法有未保存内容，是否放弃这些修改？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def change_filter(self):
        if self.guarded() or not self.confirm_discard():
            self.owner_filter.blockSignals(True)
            self.owner_filter.setCurrentIndex(self.owner_filter.findData(self._filter_id))
            self.owner_filter.blockSignals(False)
            self.method_filters.restore(self._method_filter_state)
            return
        self.check_methods(False)
        self._method_filter_state = self.method_filters.state()
        self._filter_id = self.owner_filter.currentData(); self.current = None; self.dirty = False; self.refresh(); self.new_method(initial=True)

    def refresh(self, *args):
        self.refresh_owners()
        self.records = self.store.list_teaching_methods()
        selected = self.current.get('id') if self.current else None
        checked={r['id'] for r in self.checked_methods()}
        self.method_list.blockSignals(True); self.method_list.clear()
        owner_names = {owner['id']: owner['name'] for owner in self.store.list_teaching_owners()}
        for record in self.records:
            if not self.method_filters.matches(record, owner_id=self._filter_id, owner_names=owner_names): continue
            item = QListWidgetItem(record['name'] + '\n' + STATUS[record['status']]); item.setData(Qt.ItemDataRole.UserRole, record['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if record['id'] in checked else Qt.CheckState.Unchecked)
            item.setToolTip(record['id'] + ' · v' + str(record['version'])); self.method_list.addItem(item)
            if record['id'] == selected: self.method_list.setCurrentItem(item)
        self.method_list.blockSignals(False)
        self.update_batch_actions()
        active = sum(r['enabled'] for r in self.records)
        self.summary.setText(f'显示 {self.method_list.count()} / {len(self.records)} 条 · 已启用 {active}')
        owner = next((o for o in self.store.list_teaching_owners() if o['id'] == self._filter_id), None)
        self.owner_notice.setText(owner['description'] if owner else '')
        self.owner_notice.setToolTip('\n'.join(owner['urls']) if owner else '')
        # Repaint lists after other jobs without overwriting the working editor.
        if selected and not self.dirty:
            record = self.store.get_teaching_method(selected)
            if record: self.load_record(record)
            else:self.new_method(initial=True)
        self.host.refresh_teaching_summary()

    def change_method(self, item, previous=None):
        if item: self.select_method(item.data(Qt.ItemDataRole.UserRole))

    def select_method(self, identity):
        if self.current and identity == self.current.get('id'): return
        if self.guarded() or not self.confirm_discard():
            self.restore_list_selection(); return
        record = self.store.get_teaching_method(identity)
        if record: self.load_record(record); self.restore_list_selection()

    def restore_list_selection(self):
        identity = self.current.get('id') if self.current else None
        self.method_list.blockSignals(True); self.method_list.clearSelection()
        for i in range(self.method_list.count()):
            item = self.method_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == identity: self.method_list.setCurrentItem(item); item.setSelected(True)
        self.method_list.blockSignals(False)

    def new_method(self, *args, initial=False):
        if self.guarded() or (not initial and not self.confirm_discard()): return
        self.load_record({'owner_id': self._filter_id or self.owner_edit.currentData(), 'name': '', 'purpose': '', 'steps': [],
            'checks': [], 'applicable_types': ['归纳概括'], 'not_applicable': '', 'sources': [], 'version': 0, 'status': 'draft'})
        self.method_list.clearSelection(); self.tabs.setCurrentIndex(0)

    def load_record(self, record):
        self._loading = True; self.current = deepcopy(record)
        self.owner_edit.setCurrentIndex(self.owner_edit.findData(record['owner_id'])); self.name.setText(record['name'])
        for key, field in [('purpose',self.purpose), ('not_applicable',self.boundary)]: field.setPlainText(record.get(key, ''))
        self.steps.setPlainText('\n'.join(record.get('steps', []))); self.checks.setPlainText('\n'.join(record.get('checks', [])))
        for key, check in self.type_checks.items(): check.setChecked(key in record.get('applicable_types', []))
        self._sources = deepcopy(record.get('sources', [])); self._source_index = -1; self.refresh_sources()
        versions = self.store.list_teaching_versions(record['id']) if record.get('id') else []
        self.history.setPlainText(json.dumps(versions, ensure_ascii=False, indent=2) if versions else '尚无本地版本。')
        self.state.setText(f'{record.get("id", "新建方法")} · v{record.get("version", 0)} · {STATUS[record["status"]]}')
        self.delete_button.setEnabled(bool(record.get('id')))
        self._loading = False; self.dirty = False

    def store_source_fields(self):
        if 0 <= self._source_index < len(self._sources):
            source = {key: field.text() for key, field in self.source_fields.items()}
            source.update(access_scope=self.source_scope.currentData(), excerpt=self.source_excerpt.toPlainText(), quote=self.source_quote.toPlainText())
            self._sources[self._source_index] = source

    def change_source(self, index):
        self.store_source_fields(); self._source_index = index
        old_loading = self._loading; self._loading = True
        source = self._sources[index] if 0 <= index < len(self._sources) else {}
        for key, field in self.source_fields.items(): field.setText(source.get(key, ''))
        self.source_scope.setCurrentIndex(max(0, self.source_scope.findData(source.get('access_scope', 'catalog'))))
        self.source_excerpt.setPlainText(source.get('excerpt', '')); self.source_quote.setPlainText(source.get('quote', ''))
        self._loading = old_loading
        for field in (*self.source_fields.values(), self.source_scope, self.source_excerpt, self.source_quote, self.intake_button): field.setEnabled(index >= 0)

    def refresh_sources(self, selected=0):
        self.source_select.blockSignals(True); self.source_select.clear()
        for i, source in enumerate(self._sources): self.source_select.addItem(f'{i+1} · ' + source.get('title', '未填写标题'))
        self.source_select.setCurrentIndex(selected if self._sources else -1); self.source_select.blockSignals(False)
        self.change_source(self.source_select.currentIndex())

    def add_source(self):
        if self.guarded(): return
        if len(self._sources) >= 8: self.say('来源至多八项。'); return
        self.store_source_fields(); self._sources.append({key: '' for key in SOURCE_FIELDS}); self._sources[-1]['access_scope'] = 'catalog'
        self._source_index = -1; self.refresh_sources(len(self._sources)-1); self.mark_dirty()

    def remove_source(self):
        if self.guarded() or self._source_index < 0: return
        self._sources.pop(self._source_index); self._source_index = -1; self.refresh_sources(); self.mark_dirty()

    def editable(self):
        self.store_source_fields()
        result = {'owner_id': self.owner_edit.currentData(), 'name': self.name.text(), 'purpose': self.purpose.toPlainText(),
            'steps': [s.strip() for s in self.steps.toPlainText().splitlines() if s.strip()],
            'checks': [s.strip() for s in self.checks.toPlainText().splitlines() if s.strip()],
            'applicable_types': [key for key, check in self.type_checks.items() if check.isChecked()],
            'not_applicable': self.boundary.toPlainText(), 'sources': deepcopy(self._sources)}
        if self.current.get('id'): result['id'] = self.current['id']
        return validate_teaching_method(result)

    def save_current(self):
        if self.guarded(): return
        try:
            record = self.store.save_teaching_method(self.editable(), expected_version=self.current.get('version', 0))
            self.load_record(record); self.refresh(); self.restore_list_selection(); self.say('已保存本地版本；启用前须重新审核。')
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc: self.say(str(exc))

    def enable_current(self):
        if self.guarded(): return
        if self.dirty or not self.current.get('id'):
            self.say('请先保存当前修改，再核对保存版本并明确审核启用。'); return
        review = TeachingReviewDialog(self)
        if review.exec() != QDialog.DialogCode.Accepted: return
        if not review.reviewer.text().strip() or not review.note.toPlainText().strip(): self.say('须填写本地审核人姓名和说明。'); return
        text = (f'启用 {self.current["name"]}（{self.current["id"]}，v{self.current["version"]}）？\n'
                f'本地审核人：{review.reviewer.text().strip()}\n审核说明：{review.note.toPlainText().strip()}\n'
                '启用后，匹配的方法及来源会随批改内容发送给模型。')
        if QMessageBox.question(self, '确认本地审核并启用', text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self.set_enabled(True, review.reviewer.text().strip(), review.note.toPlainText().strip())

    def disable_current(self):
        if self.guarded(): return
        if self.dirty: self.say('请先保存或明确放弃修改，再停用已保存版本。'); return
        self.set_enabled(False)

    def set_enabled(self, enabled, reviewer='', note=''):
        if self.guarded() or not self.current.get('id'): return
        try:
            record = self.store.set_teaching_method_enabled(self.current['id'], enabled, reviewer=reviewer,
                note=note, expected_version=self.current['version'])
            self.load_record(record); self.refresh(); self.say('已启用本地审核版本。' if enabled else '已停用，历史版本仍保留。')
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc: self.say(str(exc))

    def delete_current(self):
        if self.guarded():return
        if self.dirty:
            self.say('请先保存修改或明确放弃修改，再删除已保存的方法。');return
        if not self.current.get('id'):
            self.say('请先选择要删除的方法。');return
        identity=self.current['id'];version=self.current['version']
        text=(f'将“{self.current["name"]}”移入已删除列表？\n'
              '删除后不再用于新的批改；历史报告和版本保留，可从“已删除”恢复。')
        if QMessageBox.question(self,'删除教学方法',text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        if self.guarded():return
        try:
            self.store.delete_method('teaching',identity,expected_version=version)
            self.current=None;self.dirty=False;self.refresh();self.new_method(initial=True)
            self.say('方法已移入已删除列表；历史记录保留。')
        except (ValueError,TypeError,OSError,sqlite3.Error) as exc:self.say(str(exc))

    def show_deleted(self):
        if self.guarded():return
        if self.dirty:
            self.say('请先保存当前修改或明确放弃，再打开已删除列表。');return
        from .article_method_editor import MethodTrashDialog
        try:
            dialog=MethodTrashDialog(self.store,'teaching',self)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                self.host.refresh_methods()
                self.say('方法已彻底删除，不能从已删除列表恢复。' if dialog.action=='purged' else '已恢复并停用。')
        except (ValueError,TypeError,OSError,sqlite3.Error) as exc:self.say(str(exc))

    def import_file(self, path=None):
        if self.guarded(): return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, '导入教学方法 JSON', '', 'JSON (*.json)')
        if not path: return
        try:
            source = Path(path)
            if source.stat().st_size > 2*1024*1024: raise ValueError('JSON 不能超过 2 MB。')
            raw = json.loads(source.read_bytes().decode('utf-8-sig'))
            if not isinstance(raw, list) or not 1 <= len(raw) <= 100: raise ValueError('每次导入须为 1–100 条方法。')
            records = [validate_teaching_method(row) for row in raw]
            ids = [r['id'] for r in records if 'id' in r]
            if len(ids) != len(set(ids)): raise ValueError('导入标识不能重复。')
            preview = TeachingJsonPreviewDialog(records, source.name, self)
            if preview.exec() != QDialog.DialogCode.Accepted or self.guarded(): return
            imported = self.store.import_teaching_methods(records)
            dirty = self.dirty; self.refresh()
            if not dirty:
                self._filter_id = None
                self.owner_filter.blockSignals(True); self.owner_filter.setCurrentIndex(0); self.owner_filter.blockSignals(False)
                self.current = None; self.refresh(); self.select_method(imported[0]['id'])
            self.say('已导入新草稿；冲突标识会复制。实际标识：' + '、'.join(r['id'] for r in imported))
        except (ValueError, TypeError, OSError, UnicodeError, RecursionError, sqlite3.Error) as exc: self.say('导入未完成，当前编辑仍保留。' + str(exc))

    def export_file(self, path=None):
        if self.guarded(): return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '导出已保存的教学方法', '教学方法草稿.json', 'JSON (*.json)')
        if not path: return
        try:
            target = Path(path)
            if not target.suffix: target = target.with_suffix('.json')
            target.write_text(json.dumps(self.store.export_teaching_methods(), ensure_ascii=False, indent=2), encoding='utf8')
            self.say('已导出已保存的方法；不包含审核、启用或版本信任声明。未保存编辑仍保留。')
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc: self.say(str(exc))

    def read_source(self, paths=None):
        if self.guarded() or self._source_index < 0: return
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, '读取本机来源片段', '', FILE_FILTER)
        if not paths: return
        current_id = self.current.get('id'); source_index = self._source_index
        def preview(batch):
            if getattr(self.host, '_closing', False): return
            dialog = TeachingSourcePreviewDialog(batch, self)
            if dialog.exec() != QDialog.DialogCode.Accepted: return
            text = dialog.text.toPlainText()
            if not text.strip() or len(text) > 4000: self.say('来源片段须为 1–4000 字。'); return
            if self.current.get('id') == current_id and self._source_index == source_index:
                self.source_excerpt.setPlainText(text); self.say('片段已写入编辑区，请填写来源定位并核对精确引文后保存。')
        self.host.start_job('读取本机方法来源', lambda progress, cancel: read_inputs(paths, progress=progress,
            cancel=cancel, retain_failed=True), preview, domains=())


class TeachingUiMixin:
    def preflight_teaching(self, submission):
        try:
            from ..method_presets import resolve_method_choice
            return resolve_method_choice(self.store, submission)['teaching_snapshot']
        except (ValueError, TypeError) as exc:
            try:
                selection = normalize_teaching_selection(self.store.get_setting('teaching_selection'))
                names = {o['id']: o['name'] for o in self.store.list_teaching_owners(include_deleted=True)}
                prefix = '、'.join(names.get(o,o) for o in selection['owner_ids'])
            except (ValueError, TypeError): prefix = ''
            raise ValueError((prefix + '：' if prefix else '') + str(exc)) from None

    def show_teaching_selection(self):
        if self.workers: self.notify('请先完成或取消正在运行的任务。'); return
        dialog = TeachingSelectionDialog(self.store, self)
        if dialog.exec() == QDialog.DialogCode.Accepted: self.refresh_teaching_summary()

    def refresh_teaching_summary(self, *args):
        if not hasattr(self, 'teaching_summary'): return
        self.teaching_summary.setToolTip('')
        from ..method_presets import normalize_method_choice,resolve_method_choice
        try:
            choice=normalize_method_choice(self.store.get_setting('method_choice'))
            if choice['mode']!='auto':
                selected=resolve_method_choice(self.store,{'question_type':self.question_type.currentText()},choice)
                if choice['mode']=='off':text='方法辅助：关闭（教学与文章方法）'
                else:text='方法组合：'+selected['preset']['name']+' · 本次实际使用名单可预览'
                self.teaching_summary.setText(text)
                self.refresh_method_preset_preview()
                return
        except (ValueError,TypeError) as exc:
            self.teaching_summary.setText('方法组合不可用：'+str(exc));self.teaching_summary.setToolTip(str(exc))
            self.refresh_method_preset_preview();return
        try:
            selection = normalize_teaching_selection(self.store.get_setting('teaching_selection'))
            title = dict((value, text) for text, value in MODES)[selection['mode']]
            if selection['mode'] == 'off': text = '方法辅助：关闭'
            else:
                snapshot = resolve_teaching_guidance(self.store, {'question_type': self.question_type.currentText()}, selection)
                if not snapshot: text = '方法辅助：综合 · 当前题型无可用已审核方法，照常批改'
                else:
                    names = {o['id']:o['name'] for o in self.store.list_teaching_owners(include_deleted=True)}
                    owners = list(dict.fromkeys(r['owner_id'] for r in snapshot['methods']))
                    text = f'方法辅助：{title} · {len(snapshot["methods"])} 张 · ' + '、'.join(names.get(o,o) for o in owners)
            self.teaching_summary.setText(text)
        except (ValueError, TypeError) as exc:
            try:
                selected = normalize_teaching_selection(self.store.get_setting('teaching_selection'))
                names = {o['id']:o['name'] for o in self.store.list_teaching_owners(include_deleted=True)}
                choice = '、'.join(names.get(o,o) for o in selected['owner_ids'])
            except (ValueError, TypeError): choice = '无效选择'
            self.teaching_summary.setText('方法辅助：' + choice + ' · 当前题型无匹配的已审核方法；批改前须处理。')
            self.teaching_summary.setToolTip(str(exc))
        self.refresh_method_preset_preview()

    def refresh_teaching_busy(self):
        busy = bool(self.workers)
        if hasattr(self, 'teaching_select_button'): self.teaching_select_button.setEnabled(not busy)
        if hasattr(self, 'teaching_panel'): self.teaching_panel.setEnabled(not busy)
        if hasattr(self, 'enable_method_button'):
            self.update_method_actions()
        self.refresh_method_preset_busy()

