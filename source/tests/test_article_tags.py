"""Tags are a projected column: normalised, exactly matched and safe to bulk edit."""
import json
import sqlite3
from pathlib import Path

from shenlun.storage import Store, tag_column, tag_list


def add(store, title, body, **extra):
    extra.setdefault('journal', '人民日报')
    return store.upsert_article({'title': title, 'body': body, **extra})


def test_tag_helpers_normalise_case_and_separators():
    assert tag_list('生态, 民生、生态') == ['民生', '生态']
    assert tag_list(['B', 'a', ' a ']) == ['a', 'b']
    assert tag_column(['生态', '民生']) == '民生\n生态'
    assert tag_list(None) == []


def test_tags_are_projected_and_matched_as_whole_values(tmp_path):
    store = Store(tmp_path / 'tags.db')
    theory = add(store, '理论', '第一段。', tags=['理论'])
    review = add(store, '理论评论', '第二段。', tags=['理论评论', '评论'])
    assert [row['id'] for row in store.list_article_summaries(tags=('理论',), limit=10)] == [theory]
    assert [row['id'] for row in store.list_article_summaries(tags=('评论',), limit=10)] == [review]
    assert store.count_articles(tags=('评论',)) == 1
    rows = {row['id']: row for row in store.list_article_summaries(limit=10)}
    assert rows[review]['tags'] == '理论评论\n评论'
    assert rows[theory]['tags'] == '理论'
    assert store.article_tags() == [('理论', 1), ('理论评论', 1), ('评论', 1)]


def test_multiple_tags_stack_as_an_and_filter(tmp_path):
    store = Store(tmp_path / 'stack.db')
    both = add(store, '两者都有', '正文一。', tags=['生态', '民生'])
    add(store, '只有生态', '正文二。', tags=['生态'])
    assert [row['id'] for row in store.list_article_summaries(
        tags=('生态', '民生'), limit=10)] == [both]
    assert store.count_articles(tags=('生态', '民生')) == 1


def test_update_articles_keeps_body_and_paragraph_ids(tmp_path):
    store = Store(tmp_path / 'update.db')
    aid = add(store, '旧标题', '第一段。\n第二段。')
    result = store.update_articles([aid], {'title': '新标题', 'journal': '求是', 'kind': '理论',
                                          'year': 2024, 'publication': '2024-03-01 00:00:00',
                                          'author': '张三'})
    assert result == {'updated': 1}
    article = store.get_article(aid)
    assert (article['title'], article['journal'], article['kind'], article['year']) == (
        '新标题', '求是', '理论', 2024)
    assert article['author'] == '张三' and article['body'] == '第一段。\n第二段。'
    assert [p['id'] for p in article['paragraphs']] == [aid + ':p1', aid + ':p2']
    summary = store.list_article_summaries(query='新标题')[0]
    assert summary['title'] == '新标题' and summary['year'] == 2024


def test_update_articles_rejects_a_write_with_no_editable_field(tmp_path):
    store = Store(tmp_path / 'update-none.db')
    aid = add(store, '文章', '正文。')
    try:
        store.update_articles([aid], {'body': '篡改正文'})
    except ValueError as exc:
        assert '字段' in str(exc)
    else:
        raise AssertionError('a body-only write must be refused')
    assert store.get_article(aid)['body'] == '正文。'


def test_set_article_tags_adds_removes_and_replaces(tmp_path):
    store = Store(tmp_path / 'edit-tags.db')
    aid = add(store, '文章', '正文。', tags=['生态'])
    store.set_article_tags([aid], add='民生、生态')
    assert store.get_article(aid)['tags'] == ['民生', '生态']
    store.set_article_tags([aid], remove='生态')
    assert store.get_article(aid)['tags'] == ['民生']
    store.set_article_tags([aid], tags=['理论', '民生'])
    assert store.get_article(aid)['tags'] == ['民生', '理论']
    store.set_article_tags([], tags=['无关'])
    assert store.set_article_tags([], add=[]) == {'updated': 0}


def test_delete_articles_backs_up_and_blocks_referenced_rows(tmp_path):
    store = Store(tmp_path / 'delete.db')
    keep = add(store, '被方法卡引用', '第一段。')
    drop = add(store, '待删除', '第二段。')
    with sqlite3.connect(store.path) as conn:
        conn.execute('INSERT INTO analyses(article_id,version,data) VALUES(?,?,?)',
                     (drop, 'v1', '{}'))
        conn.execute('INSERT INTO cards(id,enabled,data) VALUES(?,?,?)',
                     ('card-1', 1, json.dumps({'source_article_ids': [keep]}, ensure_ascii=False)))
        conn.execute('INSERT INTO cards(id,enabled,data) VALUES(?,?,?)',
                     ('card-2', 1, json.dumps({'refs': [{'id': keep + ':p1'}]}, ensure_ascii=False)))
    result = store.delete_articles([keep, drop])
    assert result['deleted'] == 1
    assert [item['id'] for item in result['blocked']] == [keep]
    assert 'card-1' in result['blocked'][0]['reason']
    assert 'card-2' in result['blocked'][0]['reason']
    assert result['blocked'][0]['title'] == '被方法卡引用'
    assert Path(result['backup']).is_file()
    assert store.get_article(drop) is None and store.get_article(keep) is not None
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT count(*) FROM analyses WHERE article_id=?',
                            (drop,)).fetchone()[0] == 0
    # The backup still holds the row that was deleted.
    with sqlite3.connect(result['backup']) as conn:
        assert conn.execute('SELECT count(*) FROM articles').fetchone()[0] == 2


def test_delete_articles_without_ids_does_nothing(tmp_path):
    store = Store(tmp_path / 'delete-empty.db')
    assert store.delete_articles([]) == {'deleted': 0, 'blocked': [], 'backup': ''}


def test_rename_tag_rewrites_every_row_and_backs_up_first(tmp_path):
    store = Store(tmp_path / 'rename-tag.db')
    first = add(store, '甲', '正文一。', tags=['理论', '生态'])
    second = add(store, '乙', '正文二。', tags=['理论'])
    untouched = add(store, '丙', '正文三。', tags=['理论评论'])
    assert set(store.article_ids_with_tag('理论')) == {first, second}
    assert store.article_ids_with_tag('理论评论') == [untouched]

    result = store.rename_article_tag('理论', '政策')

    assert result['updated'] == 2
    assert Path(result['backup']).is_file()
    assert store.get_article(first)['tags'] == ['政策', '生态']
    assert store.get_article(second)['tags'] == ['政策']
    assert store.get_article(untouched)['tags'] == ['理论评论']
    assert dict(store.article_tags()) == {'政策': 2, '生态': 1, '理论评论': 1}
    with sqlite3.connect(result['backup']) as conn:
        stored = json.loads(conn.execute('SELECT data FROM articles WHERE id=?', (first,)).fetchone()[0])
    assert stored['tags'] == ['理论', '生态']


def test_merge_tag_into_an_existing_one_does_not_duplicate_it(tmp_path):
    store = Store(tmp_path / 'merge-tag.db')
    both = add(store, '甲', '正文一。', tags=['理论', '政策'])
    only = add(store, '乙', '正文二。', tags=['理论'])

    result = store.rename_article_tag('理论', '政策')

    assert result['updated'] == 2
    assert store.get_article(both)['tags'] == ['政策']
    assert store.get_article(only)['tags'] == ['政策']
    assert store.article_tags() == [('政策', 2)]


def test_delete_tag_removes_it_but_keeps_the_articles(tmp_path):
    store = Store(tmp_path / 'drop-tag.db')
    aid = add(store, '甲', '正文。', tags=['生态', '民生'])

    result = store.rename_article_tag('生态')

    assert result['updated'] == 1 and Path(result['backup']).is_file()
    assert store.get_article(aid)['tags'] == ['民生']
    assert store.get_article(aid)['body'] == '正文。'


def test_tag_rename_refuses_empty_noop_and_multi_target_inputs(tmp_path):
    store = Store(tmp_path / 'rename-guard.db')
    aid = add(store, '甲', '正文。', tags=['民生'])
    assert store.rename_article_tag('民生', '民生') == {'updated': 0, 'backup': ''}
    assert store.rename_article_tag('不存在的标签', '政策') == {'updated': 0, 'backup': ''}
    try:
        store.rename_article_tag('民生', '政策、生态')
    except ValueError as exc:
        assert '一次' in str(exc)
    else:
        raise AssertionError('a multi-tag target must be refused')
    try:
        store.rename_article_tag('')
    except ValueError as exc:
        assert '标签' in str(exc)
    else:
        raise AssertionError('an empty source tag must be refused')
    assert store.get_article(aid)['tags'] == ['民生']

