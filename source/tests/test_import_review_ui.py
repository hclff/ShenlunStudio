"""The import review table is the only place a parsed row can be edited before write."""
from PySide6.QtCore import Qt

from shenlun.importers import commit_imports
from shenlun.storage import Store
from shenlun.ui.library.import_review import ImportReviewDialog


def item(name, body, **extra):
    value = {'path': 'D:/素材/' + name, 'name': name, 'index': 0,
             'article': {'title': name, 'body': body, 'journal': '', 'kind': '其他'},
             'title': name, 'author': '', 'journal': '', 'publication': '', 'kind': '其他',
             'tags': [], 'characters': len(body), 'fingerprint': '', 'duplicate': False,
             'error': ''}
    value.update(extra)
    return value


def test_duplicates_start_unchecked_and_are_tinted(app):
    dialog = ImportReviewDialog({'items': [item('重复.txt', '重复正文。', duplicate=True),
                                           item('新文.txt', '新正文。')],
                                 'failed': [{'name': '坏.epub', 'error': 'EPUB 文件已损坏'}]})
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert dialog.table.item(1, 0).checkState() == Qt.CheckState.Checked
    assert dialog.table.item(0, 0).background().color().name() == '#fef3c7'
    assert dialog.table.item(0, 8).text() == '重复'
    assert '读取失败 1 条' in dialog._summary.text()
    dialog._check_all(True)
    assert '已勾选 2 条' in dialog._summary.text()
    dialog.close()


def test_edited_rows_reach_the_store_with_exact_fields(app, tmp_path):
    store = Store(tmp_path / 'review.db')
    dialog = ImportReviewDialog({'items': [item('甲.txt', '第一段。\n第二段。'),
                                           item('乙.txt', '不要导入。')], 'failed': []})
    dialog.table.item(0, 1).setText('核对后的标题')
    dialog.table.item(0, 2).setText('王五')
    dialog.table.item(0, 3).setText('求是')
    dialog.table.item(0, 4).setText('2024-06-01')
    dialog.table.item(0, 5).setText('理论')
    dialog.table.item(0, 6).setText('民生、生态')
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    result = commit_imports(dialog.items(), store)
    dialog.close()
    assert result['imported'] == 1 and not result['failed']
    article = store.get_article(result['article_ids'][0])
    assert (article['title'], article['author'], article['journal'], article['kind']) == (
        '核对后的标题', '王五', '求是', '理论')
    assert article['year'] == 2024 and article['tags'] == ['民生', '生态']
    assert [p['text'] for p in article['paragraphs']] == ['第一段。', '第二段。']
    assert article['paragraphs'][0]['id'] == article['id'] + ':p1'


def test_bulk_apply_only_touches_checked_rows(app):
    dialog = ImportReviewDialog({'items': [item('甲.txt', '正文甲。'), item('乙.txt', '正文乙。')],
                                 'failed': []})
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    dialog._journal.setText('人民日报')
    dialog._apply(3, dialog._journal.text())
    dialog._apply(6, '生态')
    assert dialog.table.item(1, 3).text() == '人民日报'
    assert dialog.table.item(1, 6).text() == '生态'
    assert dialog.table.item(0, 3).text() == ''
    rows = dialog.items()
    assert len(rows) == 1 and rows[0]['title'] == '乙.txt'
    dialog.close()
