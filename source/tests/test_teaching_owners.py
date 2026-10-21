"""Real SQLite and Qt coverage for durable subject management and history."""
import copy
import sqlite3

import pytest
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from shenlun.storage import Store
from shenlun.teaching import resolve_teaching_guidance, validate_teaching_snapshot
from shenlun.reports import teaching_review_html
from test_teaching import method, submission
from test_teaching_ui import app, host


def test_custom_owner_can_save_enable_select_and_freeze_its_name(tmp_path):
    store = Store(tmp_path/'owners.db')
    owner = store.add_teaching_owner('自建老师')
    saved = store.save_teaching_method(method(owner=owner['id']), expected_version=0)
    store.set_teaching_method_enabled(saved['id'], True, reviewer='核对人', note='核对来源', expected_version=1)
    selected = {'mode':'source','owner_ids':[owner['id']]}
    store.set_setting('teaching_selection', selected)
    snapshot = resolve_teaching_guidance(store, submission())
    assert snapshot['owner_names'] == {owner['id']:'自建老师'}
    assert validate_teaching_snapshot(snapshot, submission()) == snapshot
    assert next(o for o in Store(store.path).list_teaching_owners() if o['id']==owner['id'])['name'] == '自建老师'
    text = teaching_review_html({'teaching_snapshot':snapshot})
    assert '自建老师' in text
    assert store.usage_summary()['requests'] == 0


@pytest.mark.parametrize('name', ['', '   ', '白鹭', ' 白鹭 ', 'x'*101, None])
def test_invalid_or_duplicate_owner_is_not_saved(tmp_path, name):
    store = Store(tmp_path/'invalid.db')
    before = store.list_teaching_owners()
    with pytest.raises(ValueError):
        store.add_teaching_owner(name)
    assert store.list_teaching_owners() == before


def test_delete_builtin_owner_moves_all_methods_to_trash_and_survives_restart(tmp_path):
    store = Store(tmp_path/'delete.db')
    before = {m['id'] for m in store.list_teaching_methods() if m['owner_id']=='fenbi'}
    store.set_setting('teaching_selection', {'mode':'source','owner_ids':['fenbi']})
    store.delete_teaching_owner('fenbi')
    again = Store(store.path)
    assert 'fenbi' not in {o['id'] for o in again.list_teaching_owners()}
    assert not any(m['owner_id']=='fenbi' for m in again.list_teaching_methods())
    assert before <= {m['id'] for m in again.list_deleted_methods('teaching')}
    assert again.get_setting('teaching_selection') == {'mode':'combined','owner_ids':[]}
    with pytest.raises(ValueError):
        again.save_teaching_method(method('new', owner='fenbi'))
    with pytest.raises(ValueError):
        again.import_teaching_methods([method('new', owner='fenbi')])
    again.restore_method('teaching', sorted(before)[0])
    assert 'fenbi' in {o['id'] for o in again.list_teaching_owners()}
    assert not again.get_teaching_method(sorted(before)[0])['enabled']


def test_delete_custom_owner_keeps_frozen_snapshot_name_and_backups(tmp_path):
    store = Store(tmp_path/'history.db')
    owner = store.add_teaching_owner('历史老师')
    store.save_teaching_method(method(owner=owner['id']))
    store.set_teaching_method_enabled('synthetic-1', True, reviewer='甲', note='已核', expected_version=1)
    snapshot = resolve_teaching_guidance(store, submission())
    frozen = copy.deepcopy(snapshot)
    store.delete_teaching_owner(owner['id'])
    assert validate_teaching_snapshot(snapshot, submission()) == frozen
    assert '历史老师' in teaching_review_html({'teaching_snapshot':snapshot})
    backup = store.backup_to(tmp_path/'backup.db')
    restored = Store(tmp_path/'restored.db')
    restored.restore_from(backup)
    assert owner['id'] not in {o['id'] for o in restored.list_teaching_owners()}
    assert 'synthetic-1' in {r['id'] for r in restored.list_deleted_methods('teaching')}
    restored.restore_method('teaching','synthetic-1')
    assert next(o for o in restored.list_teaching_owners() if o['id']==owner['id'])['name'] == '历史老师'
    assert not restored.get_teaching_method('synthetic-1')['enabled']


def test_unregistered_custom_owner_cannot_be_imported(tmp_path):
    store = Store(tmp_path/'unknown.db')
    with pytest.raises(ValueError):
        store.import_teaching_methods([method(owner='owner-'+'a'*32)])


def test_stale_owner_delete_is_atomic(tmp_path):
    store = Store(tmp_path/'stale.db')
    expected = [m for m in store.list_teaching_methods() if m['owner_id']=='bailu']
    store.save_teaching_method(method('new-local','bailu'))
    with pytest.raises(ValueError, match='变化'):
        store.delete_teaching_owner('bailu', expected_methods=expected)
    assert any(o['id']=='bailu' for o in store.list_teaching_owners())
    assert not store.list_deleted_methods('teaching')


def test_bad_owner_backup_is_rejected_without_touching_active_data(tmp_path):
    store = Store(tmp_path/'source.db')
    store.add_teaching_owner('备份老师')
    backup = store.backup_to(tmp_path/'bad.db')
    with sqlite3.connect(backup) as conn:
        conn.execute('UPDATE teaching_owners SET deleted=2')
    target = Store(tmp_path/'target.db')
    before = target.list_teaching_owners()
    with pytest.raises(ValueError):
        target.restore_from(backup)
    assert target.list_teaching_owners() == before


def test_ui_add_owner_updates_filters_editor_and_selection(host, monkeypatch):
    p = host.teaching_panel
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('界面老师',True))
    p.add_owner_button.click()
    owner = next(o for o in host.store.list_teaching_owners() if o['name']=='界面老师')
    assert p.owner_filter.currentData() == p.owner_edit.currentData() == owner['id']
    p.name.setText('新主体方法'); p.save_current()
    assert host.store.get_teaching_method(p.current['id'])['owner_id'] == owner['id']
    from shenlun.ui.teaching import TeachingSelectionDialog
    selection = TeachingSelectionDialog(host.store,host)
    assert selection.owner_checks[owner['id']].text() == '界面老师'
    selection.reject()


def test_ui_delete_owner_cancel_and_confirm_preserve_history(host, monkeypatch):
    p = host.teaching_panel
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    before = [r['id'] for r in p.records if r['owner_id']=='bailu']
    calls = []
    def no(*args,**kw):
        calls.append((args,kw));return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',no)
    p.delete_owner_button.click()
    assert calls[0][1]['defaultButton'] == QMessageBox.StandardButton.No
    assert '已删除' in calls[0][0][2] and '历史' in calls[0][0][2]
    assert p.owner_filter.findData('bailu') >= 0
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    p.delete_owner_button.click()
    assert p.owner_filter.findData('bailu') == p.owner_edit.findData('bailu') == -1
    assert set(before) <= {r['id'] for r in host.store.list_deleted_methods('teaching')}


def test_ui_busy_or_dirty_owner_actions_do_not_lose_edits(host, monkeypatch):
    p = host.teaching_panel
    p.select_method('LM01'); p.name.setText('保留编辑')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    before = host.store.list_teaching_owners()
    p.add_owner();p.delete_owner()
    assert p.name.text() == '保留编辑' and p.dirty
    assert host.store.list_teaching_owners() == before
    host.workers.add(object())
    try:
        p.add_owner();p.delete_owner()
        assert host.store.list_teaching_owners() == before
    finally:
        host.workers.clear()


def test_real_review_and_custom_preset_keep_name_after_owner_deletion_and_restore(tmp_path):
    from shenlun.method_presets import resolve_method_choice
    from shenlun.reports import review_html
    from test_teaching_service import service, RAW
    store = Store(tmp_path/'review.db')
    owner = store.add_teaching_owner('自建历史主体')
    store.save_teaching_method(method(owner=owner['id']))
    store.set_teaching_method_enabled('synthetic-1',True,reviewer='甲',note='核对',expected_version=1)
    preset = store.save_method_preset({'name':'自建组合','question_type':'归纳概括',
        'teaching_ids':['synthetic-1'],'article_ids':[]},expected_version=0)
    choice = {'mode':'preset','preset_id':preset['id']}
    assert resolve_method_choice(store,RAW,choice)['teaching_snapshot']['owner_names'][owner['id']] == '自建历史主体'
    store.set_setting('method_choice',choice)
    review = service(store).assess(RAW)
    store.delete_teaching_owner(owner['id'])
    assert store.get_review(review['id']) == review
    target = Store(tmp_path/'review-restored.db')
    target.restore_from(store.backup_to(tmp_path/'review-backup.db'))
    assert target.get_review(review['id']) == review
    assert '自建历史主体' in review_html(target.get_review(review['id']))
    with pytest.raises(ValueError):resolve_method_choice(target,RAW,choice)


def test_owner_archive_rolls_back_every_method_if_any_write_fails(tmp_path, monkeypatch):
    store = Store(tmp_path/'atomic.db')
    before = store.list_teaching_methods()
    original = store._append_teaching
    calls = []
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise sqlite3.OperationalError('synthetic write failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'_append_teaching',fail_second)
    with pytest.raises(sqlite3.Error):store.delete_teaching_owner('fenbi')
    assert store.list_teaching_methods() == before
    assert not store.list_deleted_methods('teaching')
    assert any(o['id']=='fenbi' for o in store.list_teaching_owners())


def test_empty_owner_library_can_add_again_without_restarting_ui(host, monkeypatch):
    p = host.teaching_panel
    for owner in host.store.list_teaching_owners():host.store.delete_teaching_owner(owner['id'])
    p.refresh();p.new_method(initial=True)
    assert p.owner_edit.count() == 0 and not p.save_button.isEnabled()
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('重新添加',True))
    p.add_owner_button.click()
    assert p.owner_edit.count() == 1 and p.save_button.isEnabled()
    assert p.owner_edit.currentText() == '重新添加'


def test_legacy_backup_without_subject_table_remains_compatible(tmp_path):
    store = Store(tmp_path/'legacy.db')
    backup = store.backup_to(tmp_path/'legacy-backup.db')
    with sqlite3.connect(backup) as conn:conn.execute('DROP TABLE teaching_owners')
    target = Store(tmp_path/'legacy-restored.db')
    target.restore_from(backup)
    assert len(target.list_teaching_owners()) == 11


def test_all_backup_live_methods_have_live_registered_subjects(tmp_path):
    store = Store(tmp_path/'invalid-owner.db')
    owner = store.add_teaching_owner('无效备份主体')
    store.save_teaching_method(method(owner=owner['id']))
    backup = store.backup_to(tmp_path/'invalid-owner-backup.db')
    with sqlite3.connect(backup) as conn:
        conn.execute('UPDATE teaching_owners SET deleted=1 WHERE id=?',(owner['id'],))
    target = Store(tmp_path/'unaffected.db')
    with pytest.raises(ValueError):target.restore_from(backup)
    assert len(target.list_teaching_owners()) == 11
