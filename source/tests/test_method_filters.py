"""Offline search scope and real-widget selection/dirty-editor contracts."""
import importlib.util

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from test_method_library_crud import article_draft
from test_teaching_ui import method


@pytest.fixture
def host(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / 'filters.db'))
    yield window
    window.workers.clear()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    window.close()
    app.processEvents()


@pytest.mark.parametrize('query', ['名称', '用途', '步骤', '检查', '来源标题', '李老师', ' MIXED '])
def test_search_matches_each_documented_field_case_insensitively(query):
    assert importlib.util.find_spec('shenlun.ui.method_filters'), 'shared method search required'
    from shenlun.ui.method_filters import method_matches
    record = dict(name='名称 mixed', purpose='用途', steps=['步骤'], checks=['检查'],
                  owner_id='teacher', sources=[{'title': '来源标题'}],
                  applicable_types=['归纳概括'], enabled=False)
    assert method_matches(record, query=query, owner_names={'teacher': '李老师'})
    assert not method_matches(record, query=query + '不存在')
    assert not method_matches(record, query=query, question_type='文章写作', owner_names={'teacher': '李老师'})
    assert not method_matches(record, query=query, enabled=True, owner_names={'teacher': '李老师'})
    assert method_matches(record, query=query, enabled=False, owner_id='teacher', owner_names={'teacher': '李老师'})


def test_article_filters_clear_checks_keep_detail_id_and_batch_only_visible(host):
    first = host.store.save_manual_article_method(article_draft(host.store))
    second = host.store.save_manual_article_method({**article_draft(host.store), 'name': '另一方法'})
    host.refresh_methods()
    assert hasattr(host, 'article_method_search'), 'article filter toolbar required'
    host.article_batch_select_all_button.click()
    host.article_method_search.setText('另一')
    assert not host.checked_article_methods()
    assert host.method_table.rowCount() == 1
    host.method_table.selectRow(0)
    assert host.selected_card()['id'] == second['id']
    host.article_batch_select_all_button.click()
    assert [r['id'] for r in host.checked_article_methods()] == [second['id']]
    assert '1 / 2' in host.method_status.text()
    host.article_method_search.clear()
    assert host.selected_card()['id'] == second['id']
    assert not host.checked_article_methods()
    assert first['id'] != second['id']


def test_article_search_source_titles_and_composed_type_enabled_filters(host):
    saved = host.store.save_manual_article_method(article_draft(host.store))
    host.store.set_card_enabled(saved['id'], True)
    host.refresh_methods()
    assert hasattr(host, 'article_method_search'), 'article filter toolbar required'
    host.article_method_search.setText('合成文章a')
    assert host.method_table.rowCount() == 1
    host.article_method_type_filter.setCurrentText('归纳概括')
    assert host.method_table.rowCount() == 0
    host.article_method_type_filter.setCurrentText('文章写作')
    host.article_method_status_filter.setCurrentIndex(host.article_method_status_filter.findData(False))
    assert host.method_table.rowCount() == 0
    host.article_method_status_filter.setCurrentIndex(host.article_method_status_filter.findData(True))
    assert host.method_table.rowCount() == 1


def test_teacher_filters_search_all_fields_and_owner_without_losing_total_records(host):
    saved = host.store.save_teaching_method({**method('filter-one'), 'name': '专用方法',
        'checks': ['特殊检查'], 'sources': [{**method()['sources'][0], 'title': '唯一来源'}]})
    host.store.set_teaching_method_enabled(saved['id'], True, reviewer='合成核对', note='测试', expected_version=1)
    p = host.teaching_panel
    p.refresh()
    assert hasattr(p, 'method_search'), 'teaching filter toolbar required'
    total = len(p.records)
    for query in ['专用方法', '特殊检查', '唯一来源']:
        p.method_search.setText(query)
        assert p.method_list.count() == 1 and len(p.records) == total
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('bailu'))
    assert p.method_list.count() == 0
    p.owner_filter.setCurrentIndex(p.owner_filter.findData('limengyuan'))
    p.enabled_filter.setCurrentIndex(p.enabled_filter.findData(False))
    assert p.method_list.count() == 0
    p.enabled_filter.setCurrentIndex(p.enabled_filter.findData(True))
    assert p.method_list.count() == 1 and f'1 / {total}' in p.summary.text()
    p.question_type_filter.setCurrentText('文章写作')
    assert p.method_list.count() == 0


@pytest.mark.parametrize('control,value', [('method_search', '新查询'), ('question_type_filter', '文章写作'),
                                         ('enabled_filter', False), ('owner_filter', 'bailu')])
def test_cancel_teacher_filter_preserves_widgets_checks_and_unsaved_editor(host, monkeypatch, control, value):
    p = host.teaching_panel
    assert hasattr(p, 'method_search'), 'teaching filter toolbar required'
    p.select_method('LM01')
    p.check_methods(True)
    checked = {r['id'] for r in p.checked_methods()}
    p.name.setText('保留未保存编辑')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    widget = getattr(p, control)
    if control == 'method_search': widget.setText(value)
    elif control == 'question_type_filter': widget.setCurrentText(value)
    else: widget.setCurrentIndex(widget.findData(value))
    assert p.method_search.text() == '' and p.question_type_filter.currentData() is None
    assert p.enabled_filter.currentData() is None and p.owner_filter.currentData() is None
    assert {r['id'] for r in p.checked_methods()} == checked
    assert p.current['id'] == 'LM01' and p.name.text() == '保留未保存编辑' and p.dirty


def test_successful_teacher_search_clears_checks_and_limits_select_all(host, monkeypatch):
    p = host.teaching_panel
    assert hasattr(p, 'method_search'), 'teaching filter toolbar required'
    p.check_methods(True)
    p.method_search.setText('白鹭')
    assert not p.checked_methods()
    p.check_methods(True)
    assert len(p.checked_methods()) == 2 and all(r['owner_id'] == 'bailu' for r in p.checked_methods())
    p.refresh()
    assert len(p.checked_methods()) == 2


def test_filtered_article_delete_targets_visible_identity_then_restore_rematches(host, monkeypatch):
    first = host.store.save_manual_article_method(article_draft(host.store))
    second = host.store.save_manual_article_method({**article_draft(host.store), 'name': '可见方法'})
    host.refresh_methods()
    host.article_method_search.setText('可见方法')
    host.method_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    host.delete_article_method()
    assert [r['id'] for r in host.store.list_cards()] == [first['id']]
    assert host.method_table.rowCount() == 0 and host.selected_card() is None
    host.store.restore_method('article', second['id'])
    host.refresh_methods()
    host.method_table.selectRow(0)
    assert host.selected_card()['id'] == second['id']


def test_accept_teacher_filter_discards_editor_and_checks_only_after_confirmation(host, monkeypatch):
    p = host.teaching_panel
    p.select_method('LM01')
    p.check_methods(True)
    p.name.setText('已确认放弃的内容')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    p.method_search.setText('白鹭')
    assert p.method_list.count() == 2 and not p.checked_methods()
    assert not p.dirty and p.name.text() == '' and not p.current.get('id')


@pytest.mark.parametrize('tab_index', [0, 1])
def test_method_filter_toolbar_fits_minimum_window(host, tab_index):
    host.resize(1050, 720)
    host.navigation.setCurrentRow(2)
    host.methods_tabs.setCurrentIndex(tab_index)
    host.show()
    QApplication.processEvents()
    bar = host.teaching_panel.method_filters if tab_index == 0 else host.article_method_filters
    controls = [bar.search, bar.question_type, bar.enabled]
    if tab_index == 0: controls.insert(0, host.teaching_panel.owner_filter)
    assert bar.isVisible()
    for control in controls:
        assert control.width() >= control.minimumSizeHint().width()
        assert bar.rect().contains(control.geometry())
