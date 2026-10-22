"""The tag manager edits the whole corpus, so it must confirm, back up and report."""
import pytest

from shenlun.storage import Store
from shenlun.ui.library import tag_manager


@pytest.fixture
def store(tmp_path):
    data = Store(tmp_path / 'tag-manager.db')
    data.upsert_article({'title': '甲', 'body': '正文一。', 'journal': '人民日报',
                         'tags': ['理论', '生态']})
    data.upsert_article({'title': '乙', 'body': '正文二。', 'journal': '人民日报',
                         'tags': ['理论']})
    data.upsert_article({'title': '丙', 'body': '正文三。', 'journal': '求是',
                         'tags': ['理论评论']})
    return data


def select(dialog, tag):
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 0).text() == tag:
            dialog.table.selectRow(row)
            return
    raise AssertionError(f'标签 {tag} 不在列表里')


def use_input(monkeypatch, value):
    class FakeInput:
        @staticmethod
        def getText(*_args, **_kwargs):
            return value, True

    monkeypatch.setattr(tag_manager, 'QInputDialog', FakeInput)


def test_manager_lists_each_tag_with_its_count(app, store):
    dialog = tag_manager.TagManagerDialog(store)

    rows = {dialog.table.item(row, 0).text(): dialog.table.item(row, 1).text()
            for row in range(dialog.table.rowCount())}

    assert rows == {'理论': '2', '生态': '1', '理论评论': '1'}
    assert not dialog.rename_button.isEnabled()


def test_rename_updates_every_row_and_reports_the_backup(app, store, monkeypatch):
    dialog = tag_manager.TagManagerDialog(store)
    select(dialog, '理论')
    use_input(monkeypatch, '政策')
    monkeypatch.setattr(tag_manager, 'confirm_question', lambda *a, **k: True)
    emitted = []
    dialog.changed.connect(lambda: emitted.append(True))

    dialog.rename_tag()

    assert emitted == [True]
    assert dict(store.article_tags()) == {'政策': 2, '生态': 1, '理论评论': 1}
    assert '涉及 2 篇' in dialog.status.text()
    assert '备份' in dialog.status.text()
    select(dialog, '政策')
    assert dialog.rename_button.isEnabled()


def test_cancelling_the_confirmation_changes_nothing(app, store, monkeypatch):
    dialog = tag_manager.TagManagerDialog(store)
    select(dialog, '理论')
    use_input(monkeypatch, '政策')
    monkeypatch.setattr(tag_manager, 'confirm_question', lambda *a, **k: False)
    emitted = []
    dialog.changed.connect(lambda: emitted.append(True))

    dialog.rename_tag()

    assert emitted == []
    assert dict(store.article_tags()) == {'理论': 2, '生态': 1, '理论评论': 1}


def test_merge_warns_when_the_target_tag_is_new(app, store, monkeypatch):
    dialog = tag_manager.TagManagerDialog(store)
    select(dialog, '生态')
    use_input(monkeypatch, '政策')
    prompts = []

    def confirm(_parent, _title, text):
        prompts.append(text)
        return True

    monkeypatch.setattr(tag_manager, 'confirm_question', confirm)

    dialog.merge_tag()

    assert prompts and '会新建' in prompts[0]
    assert store.article_ids_with_tag('生态') == []
    assert len(store.article_ids_with_tag('政策')) == 1  # 只有甲带生态


def test_delete_removes_the_tag_but_keeps_the_articles(app, store, monkeypatch):
    dialog = tag_manager.TagManagerDialog(store)
    select(dialog, '理论评论')
    monkeypatch.setattr(tag_manager, 'confirm_destructive', lambda *a, **k: True)

    dialog.delete_tag()

    assert store.article_tags() == [('理论', 2), ('生态', 1)]
    assert len(store.list_article_summaries(limit=10)) == 3
    assert '已删除标签' in dialog.status.text()


def test_a_failed_operation_is_reported_without_touching_the_corpus(app, store, monkeypatch):
    dialog = tag_manager.TagManagerDialog(store)
    select(dialog, '理论')
    monkeypatch.setattr(tag_manager, 'confirm_destructive', lambda *a, **k: True)

    def boom(_old, _new=None):
        raise ValueError('一次只能重命名成一个标签。')

    class FakeMessageBox:
        @staticmethod
        def warning(_parent, _title, text):
            warnings.append(text)

    warnings = []
    monkeypatch.setattr(store, 'rename_article_tag', boom)
    monkeypatch.setattr(tag_manager, 'QMessageBox', FakeMessageBox)

    dialog.delete_tag()

    assert warnings == ['一次只能重命名成一个标签。']
    assert dict(store.article_tags()) == {'理论': 2, '生态': 1, '理论评论': 1}
