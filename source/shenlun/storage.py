"""Local JSON records with indexed metadata and independent worker connections."""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag
from .practice_store import PracticeStoreMixin
from .evaluation_store import EvaluationStoreMixin
from .reference_store import ReferenceStoreMixin
from .teaching_store import TeachingStoreMixin
from .preset_store import MethodPresetStoreMixin
from .practice_deletion import PracticeDeletionStoreMixin
from .method_library import MethodConflictError
from .migrations import ensure_schema
from .task_store import AssessmentTaskStoreMixin
from .rubric_library import RubricLibraryStoreMixin

_NO_EXPECTED_CARD = object()
_NO_EXPECTED_REVIEW = object()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat()


ARTICLE_SOURCE_COLUMNS = ('id', 'title', 'journal', 'kind', 'status', 'fingerprint', 'publication')
TAG_SPLIT = re.compile(r'[\s,，、;；]+')


def tag_list(tags):
    """Sorted, lower-case, de-duplicated tags; accepts a string or a sequence."""
    if tags is None:
        return []
    items = TAG_SPLIT.split(tags) if isinstance(tags, str) else list(tags)
    return sorted({str(item).strip().lower() for item in items if str(item).strip()})


def tag_column(tags):
    """Storage form of a tag list: newline separated so membership tests are exact."""
    return '\n'.join(tag_list(tags))


def article_search_text(value):
    return '\n'.join(value[key] for key in ('title', 'author', 'journal', 'body'))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def insert_article_row(conn, value, search_text):
    """Single definition of the article insert so column changes stay in one place."""
    columns = ARTICLE_SOURCE_COLUMNS + ('search_text', 'data', 'year', 'has_body', 'tags')
    conn.execute('INSERT INTO articles(' + ','.join(columns) + ') VALUES (' +
                 ','.join('?' * len(columns)) + ') ON CONFLICT(id) DO UPDATE SET '
                 'title=excluded.title,journal=excluded.journal,kind=excluded.kind,'
                 'status=excluded.status,fingerprint=excluded.fingerprint,'
                 'publication=excluded.publication,search_text=excluded.search_text,'
                 'data=excluded.data,year=excluded.year,has_body=excluded.has_body,'
                 'tags=excluded.tags',
                 tuple(value[key] for key in ARTICLE_SOURCE_COLUMNS)
                 + (search_text, _dump(value), int(value.get('year') or 0),
                    1 if str(value.get('body') or '').strip() else 0,
                    tag_column(value.get('tags'))))


def normalize_article(article):
    """Return a serializable canonical record, preserving all source extras."""
    value = dict(article)
    body = str(value.get('body') or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not body and value.get('paragraphs'):
        body = '\n'.join(str(p.get('text', '')).strip() for p in value['paragraphs'] if p.get('text', '').strip())
    url = str(value.get('url') or '').strip()
    identity = urldefrag(url)[0] if url else body or str(value.get('title', ''))
    article_id = str(value.get('id') or 'a-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24])
    paragraphs = [line.strip() for line in body.split('\n') if line.strip()]
    body = '\n'.join(paragraphs)
    status = value.get('status')
    if status not in ('available', 'unavailable'):
        status = 'available' if body else 'unavailable'
    value.update(id=article_id, title=str(value.get('title') or '未命名文章'), journal=str(value.get('journal') or ''),
                 year=int(value.get('year') or 0), issue=int(value.get('issue') or 0),
                 author=str(value.get('author') or ''), url=url, publication=str(value.get('publication') or ''),
                 kind=str(value.get('kind') or '其他'), status=status, body=body,
                 paragraphs=[{'id': f'{article_id}:p{i}', 'text': text} for i, text in enumerate(paragraphs, 1)],
                 tags=tag_list(value.get('tags')),
                 fingerprint=hashlib.sha256(body.encode('utf-8')).hexdigest() if body else '')
    return value


class Store(RubricLibraryStoreMixin,AssessmentTaskStoreMixin,PracticeStoreMixin,EvaluationStoreMixin,ReferenceStoreMixin,TeachingStoreMixin,MethodPresetStoreMixin,PracticeDeletionStoreMixin):
    def __init__(self, path: Path, *, migrate_backup: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, journal TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    publication TEXT NOT NULL, search_text TEXT NOT NULL, data TEXT NOT NULL,
                    year INTEGER NOT NULL DEFAULT 0, has_body INTEGER NOT NULL DEFAULT 0,
                    tags TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS article_filters ON articles(journal,kind);
                CREATE INDEX IF NOT EXISTS article_fingerprints ON articles(fingerprint);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_rechecks (id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS review_rechecks_review ON review_rechecks(review_id);
                CREATE TABLE IF NOT EXISTS analyses (article_id TEXT NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(article_id,version));
                CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, data TEXT NOT NULL);
            ''')
            self._init_practice_deletion(conn)
            self._init_practice(conn)
            self._init_evaluation(conn)
            self._init_reference_points(conn)
            self._init_teaching(conn)
            self._init_assessment_tasks(conn)
            self._init_rubric_library(conn)
        # Structural tables exist now; derived columns, indexes and the version
        # stamp are applied in their own transaction, with a pre-migration backup.
        upgraded = ensure_schema(self.path, backup=migrate_backup)
        if upgraded['applied']:
            # Only an upgraded database can hold reviews that predate the link
            # table; a current database links every review when it is saved.
            self._link_legacy_reviews()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=30000')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_article(self, article: dict) -> str:
        value = normalize_article(article)
        with self._connection() as conn:
            insert_article_row(conn, value, article_search_text(value))
        return value['id']

    def upsert_articles(self, articles) -> list:
        """One transaction for a batch: a rejected record does not undo its peers."""
        values = [normalize_article(article) for article in articles]
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for value in values:
                insert_article_row(conn, value, article_search_text(value))
        return [value['id'] for value in values]

    def get_article(self, id: str):
        with self._connection() as conn:
            row = conn.execute('SELECT data FROM articles WHERE id=?', (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def article_fingerprints(self):
        """Distinct non-empty body fingerprints, read in one indexed scan."""
        with self._connection() as conn:
            return {row[0] for row in conn.execute(
                "SELECT fingerprint FROM articles WHERE fingerprint<>''")}

    def article_tags(self):
        """Every tag in use with its article count, for the filter dropdown."""
        counts = {}
        with self._connection() as conn:
            for (value,) in conn.execute("SELECT tags FROM articles WHERE tags<>''"):
                for tag in value.split('\n'):
                    if tag:
                        counts[tag] = counts.get(tag, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    ARTICLE_SUMMARY_COLUMNS = ('id', 'title', 'journal', 'year', 'kind', 'status',
                               'publication', 'has_body', 'tags')

    @staticmethod
    def _terms(query='', terms=()):
        """Split a query into literal needles; only whitespace and CJK punctuation."""
        needles = [str(term).strip() for term in terms if str(term).strip()]
        if needles:
            return needles
        return [term for term in TAG_SPLIT.split(str(query or '').strip()) if term]

    def _article_clauses(self, journal='', kind='', year='', *, terms=(), match='and', tags=()):
        clauses, params = [], []
        if terms:
            # Literal substring match: a needle is never parsed as SQL or as
            # full-text syntax, and one scan of the extracted text decides it.
            if str(match).lower() == 'or' and len(terms) > 1:
                clauses.append('(' + ' OR '.join(['instr(search_text,?) > 0'] * len(terms)) + ')')
            else:
                clauses.extend(['instr(search_text,?) > 0'] * len(terms))
            params.extend(terms)
        for field, value in (('journal', journal), ('kind', kind)):
            if value:
                clauses.append(field + '=?')
                params.append(value)
        if str(year).strip():
            clauses.append('year=?')
            params.append(int(year))
        for tag in (str(value).strip().lower() for value in tags if str(value).strip()):
            # Exact membership: a newline-delimited column cannot match a prefix.
            clauses.append("instr(char(10)||tags||char(10), char(10)||?||char(10)) > 0")
            params.append(tag)
        return clauses, params

    @staticmethod
    def _relevance(terms):
        """Weighted score in SQL, so paging stays correct without reading bodies."""
        parts, params = [], []
        for term in terms:
            parts.append('(CASE WHEN instr(title,?)>0 THEN 100 ELSE 0 END)')
            params.append(term)
            parts.append('(CASE WHEN instr(journal,?)>0 THEN 20 ELSE 0 END)')
            params.append(term)
            parts.append('(CASE WHEN instr(search_text,?)>0 THEN 10 ELSE 0 END)')
            params.append(term)
            parts.append("((length(search_text)-length(replace(search_text,?,'')))/max(1,length(?)))")
            params.extend((term, term))
        return parts, params

    def list_articles(self, query='', journal='', kind='', limit=200, year='', offset=0, tags=()):
        needle = str(query or '').strip()
        clauses, params = self._article_clauses(journal, kind, year, terms=[needle] if needle else [],
                                                tags=tags)
        sql = 'SELECT data FROM articles' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        sql += ' ORDER BY publication DESC,title ASC,id ASC LIMIT ? OFFSET ?'
        params += [max(0, int(limit)), max(0, int(offset))]
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(sql, params)]

    def list_article_summaries(self, query='', *, terms=(), match='and', tags=(), journal='',
                               kind='', year='', order='default', limit=200, offset=0):
        """Scalar columns only, so a list view never parses an article body."""
        needles = self._terms(query, terms)
        clauses, params = self._article_clauses(journal, kind, year, terms=needles,
                                                match=match, tags=tags)
        score_sql, score_params = self._relevance(needles) if (
            needles and str(order) == 'relevance') else ([], [])
        columns = self.ARTICLE_SUMMARY_COLUMNS
        sql = 'SELECT ' + ','.join(columns) + (' ,(' + '+'.join(score_sql) + ') AS score'
                                              if score_sql else '') + ' FROM articles'
        sql += (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        if score_sql:
            sql += ' ORDER BY score DESC,publication DESC,title ASC,id ASC'
        elif str(order) == 'year':
            sql += ' ORDER BY year DESC,publication DESC,title ASC,id ASC'
        else:
            sql += ' ORDER BY publication DESC,title ASC,id ASC'
        sql += ' LIMIT ? OFFSET ?'
        params = score_params + params + [max(0, int(limit)), max(0, int(offset))]
        result = []
        with self._connection() as conn:
            for row in conn.execute(sql, params):
                value = dict(zip(columns, row[:len(columns)]))
                value['has_body'] = bool(value['has_body'])
                result.append(value)
        return result

    def count_articles(self, query='', *, terms=(), match='and', tags=(), journal='', kind='', year=''):
        needles = self._terms(query, terms)
        clauses, params = self._article_clauses(journal, kind, year, terms=needles,
                                                match=match, tags=tags)
        sql = 'SELECT count(*) FROM articles' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        with self._connection() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def list_article_ids(self, query='', *, terms=(), match='and', tags=(), journal='', kind='',
                         year='', only_text=False):
        """Every id behind a filter, so batch jobs need not page through the view.

        ``only_text`` keeps the rows a model call can actually use, which avoids
        loading missing or image-only articles just to skip them.
        """
        needles = self._terms(query, terms)
        clauses, params = self._article_clauses(journal, kind, year, terms=needles,
                                                match=match, tags=tags)
        clauses = list(clauses)
        if only_text:
            clauses += ["status='available'", 'has_body>0']
        sql = 'SELECT id FROM articles' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        sql += ' ORDER BY publication DESC,title ASC,id ASC'
        with self._connection() as conn:
            return [row[0] for row in conn.execute(sql, params)]

    ALLOWED_ARTICLE_FIELDS = ('title', 'author', 'journal', 'kind', 'year', 'publication', 'tags')

    def update_articles(self, ids, fields: dict) -> dict:
        """Rewrite metadata for the given rows in one transaction; bodies are kept."""
        values = {key: value for key, value in dict(fields).items()
                  if key in self.ALLOWED_ARTICLE_FIELDS}
        if not values:
            raise ValueError('没有可修改的文章字段。')
        ordered = list(dict.fromkeys(str(item) for item in ids))
        updated = 0
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for start in range(0, len(ordered), 400):
                block = ordered[start:start + 400]
                rows = conn.execute('SELECT data FROM articles WHERE id IN (' +
                                    ','.join('?' * len(block)) + ')', block).fetchall()
                for (data,) in rows:
                    article = json.loads(data)
                    article.update(values)
                    if 'tags' in values:
                        article['tags'] = tag_list(values['tags'])
                    value = normalize_article(article)
                    insert_article_row(conn, value, article_search_text(value))
                    updated += 1
        return {'updated': updated}

    def set_article_tags(self, ids, tags=None, *, add=(), remove=()) -> dict:
        """Replace or edit tags; ``add``/``remove`` apply to each row's current list."""
        ordered = list(dict.fromkeys(str(item) for item in ids))
        if not ordered:
            return {'updated': 0}
        base = None if tags is not None else {}
        added, removed = tag_list(add), set(tag_list(remove))
        updated = 0
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for start in range(0, len(ordered), 400):
                block = ordered[start:start + 400]
                rows = conn.execute('SELECT data FROM articles WHERE id IN (' +
                                    ','.join('?' * len(block)) + ')', block).fetchall()
                for (data,) in rows:
                    article = json.loads(data)
                    merged = (tag_list(tags) if base is None
                              else [tag for tag in tag_list(article.get('tags'))
                                    if tag not in removed] + added)
                    article['tags'] = tag_list(merged)
                    value = normalize_article(article)
                    insert_article_row(conn, value, article_search_text(value))
                    updated += 1
        return {'updated': updated}

    def article_ids_with_tag(self, tag):
        """Ids carrying one exact tag, for previews and tag-wide edits."""
        value = next(iter(tag_list([tag])), '')
        if not value:
            return []
        with self._connection() as conn:
            return [row[0] for row in conn.execute(
                "SELECT id FROM articles WHERE "
                "instr(char(10)||tags||char(10), char(10)||?||char(10)) > 0 "
                "ORDER BY title,id", (value,))]

    def _backup_path(self, label):
        """A timestamped sibling path that never overwrites an earlier backup."""
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        candidate = self.path.with_name(f'{self.path.stem}-{label}-{stamp}.db')
        suffix = 1
        while candidate.exists():
            candidate = self.path.with_name(f'{self.path.stem}-{label}-{stamp}-{suffix}.db')
            suffix += 1
        return candidate

    def rename_article_tag(self, old, new=None):
        """Rename a tag on every article, or remove it when ``new`` is empty.

        The whole database is backed up first: one call rewrites every row that
        carries the tag and there is no per-row undo.
        """
        sources = tag_list(old)
        if len(sources) > 1:
            raise ValueError('一次只能处理一个标签。')
        source = sources[0] if sources else ''
        if not source:
            raise ValueError('请选择要处理的标签。')
        if isinstance(new, (list, tuple, set)):
            new = '、'.join(str(item) for item in new)
        values = tag_list(new) if new is not None else []
        if len(values) > 1:
            raise ValueError('一次只能重命名成一个标签。')
        target = values[0] if values else ''
        if target == source:
            return {'updated': 0, 'backup': ''}
        ids = self.article_ids_with_tag(source)
        if not ids:
            return {'updated': 0, 'backup': ''}
        backup = self._backup_path('标签改名前备份')
        self.backup_to(backup, register=True, backup_kind='manual')
        updated = 0
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for start in range(0, len(ids), 400):
                block = ids[start:start + 400]
                rows = conn.execute('SELECT data FROM articles WHERE id IN (' +
                                    ','.join('?' * len(block)) + ')', block).fetchall()
                for (data,) in rows:
                    article = json.loads(data)
                    tags = [tag for tag in tag_list(article.get('tags')) if tag != source]
                    if target:
                        tags.append(target)
                    article['tags'] = tag_list(tags)
                    value = normalize_article(article)
                    insert_article_row(conn, value, article_search_text(value))
                    updated += 1
        return {'updated': updated, 'backup': str(backup)}

    @staticmethod
    def _article_references(conn, wanted):
        """Articles still named by a saved method or a teaching version."""
        found = {}
        for table in ('cards', 'teaching_method_versions'):
            for row_id, data in conn.execute('SELECT id,data FROM ' + table):
                for text in _strings(json.loads(data)):
                    key = text.split(':p', 1)[0] if ':p' in text else text
                    if key in wanted:
                        found.setdefault(key, set()).add(row_id)
        return found

    def delete_articles(self, ids) -> dict:
        """Delete rows nothing else points at, after writing a full backup."""
        ordered = list(dict.fromkeys(str(item) for item in ids))
        if not ordered:
            return {'deleted': 0, 'blocked': [], 'backup': ''}
        backup = self.path.with_name(self.path.stem + '-批量删除前备份-' +
                                     datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
        suffix = 1
        while backup.exists():
            backup = self.path.with_name(self.path.stem + '-批量删除前备份-' +
                                         datetime.now().strftime('%Y%m%d-%H%M%S') +
                                         f'-{suffix}.db')
            suffix += 1
        self.backup_to(backup, register=True, backup_kind='manual')
        wanted = set(ordered)
        deleted, blocked = 0, []
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            titles = dict(conn.execute('SELECT id,title FROM articles WHERE id IN (' +
                                       ','.join('?' * len(ordered)) + ')', ordered))
            references = self._article_references(conn, wanted)
            keep = [item for item in ordered if item not in references]
            for item in ordered:
                if item in references:
                    blocked.append({'id': item, 'title': titles.get(item, ''),
                                    'reason': '仍被方法或教学版本引用：' + '、'.join(sorted(references[item]))})
            for start in range(0, len(keep), 400):
                block = keep[start:start + 400]
                marks = ','.join('?' * len(block))
                conn.execute('DELETE FROM analyses WHERE article_id IN (' + marks + ')', block)
                cursor = conn.execute('DELETE FROM articles WHERE id IN (' + marks + ')', block)
                deleted += cursor.rowcount
        return {'deleted': deleted, 'blocked': blocked, 'backup': str(backup)}

    def _article_rows(self, columns, ids):
        """Chunked id lookup; older SQLite builds cap bound parameters."""
        ordered = [str(item) for item in ids]
        rows = []
        if not ordered:
            return rows
        with self._connection() as conn:
            for start in range(0, len(ordered), 400):
                block = ordered[start:start + 400]
                rows.extend(conn.execute('SELECT ' + columns + ' FROM articles WHERE id IN (' +
                                         ','.join('?' * len(block)) + ')', block).fetchall())
        return rows

    def get_articles(self, ids):
        """Batch fetch preserving the caller's order; unknown ids are skipped."""
        found = {row[0]: json.loads(row[1]) for row in self._article_rows('id,data', ids)}
        return [found[item] for item in (str(value) for value in ids) if item in found]

    def article_titles(self, ids):
        """Title lookup without touching stored bodies."""
        return dict(self._article_rows('id,title', ids))

    def stats(self):
        with self._connection() as conn:
            row = conn.execute("SELECT count(*),sum(status='available'),sum(status='unavailable'),"
                               "count(DISTINCT nullif(fingerprint,'')),"
                               "sum(CASE WHEN has_body>0 THEN 1 ELSE 0 END) FROM articles").fetchone()
            result = dict(zip(('articles', 'available', 'unavailable', 'unique_bodies', 'text_bearing'),
                              (int(v or 0) for v in row)))
            for table in ('analyses', 'cards', 'reviews'):
                result[table] = conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            result['cards']=conn.execute("""SELECT count(*) FROM cards WHERE
                NOT EXISTS (SELECT 1 FROM method_library_trash t WHERE t.kind='article' AND t.method_id=cards.id)
                AND NOT EXISTS (SELECT 1 FROM method_library_purged p WHERE p.kind='article' AND p.method_id=cards.id)""").fetchone()[0]
        return result

    def reclaimable(self):
        """Pages freed by dropped objects that a rebuild would return to disk."""
        with self._connection() as conn:
            pages = int(conn.execute('PRAGMA page_count').fetchone()[0] or 0)
            free = int(conn.execute('PRAGMA freelist_count').fetchone()[0] or 0)
            size = int(conn.execute('PRAGMA page_size').fetchone()[0] or 0)
        return {'pages': pages, 'free': free, 'page_size': size,
                'bytes': free * size, 'ratio': (free / pages) if pages else 0.0}

    def compact(self):
        """Rebuild the database file and report the pages on both sides.

        ``VACUUM`` runs on its own autocommit connection, outside any transaction,
        because SQLite refuses to vacuum inside one. The rebuild is atomic by
        itself, so an interrupted run leaves the previous file intact; the WAL
        file is left to SQLite so no other connection can read a stale frame.
        """
        before = self.reclaimable()
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout=30000')
            conn.execute('VACUUM')
        finally:
            conn.close()
        return {'before': before, 'after': self.reclaimable()}

    def set_setting(self, key, value):
        with self._connection() as conn:
            conn.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', (key, _dump(value)))

    def get_setting(self, key, default=None):
        with self._connection() as conn:
            row = conn.execute('SELECT data FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_review(self, review, *, expected_review=_NO_EXPECTED_REVIEW, connection=None):
        """Join an owned transaction when supplied; never commit it here."""
        if connection is None:
            with self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return self.save_review(review, expected_review=expected_review, connection=conn)
        if not connection.in_transaction:
            raise ValueError('保存训练记录需要已开始的事务。')
        value = dict(review)
        value.setdefault('id', str(uuid.uuid4()))
        value.setdefault('created_at', _now())
        conn = connection
        self._require_live_review(conn,value['id'])
        if expected_review is not _NO_EXPECTED_REVIEW:
            current = conn.execute('SELECT data FROM reviews WHERE id=?', (value['id'],)).fetchone()
            if not current or json.loads(current[0]) != expected_review:
                raise ValueError('训练记录已删除或被其他窗口更新，请重新载入；未覆盖原记录。')
        self._preserve_purged_teaching_history(conn,value.get('teaching_snapshot'),value.get('submission'))
        conn.execute('INSERT INTO reviews VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (value['id'], value['created_at'], _dump(value)))
        self._link_practice_review(conn,value)
        return value['id']

    def get_review(self, id):
        with self._connection() as conn:
            row = conn.execute('SELECT data FROM reviews WHERE id=?', (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_reviews(self, limit=100):
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT data FROM reviews ORDER BY created_at DESC,id DESC LIMIT ?', (max(0, int(limit)),))]

    def list_reviews_page(self, limit=100, offset=0):
        """Newest first; identical ordering to ``list_reviews``."""
        limit = max(0, min(10000, int(limit)))
        offset = max(0, int(offset))
        if not limit:
            return []
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(
                'SELECT data FROM reviews ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                (limit, offset))]

    def count_reviews(self):
        with self._connection() as conn:
            return conn.execute('SELECT count(*) FROM reviews').fetchone()[0]

    def save_review_recheck(self, review_id, payload):
        """Append one recheck; the stored review itself is never rewritten."""
        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError('复核必须关联已保存的训练记录。')
        if not isinstance(payload, dict):
            raise ValueError('复核结果格式不正确。')
        value = dict(payload)
        value['review_id'] = review_id
        created_at = value.get('created_at') or _now()
        value['created_at'] = created_at
        identity = value.get('id') or 'rc-' + uuid.uuid4().hex[:24]
        value['id'] = identity
        with self._connection() as conn:
            conn.execute('INSERT INTO review_rechecks VALUES (?,?,?,?)',
                         (identity, review_id, created_at, _dump(value)))
        return identity

    def list_review_rechecks(self, review_id, limit=50):
        with self._connection() as conn:
            rows = conn.execute('SELECT data FROM review_rechecks WHERE review_id=? '
                                'ORDER BY created_at DESC,id DESC LIMIT ?',
                                (review_id, max(1, min(500, int(limit))))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_recheck_map(self, review_id):
        """Newest verdict per annotation index, so a later recheck supersedes."""
        result = {}
        for record in self.list_review_rechecks(review_id):
            verdicts = record.get('verdicts') if isinstance(record.get('verdicts'), dict) else {}
            for key, verdict in verdicts.items():
                index = int(key) if str(key).isdigit() else None
                if index is None or index in result or not isinstance(verdict, dict):
                    continue
                merged = dict(verdict)
                merged.setdefault('created_at', record.get('created_at', ''))
                merged['record_id'] = record.get('id', '')
                result[index] = merged
        return result

    def _validate_recheck_restore(self, source, tables):
        if 'review_rechecks' not in tables:
            return
        reviews = {row[0] for row in source.execute('SELECT id FROM reviews')}
        for identity, review_id, created_at, data in source.execute(
                'SELECT id,review_id,created_at,data FROM review_rechecks'):
            if not isinstance(identity, str) or not identity.strip() or len(identity) > 512:
                raise ValueError('备份的复核记录标识不正确。')
            if review_id not in reviews:
                raise ValueError('备份的复核记录关联了不存在的训练记录。')
            if not isinstance(created_at, str) or not 1 <= len(created_at) <= 100:
                raise ValueError('备份的复核记录时间不正确。')
            try:
                value = json.loads(data)
            except (TypeError, ValueError):
                raise ValueError('备份的复核记录内容不正确。') from None
            verdicts = value.get('verdicts') if isinstance(value, dict) else None
            if not isinstance(verdicts, dict):
                raise ValueError('备份的复核记录内容不正确。')
            for key, verdict in verdicts.items():
                if not str(key).isdigit() or not isinstance(verdict, dict):
                    raise ValueError('备份的复核记录内容不正确。')
                if verdict.get('decision') not in ('uphold', 'revise', 'unclear'):
                    raise ValueError('备份的复核判定不正确。')

    def usage_details(self, limit=100, offset=0):
        """Recorded model usage, newest first; ``data`` keeps the original payload."""
        limit = max(0, min(10000, int(limit)))
        offset = max(0, int(offset))
        if not limit:
            return []
        with self._connection() as conn:
            rows = conn.execute('SELECT id,input_tokens,output_tokens,total_tokens,data '
                                'FROM usage ORDER BY id DESC LIMIT ? OFFSET ?',
                                (limit, offset)).fetchall()
        return [{'id': row[0], 'input_tokens': row[1], 'output_tokens': row[2],
                 'total_tokens': row[3], 'data': json.loads(row[4])} for row in rows]

    def save_analysis(self, article_id, analysis, version='1'):
        with self._connection() as conn:
            conn.execute('INSERT INTO analyses VALUES (?,?,?) ON CONFLICT(article_id,version) DO UPDATE SET data=excluded.data', (article_id, version, _dump(analysis)))

    def get_analysis(self, article_id, version='1'):
        with self._connection() as conn:
            row = conn.execute('SELECT data FROM analyses WHERE article_id=? AND version=?', (article_id, version)).fetchone()
        return json.loads(row[0]) if row else None

    def save_card(self, card, *, expected_card=_NO_EXPECTED_CARD):
        value = dict(card)
        value.setdefault('id', str(uuid.uuid4()))
        value['enabled'] = bool(value.get('enabled', False))
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,'article',value['id'])
            row=conn.execute('SELECT data FROM cards WHERE id=?',(value['id'],)).fetchone()
            current=json.loads(row[0]) if row else None
            if expected_card is not _NO_EXPECTED_CARD and current!=expected_card:
                raise MethodConflictError('方法已在其他窗口更新，请保留修改并重新核对。')
            conn.execute('INSERT INTO cards VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,data=excluded.data', (value['id'], int(value['enabled']), _dump(value)))
        return value['id']

    def list_cards(self, enabled_only=False,*,include_deleted=False):
        with self._connection() as conn:
            clauses=["NOT EXISTS (SELECT 1 FROM method_library_purged p WHERE p.kind='article' AND p.method_id=cards.id)"]
            if enabled_only:clauses.append('enabled=1')
            if not include_deleted:
                clauses.append("NOT EXISTS (SELECT 1 FROM method_library_trash t WHERE t.kind='article' AND t.method_id=cards.id)")
            return [json.loads(row[0]) for row in conn.execute('SELECT data FROM cards'+
                (' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY id')]

    def set_card_enabled(self, id, enabled, *, expected_card=_NO_EXPECTED_CARD):
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_live_method(conn,'article',id)
            row = conn.execute('SELECT data FROM cards WHERE id=?', (id,)).fetchone()
            current=json.loads(row[0]) if row else None
            if expected_card is not _NO_EXPECTED_CARD and current!=expected_card:
                raise MethodConflictError('方法已在其他窗口更新，请重新核验后再启用或停用。')
            if row:
                value = json.loads(row[0])
                value['enabled'] = bool(enabled)
                conn.execute('UPDATE cards SET enabled=?,data=? WHERE id=?', (int(bool(enabled)), _dump(value), id))

    def record_usage(self, usage):
        input_tokens = max(0, int(usage.get('input_tokens', 0) or 0))
        output_tokens = max(0, int(usage.get('output_tokens', 0) or 0))
        total = max(0, int(usage.get('total_tokens', input_tokens + output_tokens) or 0))
        with self._connection() as conn:
            conn.execute('INSERT INTO usage(input_tokens,output_tokens,total_tokens,data) VALUES (?,?,?,?)', (input_tokens, output_tokens, total, _dump(usage)))

    def usage_summary(self):
        with self._connection() as conn:
            row = conn.execute('SELECT count(*),coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0),coalesce(sum(total_tokens),0) FROM usage').fetchone()
        return dict(zip(('requests', 'input_tokens', 'output_tokens', 'total_tokens'), row))
