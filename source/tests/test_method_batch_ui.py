"""Real checkbox selections, cancellation, named review and partial results."""
import importlib.util

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_method_library_crud import article_draft


@pytest.fixture
def host(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(Store(tmp_path/'batch-ui.db'))
    yield window
    window.workers.clear()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    window.close();app.processEvents()


def result_dialog(monkeypatch):
    assert importlib.util.find_spec('shenlun.ui.method_batch'), 'batch result window is required'
    from shenlun.ui.method_batch import MethodBatchResultDialog
    monkeypatch.setattr(MethodBatchResultDialog,'exec',lambda self:QDialog.DialogCode.Accepted)


def teacher_checked(panel,identities):
    assert hasattr(panel,'checked_methods'), 'teaching checkbox selection is required'
    for index in range(panel.method_list.count()):
        item=panel.method_list.item(index)
        item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in identities else Qt.CheckState.Unchecked)


def test_select_all_teaching_only_checks_current_filter_and_refresh_preserves_selection(host):
    p=host.teaching_panel
    assert hasattr(p,'batch_select_all_button'), 'teaching batch toolbar is required'
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    p.batch_select_all_button.click()
    assert len(p.checked_methods())==2 and all(r['owner_id']=='bailu' for r in p.checked_methods())
    p.refresh();assert len(p.checked_methods())==2
    p.batch_clear_button.click();assert not p.checked_methods() and not p.batch_enable_button.isEnabled()


def test_teacher_filter_change_clears_previous_checks_but_cancel_keeps_them(host,monkeypatch):
    p=host.teaching_panel;teacher_checked(p,{'LM01'})
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('limengyuan'))
    assert not p.checked_methods()
    p.batch_select_all_button.click();assert len(p.checked_methods())==4
    p.owner_filter.setCurrentIndex(p.owner_filter.findData(None))
    assert not p.checked_methods()
    teacher_checked(p,{'LM01'});p.select_method('LM01');p.name.setText('未保存修改')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    assert p.owner_filter.currentData() is None and {r['id'] for r in p.checked_methods()}=={'LM01'}
    assert p.name.text()=='未保存修改' and p.dirty


def test_article_batch_cancel_then_partial_success_and_disable(host,monkeypatch):
    assert hasattr(host,'checked_article_methods'), 'article checkbox selection is required'
    result_dialog(monkeypatch)
    good=host.store.save_manual_article_method(article_draft(host.store))
    bad=host.store.save_manual_article_method({**good,'name':'无证据','refs':[]})
    host.refresh_methods();host.article_batch_select_all_button.click()
    assert len(host.checked_article_methods())==2
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    host.article_batch_enable_button.click();assert not host.store.list_cards(True)
    assert len(host.checked_article_methods())==2
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    host.article_batch_enable_button.click()
    assert [r['id'] for r in host.store.list_cards(True)]==[good['id']]
    assert sorted(r['status'] for r in host.last_method_batch_result)==['failed','success']
    host.article_batch_disable_button.click();assert not host.store.list_cards(True)
    assert len(host.checked_article_methods())==2


def test_teacher_batch_review_confirmation_and_failure_details(host,monkeypatch):
    from test_teaching_ui import method
    import shenlun.ui.teaching as teaching
    result_dialog(monkeypatch);p=host.teaching_panel
    bad=host.store.save_teaching_method(method('catalog',scope='catalog'));p.refresh()
    teacher_checked(p,{'LM01',bad['id']})
    monkeypatch.setattr(teaching.TeachingReviewDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    p.batch_enable_button.click();assert host.store.get_teaching_method('LM01')['version']==0
    def reviewed(self):
        self.reviewer.setText('批量合成核对人');self.note.setPlainText('逐项核对测试')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(teaching.TeachingReviewDialog,'exec',reviewed)
    calls=[]
    def no(*args,**kwargs):calls.append(kwargs);return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',no)
    p.batch_enable_button.click()
    assert calls[0]['defaultButton']==QMessageBox.StandardButton.No and not host.store.list_teaching_methods(True)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    p.batch_enable_button.click()
    assert host.store.get_teaching_method('LM01')['reviewer']=='批量合成核对人'
    assert sorted(r['status'] for r in p.last_batch_result)==['failed','success']
    p.batch_disable_button.click();assert not host.store.list_teaching_methods(True)


def test_batch_guards_unsaved_edits_busy_state_and_stale_review(host,monkeypatch):
    import shenlun.ui.teaching as teaching
    result_dialog(monkeypatch);p=host.teaching_panel
    teacher_checked(p,{'LM01'});p.select_method('LM01');p.name.setText('保留未保存内容')
    p.set_batch_enabled(False)
    assert p.dirty and p.name.text()=='保留未保存内容' and host.store.get_teaching_method('LM01')['version']==0
    p.load_record(host.store.get_teaching_method('LM01'))
    host.workers.add(object());p.set_batch_enabled(False);host.workers.clear()
    assert host.store.get_teaching_method('LM01')['version']==0
    def reviewed(self):
        self.reviewer.setText('合成');self.note.setPlainText('软件测试');return QDialog.DialogCode.Accepted
    monkeypatch.setattr(teaching.TeachingReviewDialog,'exec',reviewed)
    def edit(*args,**kwargs):
        host.store.save_teaching_method({**host.store.get_teaching_method('LM01'),'name':'其他窗口改动'})
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',edit);p.set_batch_enabled(True)
    assert not host.store.get_teaching_method('LM01')['enabled'] and p.last_batch_result[0]['status']=='failed'


@pytest.mark.parametrize('kind',['article','teaching'])
def test_busy_task_starting_during_confirmation_prevents_batch_writes(host,monkeypatch,kind):
    import shenlun.ui.teaching as teaching
    result_dialog(monkeypatch)
    if kind=='article':
        original=host.store.save_manual_article_method(article_draft(host.store));host.refresh_methods()
        host.article_batch_select_all_button.click()
        perform=lambda:host.set_article_batch_enabled(True)
    else:
        p=host.teaching_panel;teacher_checked(p,{'LM01'})
        def reviewed(self):
            self.reviewer.setText('合成');self.note.setPlainText('软件测试');return QDialog.DialogCode.Accepted
        monkeypatch.setattr(teaching.TeachingReviewDialog,'exec',reviewed)
        perform=lambda:p.set_batch_enabled(True)
    def begin_job(*args,**kwargs):host.workers.add(object());return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',begin_job);perform()
    assert not host.store.list_cards(True) and not host.store.list_teaching_methods(True)
    if kind=='teaching':assert host.store.get_teaching_method('LM01')['version']==0


def test_article_batch_rejects_edit_while_confirmation_is_open(host,monkeypatch):
    result_dialog(monkeypatch)
    original=host.store.save_manual_article_method(article_draft(host.store));host.refresh_methods()
    host.article_batch_select_all_button.click()
    def edit(*args,**kwargs):
        host.store.save_manual_article_method({**original,'name':'另一窗口修改'},identity=original['id'])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',edit);host.set_article_batch_enabled(True)
    assert host.last_method_batch_result[0]['status']=='failed' and not host.store.list_cards(True)
