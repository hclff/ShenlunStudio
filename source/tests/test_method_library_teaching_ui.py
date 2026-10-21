"""Teacher library creation and deliberate, cancel-safe deletion."""
from PySide6.QtWidgets import QMessageBox

from test_teaching_ui import app, host


def test_teacher_new_save_and_delete_cancel_then_confirm(host, monkeypatch):
    p=host.teaching_panel
    p.new_method();p.name.setText('自行新建的教学检查');p.purpose.setPlainText('核对材料')
    p.steps.setPlainText('读题\n对应材料');p.save_current()
    identity=p.current['id']
    assert host.store.get_teaching_method(identity)['name']=='自行新建的教学检查'
    calls=[]
    def reject(*args,**kwargs):
        calls.append((args,kwargs));return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',reject)
    p.delete_current()
    assert host.store.get_teaching_method(identity) is not None and p.current['id']==identity
    assert calls[0][1].get('defaultButton')==QMessageBox.StandardButton.No
    assert '待核' not in calls[0][0][2]
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    p.delete_current()
    assert host.store.get_teaching_method(identity) is None
    assert not p.current.get('id') and not p.delete_button.isEnabled()
    assert host.store.list_deleted_methods('teaching')[0]['id']==identity


def test_delete_teacher_with_dirty_edits_or_busy_job_preserves_content(host, monkeypatch):
    p=host.teaching_panel;p.select_method('LM01');p.name.setText('尚未保存')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    p.delete_current()
    assert p.dirty and p.name.text()=='尚未保存' and host.store.get_teaching_method('LM01')
    p.load_record(host.store.get_teaching_method('LM01'));host.workers.add(object())
    try:p.delete_current();assert host.store.get_teaching_method('LM01')
    finally:host.workers.clear()


def test_delete_teacher_stale_version_does_not_remove_newer_edit(host, monkeypatch):
    p=host.teaching_panel;p.select_method('LM01')
    fresh=host.store.save_teaching_method({**p.current,'name':'另一窗口修改'},expected_version=0)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    p.delete_current()
    assert host.store.get_teaching_method('LM01')==fresh
    assert '其他窗口' in p.message.text()


def test_parent_reports_permanent_removal_instead_of_recovery(host,monkeypatch):
    from PySide6.QtWidgets import QDialog
    from shenlun.ui.article_method_editor import MethodTrashDialog
    host.store.delete_method('teaching','LM01')
    def purge(self):
        self.store.purge_method('teaching','LM01');self.action='purged'
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(MethodTrashDialog,'exec',purge)
    host.teaching_panel.show_deleted()
    assert '彻底删除' in host.teaching_panel.message.text()


def test_parent_reports_restored_method_without_pending_prompt(host,monkeypatch):
    from PySide6.QtWidgets import QDialog
    from shenlun.ui.article_method_editor import MethodTrashDialog
    host.store.delete_method('teaching','LM01')
    def restore(self):
        self.store.restore_method('teaching','LM01');self.action='restored'
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(MethodTrashDialog,'exec',restore)
    host.teaching_panel.show_deleted()
    assert host.teaching_panel.message.text() == '已恢复并停用。'
