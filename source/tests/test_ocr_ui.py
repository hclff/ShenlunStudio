"""Real Qt contracts for deliberate local/optional-image transcription."""
import io
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from shenlun.storage import Store
from shenlun.ui.intake import IntakeOptionsDialog, IntakePreviewDialog


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from shenlun.ui import MainWindow
    w = MainWindow(Store(tmp_path / 'ocr-ui.db'))
    yield w
    w.close()
    app.processEvents()


def image_bytes(size=(120, 80)):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    w, h = size
    for box, color in [((0, 0, w//2-1, h//2-1), 'red'),
                       ((w//2, 0, w-1, h//2-1), 'green'),
                       ((0, h//2, w//2-1, h-1), 'blue'),
                       ((w//2, h//2, w-1, h-1), 'yellow')]:
        draw.rectangle(box, fill=color)
    stream = io.BytesIO(); image.save(stream, 'PNG')
    return stream.getvalue()


def page(text='本机初稿', number=1, source=None):
    return SimpleNamespace(source='合成打印.png', number=number, text=text, method='图片OCR',
                           preview_png=image_bytes((12, 8)), source_image=source or image_bytes(),
                           ocr_info={'operations':['灰度与对比度增强'], 'segments':1},
                           fields={}, warnings=[])


def preview(window, pages=None):
    return IntakePreviewDialog(SimpleNamespace(pages=pages or [page()], errors=[]), window)


def drain(app, predicate):
    until = time.monotonic() + 4
    while not predicate() and time.monotonic() < until:
        app.processEvents(); QTest.qWait(5)
    assert predicate(), 'Qt operation did not finish'


def accept_candidate(monkeypatch, answer=QDialog.DialogCode.Accepted):
    from shenlun.ui.image_tools import CandidateDialog
    seen = []
    def interact(dialog):
        seen.append((dialog.old_text.toPlainText(), dialog.new_text.toPlainText()))
        return answer
    monkeypatch.setattr(CandidateDialog, 'exec', interact)
    return seen


def test_file_import_defaults_to_enhanced_and_keeps_failed_image(window, app, tmp_path, monkeypatch):
    import shenlun.intake as reader
    path = tmp_path / 'photo.png'; path.write_bytes(image_bytes())
    def unavailable(*args, **kwargs):
        raise ValueError('本机引擎不可用')
    import shenlun.image_ocr as local
    monkeypatch.setattr(local, 'recognize_image', unavailable)
    batches = []
    window.preview_intake = lambda batch, target: batches.append(batch)
    window.import_paths([str(path)], ask_options=False)
    drain(app, lambda: not window.workers)
    assert len(batches) == 1 and len(batches[0].pages) == 1
    assert batches[0].pages[0].source_image
    assert batches[0].pages[0].text == ''
    assert batches[0].pages[0].warnings


def test_clipboard_and_file_options_route_selected_local_mode(window, monkeypatch):
    import shenlun.intake as reader
    calls = []
    monkeypatch.setattr(reader, 'read_inputs', lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(reader, 'read_image_bytes', lambda *args, **kwargs: calls.append(kwargs))
    def options(dialog):
        dialog.ocr_mode.setCurrentIndex(dialog.ocr_mode.findData('standard'))
        dialog.ocr_columns.setCurrentIndex(dialog.ocr_columns.findData(2))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(IntakeOptionsDialog, 'exec', options)
    window.start_job = lambda title, operation, callback, **kwargs: operation(lambda text: None, threading.Event())
    window.import_paths(['fixture.png'])
    mime = QMimeData(); image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white); mime.setImageData(image)
    window.intake_from_mime(mime)
    assert len(calls) == 2
    assert all(c['ocr_options'] == {'mode':'standard','columns':2} and c['retain_failed'] for c in calls)


def test_selected_bytes_use_full_source_rotated_crop_not_thumbnail(window):
    dialog = preview(window)
    dialog.set_image_region(90, (0, 0, .5, .5))
    with Image.open(io.BytesIO(dialog.selected_image())) as image:
        assert image.size == (40, 60)
        assert image.getpixel((10, 10)) == (0, 0, 255)
    assert '90' in dialog.region_label.text()
    assert '选区' in dialog.region_label.text()
    dialog.reject()


def test_drag_roi_maps_rotated_image_and_can_reset(app):
    from shenlun.ui.image_tools import ImageToolsDialog
    dialog = ImageToolsDialog(image_bytes())
    dialog.resize(900, 620); dialog.show(); app.processEvents()
    dialog.rotate_image()
    app.processEvents()
    view = dialog.roi_view
    a = view.mapFromScene(8, 12); b = view.mapFromScene(40, 60)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b)
    assert dialog.rotation == 90
    assert dialog.crop == pytest.approx((.1,.1,.5,.5), abs=.025)
    dialog.clear_crop()
    assert dialog.crop is None
    dialog.close()


def test_ai_decline_never_reads_credentials_or_starts_service(window, monkeypatch):
    window.config.update(base_url='http://127.0.0.1:12345/v1', model='fixture-vision')
    dialog = preview(window)
    dialog.text_edit.setPlainText('人工修订保留')
    accessed = []
    window.get_service = lambda **kwargs: accessed.append('key')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    dialog.ai_recognize()
    assert accessed == [] and not dialog._jobs
    assert dialog.text_edit.toPlainText() == '人工修订保留'


def test_ai_confirmation_default_no_contains_selection_budget_before_key(window, app, monkeypatch):
    window.config.update(base_url='http://127.0.0.1:12345/v1', model='fixture-vision')
    dialog = preview(window, [page('第一页'), page('第二页', 2, image_bytes((200,100)))])
    dialog.page_list.setCurrentRow(1)
    dialog.set_image_region(90, (0,0,.5,.5))
    dialog.text_edit.setPlainText('人工第二页')
    order = []; sent = []
    def consent(parent, title, message, buttons, defaultButton):
        assert defaultButton == QMessageBox.StandardButton.No
        assert 'fixture-vision' in message and '127.0.0.1' in message
        assert 'token' in message and '选区' in message and '人工' in message
        order.append('consent')
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', consent)
    class Service:
        def recognize_image(self, data, **kwargs):
            sent.append(data)
            return {'text':'候选第二页', 'uncertainties':['数字请核对'], 'warnings':[], 'info':{}}
    def service(**kwargs):
        order.append('key'); return Service()
    window.get_service = service
    seen = accept_candidate(monkeypatch)
    dialog.ai_recognize()
    drain(app, lambda: not dialog._jobs)
    drain(app, lambda: dialog.text_edit.toPlainText() == '候选第二页')
    assert order == ['consent','key']
    assert seen == [('人工第二页','候选第二页')]
    with Image.open(io.BytesIO(sent[0])) as image:
        assert image.size == (50,100)
    dialog.undo_recognition()
    assert dialog.text_edit.toPlainText() == '人工第二页'
    dialog.page_list.setCurrentRow(0)
    assert dialog.text_edit.toPlainText() == '第一页'
    dialog.reject()


def test_ai_budget_guard_happens_before_consent_and_key(window, monkeypatch):
    window.config.update(base_url='http://127.0.0.1:12345/v1', model='fixture', session_token_budget=128)
    dialog = preview(window)
    accessed = []
    window.get_service = lambda **kwargs: accessed.append('key')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: accessed.append('consent'))
    dialog.ai_recognize()
    assert accessed == [] and not dialog._jobs
    assert '预算' in dialog.messages.toPlainText()


@pytest.mark.parametrize('decision', [QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected])
def test_local_candidate_requires_acceptance_and_preserves_manual_undo(window, app, monkeypatch, decision):
    import shenlun.image_ocr as local
    dialog = preview(window)
    dialog.text_edit.setPlainText('人工改过的字')
    monkeypatch.setattr(local, 'recognize_image', lambda *a, **k: {'text':'重新识别文字', 'warnings':[],
            'info':{'operations':['灰度与对比度增强'], 'segments':2}})
    seen = accept_candidate(monkeypatch, decision)
    dialog.retry_local()
    drain(app, lambda: not dialog._jobs)
    drain(app, lambda: bool(seen))
    assert seen == [('人工改过的字','重新识别文字')]
    if decision == QDialog.DialogCode.Accepted:
        assert dialog.text_edit.toPlainText() == '重新识别文字'
        dialog.undo_recognition()
    assert dialog.text_edit.toPlainText() == '人工改过的字'
    dialog.reject()


def test_local_failure_keeps_manual_text(window, app, monkeypatch):
    import shenlun.image_ocr as local
    dialog = preview(window); dialog.text_edit.setPlainText('人工文字')
    def fail(*a, **k): raise RuntimeError('fixture engine unavailable')
    monkeypatch.setattr(local, 'recognize_image', fail)
    dialog.retry_local(); drain(app, lambda: not dialog._jobs)
    assert dialog.text_edit.toPlainText() == '人工文字'
    assert 'fixture engine unavailable' in dialog.messages.toPlainText()
    assert dialog.text_edit.isEnabled()
    dialog.reject()


def test_close_retains_worker_cancels_queued_candidate_and_keeps_host(window, app, monkeypatch):
    import shenlun.image_ocr as local
    source = page(); dialog = preview(window, [source])
    window.materials_edit.setPlainText('宿主原文')
    dialog.text_edit.setPlainText('人工修订'); dialog.show()
    started = threading.Event(); release = threading.Event()
    def slow(*a, **k):
        started.set(); release.wait(3)
        return {'text':'迟到候选','warnings':[], 'info':{}}
    monkeypatch.setattr(local, 'recognize_image', slow)
    seen = accept_candidate(monkeypatch)
    dialog.retry_local(); drain(app, started.is_set)
    worker = next(iter(dialog._jobs))
    assert not dialog.page_list.isEnabled()
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()
    dialog.close()
    assert worker.isRunning() and worker.cancel_event.is_set()
    assert dialog.isVisible()
    release.set(); drain(app, lambda: not dialog._jobs)
    drain(app, lambda: not dialog.isVisible())
    assert not seen and source.text == '本机初稿'
    assert window.materials_edit.toPlainText() == '宿主原文'
    assert dialog.text_edit.toPlainText() == '人工修订'


def test_full_source_zoom_and_controls_remain_readable_at_minimum(window, app):
    dialog = preview(window, [page(source=image_bytes((1200,1800)))])
    dialog.resize(1050,720); dialog.show(); app.processEvents()
    assert dialog.source_view.original.width() == 1200
    assert dialog.text_edit.viewport().width() >= 230
    assert dialog.source_view.width() >= 200
    for control in [dialog.local_retry_button, dialog.ai_button, dialog.image_tools_button, dialog.undo_button]:
        assert control.isVisibleTo(dialog)
        assert dialog.rect().contains(control.mapTo(dialog, QPoint(0,0)))
        assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
    dialog.source_view.set_zoom(1)
    app.processEvents()
    assert dialog.source_view.pixmap().width() == 1200
    dialog.source_view.fit_source()
    app.processEvents()
    dialog.close()


def test_candidate_buttons_require_explicit_acceptance_and_show_uncertainty(app):
    from shenlun.ui.image_tools import CandidateDialog
    dialog = CandidateDialog('人工修订', {'text':'新候选','uncertainties':['数字3或8'], 'warnings':[]}, '当前选区')
    assert '数字3或8' in dialog.warnings.toPlainText()
    QTimer.singleShot(0, lambda: dialog.buttons.button(dialog.buttons.StandardButton.Cancel).click())
    assert dialog.exec() == QDialog.DialogCode.Rejected
    assert dialog.old_text.toPlainText() == '人工修订'
    QTimer.singleShot(0, lambda: dialog.buttons.button(dialog.buttons.StandardButton.Ok).click())
    assert dialog.exec() == QDialog.DialogCode.Accepted


def test_cancel_after_result_queued_drops_candidate_and_restores_controls(window, app, monkeypatch):
    import shenlun.image_ocr as local
    dialog = preview(window); dialog.text_edit.setPlainText('人工保留')
    monkeypatch.setattr(local, 'recognize_image', lambda *a, **k: {'text':'迟到内容', 'warnings':[], 'info':{}})
    seen = accept_candidate(monkeypatch)
    dialog.retry_local()
    worker = next(iter(dialog._jobs))
    assert worker.wait(1000)  # result queued; GUI has not delivered it yet
    dialog.cancel_recognition()
    drain(app, lambda: not dialog._jobs)
    assert seen == [] and dialog.text_edit.toPlainText() == '人工保留'
    assert dialog.page_list.isEnabled() and dialog.local_retry_button.isEnabled()
    assert '取消' in dialog.messages.toPlainText()
    dialog.reject()


def test_outer_cancel_after_candidate_acceptance_leaves_all_host_and_source_text(window, app, monkeypatch):
    import shenlun.image_ocr as local
    source = page('读取原稿'); dialog = preview(window, [source])
    window.answer_edit.setPlainText('宿主原答')
    monkeypatch.setattr(local, 'recognize_image', lambda *a, **k: {'text':'已采用候选', 'warnings':[], 'info':{}})
    accept_candidate(monkeypatch)
    dialog.retry_local()
    drain(app, lambda: dialog.text_edit.toPlainText() == '已采用候选')
    assert dialog.selection()['text'] == '已采用候选'
    dialog.reject()
    assert window.answer_edit.toPlainText() == '宿主原答'
    assert source.text == '读取原稿'


def test_structured_page_has_no_recognition_controls_and_missing_config_reads_no_key(window):
    source = SimpleNamespace(source='题库.json', number=1, text='记录', method='题目JSON',
                             preview_png=b'', warnings=[], fields={'materials':'材料'})
    structured = preview(window, [source])
    assert not structured.ai_button.isEnabled() and not structured.local_retry_button.isEnabled()
    structured.reject()
    dialog = preview(window)
    accesses = []; window.get_service = lambda **kwargs: accesses.append('key')
    dialog.ai_recognize()
    assert not accesses and not dialog._jobs
    assert '设置' in dialog.messages.toPlainText()
    dialog.reject()


def test_parent_close_also_retains_dialog_worker_until_finished(window, app, monkeypatch):
    import shenlun.image_ocr as local
    dialog = preview(window); window.show(); dialog.show()
    started = threading.Event(); release = threading.Event()
    def slow(*a, **k):
        started.set(); release.wait(3)
        return {'text':'迟到候选','warnings':[], 'info':{}}
    monkeypatch.setattr(local, 'recognize_image', slow)
    seen = accept_candidate(monkeypatch)
    dialog.retry_local(); drain(app, started.is_set)
    worker = next(iter(dialog._jobs))
    window.close()
    assert worker in window.workers and worker.cancel_event.is_set()
    assert window.isVisible() and worker.isRunning()
    release.set()
    drain(app, lambda: not window.workers)
    drain(app, lambda: not window.isVisible())
    assert not dialog.isVisible() and not seen


def test_cancel_then_retry_keeps_crop_and_all_commit_controls_locked(window, app, monkeypatch):
    import shenlun.image_ocr as local
    dialog = preview(window); dialog.set_image_region(90, (0,0,.5,.5))
    release = threading.Event()
    monkeypatch.setattr(local, 'recognize_image', lambda *a, **k: release.wait(3) or {})
    dialog.retry_local()
    try:
        assert not dialog.replace_check.isEnabled()
        assert not dialog.target_combo.isEnabled()
        assert not dialog.image_tools_button.isEnabled()
        dialog.set_image_region(0, None)
        assert Image.open(io.BytesIO(dialog.selected_image())).size == (40,60)
        dialog.cancel_recognition()
    finally:
        release.set(); drain(app, lambda: not dialog._jobs)
    assert dialog.replace_check.isEnabled()
    dialog.reject()


def test_fit_preview_does_not_enlarge_extremely_tall_narrow_source(app):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QWidget
    from shenlun.ui.intake import SourcePreview
    requests = []
    class TallSource:
        def isNull(self): return False
        def width(self): return 10
        def height(self): return 20000
        def scaledToWidth(self, width, mode):
            # Record the allocation Qt would request without allocating 180MP.
            requests.append((width, width * 2000))
            return QPixmap(1, 1)
    viewport = QWidget(); viewport.resize(308, 300)
    source = SourcePreview(); source.setParent(viewport)
    source.set_source(TallSource())
    assert requests
    assert all(0 < width <= 10 for width, height in requests)
    assert max(width * height for width, height in requests) <= 200000
    viewport.close()
