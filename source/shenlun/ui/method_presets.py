"""Named exact-method combinations, edited locally without changing method trust."""
from copy import deepcopy
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..domain import RUBRICS
from .widgets import button, editor


ERRORS = (ValueError, TypeError, OSError, sqlite3.Error)
KINDS = (('teaching', '教学方法'), ('article', '文章方法'))


def plain_label(text=''):
    value = QLabel(text)
    value.setWordWrap(True)
    value.setTextFormat(Qt.TextFormat.PlainText)
    return value


def method_status(record):
    if record is None:
        return '不可用 · 已删除或不存在'
    if record.get('enabled'):
        return '已启用'
    return '已停用' if record.get('status') == 'disabled' else '草稿 · 未启用'


def method_description(record, identity):
    if not record:
        return f'{identity} · {method_status(None)}'
    version = f' · 版本 {record["version"]}' if 'version' in record else ''
    return f'{record.get("name", identity)} · {identity}{version} · {method_status(record)}'


def preview_text(resolved):
    """Describe the actual resolved live records, including their identity/version."""
    choice = resolved['choice']
    title = {'auto': '自动匹配', 'off': '关闭方法辅助', 'preset': '指定组合'}[choice['mode']]
    preset = resolved.get('preset')
    if preset:
        title += f'：{preset["name"]} · 组合版本 {preset["version"]}'
    lines = [title]
    for kind, caption in KINDS:
        rows = ((resolved.get('teaching_snapshot') or {}).get('methods', [])
                if kind == 'teaching' else resolved.get('article_methods', []))
        lines.append(f'\n{caption}：{len(rows)} 项')
        lines.extend('• ' + method_description(r, r['id']) for r in rows)
    lines.append('\n批改时保存本次方法与来源版本。')
    return '\n'.join(lines)


class MethodPresetDialog(QDialog):
    def __init__(self, store, host):
        super().__init__(host)
        self.store = store
        self.host = host
        self.current = None
        self.dirty = False
        self._loading = False
        self._closing_dialog = False
        self._members = {kind: [] for kind, _ in KINDS}
        self.records = {}
        self.setWindowTitle('方法组合 · 精确成员')
        self.setObjectName('method_preset_dialog')
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label('每类最多 6 项；使用前须全部可用。'))
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        self.preset_list = QListWidget()
        self.preset_list.setAccessibleName('已保存的方法组合')
        left_layout.addWidget(self.preset_list, 1)
        self.new_button = button('新建组合', self.new_preset, name='new_method_preset')
        left_layout.addWidget(self.new_button)
        right = QWidget(); right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        form = QFormLayout()
        self.name = QLineEdit(); self.name.setMaxLength(100)
        self.name.setAccessibleName('组合名称')
        self.question_type = QComboBox(); self.question_type.addItems(list(RUBRICS))
        self.question_type.setAccessibleName('组合适用题型')
        form.addRow('组合名称', self.name)
        form.addRow('适用题型', self.question_type)
        right_layout.addLayout(form)
        self.load_checks_button = button('载入当前勾选', self.load_current_checks)
        self.load_checks_button.setToolTip('用当前勾选替换组合成员。')
        right_layout.addWidget(self.load_checks_button)
        self.tabs = QTabWidget()
        self.pools = {}; self.searches = {}; self.states = {}; self.types = {}; self.counts = {}
        for kind, caption in KINDS:
            page = QWidget(); body = QVBoxLayout(page)
            body.setContentsMargins(8, 8, 8, 8)
            search = QLineEdit(); search.setPlaceholderText('搜索名称、标识、步骤或来源…')
            search.setClearButtonEnabled(True); search.setAccessibleName(caption + '搜索')
            state = QComboBox()
            for text, value in [('全部状态', None), ('已启用', True), ('未启用 / 草稿', False)]:
                state.addItem(text, value)
            types = QComboBox(); types.addItem('全部题型', None)
            for text in RUBRICS: types.addItem(text, text)
            filters = QHBoxLayout(); filters.addWidget(search, 1); filters.addWidget(state); filters.addWidget(types)
            body.addLayout(filters)
            pool = QListWidget(); pool.setAccessibleName(caption + '候选，勾选加入组合')
            body.addWidget(pool, 1)
            count = plain_label(); body.addWidget(count)
            self.pools[kind] = pool; self.searches[kind] = search
            self.states[kind] = state; self.types[kind] = types; self.counts[kind] = count
            search.textChanged.connect(lambda *args, k=kind: self.render_pool(k))
            state.currentIndexChanged.connect(lambda *args, k=kind: self.render_pool(k))
            types.currentIndexChanged.connect(lambda *args, k=kind: self.render_pool(k))
            pool.itemChanged.connect(lambda item, k=kind: self.member_toggled(k, item))
            self.tabs.addTab(page, caption + '候选')
        member_page = QWidget(); member_layout = QVBoxLayout(member_page)
        member_layout.addWidget(plain_label('筛选不影响已选成员。'))
        self.members_list = QListWidget(); self.members_list.setAccessibleName('组合完整成员')
        member_layout.addWidget(self.members_list, 1)
        member_layout.addWidget(button('移除所选成员', self.remove_selected_member))
        self.tabs.addTab(member_page, '组合完整成员')
        right_layout.addWidget(self.tabs, 1)
        self.member_summary = plain_label(); right_layout.addWidget(self.member_summary)
        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([200, 700]); split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        self.message = plain_label(); layout.addWidget(self.message)
        actions = QHBoxLayout()
        self.save_button = button('保存组合', self.save_current, True)
        self.reload_button = button('重新载入', self.reload_current)
        self.delete_button = button('删除组合…', self.delete_current)
        actions.addWidget(self.save_button); actions.addWidget(self.reload_button)
        actions.addWidget(self.delete_button); actions.addStretch()
        close = button('关闭', self.reject); actions.addWidget(close)
        layout.addLayout(actions)
        self.name.textChanged.connect(self.mark_dirty)
        self.question_type.currentTextChanged.connect(self.mark_dirty)
        self.preset_list.currentItemChanged.connect(self._list_selected)
        self.refresh(); self.new_preset(initial=True)
        self.host._method_preset_dialog = self
        self.refresh_busy()

    def guarded(self):
        if self.host.workers or getattr(self.host, '_closing', False):
            self.message.setText('请先完成或取消正在运行的任务。')
            return True
        return False

    def refresh_busy(self):
        idle = not self.host.workers and not getattr(self.host, '_closing', False)
        # Keep the Close button available; dirty edits are still protected.
        for control in (self.preset_list, self.name, self.question_type, self.tabs,
                        self.load_checks_button, self.save_button, self.new_button):
            control.setEnabled(idle)
        self.delete_button.setEnabled(bool(idle and self.current))
        self.reload_button.setEnabled(bool(idle and self.current))

    def mark_dirty(self, *args):
        if not self._loading:
            self.dirty = True

    def may_discard(self):
        return not self.dirty or QMessageBox.question(self, '保留未保存的组合？',
            '组合还有未保存的修改。是否放弃这些修改？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def reject(self):
        if self._closing_dialog or self.may_discard():
            super().reject()

    def closeEvent(self, event):
        if not self.may_discard():
            event.ignore(); return
        self._closing_dialog = True
        try:
            super().closeEvent(event)
        finally:
            self._closing_dialog = False

    def member_ids(self, kind):
        return list(self._members[kind])

    def refresh(self):
        """Refresh labels and candidates, never replace unsaved member selection."""
        try:
            presets = self.store.list_method_presets()
            self.records = {'teaching': {r['id']: r for r in self.store.list_teaching_methods()},
                            'article': {r['id']: r for r in self.store.list_cards()}}
            self.preset_list.blockSignals(True)
            self.preset_list.clear()
            for record in presets:
                item = QListWidgetItem(record['name'] + ' · ' + record['question_type'])
                item.setData(Qt.ItemDataRole.UserRole, record['id'])
                self.preset_list.addItem(item)
                if self.current and self.current['id'] == record['id']:
                    self.preset_list.setCurrentItem(item)
            self.preset_list.blockSignals(False)
            self.render_members()
            for kind, _ in KINDS: self.render_pool(kind)
        except ERRORS as exc:
            self.preset_list.blockSignals(False)
            self.message.setText('刷新未完成，编辑内容仍保留。' + str(exc))

    def _list_selected(self, item, previous):
        if item:
            self.select_preset(item.data(Qt.ItemDataRole.UserRole))

    def restore_list_selection(self):
        self.preset_list.blockSignals(True)
        self.preset_list.setCurrentRow(-1)
        if self.current:
            for index in range(self.preset_list.count()):
                item = self.preset_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == self.current['id']:
                    self.preset_list.setCurrentItem(item); break
        self.preset_list.blockSignals(False)

    def select_preset(self, identity):
        if self.current and self.current['id'] == identity: return
        if self.guarded() or not self.may_discard():
            self.restore_list_selection(); return
        try:
            record = self.store.get_method_preset(identity)
            if not record: raise ValueError('组合已删除，请刷新列表。')
            self.load_record(record)
            self.restore_list_selection()
        except ERRORS as exc:
            self.message.setText(str(exc)); self.restore_list_selection()

    def load_record(self, record):
        self._loading = True
        try:
            self.current = deepcopy(record)
            self.name.setText(record['name'] if record else '')
            self.question_type.setCurrentText(record['question_type'] if record else self.host.question_type.currentText())
            self._members = {kind: list(record[kind + '_ids']) if record else [] for kind, _ in KINDS}
            self.dirty = False
            self.render_members()
            for kind, _ in KINDS: self.render_pool(kind)
            self.refresh_busy()
        finally:
            self._loading = False

    def new_preset(self, *args, initial=False):
        if not initial and (self.guarded() or not self.may_discard()): return
        self.load_record(None); self.restore_list_selection()
        self.message.setText('填写名称并勾选精确成员；保存不会启用任何方法。')

    def reload_current(self):
        if self.guarded() or not self.current or not self.may_discard(): return
        try:
            record = self.store.get_method_preset(self.current['id'])
            if record is None: raise ValueError('组合已删除，当前编辑仍保留；可新建组合后重新选择成员。')
            self.load_record(record); self.refresh()
            self.message.setText('已载入最新保存的组合。')
        except ERRORS as exc:
            self.message.setText('重新载入未完成，编辑内容仍保留。' + str(exc))

    def render_pool(self, kind):
        if not self.records: return
        query = self.searches[kind].text().strip().casefold()
        state = self.states[kind].currentData(); question_type = self.types[kind].currentData()
        pool = self.pools[kind]; pool.blockSignals(True); pool.clear()
        candidates = dict(self.records[kind])
        for identity in self._members[kind]: candidates.setdefault(identity, None)
        for identity, record in candidates.items():
            if query and query not in (str(record) + identity).casefold(): continue
            if state is not None and bool((record or {}).get('enabled')) != state: continue
            if question_type and question_type not in (record or {}).get('applicable_types', []): continue
            item = QListWidgetItem(method_description(record, identity))
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft)
            item.setToolTip(str((record or {}).get('purpose') or '') + '\n适用题型：' + '、'.join((record or {}).get('applicable_types', [])))
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if identity in self._members[kind] else Qt.CheckState.Unchecked)
            pool.addItem(item)
        pool.blockSignals(False)
        self.counts[kind].setText(f'显示 {pool.count()} / 全部 {len(candidates)} 项 · 已加入 {len(self._members[kind])} / 6 项')

    def render_members(self):
        self.members_list.clear()
        for kind, caption in KINDS:
            for identity in self._members[kind]:
                record = self.records.get(kind, {}).get(identity)
                item = QListWidgetItem(caption + ' · ' + method_description(record, identity))
                item.setData(Qt.ItemDataRole.UserRole, (kind, identity))
                self.members_list.addItem(item)
        self.member_summary.setText(f'组合成员：教学 {len(self._members["teaching"])} / 6 · 文章 {len(self._members["article"])} / 6 · 完整名单见第三页')

    def member_toggled(self, kind, item):
        identity = item.data(Qt.ItemDataRole.UserRole)
        if self.guarded():
            self.render_pool(kind); return
        checked = item.checkState() == Qt.CheckState.Checked
        if checked and identity not in self._members[kind]:
            if len(self._members[kind]) >= 6:
                self.message.setText('每类最多 6 项，请先移除已有成员。')
                self.render_pool(kind); return
            self._members[kind].append(identity)
        elif not checked and identity in self._members[kind]:
            self._members[kind].remove(identity)
        else: return
        self.mark_dirty(); self.render_members(); self.render_pool(kind)

    def remove_selected_member(self):
        if self.guarded(): return
        item = self.members_list.currentItem()
        if not item: return
        kind, identity = item.data(Qt.ItemDataRole.UserRole)
        self._members[kind].remove(identity)
        self.mark_dirty(); self.render_members(); self.render_pool(kind)

    def load_current_checks(self):
        if self.guarded(): return
        selected = {'teaching': self.host.teaching_panel.checked_methods(),
                    'article': self.host.checked_article_methods()}
        if not any(selected.values()):
            self.message.setText('方法库当前没有勾选，原组合成员保留。'); return
        if any(len(rows) > 6 for rows in selected.values()):
            self.message.setText('每类最多 6 项；请减少方法库勾选后重试，原组合成员保留。'); return
        self._members = {kind: [r['id'] for r in rows] for kind, rows in selected.items()}
        self.mark_dirty(); self.refresh()
        self.message.setText('已载入当前两类勾选；请核对题型和成员后保存。')

    def save_current(self):
        if self.guarded(): return
        value = dict(name=self.name.text(), question_type=self.question_type.currentText(),
                     teaching_ids=self.member_ids('teaching'), article_ids=self.member_ids('article'))
        if self.current: value['id'] = self.current['id']
        try:
            record = self.store.save_method_preset(value, expected_version=self.current['version'] if self.current else 0)
            self.load_record(record); self.refresh(); self.host.refresh_method_presets()
            self.message.setText('组合已保存；方法启用状态未改变。')
        except ERRORS as exc:
            self.message.setText('保存未完成，修改仍保留。' + str(exc))

    def delete_current(self):
        if self.guarded() or not self.current: return
        if QMessageBox.question(self, '删除方法组合',
                f'删除“{self.current["name"]}”？\n删除正在使用的组合后关闭方法辅助，方法本身的启用状态不变。历史记录仍保留。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        if self.guarded(): return
        try:
            self.store.delete_method_preset(self.current['id'], expected_version=self.current['version'])
            self.load_record(None); self.refresh(); self.host.refresh_method_presets()
            self.message.setText('组合已删除；正在使用的组合已切换为关闭方法辅助。')
        except ERRORS as exc:
            self.message.setText('删除未完成，编辑内容仍保留。' + str(exc))


class MethodPresetUiMixin:
    def init_method_presets(self, settings_layout):
        controls = QHBoxLayout()
        controls.addWidget(QLabel('使用方法'))
        self.method_choice = QComboBox(); self.method_choice.setObjectName('method_choice')
        self.method_choice.setAccessibleName('本次批改的方法组合')
        self.method_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.method_choice.setMinimumContentsLength(12)
        controls.addWidget(self.method_choice, 1)
        self.method_preset_manage_button = button('管理组合…', self.show_method_presets)
        self.method_preset_preview_button = button('查看本次方法…', self.show_method_preview)
        controls.addWidget(self.teaching_select_button)
        controls.addWidget(self.method_preset_manage_button); controls.addWidget(self.method_preset_preview_button)
        settings_layout.addLayout(controls)
        self.method_preset_summary = plain_label()
        self.method_preset_summary.setObjectName('muted')
        self.method_preset_summary.setParent(self.exercise_settings_card)
        self.method_preset_summary.hide()  # Counts are visible on the preview button without consuming another input row.
        self.method_choice.currentIndexChanged.connect(self._method_choice_changed)
        self.refresh_method_presets()

    def refresh_method_presets(self, *args):
        if not hasattr(self, 'method_choice'): return
        try:
            from ..method_presets import normalize_method_choice
            try:
                value = normalize_method_choice(self.store.get_setting('method_choice'))
                selected = value.get('preset_id') if value['mode'] == 'preset' else value['mode']
            except (ValueError, TypeError):
                selected = None
            presets = self.store.list_method_presets()
            self.method_choice.blockSignals(True); self.method_choice.clear()
            self.method_choice.addItem('自动匹配 · 沿用方法辅助设置', 'auto')
            self.method_choice.addItem('关闭 · 不使用教学或文章方法', 'off')
            for record in presets:
                self.method_choice.addItem(record['name'] + ' · ' + record['question_type'], record['id'])
            index = self.method_choice.findData(selected)
            if index < 0:
                self.method_choice.addItem('保存的组合不可用 · 请重新选择', selected)
                index = self.method_choice.count() - 1
            self.method_choice.setCurrentIndex(index)
        except ERRORS as exc:
            self.method_preset_summary.setText('组合列表不可用：' + str(exc))
        finally:
            self.method_choice.blockSignals(False)
        self.refresh_teaching_summary(); self.refresh_method_preset_busy()

    def _method_choice_changed(self, *args):
        if self.workers or getattr(self, '_closing', False):
            self.refresh_method_presets(); return
        selected = self.method_choice.currentData()
        value = {'mode': selected} if selected in ('auto', 'off') else {'mode': 'preset', 'preset_id': selected}
        try:
            from ..method_presets import normalize_method_choice
            self.store.set_setting('method_choice', normalize_method_choice(value))
        except ERRORS as exc:
            self.refresh_method_presets(); self.notify('方法选择未保存。' + str(exc)); return
        self.refresh_teaching_summary(); self.refresh_method_preset_busy()

    def method_preview(self, submission=None):
        from ..method_presets import resolve_method_choice
        if submission is None:
            submission = {'question_type': self.question_type.currentText()}
        return resolve_method_choice(self.store, submission)

    def invalidate_method_context(self, *args):
        """Mark the method preview stale without touching the library.

        Method resolution validates every enabled card against its source
        articles. Doing that inside the type combo's signal handler froze the
        window on each switch, so the lists are resolved on demand instead.
        """
        if hasattr(self, 'method_preset_summary'):
            self.method_preset_summary.setText('本次方法：题型已更改，打开“查看本次方法…”核对。')
            self.method_preset_summary.setToolTip('')
        if hasattr(self, 'method_preset_preview_button'):
            self.method_preset_preview_button.setText('查看本次方法…')
            self.method_preset_preview_button.setToolTip('题型已更改，打开后核对本次实际使用的方法名单。')
        try:
            from ..method_presets import normalize_method_choice
            mode = normalize_method_choice(self.store.get_setting('method_choice'))['mode']
        except (ValueError, TypeError):
            mode = 'auto'
        if mode != 'auto':
            # Explicit presets only resolve their small member list, so keeping
            # their counts current stays cheap.
            self.refresh_teaching_summary()
            return
        if hasattr(self, 'teaching_summary'):
            self.teaching_summary.setText('方法辅助：题型已更改，批改前将按当前题型核对。')
            self.teaching_summary.setToolTip('')

    def _apply_method_preview(self, resolved):
        teaching = (resolved.get('teaching_snapshot') or {}).get('methods', [])
        article = resolved.get('article_methods', [])
        self.method_preset_summary.setText(f'本次方法：教学 {len(teaching)} 项 · 文章 {len(article)} 项 · 查看完整名单后批改。')
        self.method_preset_summary.setToolTip(preview_text(resolved))
        self.method_preset_preview_button.setText(f'查看方法（{len(teaching)+len(article)}）…')
        self.method_preset_preview_button.setToolTip(preview_text(resolved))

    def refresh_method_preset_preview(self, *args):
        if not hasattr(self, 'method_preset_summary'): return
        try:
            resolved = self.method_preview()
        except ERRORS as exc:
            self.method_preset_summary.setText('本次方法不可用：' + str(exc))
            self.method_preset_summary.setToolTip(str(exc))
            self.method_preset_preview_button.setText('方法不可用…')
            self.method_preset_preview_button.setToolTip(str(exc))
            return
        self._apply_method_preview(resolved)

    def refresh_method_preset_busy(self):
        if not hasattr(self, 'method_choice'): return
        idle = not self.workers and not getattr(self, '_closing', False)
        for control in (self.method_choice, self.method_preset_manage_button, self.method_preset_preview_button):
            control.setEnabled(idle)
        try:
            from ..method_presets import normalize_method_choice
            selected = normalize_method_choice(self.store.get_setting('method_choice'))
        except ERRORS:
            selected = {}
        if hasattr(self, 'teaching_select_button'):
            self.teaching_select_button.setEnabled(idle and isinstance(selected, dict) and selected.get('mode') == 'auto')
        dialog = getattr(self, '_method_preset_dialog', None)
        if dialog: dialog.refresh_busy()

    def show_method_presets(self):
        if self.workers or getattr(self, '_closing', False):
            self.notify('请先完成或取消正在运行的任务。'); return
        dialog = MethodPresetDialog(self.store, self)
        choice = self.store.get_setting('method_choice', {'mode': 'auto'})
        if isinstance(choice, dict) and choice.get('mode') == 'preset':
            dialog.select_preset(choice.get('preset_id'))
        dialog.exec(); self.refresh_method_presets()

    def show_method_preview(self):
        if self.workers or getattr(self, '_closing', False):
            self.notify('请先完成或取消正在运行的任务。'); return
        try:
            resolved = self.method_preview()
        except ERRORS as exc:
            self.notify('本次方法不可用：' + str(exc)); return
        if hasattr(self, 'method_preset_summary'):
            self._apply_method_preview(resolved)
        dialog = QDialog(self); dialog.setWindowTitle('本次实际方法 · 只读预览'); dialog.resize(760, 560)
        layout = QVBoxLayout(dialog); text = editor(readonly=True)
        text.setPlainText(preview_text(resolved)); layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject); layout.addWidget(buttons); dialog.exec()
