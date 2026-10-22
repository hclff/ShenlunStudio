"""File and publisher-specific official corpus import, without model calls."""
import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

from lxml import etree, html

from .document_readers import DOCUMENT_SUFFIXES, MAX_FILE_BYTES, read_document, read_json_articles
from .storage import normalize_article, tag_list


class ImportCancelled(Exception):
    """A user canceled the operation; already committed earlier records remain."""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ImportCancelled('导入已取消')


def _progress(progress, message):
    if progress:
        progress(message)


def _publisher(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('仅支持求是网、人民网官方 HTTP(S) 地址')
    if parts.port not in (None, 80, 443):
        raise ValueError('官方来源端口无效')
    host = parts.hostname.lower().rstrip('.')
    for publisher in ('qstheory.cn', 'people.com.cn'):
        if host == publisher or host.endswith('.' + publisher):
            return publisher
    raise ValueError('仅支持 qstheory.cn、people.com.cn 官方来源')


class OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def __init__(self, publisher):
        self.publisher = publisher

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _publisher(newurl) != self.publisher:
            raise ValueError('官方来源不能重定向到其他发布者')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_official_html(url, cancel=None):
    publisher = _publisher(url)
    opener = urllib.request.build_opener(OfficialRedirectHandler(publisher))
    for attempt in range(2):
        _check_cancel(cancel)
        request = urllib.request.Request(url, headers={'User-Agent': 'ShenlunStudio/0.1 (official corpus reader)', 'Accept': 'text/html'})
        try:
            with opener.open(request, timeout=25) as response:
                if _publisher(response.geturl()) != publisher:
                    raise ValueError('官方来源重定向发布者不一致')
                raw = response.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    raise ValueError('官方网页超过25MB限制')
                charset = response.headers.get_content_charset()
            _check_cancel(cancel)
            # Most official pages declare UTF-8; legacy editions sometimes GBK.
            for encoding in dict.fromkeys([charset or 'utf-8', 'utf-8', 'gb18030']):
                try:
                    return raw.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    pass
            raise ValueError('官方网页字符编码无法识别')
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 1:
                raise
    raise RuntimeError('官方网页下载失败')


def _tree(text):
    try:
        return html.fromstring(text, parser=html.HTMLParser(huge_tree=True))
    except (etree.ParserError, etree.XMLSyntaxError) as exc:
        raise ValueError('官方网页没有可解析的HTML正文') from exc


def _clean(text):
    return re.sub(r'[\s\xa0\u3000]+', ' ', text).strip()


def _first_text(tree, paths):
    for path in paths:
        nodes = tree.xpath(path)
        if nodes:
            value = nodes[0] if isinstance(nodes[0], str) else nodes[0].text_content()
            if _clean(value):
                return _clean(value)
    return ''


def is_auxiliary_title(title):
    return any(word in title for word in ('本版责编','欢迎赐稿','图片报道','征稿启事'))


def classify_kind(title, journal='', source=''):
    if is_auxiliary_title(title):
        return '其他'
    if any(word in title for word in ('导读', '目录')):
        return '导读'
    if any(word in title for word in ('推荐', '书目', '荐读')):
        return '推荐'
    if any(word in title for word in ('统计', '主要指标', '数据图表')):
        return '统计'
    if any(word in title for word in ('人物', '先进事迹', '人物专访')):
        return '人物'
    if any(word in title + source for word in ('评论', '人民时评', '人民论坛', '钟声', '仲音', '社论', '编辑部')):
        return '评论'
    if journal in ('求是', '红旗文稿') or '理论' in source:
        return '理论'
    return '其他'


def parse_official_article(html_text: str, url: str) -> dict:
    publisher = _publisher(url)
    tree = _tree(html_text)
    title = _first_text(tree, ['//h1', '//meta[@property="og:title"]/@content', '//title'])
    if not title:
        raise ValueError('官方网页缺少文章标题')
    paths = ['//*[@id="ozoom"]', '//*[@class="highlight"]', '//*[@id="article-content"]',
             '//*[contains(concat(" ",normalize-space(@class)," ")," article-content ")]',
             '//*[contains(concat(" ",normalize-space(@class)," ")," rm_txt_con ")]',
             '//*[contains(concat(" ",normalize-space(@class)," ")," TRS_Editor ")]',
             '//*[@id="Zoom"]', '//*[@id="zoom"]', '//*[@id="content"]']
    body_node = None
    for path in paths:
        nodes = tree.xpath(path)
        if nodes and _clean(nodes[0].text_content()):
            body_node = nodes[0]
            break
    if body_node is None:
        if publisher=='people.com.cn' and is_auxiliary_title(title):
            dated=re.search(r'/(20\d{2})[-/]?(\d{2})/(\d{2})/',url)
            publication=f'{dated[1]}-{dated[2]}-{dated[3]}' if dated else ''
            return normalize_article({'title':title,'url':urldefrag(url)[0],'journal':'人民日报','publication':publication,
                    'year':int(dated[1]) if dated else 0,'issue':0,'author':'','body':'','paragraphs':[],
                    'status':'available','kind':'其他','training_eligible':False,'text_status':'no_text'})
        raise ValueError('未识别官方文章正文区域，拒绝将整页导航当作正文')
    for junk in body_node.xpath('.//script|.//style|.//nav|.//iframe|.//form'):
        junk.drop_tree()
    paragraphs = [_clean(node.text_content()) for node in body_node.xpath('.//p') if _clean(node.text_content())]
    if not paragraphs:
        paragraphs = [_clean(line) for line in body_node.text_content().splitlines() if _clean(line)]
    source = _first_text(tree, ['//span[@class="appellation"][contains(.,"来源")]',
                               '//*[contains(concat(" ",normalize-space(@class)," ")," source ")]',
                               '//*[contains(concat(" ",normalize-space(@class)," ")," lai ")]',
                               '//*[contains(concat(" ",normalize-space(@class)," ")," art-sr-time ")]',
                               '//meta[@name="source"]/@content'])
    author = _first_text(tree, ['//span[@class="appellation"][contains(.,"作者")]',
                               '//founder-author', '//p[@class="sec"]/text()[normalize-space()]',
                               '//*[contains(concat(" ",normalize-space(@class)," ")," author ")]', '//h4'])
    if not author:
        author = _first_text(tree, ['//meta[translate(@name,"AUTHOR","author")="author"]/@content'])
        if re.fullmatch(r'第\s*\d+\s*版', author) or author in ('求是网', '人民网'):
            author = ''
    if not author:
        match = re.search(r'作者\s*[:：]\s*(.+?)(?=\s+\d{4}[-年/]|\s+来源|$)', source)
        author = match.group(1).strip() if match else ''
    author = re.sub(r'^作者\s*[:：]\s*', '', author)
    journal = '人民日报' if publisher == 'people.com.cn' else ('红旗文稿' if '红旗文稿' in source else '求是')
    publication = _first_text(tree, ['//*[contains(concat(" ",normalize-space(@class)," ")," newstime ")]',
                                     '//p[@class="sec"]//span[@class="date"]'])
    # The electronic paper templates carry a stale 2013 meta publishdate.
    # Visible edition dates and edition URL paths are authoritative there.
    if not publication and publisher != 'people.com.cn':
        publication = _first_text(tree, ['//meta[@name="publishdate"]/@content', '//meta[@name="pubdate"]/@content'])
    match = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})', publication or source)
    if not match:
        match = re.search(r'/(20\d{2})[-/]?(\d{2})/(\d{2})/', url)
    if match:
        publication = f'{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}'
    issue_match = re.search(r'(20\d{2})/(\d{1,2})', source) if publisher == 'qstheory.cn' else None
    paragraphs = _remove_heading(paragraphs, title, author)
    if not paragraphs:
        raise ValueError('官方文章没有可提取正文')
    return normalize_article({'title': title, 'url': urldefrag(url)[0], 'journal': journal,
        'year': int(issue_match[1]) if issue_match else int(publication[:4]) if publication[:4].isdigit() else 0,
        'issue': int(issue_match[2]) if issue_match else 0, 'author': author, 'publication': publication,
        'kind': classify_kind(title, journal, source), 'body': '\n'.join(paragraphs), 'source_metadata': source,
        'training_eligible':not is_auxiliary_title(title)})


def _remove_heading(paragraphs, title, author):
    result = list(paragraphs)
    for _ in range(2):
        if result and result[0].rstrip('※* ') in (title, author) and result[0]:
            result.pop(0)
    return result


def _result():
    return {'processed': 0, 'available': 0, 'unavailable': 0, 'duplicates': 0, 'article_ids': []}


def _save(article, store, result, fingerprints, cancel):
    _check_cancel(cancel)
    value = normalize_article(article)
    fingerprint = value['fingerprint']
    if fingerprint and fingerprint in fingerprints:
        result['duplicates'] += 1
    if fingerprint:
        fingerprints.add(fingerprint)
    article_id = store.upsert_article(value)
    result['processed'] += 1
    result[value['status']] += 1
    result['article_ids'].append(article_id)


def _fingerprints(store):
    return store.article_fingerprints()


def import_manifest(path: Path, store, progress=None, cancel=None) -> dict:
    _check_cancel(cancel)
    # A dedicated path for the already downloaded 38MB source manifest.
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('issues'), list):
        raise ValueError('不是已支持的官方来源清单')
    result, fingerprints = _result(), _fingerprints(store)
    for issue in data['issues']:
        for source in issue.get('articles', []):
            _check_cancel(cancel)
            article = dict(source)
            # Body and canonical paragraphs replace bulky source block copies;
            # URL, cache path, catalogue title and metadata remain traceable.
            article.pop('blocks', None)
            metadata = source.get('metadata', [])
            metadata = [metadata] if isinstance(metadata, str) else metadata
            author = source.get('author', '')
            for item in metadata:
                match = re.search(r'作者\s*[:：]\s*(.+)', str(item))
                if match:
                    author = match[1].strip()
                    break
            title = source.get('title') or source.get('title_in_catalogue') or '未命名文章'
            paragraphs = [str(block.get('text', '')).strip() for block in source.get('blocks', [])
                          if block.get('type') == 'text' and str(block.get('text', '')).strip()]
            paragraphs = _remove_heading(paragraphs, title, author)
            images = [dict(block) for block in source.get('blocks', []) if block.get('type') == 'image']
            article.update(title=title, author=author, journal=issue.get('journal', ''), year=issue.get('year', 0),
                           issue=issue.get('number', 0), body='' if source.get('unavailable') else '\n'.join(paragraphs),
                           status='unavailable' if source.get('unavailable') else 'available', images=images,
                           text_status='text' if paragraphs and not source.get('unavailable') else 'image_only' if images else 'no_text',
                           kind=classify_kind(title, issue.get('journal', ''), ' '.join(map(str, metadata))))
            _save(article, store, result, fingerprints, cancel)
            if result['processed'] % 20 == 0:
                _progress(progress, f"已导入 {result['processed']} 篇官方语料")
    _progress(progress, f"导入完成：{result['processed']} 篇，可用 {result['available']} 篇，暂缺 {result['unavailable']} 篇")
    return result


def read_text_file(path: Path) -> str:
    """Raw text of a supported document; kept for callers that only need a string."""
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('文件超过25MB限制；官方来源清单请使用专用导入')
    if path.suffix.lower() in ('.txt', '.md', '.json'):
        return path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    return read_document(path)['body']


def expand_paths(paths):
    """Flatten files and folders into a stable, filtered, de-duplicated list."""
    collected = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            collected.extend(sorted((child for child in path.rglob('*') if child.is_file()),
                                    key=lambda child: (str(child.parent).lower(), child.name.lower())))
        elif path.is_file():
            collected.append(path)
        else:
            raise ValueError(f'找不到文件或文件夹：{path}')
    files, seen = [], set()
    for path in collected:
        name = path.name
        if name.startswith(('.', '~$')) or name.lower() in ('thumbs.db', 'desktop.ini'):
            continue
        if path.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        files.append(path)
    return files


def _preview_item(article, path, known, index):
    value = normalize_article(article)
    if value['publication'][:4].isdigit() and not value['year']:
        value['year'] = int(value['publication'][:4])
    fingerprint = value['fingerprint']
    return {'path': str(path), 'name': path.name, 'index': index, 'article': value,
            'title': value['title'], 'author': value['author'], 'journal': value['journal'],
            'kind': value['kind'], 'publication': value['publication'], 'tags': value['tags'],
            'characters': len(value['body']), 'fingerprint': fingerprint,
            'duplicate': bool(fingerprint and fingerprint in known), 'error': ''}


def preview_files(paths, store, progress=None, cancel=None) -> dict:
    """Parse only: every candidate is returned for review before anything is written."""
    files = expand_paths(paths)
    known = _fingerprints(store)
    items, failed = [], []
    for position, path in enumerate(files, 1):
        _check_cancel(cancel)
        _progress(progress, f'正在读取 {position}/{len(files)}：{path.name}')
        try:
            if path.suffix.lower() == '.json':
                articles = read_json_articles(path)
            else:
                articles = [read_document(path)]
        except (ValueError, OSError) as exc:
            failed.append({'path': str(path), 'name': path.name, 'error': str(exc)})
            continue
        for index, article in enumerate(articles):
            article = dict(article)
            if not article.get('body') and path.suffix.lower() != '.json':
                failed.append({'path': str(path), 'name': path.name, 'error': '没有正文'})
                continue
            article.setdefault('source_format', path.suffix.lower().lstrip('.'))
            article.setdefault('local_path', str(path.resolve()))
            item = _preview_item(article, path, known, index)
            if item['fingerprint']:
                known.add(item['fingerprint'])
            items.append(item)
    _progress(progress, f'读取完成：待确认 {len(items)} 篇，失败 {len(failed)} 篇')
    return {'items': items, 'failed': failed, 'total': len(items) + len(failed)}


def commit_imports(items, store, progress=None, cancel=None) -> dict:
    """Write reviewed items in one transaction; each row fails or succeeds alone."""
    result = {'imported': 0, 'duplicates': 0, 'failed': [], 'article_ids': []}
    known = _fingerprints(store)
    values, sources = [], []
    for position, item in enumerate(items, 1):
        _check_cancel(cancel)
        if position % 10 == 0:
            _progress(progress, f'正在写入 {position}/{len(items)}')
        source = str(item.get('path') or item.get('name') or '')
        try:
            article = dict(item.get('article') or {})
            if not article:
                raise ValueError('缺少文章数据')
            for key in ('title', 'author', 'journal', 'kind'):
                if item.get(key) is not None:
                    article[key] = item[key]
            publication = str(item.get('publication') or '').strip()
            article['publication'] = publication
            if publication[:4].isdigit():
                article['year'] = int(publication[:4])
            if item.get('tags') is not None:
                article['tags'] = tag_list(item['tags'])
            value = normalize_article(article)
            if not value['body'].strip():
                raise ValueError('没有正文')
            if value['fingerprint'] in known:
                result['duplicates'] += 1
                continue
            known.add(value['fingerprint'])
            values.append(value)
            sources.append(source)
        except (ValueError, OSError) as exc:
            result['failed'].append({'path': source, 'error': str(exc)})
    if values:
        try:
            result['article_ids'] = store.upsert_articles(values)
            result['imported'] = len(result['article_ids'])
        except Exception as exc:  # noqa: BLE001 - a failed batch is reported, not raised
            result['failed'].extend({'path': source, 'error': f'写入失败：{exc}'} for source in sources)
    _progress(progress, f"导入完成：成功 {result['imported']} 篇，重复 {result['duplicates']} 篇，失败 {len(result['failed'])} 篇")
    return result


def import_file(path, store, progress=None, cancel=None) -> dict:
    _check_cancel(cancel)
    path = Path(path)
    # Only the explicitly recognized manifest filename gets the larger path.
    if path.suffix.lower() == '.json' and path.name.lower() == 'manifest.json':
        if path.stat().st_size > MAX_FILE_BYTES:
            return import_manifest(path, store, progress, cancel)
    _check_cancel(cancel)
    if path.suffix.lower() == '.json':
        articles = read_json_articles(path)
    else:
        document = read_document(path)
        if not document['body'].strip():
            raise ValueError('文件没有正文')
        articles = [{'title': document['title'], 'author': document['author'],
                     'publication': document['publication'], 'tags': document['tags'],
                     'body': document['body'], 'local_path': str(path.resolve()),
                     'source_format': document['format']}]
    result, fingerprints = _result(), _fingerprints(store)
    for article in articles:
        _save(article, store, result, fingerprints, cancel)
    _progress(progress, f"文件导入完成：{result['processed']} 篇")
    return result


def import_url(url: str, store, progress=None, cancel=None) -> dict:
    _check_cancel(cancel)
    _publisher(url)
    _progress(progress, '正在读取官方文章')
    article = parse_official_article(fetch_official_html(url, cancel), url)
    result = _result()
    _save(article, store, result, _fingerprints(store), cancel)
    _progress(progress, f"已导入：{article['title']}")
    return result


def _layout_candidates(day):
    old = f'https://paper.people.com.cn/rmrb/html/{day:%Y-%m}/{day:%d}/nbs.D110000renmrb_01.htm'
    new = f'https://paper.people.com.cn/rmrb/pc/layout/{day:%Y%m}/{day:%d}/node_01.html'
    return [new, old] if day.year >= 2025 else [old, new]


def _links(text, url):
    tree = _tree(text)
    result = []
    for node in tree.xpath('//a[@href]|//area[@href]'):
        href = urldefrag(urljoin(url, node.get('href')))[0]
        try:
            if _publisher(href) == 'people.com.cn' and not urlsplit(href).path.lower().endswith('.pdf'):
                result.append((href, _clean(node.text_content())))
        except ValueError:
            continue
    return result


def _is_article_url(url):
    path = urlsplit(url).path
    return bool(re.search(r'/(?:content_\d+\.html|nw\.[^/]+\.htm)$', path))


def _is_layout_url(url):
    return bool(re.search(r'/(?:node_\d+\.html|nbs\.D110000renmrb_\d+\.htm)$', urlsplit(url).path))


def import_people_range(start_date: str, end_date: str, store, progress=None, cancel=None) -> dict:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end or (end - start).days >= 31:
        raise ValueError('人民日报每次导入须为按顺序排列的1至31天')
    _check_cancel(cancel)
    completed = set(store.get_setting('people_import_completed_dates', []))
    failures = dict(store.get_setting('people_import_failed_urls', {}))
    page_labels = dict(store.get_setting('people_import_page_labels', {}))
    result, fingerprints = _result(), _fingerprints(store)
    result.update(completed_dates=[], failed_dates=[])
    for offset in range((end - start).days + 1):
        _check_cancel(cancel)
        day = start + timedelta(days=offset)
        key = day.isoformat()
        if key in completed and key not in failures:
            continue
        _progress(progress, f'正在发现人民日报 {key} 评论/理论版')
        layout_url, layout_links = '', []
        for candidate in _layout_candidates(day):
            try:
                candidate_links = _links(fetch_official_html(candidate, cancel), candidate)
                if not any(_is_layout_url(link) for link, _ in candidate_links):
                    raise ValueError('未识别人民日报每日版面目录')
                layout_url, layout_links = candidate, candidate_links
                break
            except ImportCancelled:
                raise
            except (ValueError, OSError, urllib.error.URLError):
                continue
        if not layout_url:
            failures[key] = sorted(set(failures.get(key,[])+_layout_candidates(day)))
            result['failed_dates'].append(key)
        else:
            pages = {link: label for link, label in layout_links if any(word in label for word in ('评论', '理论')) and _is_layout_url(link)}
            page_labels.update(pages)
            # A directory can change while a failed edition page remains valid.
            # Never erase pending edition work merely because rediscovery omits it.
            for pending in failures.get(key, []):
                if _is_layout_url(pending) and pending not in _layout_candidates(day):
                    pages.setdefault(pending,page_labels.get(pending,''))
            urls, failed_urls = {}, []
            for page_url, label in pages.items():
                _check_cancel(cancel)
                try:
                    article_links = _links(fetch_official_html(page_url, cancel), page_url)
                    if not any(_is_article_url(link) for link, _ in article_links):
                        raise ValueError('评论/理论版未发现文章，保留日期等待重试')
                    for article_url, _ in article_links:
                        if _is_article_url(article_url):
                            urls[article_url] = '理论' if '理论' in label else '评论' if '评论' in label else ''
                except ImportCancelled:
                    raise
                except (ValueError, OSError, urllib.error.URLError):
                    failed_urls.append(page_url)
            for pending in failures.get(key, []):
                if _is_article_url(pending):
                    urls.setdefault(pending, '')
            for article_url, kind in sorted(urls.items()):
                _check_cancel(cancel)
                try:
                    article = parse_official_article(fetch_official_html(article_url, cancel), article_url)
                    if kind and not is_auxiliary_title(article['title']):
                        article['kind'] = kind
                    _save(article, store, result, fingerprints, cancel)
                    _progress(progress, f"{key}：{article['title']}")
                except ImportCancelled:
                    raise
                except (ValueError, OSError, urllib.error.URLError):
                    failed_urls.append(article_url)
            if failed_urls:
                failures[key] = sorted(set(failed_urls))
                result['failed_dates'].append(key)
            else:
                completed.add(key)
                failures.pop(key, None)
                result['completed_dates'].append(key)
        _check_cancel(cancel)
        store.set_setting('people_import_completed_dates', sorted(completed))
        store.set_setting('people_import_failed_urls', failures)
        store.set_setting('people_import_page_labels', page_labels)
    _progress(progress, f"人民日报导入完成：{result['processed']} 篇，失败日期 {len(result['failed_dates'])} 天")
    return result
