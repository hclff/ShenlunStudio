"""The model-backed article table must look exactly like the widget table it replaced.

The article list was rebuilt on ``QAbstractTableModel`` to stop creating one
widget item per cell, so every visible property has to stay identical; otherwise
the workspace changes appearance without anyone asking for it.
"""
import pytest
from PySide6.QtWidgets import QTableWidgetItem

from shenlun.ui.table_models import ArticleSummaryModel, HighlightRole
from shenlun.ui.widgets import model_table, table

HEADERS = list(ArticleSummaryModel.HEADERS)
ROWS = [
    {'id': 'a1', 'title': '为一分文化觅得更多知音（评论员观察）', 'journal': '人民日报',
     'year': 2026, 'kind': '评论', 'status': 'available', 'has_body': 1},
    {'id': 'a2', 'title': '从平陆运河看“竞合”（人民锐评）', 'journal': '人民日报',
     'year': 2026, 'kind': '评论', 'status': 'available', 'has_body': 0},
    {'id': 'a3', 'title': '以投资优化支撑产业升级（人民时评）', 'journal': '光明日报',
     'year': 2025, 'kind': '理论', 'status': 'unavailable', 'has_body': 0},
]


@pytest.fixture
def pair(app):
    model = ArticleSummaryModel(ROWS)
    legacy = table(HEADERS)
    legacy.setRowCount(len(ROWS))
    for row_index in range(len(ROWS)):
        for column in range(len(HEADERS)):
            text = model.data(model.index(row_index, column)) or ''
            legacy.setItem(row_index, column, QTableWidgetItem(text))
    modern = model_table(model)
    for widget in (legacy, modern):
        widget.resize(900, 320)
        widget.show()
    app.processEvents()
    yield legacy, modern
    for widget in (legacy, modern):
        widget.close()


def test_selection_and_paint_flags_match(pair):
    legacy, modern = pair
    assert modern.selectionBehavior() == legacy.selectionBehavior()
    assert modern.editTriggers() == legacy.editTriggers()
    assert modern.alternatingRowColors() == legacy.alternatingRowColors()
    assert modern.showGrid() == legacy.showGrid()
    assert modern.wordWrap() == legacy.wordWrap()
    assert modern.textElideMode() == legacy.textElideMode()
    assert modern.hasMouseTracking() == legacy.hasMouseTracking()
    assert modern.frameWidth() == legacy.frameWidth()


def test_scrollbar_policy_matches(pair):
    legacy, modern = pair
    assert modern.horizontalScrollBarPolicy() == legacy.horizontalScrollBarPolicy()
    assert modern.verticalScrollBarPolicy() == legacy.verticalScrollBarPolicy()


def test_header_and_rows_match(pair):
    legacy, modern = pair
    assert modern.verticalHeader().isHidden() == legacy.verticalHeader().isHidden()
    assert modern.verticalHeader().defaultSectionSize() == legacy.verticalHeader().defaultSectionSize()
    assert modern.horizontalHeader().stretchLastSection() == legacy.horizontalHeader().stretchLastSection()
    assert modern.rowCount() == legacy.rowCount()


def test_column_widths_match(pair):
    legacy, modern = pair
    widths = [legacy.horizontalHeader().sectionSize(column) for column in range(len(HEADERS))]
    assert [modern.horizontalHeader().sectionSize(column) for column in range(len(HEADERS))] == widths


def test_viewport_geometry_matches(pair):
    legacy, modern = pair
    assert modern.viewport().size() == legacy.viewport().size()
    assert modern.size() == legacy.size()


def test_appending_a_page_keeps_rows_and_positions():
    model = ArticleSummaryModel(ROWS[:2])
    model.append_rows([ROWS[2]])
    assert model.rowCount() == 3
    assert model.index_of('a3') == 2
    assert model.index_of('missing') == -1
    model.append_rows([])
    assert model.rowCount() == 3


def test_highlight_is_exposed_only_for_the_title_column():
    model = ArticleSummaryModel(ROWS)
    assert model.data(model.index(0, 0), HighlightRole) is None
    model.set_highlight('人民')
    assert model.data(model.index(0, 0), HighlightRole) == ('人民',)
    assert model.data(model.index(0, 1), HighlightRole) is None
    model.set_highlight('')
    assert model.data(model.index(0, 0), HighlightRole) is None


def test_highlight_splits_a_query_into_terms():
    model = ArticleSummaryModel(ROWS)
    model.set_highlight('人民、治理')
    assert model.data(model.index(0, 0), HighlightRole) == ('人民', '治理')
    assert model.highlight_terms() == ('人民', '治理')
