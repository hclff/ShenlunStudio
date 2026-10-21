"""User-visible shell behavior, real Qt events and honest task states."""
import time
import threading

import pytest
from PySide6.QtCore import Qt,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog

from shenlun.storage import Store


@pytest.fixture(scope='session')
def app():return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app,tmp_path):
    from shenlun.ui import MainWindow
    result=MainWindow(Store(tmp_path/'workbench.db'))
    result.show();app.processEvents()
    yield result
    result.close();app.processEvents()


def drain(app,predicate):
    end=time.monotonic()+4
    while not predicate() and time.monotonic()<end:
        app.processEvents();QTest.qWait(10)
    assert predicate()


def test_navigation_breadcrumb_and_settings_shortcut_are_consistent(window,app):
    assert hasattr(window,'breadcrumb'), 'page context must be visible in shell'
    window.navigation.setCurrentRow(1);app.processEvents()
    assert window.pages.currentIndex()==1 and '文章资料库' in window.breadcrumb.text()
    window.settings_action.trigger();app.processEvents()
    assert window.navigation.currentRow()==4 and '设置' in window.breadcrumb.text()
    QTest.keyClick(window,Qt.Key.Key_1,Qt.KeyboardModifier.AltModifier)
    assert window.pages.currentIndex()==0


def test_import_and_save_menu_actions_call_actual_existing_flows(window,monkeypatch):
    assert hasattr(window,'import_action')
    called=[]
    monkeypatch.setattr(window,'load_input_file',lambda target='materials':called.append(target))
    window.import_action.trigger()
    assert called==['materials']
    window.materials_edit.setPlainText('保留的草稿材料')
    window.save_action.trigger()
    assert window.store.get_setting('draft')['materials']=='保留的草稿材料'


def test_failed_task_remains_failed_after_worker_finishes(window,app):
    assert hasattr(window,'task_state')
    def fails(progress,cancel):raise ValueError('可读的导入错误')
    window.start_job('读取文件',fails,lambda result:None)
    drain(app,lambda:not window.workers)
    assert window.task_state=='error'
    assert '读取文件' in window.status_label.text()
    assert '可读的导入错误' in window.failure_details.toPlainText()
    assert window.failure_details.isHidden()
    assert not window.global_cancel.isVisible()
    assert window.assess_button.isEnabled()


def test_success_and_cancelled_states_do_not_claim_network_connected(window,app):
    assert hasattr(window,'task_state')
    window.start_job('本机处理',lambda p,c:'done',lambda result:None)
    drain(app,lambda:not window.workers)
    assert window.task_state=='success'
    released=threading.Event()
    window.start_job('本机OCR',lambda p,c:released.wait(2),lambda result:None)
    window.cancel_jobs();released.set()
    drain(app,lambda:not window.workers)
    assert window.task_state=='cancelled'
    assert '网络请求' not in window.status_label.text()
    assert '已连接' not in window.connection_label.text()


def test_help_dialog_opens_without_configuring_or_calling_model(window,app):
    assert hasattr(window,'show_help')
    seen=[]
    def dismiss():
        dialog=app.activeModalWidget()
        if isinstance(dialog,QDialog):
            seen.append(dialog.objectName());dialog.reject()
    timer=QTimer();timer.timeout.connect(dismiss);timer.start(10)
    try:window.show_help()
    finally:timer.stop()
    assert seen==['help_dialog'] and not window.workers
    assert not window.store.usage_summary()['requests']


def test_empty_table_guide_clears_when_data_arrives(window,app):
    window.navigation.setCurrentRow(1);app.processEvents()
    table=window.corpus_table
    assert hasattr(table,'empty_hint')
    assert table.empty_hint.isVisible()
    window.store.upsert_article({'title':'阅读素材','body':'材料正文','journal':'求是'})
    window.refresh_corpus();app.processEvents()
    assert table.rowCount()==1 and not table.empty_hint.isVisible()


def test_modal_result_callback_preserves_success_state_after_nested_event_loop(window,app):
    def modal_result(result):
        dialog=QDialog(window)
        QTimer.singleShot(20,dialog.reject)
        dialog.exec()
    window.start_job('读取本机文件',lambda p,c:'read',modal_result)
    drain(app,lambda:not window.workers)
    assert window.task_state=='success'


def test_release_page_capture_preserves_navigation_and_window_size(window,tmp_path):
    import shenlun.__main__ as entry
    assert hasattr(entry,'probe_workbench')
    window.navigation.setCurrentRow(4)
    original=window.size()
    report=entry.probe_workbench(window,tmp_path/'workbench.png')
    assert window.navigation.currentRow()==4 and window.size()==original
    assert len(report['pages'])==6 and len(report['minimum_pages'])==6
    assert all(item['width']==1050 and item['height']==720 for item in report['minimum_pages'])
    assert all((tmp_path/item['screenshot']).stat().st_size>5000 for item in report['pages'])


def test_cancel_after_worker_emits_result_still_suppresses_pending_gui_callback(window,app):
    delivered=[]
    job=window.start_job('读取文件',lambda p,c:'read',delivered.append)
    assert job.wait(1000)
    window.cancel_jobs()
    drain(app,lambda:not window.workers)
    assert delivered==[]
    assert window.task_state=='cancelled'
