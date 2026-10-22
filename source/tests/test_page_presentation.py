"""Behavioral presentation contracts against real native Qt controls."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from shenlun.storage import Store
from shenlun.ui import MainWindow
from shenlun.ui.intake import IntakePreviewDialog


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    result = MainWindow(Store(tmp_path / 'presentation.db'))
    yield result
    result.close()
    app.processEvents()


def test_report_export_waits_for_real_review(window):
    assert not window.export_report_button.isEnabled()
    assert not window.export_pdf_button.isEnabled()
    window.show_review({'title': '训练', 'submission': {'answer': '原答'},
                        'assessment': {}, 'revisions': {}, 'sources': []})
    assert window.export_report_button.isEnabled()
    assert window.export_pdf_button.isEnabled()


def test_article_actions_follow_selected_source_and_text(window, app):
    assert not window.article_source_button.isEnabled()
    assert not window.extract_button.isEnabled()
    window.store.upsert_article({'title': '有文本', 'body': '可核对正文。'})
    window.refresh_corpus()
    window.corpus_table.selectRow(0)
    app.processEvents()
    assert window.extract_button.isEnabled()
    assert not window.article_source_button.isEnabled()
    window.corpus_table.clearSelection()
    assert not window.extract_button.isEnabled()


def test_method_empty_action_navigates_to_articles_and_selection_gates_actions(window, app):
    assert not window.enable_method_button.isEnabled()
    assert not window.disable_method_button.isEnabled()
    assert not window.method_evidence_button.isEnabled()
    window.navigation.setCurrentRow(2)
    window.method_library_button.click()
    assert window.pages.currentIndex() == 1
    window.store.save_card({'id': 'candidate', 'name': '方法', 'enabled': False,
                            'refs': [{'id': 'article:p1', 'quote': '证据'}]})
    window.refresh_methods()
    window.method_table.selectRow(0)
    app.processEvents()
    assert window.enable_method_button.isEnabled()
    assert not window.disable_method_button.isEnabled()
    assert window.method_evidence_button.isEnabled()
    window.method_table.clearSelection()
    assert not window.enable_method_button.isEnabled()
    assert not window.method_evidence_button.isEnabled()


def test_history_empty_action_and_saved_selection(window, app):
    assert not window.reopen_history_button.isEnabled()
    assert not window.export_history_button.isEnabled()
    window.navigation.setCurrentRow(3)
    window.assessment_tabs.setCurrentIndex(2)
    window.history_start_button.click()
    assert window.pages.currentIndex() == 0
    assert window.assessment_tabs.currentIndex() == 0
    rid = window.store.save_review({'title': '原题', 'submission': {'answer': '原答'},
                                    'assessment': {}, 'revisions': {}, 'sources': []})
    window.refresh_history()
    window.navigation.setCurrentRow(3)
    window.review_list.setCurrentRow(0)
    app.processEvents()
    assert window.review_list.currentItem().data(Qt.ItemDataRole.UserRole) == rid
    assert window.reopen_history_button.isEnabled()
    assert window.export_history_button.isEnabled()
    assert window.pages.currentIndex() == 3
    assert '原题' in window.history_explanation.toPlainText()
    window.open_history_button.click()
    assert window.pages.currentIndex() == 0
    assert window.assessment_tabs.currentIndex() == 2
    window.navigation.setCurrentRow(3)
    window.reopen_history_button.click()
    assert window.answer_edit.toPlainText() == '原答'
    assert window.assessment_tabs.currentIndex() == 0
    window.navigation.setCurrentRow(3)
    window.review_list.clearSelection()
    assert not window.open_history_button.isEnabled()
    assert not window.reopen_history_button.isEnabled()
    assert not window.export_history_button.isEnabled()


def test_intake_page_actions_respect_list_boundaries(window):
    pages = [SimpleNamespace(source='材料.txt', number=i, text=str(i), method='文本',
                             preview_png=b'', warnings=[], fields={}) for i in (1, 2)]
    dialog = IntakePreviewDialog(SimpleNamespace(pages=pages, errors=[]), window)
    assert not dialog.move_up_button.isEnabled()
    assert dialog.move_down_button.isEnabled()
    dialog.move_down_button.click()
    assert dialog.move_up_button.isEnabled()
    assert not dialog.move_down_button.isEnabled()
    dialog.remove_page_button.click()
    dialog.remove_page_button.click()
    assert not dialog.remove_page_button.isEnabled()
    assert not dialog.text_edit.isEnabled()
    dialog.close()


def test_history_selection_prepares_report_without_leaving_list(window, app):
    window.store.save_review({'title': '已保存练习', 'submission': {'answer': '保留原答'},
                              'assessment': {}, 'revisions': {}, 'sources': []})
    window.refresh_history()
    window.navigation.setCurrentRow(3)
    window.review_list.setCurrentRow(0)
    app.processEvents()
    assert window.pages.currentIndex() == 3
    assert window.result_original.toPlainText() == '保留原答'
    assert window.open_history_button.isEnabled()


def test_report_focus_at_minimum_size_and_return_restores_training_controls(window, app):
    window.resize(1050, 720)
    window.show()
    window.answer_edit.setPlainText('保留正在编辑的原答')
    window.assessment_tabs.setCurrentIndex(2)
    app.processEvents()
    assert window.result_report.viewport().height() >= 250
    assert not window.exercise_settings_card.isVisibleTo(window)
    assert not window.scope_label.isVisibleTo(window)
    assert not window.assessment_actions.isVisibleTo(window)
    assert window.return_to_question_button.isVisibleTo(window)
    window.return_to_question_button.click()
    app.processEvents()
    assert window.assessment_tabs.currentIndex() == 0
    assert window.exercise_settings_card.isVisibleTo(window)
    assert window.scope_label.isVisibleTo(window)
    assert window.assess_button.isVisibleTo(window)
    assert window.cancel_button.isVisibleTo(window)
    assert window.answer_edit.toPlainText() == '保留正在编辑的原答'
    window.assessment_tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.exercise_settings_card.isVisibleTo(window)
    assert window.scope_label.isVisibleTo(window)
    assert window.assessment_actions.isVisibleTo(window)


@pytest.mark.parametrize('size', [(1050, 720), (1360, 900)])
def test_work_areas_and_primary_actions_remain_reachable(window, app, size):
    window.resize(*size)
    window.show()
    controls = [
        [window.question_type, window.materials_edit, window.answer_edit, window.assess_button],
        [window.corpus_table, window.article_tabs, window.extract_button, window.filter_extract_button],
        [window.method_library_button],
        [window.history_start_button, window.calibration_label],
        [window.model_url, window.api_key],
    ]
    for index, page_controls in enumerate(controls):
        window.navigation.setCurrentRow(index)
        if index == 2:
            # Publication actions now have their own visible tab beside teaching.
            window.methods_tabs.setCurrentIndex(1)
        app.processEvents()
        assert (window.width(), window.height()) == size
        for control in page_controls:
            assert control.isVisibleTo(window)
            top_left = control.mapTo(window, QPoint(0, 0))
            bottom_right = control.mapTo(window, control.rect().bottomRight())
            assert window.rect().contains(top_left)
            assert window.rect().contains(bottom_right)
        if index == 2:
            window.methods_tabs.setCurrentWidget(window.teaching_panel)
            app.processEvents()
            for control in (window.teaching_panel.method_list, window.teaching_panel.save_button,
                            window.teaching_panel.enable_button):
                assert control.isVisibleTo(window)
                assert window.rect().contains(control.mapTo(window, QPoint(0, 0)))
                assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
    settings_scroll = window.pages.widget(4).findChild(QScrollArea)
    settings_scroll.ensureWidgetVisible(window.send_temperature)
    app.processEvents()
    assert settings_scroll.viewport().rect().contains(
        window.send_temperature.mapTo(settings_scroll.viewport(), QPoint(0, 0)))
