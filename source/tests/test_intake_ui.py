"""Real Qt boundaries for deliberate, reversible question intake."""
import importlib.util
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from shenlun.storage import Store


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from shenlun.ui import MainWindow
    w = MainWindow(Store(tmp_path / 'intake.db'))
    yield w
    w.close()
    app.processEvents()


def page(text, number=1, **kwargs):
    return SimpleNamespace(source='测试.txt', number=number, text=text, method='文本',
                           preview_png=b'', warnings=kwargs.get('warnings', []),
                           fields=kwargs.get('fields', {}))


def preview(window, pages, **kwargs):
    assert importlib.util.find_spec('shenlun.ui.intake'), 'Question intake preview is required'
    from shenlun.ui.intake import IntakePreviewDialog
    return IntakePreviewDialog(SimpleNamespace(pages=pages, errors=kwargs.pop('errors', [])),
                               window, **kwargs)


def test_preview_cancel_keeps_editors_and_backend_pages_untouched(window):
    window.materials_edit.setPlainText('已有材料')
    source = page('新材料')
    dialog = preview(window, [source])
    dialog.text_edit.setPlainText('预览中修改')
    dialog.reject()
    assert window.materials_edit.toPlainText() == '已有材料'
    assert source.text == '新材料'


def test_preview_edits_checks_order_remove_and_visible_failures(window):
    dialog = preview(window, [page('甲'), page('乙', 2), page('丙', 3)],
                     errors=['坏文件无法读取'])
    dialog.text_edit.setPlainText('甲修订')
    dialog.page_list.setCurrentRow(2)
    dialog.move_page(-1)
    dialog.page_list.item(2).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selection()['text'] == '甲修订\n\n丙'
    dialog.remove_page()
    assert dialog.selection()['text'] == '甲修订'
    assert '坏文件无法读取' in dialog.messages.toPlainText()


def test_confirmation_appends_by_default_and_replace_is_explicit(window):
    window.materials_edit.setPlainText('旧材料')
    dialog = preview(window, [page('新材料')])
    assert dialog.selection()['replace'] is False
    window.apply_intake_selection(dialog.selection())
    assert window.materials_edit.toPlainText() == '旧材料\n\n新材料'
    dialog.replace_check.setChecked(True)
    window.apply_intake_selection(dialog.selection())
    assert window.materials_edit.toPlainText() == '新材料'


def test_structured_records_select_individually_and_clear_answer_only_explicitly(window):
    window.answer_edit.setPlainText('旧答案')
    records = [page('不应直接倒入JSON', fields={'question':'题一','materials':'料一'}),
               page('另一个', 2, fields={'question':'题二','materials':'料二'})]
    dialog = preview(window, records)
    assert not dialog.exercise_check.isEnabled()
    dialog.page_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selection()['text'] == '料一'
    window.apply_intake_selection(dialog.selection())
    assert window.answer_edit.toPlainText() == '旧答案'
    assert window.materials_edit.toPlainText() == '料一'
    dialog.exercise_check.setChecked(True)
    window.apply_intake_selection(dialog.selection())
    assert window.question_edit.toPlainText() == '题一'
    assert window.answer_edit.toPlainText() == ''


def test_mime_image_and_files_route_preview_but_plain_text_pastes_natively(window, app):
    window.answer_edit.setPlainText('原答')
    routed = []
    window.intake_from_mime = lambda mime, target=None: routed.append((mime.hasImage(), target))
    mime = QMimeData()
    mime.setText('普通粘贴')
    window.answer_edit.moveCursor(window.answer_edit.textCursor().MoveOperation.End)
    window.answer_edit.insertFromMimeData(mime)
    assert window.answer_edit.toPlainText() == '原答普通粘贴'
    image_mime = QMimeData()
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    image_mime.setImageData(image)
    window.answer_edit.insertFromMimeData(image_mime)
    assert routed == [(True, 'answer')]
    assert window.answer_edit.toPlainText() == '原答普通粘贴'


def test_real_text_worker_result_opens_preview_on_gui_and_cancel_does_not_apply(window, app, tmp_path, monkeypatch):
    assert hasattr(window, 'import_paths'), 'File import must use reviewed worker flow'
    from shenlun.ui.intake import IntakePreviewDialog
    path = tmp_path / 'material.txt'
    path.write_text('后台读取的材料', encoding='utf-8')
    window.materials_edit.setPlainText('保留')
    seen = []
    def reject_preview(dialog):
        from PySide6.QtCore import QThread
        seen.append((dialog.text_edit.toPlainText(), QThread.currentThread() == app.thread()))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(IntakePreviewDialog, 'exec', reject_preview)
    window.import_paths([str(path)], 'materials', ask_options=False)
    until = time.monotonic() + 5
    while window.workers and time.monotonic() < until:
        app.processEvents()
        QTest.qWait(10)
    assert not window.workers
    assert seen == [('后台读取的材料', True)]
    assert window.materials_edit.toPlainText() == '保留'


def test_structured_full_replacement_shows_settings_before_confirmation(window):
    dialog = preview(window, [page('记录', fields={'question':'概括','materials':'材料',
                     'exam':'江苏省考','year':'2025','max_score':30,'word_limit':200})])
    assert '2025' in dialog.record_details.toPlainText()
    assert '30' in dialog.record_details.toPlainText()


def test_full_exercise_edited_fields_cannot_bypass_combined_text_limit(window):
    dialog = preview(window, [page('记录', fields={'question':'题干','materials':'材料'})])
    dialog.text_edit.setPlainText('材' * 125001)
    dialog.target_combo.setCurrentIndex(dialog.target_combo.findData('question'))
    dialog.text_edit.setPlainText('题' * 125001)
    dialog.exercise_check.setChecked(True)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert '25 万字' in dialog.messages.toPlainText()


def test_actual_options_then_worker_preview_confirm_writes_edited_target(window, app, tmp_path):
    from shenlun.ui.intake import IntakeOptionsDialog, IntakePreviewDialog
    path = tmp_path / 'question.txt'
    path.write_text('文件题干', encoding='utf-8')
    stages = []
    timer = QTimer()
    def interact():
        dialog = app.activeModalWidget()
        if isinstance(dialog, IntakeOptionsDialog):
            stages.append('options')
            dialog.target_combo.setCurrentIndex(dialog.target_combo.findData('question'))
            dialog.accept()
        elif isinstance(dialog, IntakePreviewDialog):
            stages.append('preview')
            assert window.question_edit.toPlainText() == ''
            dialog.text_edit.setPlainText('核对后的题干')
            dialog.accept()
    timer.timeout.connect(interact)
    timer.start(10)
    try:
        window.import_paths([str(path)])
        until = time.monotonic() + 5
        while window.workers and time.monotonic() < until:
            app.processEvents()
            QTest.qWait(10)
    finally:
        timer.stop()
    assert stages == ['options', 'preview']
    assert window.question_edit.toPlainText() == '核对后的题干'
    assert window.materials_edit.toPlainText() == ''


def test_keyboard_paste_image_and_drop_file_keep_answer_until_review(window, app, tmp_path):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    window.show()
    window.answer_edit.setFocus()
    routed = []
    window.intake_from_mime = lambda mime, target=None: routed.append((mime.hasImage(), mime.hasUrls(), target))
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    app.clipboard().setImage(image)
    QTest.keySequence(window.answer_edit, QKeySequence.StandardKey.Paste)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / 'answer.docx'))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.answer_edit.viewport(), enter)
    QApplication.sendEvent(window.answer_edit.viewport(), drop)
    assert routed == [(True, False, 'answer'), (False, True, 'answer')]
    assert window.answer_edit.toPlainText() == ''
    app.clipboard().clear()


def test_source_comparison_fits_preview_width_without_cropping(window, app):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    image = QImage(1000, 1400, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    buffer.close()
    source = page('材料')
    source.preview_png = bytes(data)
    dialog = preview(window, [source])
    dialog.resize(800, 600)
    dialog.show()
    app.processEvents()
    assert dialog.source_view.pixmap().width() <= dialog.source_view.width()
    dialog.close()


def test_structured_exam_name_round_trips_without_silent_preset_substitution(window):
    dialog=preview(window,[page('记录',fields={'question':'题干','materials':'材料','exam':'浙江省考'})])
    dialog.exercise_check.setChecked(True)
    window.apply_intake_selection(dialog.selection())
    assert window.raw_submission()['exam']=='浙江省考'


def test_copied_web_image_with_remote_url_uses_image_preview(window,monkeypatch):
    from shenlun.ui.intake import IntakeOptionsDialog
    mime=QMimeData();image=QImage(20,20,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.white)
    mime.setImageData(image);mime.setUrls([QUrl('https://example.org/material.png')])
    monkeypatch.setattr(IntakeOptionsDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    jobs=[]
    window.start_job=lambda title,operation,on_result,**kwargs:jobs.append(title)
    window.intake_from_mime(mime,'materials')
    assert jobs==['本机图片文字识别']
