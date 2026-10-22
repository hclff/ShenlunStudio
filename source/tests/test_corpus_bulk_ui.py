"""Corpus table: tag column, tag facet, multi-term highlight and bulk actions."""
from pathlib import Path

from PySide6.QtCore import Qt

from shenlun.ui import widgets
from shenlun.ui.library import corpus as corpus_module
from shenlun.ui.table_models import HighlightRole


class FakeInput:
    value = '求是'

    @staticmethod
    def getText(*args, **kwargs):
        return FakeInput.value, True


class FakeSave:
    path = ''

    @staticmethod
    def getSaveFileName(*args, **kwargs):
        return FakeSave.path, ''


class FakeInputInvalidYear(FakeInput):
    value = '24'


def test_corpus_exposes_a_tag_column_and_tooltip(window):
    aid = window.store.upsert_article({'title': '生态治理', 'body': '山水林田湖草沙。',
                                       'journal': '人民日报', 'year': 2025,
                                       'tags': ['生态', '民生']})
    window.refresh_corpus_fresh()
    model = window.corpus_model
    assert model.HEADERS[-1] == '标签'
    assert model.index(0, 5).data() == '民生、生态'
    assert model.index(0, 5).data(Qt.ItemDataRole.ToolTipRole) == '民生、生态'
    assert model.index(0, 0).data(Qt.ItemDataRole.UserRole) == aid


def test_tag_facet_stacks_exact_matches(window):
    window.store.upsert_article({'title': '生态治理', 'body': '正文一。', 'tags': ['生态', '民生']})
    window.store.upsert_article({'title': '生态保护', 'body': '正文二。', 'tags': ['生态']})
    window.store.upsert_article({'title': '理论评论', 'body': '正文三。', 'tags': ['理论评论']})
    window.refresh_corpus_fresh()
    window._selected_tags = {'生态'}
    window.refresh_corpus_fresh()
    assert window.corpus_table.rowCount() == 2
    window._selected_tags = {'生态', '民生'}
    window.refresh_corpus_fresh()
    assert window.corpus_table.rowCount() == 1
    assert window.corpus_table.model().index(0, 0).data() == '生态治理'
    window._selected_tags = {'理论'}
    window.refresh_corpus_fresh()
    assert window.corpus_table.rowCount() == 0
    window._selected_tags = set()


def test_multiple_terms_are_highlighted_and_merged(window):
    window.store.upsert_article({'title': '基层治理与民生', 'body': '正文。'})
    window.corpus_search.setText('基层、民生')
    window.refresh_corpus()
    assert window.corpus_model.index(0, 0).data(HighlightRole) == ('基层', '民生')
    assert widgets.HighlightDelegate._spans('基层治理与民生', ('基层', '民生')) == [(0, 2), (5, 7)]
    assert widgets.HighlightDelegate._spans('民生民生', ('民生', '民生')) == [(0, 4)]


def test_match_mode_switches_between_and_or(window):
    window.store.upsert_article({'title': '基层治理', 'body': '基层事务。'})
    window.store.upsert_article({'title': '民生答卷', 'body': '民生实事。'})
    window.corpus_search.setText('基层 民生')
    window.match_filter.setCurrentIndex(0)
    window.refresh_corpus()
    assert window.corpus_table.rowCount() == 0
    window.match_filter.setCurrentIndex(1)
    window.refresh_corpus()
    assert window.corpus_table.rowCount() == 2
    window.match_filter.setCurrentIndex(0)
    window.corpus_search.clear()


def test_bulk_metadata_update_applies_to_every_selected_row(window, monkeypatch):
    window.store.upsert_article({'title': '甲', 'body': '正文甲。'})
    window.store.upsert_article({'title': '乙', 'body': '正文乙。'})
    window.refresh_corpus_fresh()
    window.corpus_table.selectAll()
    monkeypatch.setattr(corpus_module, 'QInputDialog', FakeInput)
    window.bulk_update('journal')
    assert {row['journal'] for row in window.store.list_article_summaries(limit=10)} == {'求是'}
    window.corpus_table.selectAll()
    window.bulk_tags(add=True)
    rows = window.store.list_article_summaries(limit=10)
    assert all('求是' in row['tags'] for row in rows)


def test_invalid_year_is_rejected_without_a_write(window, monkeypatch):
    aid = window.store.upsert_article({'title': '甲', 'body': '正文甲。', 'year': 2020})
    window.refresh_corpus_fresh()
    window.corpus_table.selectAll()
    monkeypatch.setattr(corpus_module, 'QInputDialog', FakeInputInvalidYear)
    messages = []
    monkeypatch.setattr(window, 'notify', messages.append)
    window.bulk_update('year')
    assert window.store.get_article(aid)['year'] == 2020
    assert any('四位' in message for message in messages)


def test_bulk_delete_confirms_backs_up_and_removes(window, monkeypatch):
    aid = window.store.upsert_article({'title': '待删', 'body': '正文甲。'})
    window.refresh_corpus_fresh()
    window.corpus_table.selectAll()
    monkeypatch.setattr(corpus_module, 'confirm_destructive', lambda *args, **kwargs: True)
    messages = []
    monkeypatch.setattr(window, 'notify', messages.append)
    window.bulk_delete()
    assert window.store.get_article(aid) is None
    assert any('备份' in message for message in messages)
    assert list(Path(window.store.path).parent.glob('*批量删除前备份*.db'))


def test_export_selected_writes_the_chosen_format(window, monkeypatch, tmp_path):
    window.store.upsert_article({'title': '甲', 'body': '第一段。', 'journal': '人民日报',
                                 'tags': ['民生']})
    window.refresh_corpus_fresh()
    window.corpus_table.selectAll()
    target = tmp_path / '导出.md'
    FakeSave.path = str(target)
    monkeypatch.setattr(corpus_module, 'QFileDialog', FakeSave)
    window.export_selected()
    text = target.read_text(encoding='utf-8')
    assert 'title: "甲"' in text and '第一段。' in text and '民生' in text
