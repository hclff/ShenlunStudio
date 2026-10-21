"""Local article-method forms and reversible method-library actions."""
import importlib.util
import sqlite3

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QMessageBox

from shenlun.storage import Store


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from shenlun.ui import MainWindow
    host = MainWindow(Store(tmp_path / 'article-ui.db'))
    yield host
    host.workers.clear()
    host.close()
    app.processEvents()


def editor_class():
    assert importlib.util.find_spec('shenlun.ui.article_method_editor'), 'article method form is required'
    from shenlun.ui.article_method_editor import ArticleMethodEditorDialog
    return ArticleMethodEditorDialog


def fill(dialog):
    dialog.name.setText('行动与效果')
    dialog.purpose.setPlainText('核对行动带来的作用')
    dialog.steps.setPlainText('寻找行动\n核对效果')
    dialog.type_checks['文章写作'].setChecked(True)
    dialog.boundary.setPlainText('缺少证据时不推断因果')


def test_editor_uses_compact_guidance(window):
    dialog = editor_class()(window.store, parent=window)
    labels = '\n'.join(widget.text() for widget in dialog.findChildren(QLabel))
    placeholders = '\n'.join(widget.placeholderText() for widget in dialog.findChildren(QLineEdit))
    assert '保存后为停用草稿；启用前须有两篇正文引文。' in labels
    assert '没有匹配的正文文章。' in labels
    assert '搜索文章标题或正文…' in placeholders
    for redundant in ('可暂不添加证据', '编辑已启用的方法后须重新核验',
                      '最多显示 200 条', '原文搜索、编辑和保存均在本机完成',
                      '可先保存草稿，之后到文章资料库导入原文'):
        assert redundant not in labels + placeholders
    dialog.reject()


def test_empty_library_has_create_and_trash_actions(window):
    assert hasattr(window, 'new_method_button'), 'empty library must allow creation'
    assert window.new_method_button.isEnabled()
    assert window.deleted_methods_button.isEnabled()
    assert not window.edit_method_button.isEnabled()
    assert not window.delete_method_button.isEnabled()


def test_form_saves_evidenceless_draft_and_edit_stays_disabled(window):
    dialog = editor_class()(window.store, parent=window)
    fill(dialog)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    card = window.store.list_cards()[0]
    assert not card['enabled'] and card['refs'] == []
    edit = editor_class()(window.store, card, window)
    edit.name.setText('改过的方法')
    edit.accept()
    assert len(window.store.list_cards()) == 1
    assert window.store.list_cards()[0]['name'] == '改过的方法'
    assert not window.store.list_cards()[0]['enabled']


def test_article_search_selects_paragraph_and_exact_quote(window):
    identity = window.store.upsert_article({'title':'窗口服务','body':'保留人工窗口，帮助群众办理。\n开通电话预约。'})
    dialog = editor_class()(window.store, parent=window)
    fill(dialog)
    dialog.article_search.setText('群众')
    dialog.search_articles()
    assert dialog.article_list.count() == 1
    dialog.article_list.setCurrentRow(0)
    assert dialog.paragraph_select.count() == 2
    cursor = dialog.paragraph_text.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(6, cursor.MoveMode.KeepAnchor)
    dialog.paragraph_text.setTextCursor(cursor)
    dialog.use_selected_quote()
    assert dialog.quote.toPlainText() == '保留人工窗口'
    dialog.add_evidence()
    assert dialog.refs == [{'id': identity + ':p1', 'quote':'保留人工窗口'}]
    dialog.accept()
    assert window.store.list_cards()[0]['refs'] == dialog.refs


def test_invalid_quote_and_save_error_preserve_editing(window, monkeypatch):
    window.store.upsert_article({'title':'原文','body':'精确原文。'})
    dialog = editor_class()(window.store, parent=window)
    fill(dialog)
    dialog.article_list.setCurrentRow(0)
    dialog.quote.setPlainText('编造引文')
    dialog.add_evidence()
    assert dialog.refs == [] and '原文' in dialog.message.text()
    monkeypatch.setattr(window.store, 'save_manual_article_method', lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('磁盘忙')))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.name.text() == '行动与效果' and '磁盘忙' in dialog.message.text()


def test_delete_cancel_preserves_selection_and_busy_recheck(window, monkeypatch):
    window.store.save_card({'id':'test-method','name':'保留我','enabled':False})
    window.refresh_methods()
    window.method_table.selectRow(0)
    before = window.selected_card()
    def cancel(*args, **kwargs):
        assert kwargs['defaultButton'] == QMessageBox.StandardButton.No
        assert '历史' in args[2] and '已删除' in args[2]
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    assert hasattr(window, 'delete_article_method'), 'delete action is required'
    window.delete_article_method()
    assert window.selected_card() == before and window.store.list_cards()
    def busy(*args, **kwargs):
        window.workers.add(object())
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', busy)
    window.delete_article_method()
    assert window.store.list_cards()


def test_busy_form_does_not_save_and_trash_restores_disabled(window):
    dialog = editor_class()(window.store, parent=window)
    fill(dialog)
    window.workers.add(object())
    dialog.accept()
    assert not window.store.list_cards()
    window.workers.clear()
    dialog.accept()
    card = window.store.list_cards()[0]
    window.store.delete_method('article', card['id'])
    from shenlun.ui.article_method_editor import MethodTrashDialog
    trash = MethodTrashDialog(window.store, 'article', window)
    assert '待核' not in '\n'.join(label.text() for label in trash.findChildren(QLabel))
    trash.method_list.setCurrentRow(0)
    window.workers.add(object())
    trash.restore_selected()
    assert not window.store.list_cards()
    window.workers.clear()
    trash.restore_selected()
    assert trash.result() == QDialog.DialogCode.Accepted
    assert not window.store.list_cards()[0]['enabled']
    assert window.store.list_cards()[0]['status'] == '恢复的方法草稿'


def test_delete_success_and_restore_keep_frozen_history(window, monkeypatch):
    window.store.save_card({'id':'kept-snapshot','name':'原方法','enabled':False})
    frozen = window.store.list_cards()[0]
    review_id = window.store.save_review({'method_snapshot':[frozen]})
    window.refresh_methods()
    window.method_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.delete_article_method()
    assert window.method_table.rowCount() == 0
    assert window.store.get_review(review_id)['method_snapshot'] == [frozen]
    assert window.new_method_button.isEnabled() and window.deleted_methods_button.isEnabled()


def test_cancel_editor_never_writes_or_changes_selected_method(window):
    window.store.save_card({'id':'selected','name':'原方法','enabled':False,'steps':['原步骤']})
    window.refresh_methods()
    window.method_table.selectRow(0)
    before = window.selected_card()
    dialog = editor_class()(window.store, before, window)
    dialog.name.setText('未保存的名称')
    dialog.reject()
    assert window.selected_card() == before
    assert window.store.list_cards() == [before]


def test_worker_state_disables_all_library_mutation_buttons(window):
    window.store.save_card({'id':'busy-selected','name':'原方法','enabled':False})
    window.refresh_methods()
    window.method_table.selectRow(0)
    window.workers.add(object())
    window.refresh_teaching_busy()
    for control in (window.new_method_button, window.deleted_methods_button,
                    window.edit_method_button, window.delete_method_button,
                    window.enable_method_button):
        assert not control.isEnabled()


def test_trash_read_error_stays_visible_and_never_restores(window, monkeypatch):
    editor_class()
    from shenlun.ui.article_method_editor import MethodTrashDialog
    def fail(*args):
        raise sqlite3.OperationalError('资料库无法读取')
    monkeypatch.setattr(window.store, 'list_deleted_methods', fail)
    dialog = MethodTrashDialog(window.store, 'article', window)
    assert '无法读取' in dialog.message.text()
    assert not dialog.restore_button.isEnabled()


def test_evidence_search_skips_articles_excluded_from_training(window):
    window.store.upsert_article({'title':'不可用于训练','body':'不应选作方法证据。','training_eligible':False})
    window.store.upsert_article({'title':'可用原文','body':'可以核对的证据。'})
    dialog = editor_class()(window.store, parent=window)
    assert dialog.article_list.count() == 1
    assert dialog.article_list.item(0).text() == '可用原文'


def test_concurrent_edit_conflict_preserves_dialog_input_and_newer_card(window):
    dialog = editor_class()(window.store, parent=window)
    fill(dialog)
    dialog.accept()
    original = window.store.list_cards()[0]
    editing = editor_class()(window.store, original, window)
    editing.name.setText('本窗口未保存内容')
    window.store.save_manual_article_method({**original, 'name':'另一窗口的新名称'}, identity=original['id'])
    editing.accept()
    assert editing.result() == QDialog.DialogCode.Rejected
    assert editing.name.text() == '本窗口未保存内容'
    assert window.store.list_cards()[0]['name'] == '另一窗口的新名称'
    assert editing.message.text()


def test_editor_forms_scroll_and_save_remains_reachable_at_small_size(window, app):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QDialogButtonBox, QScrollArea
    dialog = editor_class()(window.store, parent=window)
    dialog.resize(760, 600)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 1050 and dialog.height() <= 720
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        assert isinstance(dialog.tabs.currentWidget(), QScrollArea)
        save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
        assert save.isVisibleTo(dialog)
        assert dialog.rect().contains(save.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(save.mapTo(dialog, save.rect().bottomRight()))
    dialog.reject()


def deleted_article_dialog(window):
    from shenlun.ui.article_method_editor import MethodTrashDialog
    window.store.save_card({'id':'purge-selected','name':'待彻底删除的方法','enabled':False})
    window.store.delete_method('article', 'purge-selected')
    return MethodTrashDialog(window.store, 'article', window)


def test_trash_permanent_delete_requires_selection_and_empty_list_disables_it(window):
    from shenlun.ui.article_method_editor import MethodTrashDialog
    empty = MethodTrashDialog(window.store, 'article', window)
    assert hasattr(empty, 'purge_button'), 'trash must offer permanent deletion'
    assert not empty.purge_button.isEnabled()
    dialog = deleted_article_dialog(window)
    assert not dialog.purge_button.isEnabled()
    dialog.method_list.setCurrentRow(0)
    assert dialog.purge_button.isEnabled()
    dialog.method_list.setCurrentRow(-1)
    assert not dialog.purge_button.isEnabled()


def test_permanent_delete_cancel_keeps_record_selection_and_details(window, monkeypatch):
    dialog = deleted_article_dialog(window)
    dialog.method_list.setCurrentRow(0)
    selected = dialog.method_list.currentItem()
    original = window.store.list_deleted_methods('article')
    def cancel(*args, **kwargs):
        assert kwargs['defaultButton'] == QMessageBox.StandardButton.No
        assert '待彻底删除的方法' in args[2]
        assert '不能从已删除列表恢复' in args[2]
        assert '历史批改' in args[2] and '冻结评测' in args[2]
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    assert hasattr(dialog, 'purge_selected'), 'permanent deletion action is required'
    dialog.purge_selected()
    assert dialog.method_list.currentItem() is selected
    assert window.store.list_deleted_methods('article') == original
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_permanent_delete_busy_before_or_after_confirmation_never_writes(window, monkeypatch):
    dialog = deleted_article_dialog(window)
    dialog.method_list.setCurrentRow(0)
    assert hasattr(dialog, 'purge_selected'), 'permanent deletion action is required'
    calls = []
    def confirm(*args, **kwargs):
        calls.append(True)
        window.workers.add(object())
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    window.workers.add(object())
    dialog.purge_selected()
    assert not calls
    window.workers.clear()
    dialog.purge_selected()
    assert calls == [True] and window.store.list_deleted_methods('article')
    assert dialog.method_list.currentRow() == 0


@pytest.mark.parametrize('error', [ValueError('记录已改变'), sqlite3.OperationalError('磁盘忙'), OSError('磁盘不可写')])
def test_permanent_delete_failure_keeps_selected_record_and_error(window, monkeypatch, error):
    dialog = deleted_article_dialog(window)
    dialog.method_list.setCurrentRow(0)
    assert hasattr(dialog, 'purge_selected'), 'permanent deletion action is required'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(window.store, 'purge_method', fail, raising=False)
    dialog.purge_selected()
    assert dialog.method_list.count() == 1 and dialog.method_list.currentRow() == 0
    assert str(error) in dialog.message.text()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert window.store.list_deleted_methods('article')


@pytest.mark.parametrize('kind', ['article', 'teaching'])
def test_real_permanent_delete_confirmation_closes_and_preserves_other_deleted_methods(window, app, kind):
    from PySide6.QtCore import QTimer, Qt
    from shenlun.ui.article_method_editor import MethodTrashDialog
    if kind == 'article':
        dialog = deleted_article_dialog(window)
        selected_id = 'purge-selected'
        window.store.save_card({'id':'other-deleted','name':'另一个已删除的方法','enabled':False})
        window.store.delete_method('article', 'other-deleted')
    else:
        selected_id = 'LM01'
        window.store.delete_method('teaching', selected_id)
        window.store.delete_method('teaching', 'GH_BL01')
        dialog = MethodTrashDialog(window.store, kind, window.teaching_panel)
    # The dialog was opened before another article was deleted; purge only its displayed record.
    dialog.method_list.setCurrentRow(next(index for index in range(dialog.method_list.count())
        if dialog.method_list.item(index).data(Qt.ItemDataRole.UserRole) == selected_id))
    assert hasattr(dialog, 'purge_selected'), 'permanent deletion action is required'
    confirmation = []
    def choose_yes():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            confirmation.append((box.text(), box.defaultButton() == box.button(QMessageBox.StandardButton.No)))
            box.button(QMessageBox.StandardButton.Yes).click()
    QTimer.singleShot(0, choose_yes)
    dialog.purge_selected()
    assert confirmation and confirmation[0][1]
    assert dialog.result() == QDialog.DialogCode.Accepted
    remaining = window.store.list_deleted_methods(kind)
    assert len(remaining) == 1 and remaining[0]['id'] != selected_id


def test_stale_trash_window_cannot_purge_restored_then_deleted_method(window, monkeypatch):
    dialog = deleted_article_dialog(window)
    dialog.method_list.setCurrentRow(0)
    assert hasattr(dialog, 'purge_selected'), 'permanent deletion action is required'
    old = window.store.list_deleted_methods('article')[0]
    window.store.restore_method('article', old['id'])
    window.store.delete_method('article', old['id'])
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.purge_selected()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.method_list.currentRow() == 0
    assert window.store.list_deleted_methods('article')[0]['id'] == old['id']
