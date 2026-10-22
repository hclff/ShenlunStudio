"""Exports keep the metadata and every paragraph in all three formats."""
import json

import pytest

from shenlun.article_export import export_articles, paragraphs_of


def article(title, body='第一段。\n第二段。'):
    return {'id': 'a-' + title, 'title': title, 'author': '张三', 'journal': '人民日报',
            'publication': '2025-05-01 00:00:00', 'kind': '评论',
            'url': 'https://paper.people.com.cn/x.html', 'tags': ['民生', '生态'],
            'body': body, 'paragraphs': [{'id': 'a-' + title + ':p1', 'text': '第一段。'},
                                         {'id': 'a-' + title + ':p2', 'text': '第二段。'}]}


def test_paragraphs_fall_back_to_the_body():
    assert paragraphs_of(article('甲')) == ['第一段。', '第二段。']
    assert paragraphs_of({'body': '只有正文。'}) == ['只有正文。']


def test_markdown_has_front_matter_and_separates_articles(tmp_path):
    path = tmp_path / '导出.md'
    export_articles([article('甲'), article('乙')], path)
    text = path.read_text(encoding='utf-8')
    assert text.count('title: "甲"') == 1 and text.count('title: "乙"') == 1
    assert 'tags: ["民生", "生态"]' in text
    assert '第一段。' in text and '第二段。' in text
    assert '\n\n---\n\n' in text


def test_json_round_trips_the_stored_payload(tmp_path):
    path = tmp_path / '导出.json'
    source = [article('甲'), article('乙')]
    export_articles(source, path)
    assert json.loads(path.read_text(encoding='utf-8')) == source


def test_docx_contains_title_metadata_tags_and_paragraphs(tmp_path):
    docx = pytest.importorskip('docx')
    path = tmp_path / '导出.docx'
    export_articles([article('甲'), article('乙')], path)
    texts = [paragraph.text for paragraph in docx.Document(str(path)).paragraphs]
    assert '甲' in texts and '乙' in texts
    assert any('张三' in text and '人民日报' in text for text in texts)
    assert '标签：民生、生态' in texts
    assert texts.count('第一段。') == 2


def test_unknown_suffix_and_empty_selection_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='Markdown'):
        export_articles([article('甲')], tmp_path / '导出.xlsx')
    with pytest.raises(ValueError, match='选择'):
        export_articles([], tmp_path / '导出.md')
