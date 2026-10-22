"""Preview before write: folder expansion, duplicate marking and reviewed commits."""
import threading

import pytest

from shenlun import importers
from shenlun.storage import Store


def test_expand_paths_filters_hidden_temp_and_unknown_files(tmp_path):
    (tmp_path / 'b.txt').write_text('乙。', encoding='utf-8')
    (tmp_path / 'a.md').write_text('# 甲\n\n正文。', encoding='utf-8')
    (tmp_path / 'sheet.xlsx').write_text('x', encoding='utf-8')
    (tmp_path / '.hidden.txt').write_text('x', encoding='utf-8')
    (tmp_path / '~$lock.txt').write_text('x', encoding='utf-8')
    (tmp_path / 'Thumbs.db').write_text('x', encoding='utf-8')
    nested = tmp_path / 'sub'
    nested.mkdir()
    (nested / 'c.txt').write_text('丙。', encoding='utf-8')
    files = importers.expand_paths([tmp_path])
    assert [path.name for path in files] == ['a.md', 'b.txt', 'c.txt']
    # The same file named twice is still read once.
    assert [path.name for path in importers.expand_paths([tmp_path, tmp_path / 'a.md'])] == [
        'a.md', 'b.txt', 'c.txt']


def test_preview_marks_duplicates_without_writing(tmp_path):
    store = Store(tmp_path / 'preview.db')
    store.upsert_article({'title': '旧文', 'body': '重复正文。'})
    (tmp_path / 'dup.txt').write_text('重复正文。', encoding='utf-8')
    (tmp_path / 'new.txt').write_text('新正文。', encoding='utf-8')
    messages = []
    preview = importers.preview_files([tmp_path], store, progress=messages.append)
    assert store.stats()['articles'] == 1
    assert preview['total'] == 2 and not preview['failed']
    assert {item['name']: item['duplicate'] for item in preview['items']} == {
        'dup.txt': True, 'new.txt': False}
    assert preview['items'][1]['characters'] == len('新正文。')
    assert messages


def test_preview_reports_unreadable_files_and_keeps_reading(tmp_path):
    store = Store(tmp_path / 'preview-bad.db')
    (tmp_path / 'a-bad.epub').write_text('not a zip', encoding='utf-8')
    (tmp_path / 'b-good.txt').write_text('可读正文。', encoding='utf-8')
    preview = importers.preview_files([tmp_path], store)
    assert [item['name'] for item in preview['items']] == ['b-good.txt']
    assert [item['name'] for item in preview['failed']] == ['a-bad.epub']
    assert 'EPUB' in preview['failed'][0]['error']
    assert preview['total'] == 2


def test_commit_imports_applies_reviewed_metadata(tmp_path):
    store = Store(tmp_path / 'commit.db')
    (tmp_path / 'one.md').write_text('# 原标题\n\n第一段。\n第二段。', encoding='utf-8')
    preview = importers.preview_files([tmp_path / 'one.md'], store)
    item = dict(preview['items'][0])
    item.update(title='核对后的标题', author='李四', journal='求是', kind='理论',
                publication='2024-06-01 00:00:00', tags=['民生', '生态'])
    result = importers.commit_imports([item], store)
    assert result['imported'] == 1 and not result['failed']
    article = store.get_article(result['article_ids'][0])
    assert (article['title'], article['author'], article['journal'], article['kind']) == (
        '核对后的标题', '李四', '求是', '理论')
    assert article['publication'] == '2024-06-01 00:00:00' and article['year'] == 2024
    assert article['tags'] == ['民生', '生态']
    assert [p['text'] for p in article['paragraphs']] == ['第一段。', '第二段。']
    assert [p['id'] for p in article['paragraphs']] == [
        article['id'] + ':p1', article['id'] + ':p2']


def test_commit_imports_skips_known_bodies_and_reports_bad_rows(tmp_path):
    store = Store(tmp_path / 'commit-dup.db')
    store.upsert_article({'title': '旧文', 'body': '重复正文。'})
    (tmp_path / 'dup.txt').write_text('重复正文。', encoding='utf-8')
    preview = importers.preview_files([tmp_path / 'dup.txt'], store)
    result = importers.commit_imports(preview['items'], store)
    assert result['duplicates'] == 1 and result['imported'] == 0
    assert store.stats()['articles'] == 1
    failed = importers.commit_imports([{'path': 'empty.txt', 'article': {'title': '空文'}}], store)
    assert failed['failed'] == [{'path': 'empty.txt', 'error': '没有正文'}]


def test_preview_and_commit_both_stop_on_cancel(tmp_path):
    store = Store(tmp_path / 'cancel.db')
    (tmp_path / 'one.txt').write_text('正文。', encoding='utf-8')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(importers.ImportCancelled):
        importers.preview_files([tmp_path], store, cancel=cancel)
    with pytest.raises(importers.ImportCancelled):
        importers.commit_imports([{'path': 'one.txt', 'article': {'title': '甲', 'body': '正文。'}}],
                                 store, cancel=cancel)
    assert store.stats()['articles'] == 0


def test_summary_names_skipped_articles_instead_of_only_counting_them():
    from shenlun.ui.library.imports import import_summary

    text = import_summary('提炼所选文章', {'processed': 3, 'skipped': 2,
                                          'skipped_titles': ['基层治理', '民生答卷']})
    assert '处理 3 篇' in text and '跳过已完成 2 篇' in text
    assert '基层治理、民生答卷' in text

    # Only the first three titles are spelled out; the rest stay a count.
    many = import_summary('提炼所选文章', {'skipped': 9,
                                          'skipped_titles': ['甲', '乙', '丙', '丁']})
    assert '甲、乙、丙' in many and '等 9 篇' in many and '丁' not in many


def test_summary_falls_back_to_a_plain_count_without_titles():
    from shenlun.ui.library.imports import import_summary

    text = import_summary('提炼所选文章', {'skipped': 2})
    assert '跳过已完成 2 篇' in text and '（' not in text
