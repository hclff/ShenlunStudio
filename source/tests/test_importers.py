import json
import threading
from datetime import date
from pathlib import Path

import pytest

from shenlun import importers
from shenlun.storage import Store


FIXTURES = Path(__file__).parent / 'fixtures'


def test_manifest_retains_missing_author_metadata_and_is_idempotent(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'issues': [{'journal': '求是', 'year': 2024, 'number': 2, 'articles': [
        {'url': 'https://www.qstheory.cn/a', 'title': '基层治理', 'metadata': ['来源：《求是》2024/02', '作者：张三'], 'blocks': [{'type': 'text', 'text': '基层治理'}, {'type': 'text', 'text': '张三'}, {'type': 'text', 'text': '坚持人民至上。'}, {'type': 'image', 'url': 'ignore'}]},
        {'url': 'https://www.qstheory.cn/b', 'title': '另一来源', 'blocks': [{'type': 'text', 'text': '坚持人民至上。'}]},
        {'url': 'https://www.qstheory.cn/c', 'title': '暂缺', 'unavailable': True, 'blocks': []}]}]}, ensure_ascii=False), encoding='utf-8')
    store = Store(tmp_path / 'test.db')
    progress = []
    result = importers.import_manifest(path, store, progress.append)
    assert result['processed'] == 3 and result['available'] == 2 and result['unavailable'] == 1
    assert result['duplicates'] == 1
    a = store.list_articles('基层治理')[0]
    assert (a['author'], a['year'], a['issue'], a['journal']) == ('张三', 2024, 2, '求是')
    assert a['body'] == '坚持人民至上。'
    assert a['paragraphs'] == [{'id': a['id'] + ':p1', 'text': '坚持人民至上。'}]
    importers.import_manifest(path, store)
    assert store.stats()['articles'] == 3 and progress


@pytest.mark.parametrize('fixture,url,title,author', [
    ('qstheory.html', 'https://www.qstheory.cn/20261014/a/c.html', '扎实推进基层治理', '张三'),
    ('people-old.html', 'http://paper.people.com.cn/rmrb/html/2024-01/01/nw.D110000renmrb_20240101_1-05.htm', '让民生答卷更有温度', '李四'),
    ('people-new.html', 'https://paper.people.com.cn/rmrb/pc/content/202610/14/content_30123456.html', '以实干推动高质量发展', '王五'),
])
def test_official_parser_extracts_body_without_menus(fixture, url, title, author):
    article = importers.parse_official_article((FIXTURES / fixture).read_text(encoding='utf-8'), url)
    assert article['title'] == title and article['author'] == author
    assert len(article['paragraphs']) == 2
    assert '网站导航' not in article['body'] and '下一篇' not in article['body']
    assert article['paragraphs'][0]['id'] == article['id'] + ':p1'
    assert article['status'] == 'available'


@pytest.mark.parametrize('url', ['https://people.com.cn.evil.test/a', 'https://evil.test/?q=people.com.cn', 'file:///etc/passwd', 'https://user:pass@www.people.com.cn/a'])
def test_rejects_off_domain_before_network(url, tmp_path):
    with pytest.raises(ValueError):
        importers.import_url(url, Store(tmp_path / 'test.db'))


def test_text_import_size_bom_json_and_scan_pdf(tmp_path):
    store = Store(tmp_path / 'test.db')
    path = tmp_path / '文章.md'
    path.write_text('\ufeff第一段。\r\n\r\n第二段。', encoding='utf-8', newline='')
    assert importers.read_text_file(path) == '第一段。\n\n第二段。'
    assert importers.import_file(path, store)['available'] == 1
    source = tmp_path / 'items.json'
    source.write_text(json.dumps([{'title': '自备语料', 'body': '独立内容。'}]), encoding='utf-8')
    importers.import_file(source, store)
    assert store.stats()['articles'] == 2
    huge = tmp_path / 'too-big.txt'
    with huge.open('wb') as output:
        output.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='25'):
        importers.read_text_file(huge)
    pypdf = pytest.importorskip('pypdf')
    pdf = tmp_path / 'scan.pdf'
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    with pytest.raises(ValueError, match='文字|文本|OCR'):
        importers.read_text_file(pdf)


def test_cancel_never_persists_post_cancel_article(tmp_path):
    cancel = threading.Event()
    cancel.set()
    store = Store(tmp_path / 'test.db')
    path = tmp_path / 'input.txt'
    path.write_text('正文', encoding='utf-8')
    with pytest.raises(importers.ImportCancelled):
        importers.import_file(path, store, cancel=cancel)
    assert store.stats()['articles'] == 0


def test_people_range_retry_only_marks_whole_successful_dates(tmp_path, monkeypatch):
    store = Store(tmp_path / 'test.db')
    failed = [True]
    def fetch(url, cancel=None):
        if 'node_' in url:
            return '<a href="node_05.html">05版：评论</a><a href="node_02.html">要闻</a><a href="content_30123456.html">文章一</a><a href="content_30123457.html">文章二</a>'
        if '30123457' in url and failed[0]:
            raise OSError('temporary')
        return (FIXTURES / 'people-new.html').read_text(encoding='utf-8')
    monkeypatch.setattr(importers, 'fetch_official_html', fetch)
    result = importers.import_people_range('2026-10-14', '2026-10-14', store)
    assert result['failed_dates'] == ['2026-10-14']
    assert store.get_setting('people_import_completed_dates', []) == []
    assert store.get_setting('people_import_failed_urls')['2026-10-14']
    failed[0] = False
    result = importers.import_people_range('2026-10-14', '2026-10-14', store)
    assert result['completed_dates'] == ['2026-10-14']
    assert store.get_setting('people_import_completed_dates') == ['2026-10-14']
    assert store.get_setting('people_import_failed_urls') == {}
    assert store.stats()['articles'] == 2
    with pytest.raises(ValueError, match='31'):
        importers.import_people_range('2026-01-01', '2026-03-01', store)


def test_redirect_validation_rejects_other_publisher():
    import urllib.request
    handler = importers.OfficialRedirectHandler('people.com.cn')
    request = urllib.request.Request('https://paper.people.com.cn/a')
    with pytest.raises(ValueError):
        handler.redirect_request(request, None, 302, 'redirect', {}, 'https://evil.test/a')


def test_retry_keeps_failed_layout_even_if_directory_no_longer_lists_it(tmp_path,monkeypatch):
    store=Store(tmp_path/'retry-layout.db')
    day='2026-10-14'
    pending='https://paper.people.com.cn/rmrb/pc/layout/202610/14/node_05.html'
    store.set_setting('people_import_failed_urls',{day:[pending]})
    fetched=[]
    def fetch(url,cancel=None):
        fetched.append(url)
        if url==pending:
            return '<a href="../../../content/202610/14/content_123.html">原先失败版面的文章</a>'
        if 'node_' in url:
            return '<a href="node_02.html">要闻</a>'
        return (FIXTURES/'people-new.html').read_text(encoding='utf8')
    monkeypatch.setattr(importers,'fetch_official_html',fetch)
    result=importers.import_people_range(day,day,store)
    assert pending in fetched
    assert result['processed']==1
    assert result['completed_dates']==[day]
    assert store.get_setting('people_import_failed_urls')=={}


def test_directory_outage_does_not_replace_older_pending_work(tmp_path,monkeypatch):
    store=Store(tmp_path/'retry-outage.db')
    day='2026-10-14';pending='https://paper.people.com.cn/rmrb/pc/layout/202610/14/node_05.html'
    store.set_setting('people_import_failed_urls',{day:[pending]})
    def outage(url,cancel=None):raise OSError('directory temporarily unavailable')
    monkeypatch.setattr(importers,'fetch_official_html',outage)
    result=importers.import_people_range(day,day,store)
    assert result['failed_dates']==[day]
    assert pending in store.get_setting('people_import_failed_urls')[day]


@pytest.mark.parametrize('fixture,url,title,author,publication,paragraphs', [
    ('people-real-new-article.html', 'https://paper.people.com.cn/rmrb/pc/content/202610/14/content_30185481.html', '以投资优化支撑产业升级（人民时评）', '刘元春', '2026-10-14', 10),
    ('people-real-old-article.html', 'https://paper.people.com.cn/rmrb/html/2024-01/01/nw.D110000renmrb_20240101_5-01.htm', '为了更加美好的明天拼搏奋进', '新华社记者', '2024-01-01', 41),
])
def test_real_people_samples_ignore_placeholder_meta(fixture, url, title, author, publication, paragraphs):
    article = importers.parse_official_article((FIXTURES / fixture).read_text(encoding='utf-8'), url)
    assert article['title'] == title
    assert article['author'] == author
    assert article['publication'] == publication
    assert article['year'] == int(publication[:4])
    assert len(article['paragraphs']) == paragraphs
    assert '网上公示' not in article['body']


@pytest.mark.parametrize('day,index_name,page_name,article_name', [
    ('2024-01-02', 'nbs.D110000renmrb_01.htm', 'nbs.D110000renmrb_05.htm', 'nw.D110000renmrb_20240102_1-05.htm'),
    ('2025-01-02', 'node_01.html', 'node_05.html', '../../../content/202501/02/content_123.html'),
])
def test_people_range_discovers_old_new_formats_and_keeps_html(day, index_name, page_name, article_name, tmp_path, monkeypatch):
    store = Store(tmp_path / 'range.db')
    def fetch(url, cancel=None):
        if url.endswith(index_name):
            return f'<a href="{page_name}">05版：理论</a><a href="https://theory.people.com.cn/">理论频道</a>'
        if url.endswith(page_name):
            return f'<a href="{article_name}">文章正文</a><a href="article.pdf">PDF版</a>'
        if url.endswith('.pdf'):
            pytest.fail('must prefer HTML over PDF')
        if url == 'https://theory.people.com.cn/':
            pytest.fail('must discover daily edition pages, not whole theory channel')
        return (FIXTURES / 'people-old.html').read_text(encoding='utf-8')
    monkeypatch.setattr(importers, 'fetch_official_html', fetch)
    result = importers.import_people_range(day, day, store)
    assert result['completed_dates'] == [day]
    assert result['available'] == 1
    assert store.list_articles()[0]['kind'] == '理论'


def test_url_cancellation_after_download_keeps_store_empty(tmp_path, monkeypatch):
    cancel = threading.Event()
    store = Store(tmp_path / 'cancel.db')
    def fetch(url, cancel=None):
        cancel.set()
        return (FIXTURES / 'people-new.html').read_text(encoding='utf-8')
    monkeypatch.setattr(importers, 'fetch_official_html', fetch)
    with pytest.raises(importers.ImportCancelled):
        importers.import_url('https://paper.people.com.cn/rmrb/pc/content/202610/14/content_123.html', store, cancel=cancel)
    assert store.stats()['articles'] == 0


def test_unrecognized_or_empty_official_page_fails_closed():
    for text in ['', '<h1>目录</h1><nav>导航菜单</nav>']:
        with pytest.raises(ValueError):
            importers.parse_official_article(text, 'https://paper.people.com.cn/a')


def test_manifest_image_statistics_keep_source_availability_without_fake_ocr(tmp_path):
    path = tmp_path / 'images.json'
    path.write_text(json.dumps({'issues': [{'journal': '求是', 'year': 2024, 'number': 1, 'articles': [
        {'url': 'https://www.qstheory.cn/chart', 'title': '经济社会发展统计图表', 'blocks': [{'type': 'image', 'url': 'https://www.qstheory.cn/chart.jpg', 'alt': ''}]},
        {'url': 'https://www.qstheory.cn/lost', 'title': '缺失文章', 'unavailable': True, 'blocks': []}]}]}, ensure_ascii=False), encoding='utf-8')
    store = Store(tmp_path / 'images.db')
    result = importers.import_manifest(path, store)
    assert result['available'] == 1 and result['unavailable'] == 1
    article = store.list_articles('统计')[0]
    assert article['status'] == 'available' and article['body'] == '' and article['paragraphs'] == []
    assert article['images'][0]['url'] == 'https://www.qstheory.cn/chart.jpg'
    assert article['text_status'] == 'image_only'
    assert store.stats()['unique_bodies'] == 0


def test_real_qstheory_visible_author_and_issue_override_site_meta():
    article = importers.parse_official_article((FIXTURES / 'qstheory-real.html').read_text(encoding='utf-8'), 'https://www.qstheory.cn/dukan/qs/2023-12/31/c_1130048939.htm')
    assert article['author'] == '习近平'
    assert article['journal'] == '求是'
    assert article['year'] == 2024 and article['issue'] == 1
    assert article['publication'] == '2023-12-31'
    assert article['paragraphs'][0]['text'].startswith('今后5年')
