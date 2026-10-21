"""Local input review: reading never changes an exercise until confirmation."""
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox


from .controls import TARGETS, IntakeEditor
from .options import IntakeOptionsDialog
from .preview import IntakePreviewDialog


class IntakeMixin:
    def intake_editor(self, target, name='', placeholder=''):
        result = IntakeEditor(lambda mime: self.intake_from_mime(mime, target))
        result.setObjectName(name)
        result.setPlaceholderText(placeholder)
        return result

    def current_intake_target(self):
        for _, key in TARGETS:
            if getattr(self, key + '_edit').hasFocus():
                return key
        return 'materials'

    def load_input_file(self, target='materials'):
        from ...intake import FILE_FILTER
        paths, _ = QFileDialog.getOpenFileNames(self, '导入题目 / 材料 / 原答', '', FILE_FILTER)
        if paths:
            self.import_paths(paths, target)

    def import_paths(self, paths, target='materials', ask_options=True):
        from ...intake import read_inputs
        if self.workers:
            self.notify('已有任务正在运行，请完成或取消后再导入。')
            return
        page_spec, force_ocr = '', False
        ocr_options = {'mode': 'enhanced', 'columns': 1}
        if ask_options:
            options = IntakeOptionsDialog(self, target, any(Path(path).suffix.lower() == '.pdf' for path in paths))
            if options.exec() != QDialog.DialogCode.Accepted:
                return
            target = options.target_combo.currentData()
            page_spec, force_ocr = options.page_spec.text(), options.force_ocr.isChecked()
            ocr_options = {'mode': options.ocr_mode.currentData(), 'columns': options.ocr_columns.currentData()}
        self.start_job('读取本机文件', lambda progress, cancel: read_inputs(paths, progress=progress, cancel=cancel,
                       force_ocr=force_ocr, page_spec=page_spec, ocr_options=ocr_options, retain_failed=True), lambda batch: self.preview_intake(batch, target),
                       domains=())

    def import_clipboard(self):
        self.intake_from_mime(QApplication.clipboard().mimeData(), self.current_intake_target())

    def intake_from_mime(self, mime, target=None):
        from ...intake import ImportBatch, InputPage, read_image_bytes
        target = target or 'materials'
        if self.workers:
            self.notify('已有任务正在运行，请完成或取消后再导入。')
            return
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()] if mime.hasUrls() else []
        if paths:
            self.import_paths(paths, target)
        elif mime.hasImage():
            # Clipboard/QMimeData and image copying are GUI-thread only.
            image = mime.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if not isinstance(image, QImage) or image.isNull():
                self.notify('剪贴板图片不可读取，请另存图片后导入。')
                return
            if image.sizeInBytes() > 100 * 1024 * 1024:
                self.notify('图片过大，请缩小后导入。')
                return
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            ok = image.copy().save(buffer, 'PNG')
            buffer.close()
            if not ok or data.size() > 25 * 1024 * 1024:
                self.notify('图片无法编码或超过 25 MB，请缩小后导入。')
                return
            payload = bytes(data)
            options = IntakeOptionsDialog(self, target)
            if options.exec() != QDialog.DialogCode.Accepted:
                return
            target = options.target_combo.currentData()
            ocr_options = {'mode': options.ocr_mode.currentData(), 'columns': options.ocr_columns.currentData()}
            self.start_job('本机图片文字识别', lambda progress, cancel: read_image_bytes(payload, progress=progress, cancel=cancel, ocr_options=ocr_options, retain_failed=True),
                           lambda batch: self.preview_intake(batch, target), domains=())
        elif mime.hasUrls():
            self.notify('请拖入本机文件；网页链接不作为题目导入。')
        elif mime.hasText() and mime.text().strip():
            text = mime.text()
            if len(text) > 250000:
                self.notify('剪贴板文字超过 25 万字，请分批导入。')
                return
            self.preview_intake(ImportBatch(pages=[InputPage(source='剪贴板文字', number=1, text=text, method='文本')]), target)
        else:
            self.notify('剪贴板中没有可导入的文字、图片或本机文件。')

    def preview_intake(self, batch, target='materials'):
        if self._closing:
            return
        dialog = IntakePreviewDialog(batch, self, target=target)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_intake_selection(dialog.selection())

    def apply_intake_selection(self, selection):
        if selection.get('mode') == 'routing':
            if selection.get('committed'):
                self.assessment_tabs.setCurrentIndex(0)
                self.notify('已写入核对后的内容。')
            return
        if selection['fields'] is not None:
            if self.load_submission(selection['fields']) is False:
                return
        else:
            if not self.flush_workspace(archive_previous=True):
                return
            edit = getattr(self, selection['target'] + '_edit')
            old = edit.toPlainText()
            text = selection['text']
            if old and text and not selection['replace']:
                text = old + '\n\n' + text
            elif old and not text and not selection['replace']:
                text = old
            edit.setPlainText(text)
            if not self.flush_workspace():
                return
        self.assessment_tabs.setCurrentIndex(0)
        self.notify('已写入核对后的内容。')

    def reload_stale_workspace(self):
        """Explicit recovery for editors protected after a stale modal context."""
        from ...state_write import state_write
        try:
            generation = self.store.assessment_generation()
            opening = self.store.load_workspace()
            if QMessageBox.question(self, '重新载入当前草稿',
                    '将重新载入当前数据库草稿，并放弃保留的编辑内容。是否继续？',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
            with state_write(self.store, generation) as conn:
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                current = json.loads(row[0]) if row else None
                if current != opening:
                    raise ValueError('确认期间草稿又有变化，请重新载入后核对。')
                value = current or {'submission': {}, 'question_id': None}
                self.store._workspace_snapshot(value)
                self.apply_workspace(value, persist=False)
            self._workspace_context_stale = False
            self.set_workspace_paused(False)
            self.autosave_label.setText('已重新载入 · 本机草稿')
            self.notify('已载入当前数据库草稿。请重新打开相关对话框并核对。')
            return True
        except Exception as exc:
            self.notify('重新载入失败，原输入和暂停状态已保留：' + str(exc))
            return False
