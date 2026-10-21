"""Explicit, conflict-checked removal of saved practice records and questions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox

from .widgets import button, row


class PracticeDeletionMixin:
    def _practice_delete_widgets(self, kind):
        if kind not in ('reviews', 'questions'):
            raise ValueError('不支持的训练数据类型。')
        prefix = 'review' if kind == 'reviews' else 'question'
        return prefix, getattr(self, prefix + '_list', None)

    def build_practice_delete_toolbar(self, kind):
        prefix, listing = self._practice_delete_widgets(kind)
        controls = (
            ('select_all_button', '全选当前列表', lambda: self.set_practice_checks(kind, True)),
            ('clear_checks_button', '清空勾选', lambda: self.set_practice_checks(kind, False)),
            ('bulk_delete_button', '删除勾选项', lambda: self.delete_practice_selection(kind, checked=True)),
            ('delete_button', '删除当前项', lambda: self.delete_practice_selection(kind)),
        )
        widgets = []
        for suffix, title, callback in controls:
            control = button(title, callback, name=prefix + '_' + suffix)
            setattr(self, prefix + '_' + suffix, control)
            widgets.append(control)
        label = QLabel('已勾选 0 项')
        label.setObjectName(prefix + '_checked_label')
        setattr(self, prefix + '_checked_label', label)
        layout = row(*widgets, label)
        layout.addStretch()
        listing.itemChanged.connect(lambda *_: self.refresh_practice_delete_actions())
        listing.currentItemChanged.connect(lambda *_: self.refresh_practice_delete_actions())
        self.refresh_practice_delete_actions()
        return layout

    def checked_practice_ids(self, kind):
        _, listing = self._practice_delete_widgets(kind)
        if listing is None:
            return []
        return [listing.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(listing.count())
                if not listing.item(index).isHidden()
                and listing.item(index).checkState() == Qt.CheckState.Checked]

    def set_practice_checks(self, kind, checked):
        _, listing = self._practice_delete_widgets(kind)
        if listing is None:
            return
        listing.blockSignals(True)
        try:
            for index in range(listing.count()):
                item = listing.item(index)
                if not item.isHidden():
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            listing.blockSignals(False)
        self.refresh_practice_delete_actions()

    def refresh_practice_delete_actions(self):
        available = not bool(self.workers) and not self._closing
        for kind in ('reviews', 'questions'):
            prefix, listing = self._practice_delete_widgets(kind)
            if listing is None or not hasattr(self, prefix + '_delete_button'):
                continue
            checked = len(self.checked_practice_ids(kind))
            getattr(self, prefix + '_checked_label').setText(f'已勾选 {checked} 项')
            current = listing.currentItem()
            getattr(self, prefix + '_delete_button').setEnabled(
                available and current is not None and not current.isHidden())
            getattr(self, prefix + '_bulk_delete_button').setEnabled(available and bool(checked))
            getattr(self, prefix + '_select_all_button').setEnabled(available and bool(listing.count()))
            getattr(self, prefix + '_clear_checks_button').setEnabled(available and bool(checked))

    def clear_deleted_review_display(self):
        self.current_review = None
        for name in ('result_report', 'result_original', 'result_minimal', 'result_model', 'annotation_list'):
            getattr(self, name).clear()
        self.revision_status.clear()
        self.export_report_button.setEnabled(False)
        self.export_pdf_button.setEnabled(False)

    def delete_practice_selection(self, kind, checked=False):
        prefix, listing = self._practice_delete_widgets(kind)
        if self.workers or self._closing:
            self.notify('请先完成或取消当前任务，再删除训练数据。')
            return
        if checked:
            ids = self.checked_practice_ids(kind)
        else:
            current = listing.currentItem()
            ids = [current.data(Qt.ItemDataRole.UserRole)] if current and not current.isHidden() else []
        if not ids:
            self.notify('请先勾选要删除的项目。' if checked else '请先选择要删除的项目。')
            return
        if not self.flush_workspace():
            return
        try:
            preview = self.store.prepare_practice_deletion(kind, ids)
        except Exception as exc:
            self.notify('无法准备删除，请刷新列表后重试：' + str(exc))
            return
        noun = '训练记录' if kind == 'reviews' else '题目'
        titles = [str(title).replace('<', '＜').replace('>', '＞')[:90]
                  for title in preview.get('titles', [])[:6]]
        content = f'确定永久删除 {preview["count"]} 条{noun}？此操作无法撤销。'
        if titles:
            content += '\n\n' + '\n'.join('• ' + title for title in titles)
            if preview['count'] > len(titles):
                content += f'\n另有 {preview["count"] - len(titles)} 项。'
        if kind == 'reviews':
            content += (f'\n\n同时删除绑定的 {preview.get("human_count", 0)} 条人工评分。'
                        '\n题库中的题目及其草稿继续保留。')
        else:
            content += (f'\n\n同时删除本题专属草稿 {preview.get("draft_count", 0)} 份、'
                        f'参考要点版本 {preview.get("reference_version_count", 0)} 条。'
                        f'\n关联的 {preview.get("review_count", 0)} 条训练报告和人工评分继续保留。'
                        '\n当前编辑器和最近草稿中的文字继续保留，并解除题目关联。')
        answer = QMessageBox.question(self, '删除' + noun, content,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.workers or self._closing:
            self.notify('当前任务状态已变化，删除已取消。请稍后重试。')
            return
        try:
            count = self.store.delete_practice_items(kind, ids, expected_token=preview['token'])
        except Exception as exc:
            # Another window may have removed the displayed report while this
            # confirmation was open. Re-read before allowing any stale export.
            refresh_error = ''
            try:
                self.refresh_history()
                self.refresh_questions()
                self.refresh_calibration()
            except Exception as failure:
                refresh_error = '；列表刷新失败：' + str(failure)
            self.notify('删除未完成，请刷新列表后重试：' + str(exc) + refresh_error)
            self.refresh_practice_delete_actions()
            return
        saved = True
        if kind == 'reviews' and (self.current_review or {}).get('id') in ids:
            self.clear_deleted_review_display()
        if kind == 'questions' and self.current_question_id in ids:
            self.current_question_id = None
            self._workspace_dirty = True
            saved = self.flush_workspace()
        self.set_practice_checks(kind, False)
        self.refresh_history()
        self.refresh_questions()
        self.refresh_calibration()
        self.refresh_practice_delete_actions()
        message = f'已永久删除 {count} 条{noun}。'
        if kind == 'questions':
            message += '训练报告和当前编辑文字已保留。'
        if not saved:
            message += '当前文字仍在编辑器中；草稿写入失败，请重试保存。'
        self.notify(message)
