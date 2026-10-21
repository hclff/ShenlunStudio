"""Evidence-checked article methods installed by the normal desktop entry point."""
import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from .domain import RUBRICS, require_list, require_text
from .service import AppService
from .storage import _dump, insert_article_row, normalize_article


class _ArticleLookup:
    def __init__(self, articles):
        self.articles = articles

    def get_article(self, identity):
        return self.articles.get(identity)


def _read_catalog(path, seed):
    path, seed = Path(path), Path(seed)
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('内置文章方法文件缺失或过大，请重新解压完整软件。')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('内置文章方法格式不正确。')
    catalog_id = require_text(data.get('catalog_id'), '内置方法目录标识')
    cards = require_list(data.get('cards'), '内置文章方法', 200)
    identities = set()
    wanted = set()
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError('内置方法必须为对象。')
        identity = require_text(card.get('id'), '内置方法标识')
        if identity in identities:
            raise ValueError('内置方法编号重复。')
        identities.add(identity)
        for field in ('name', 'purpose', 'not_applicable'):
            require_text(card.get(field), '内置方法' + field)
        steps = require_list(card.get('steps'), '内置方法步骤', 15)
        types = require_list(card.get('applicable_types'), '内置方法题型', 5)
        sources = require_list(card.get('source_article_ids'), '内置方法来源', 30)
        if not steps or not types or not sources or any(t not in RUBRICS for t in types):
            raise ValueError('内置方法缺少步骤、题型或来源。')
        for text in steps + sources:
            require_text(text, '内置方法步骤或来源')
        if not card.get('refs'):
            raise ValueError('内置方法缺少原文引用。')
        wanted.update(sources)
    articles = {}
    with closing(sqlite3.connect(seed.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        for identity in sorted(wanted):
            row = conn.execute('SELECT data FROM articles WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('内置方法缺少配套文章：' + identity)
            article = normalize_article(json.loads(row[0]))
            if article['id'] != identity:
                raise ValueError('内置文章编号不一致。')
            articles[identity] = article
    service = AppService(_ArticleLookup(articles), {}, '')
    result = []
    for original in cards:
        card = copy.deepcopy(original)
        service._validate_card(card)
        card.update(enabled=False, status='软件内置文章方法',
                    builtin_catalog=catalog_id, builtin_catalog_version=data.get('version', 1))
        card.setdefault('curation', {})['human_reviewed'] = False
        result.append(card)
    return result, articles


def install_bundled_article_methods(store, catalog_path, seed_path, *, backup_existing=True):
    """Add missing IDs only; preserve local content, activation and article revisions."""
    cards, articles = _read_catalog(catalog_path, seed_path)
    existing = {card['id'] for card in store.list_cards(include_deleted=True)}
    existing.update(store.purged_method_ids('article'))
    missing = [card for card in cards if card['id'] not in existing]
    if not missing:
        return {'added': 0, 'source_conflicts': 0}
    if backup_existing:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        store.backup_to(store.path.parent / ('内置文章方法导入前备份-' + stamp + '.db'))
    added = conflicts = 0
    with store._connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for template in missing:
            # Recheck within the transaction in case another startup won the race.
            if (conn.execute('SELECT 1 FROM cards WHERE id=?', (template['id'],)).fetchone()
                    or store._method_deleted(conn,'article',template['id'])):
                continue
            card = copy.deepcopy(template)
            actual = {}
            for identity in card['source_article_ids']:
                row = conn.execute('SELECT data FROM articles WHERE id=?', (identity,)).fetchone()
                if row:
                    actual[identity] = json.loads(row[0])
                    continue
                article = articles[identity]
                search_text = '\n'.join(article[key] for key in ('title', 'author', 'journal', 'body'))
                insert_article_row(conn, article, search_text)
                if store.fts_available:
                    conn.execute('INSERT INTO article_search(article_id,text) VALUES (?,?)', (identity, search_text))
                actual[identity] = article
            try:
                AppService(_ArticleLookup(actual), {}, '')._validate_card(card)
            except ValueError:
                card['status'] = '内置方法与本机文章不一致，已停用'
                conflicts += 1
            conn.execute('INSERT INTO cards VALUES (?,?,?)', (card['id'], 0, _dump(card)))
            added += 1
    return {'added': added, 'source_conflicts': conflicts}
