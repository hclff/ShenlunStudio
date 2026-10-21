"""Local teaching-method editing, provenance review and deliberate selection."""

from PySide6.QtWidgets import QDialog

from ...teaching import normalize_teaching_selection, resolve_teaching_guidance
from .constants import MODES
from .dialogs import TeachingSelectionDialog



class TeachingUiMixin:
    def preflight_teaching(self, submission):
        try:
            from ...method_presets import resolve_method_choice
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
        from ...method_presets import normalize_method_choice,resolve_method_choice
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
