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


def insert_article_row(conn, value, search_text):
    """Single definition of the article insert so column changes stay in one place."""
    columns = ARTICLE_SOURCE_COLUMNS + ('search_text', 'data', 'year', 'has_body')
    conn.execute('INSERT INTO articles(' + ','.join(columns) + ') VALUES (' +
                 ','.join('?' * len(columns)) + ') ON CONFLICT(id) DO UPDATE SET '
                 'title=excluded.title,journal=excluded.journal,kind=excluded.kind,'
                 'status=excluded.status,fingerprint=excluded.fingerprint,'
                 'publication=excluded.publication,search_text=excluded.search_text,'
                 'data=excluded.data,year=excluded.year,has_body=excluded.has_body',
                 tuple(value[key] for key in ARTICLE_SOURCE_COLUMNS)
                 + (search_text, _dump(value), int(value.get('year') or 0),
                    1 if str(value.get('body') or '').strip() else 0))


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
                    year INTEGER NOT NULL DEFAULT 0, has_body INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS article_filters ON articles(journal,kind);
                CREATE INDEX IF NOT EXISTS article_fingerprints ON articles(fingerprint);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analyses (article_id TEXT NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(article_id,version));
                CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, data TEXT NOT NULL);
            ''')
            self.fts_available = False
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS article_search USING fts5(article_id UNINDEXED, text, tokenize='trigram')")
                self.fts_available = True
                # Populate only on upgrade from a database without an index.
                if conn.execute('SELECT count(*) FROM article_search').fetchone()[0] != conn.execute('SELECT count(*) FROM articles').fetchone()[0]:
                    conn.execute('DELETE FROM article_search')
                    conn.execute('INSERT INTO article_search(article_id,text) SELECT id,search_text FROM articles')
            except sqlite3.OperationalError:
                # Older SQLite builds keep literal search available.
                pass
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
        search_text = '\n'.join(value[key] for key in ('title', 'author', 'journal', 'body'))
        with self._connection() as conn:
            insert_article_row(conn, value, search_text)
            if self.fts_available:
                conn.execute('DELETE FROM article_search WHERE article_id=?', (value['id'],))
                conn.execute('INSERT INTO article_search(article_id,text) VALUES (?,?)', (value['id'], search_text))
        return value['id']

    def get_article(self, id: str):
        with self._connection() as conn:
            row = conn.execute('SELECT data FROM articles WHERE id=?', (id,)).fetchone()
        return json.loads(row[0]) if row else None

    ARTICLE_SUMMARY_COLUMNS = ('id', 'title', 'journal', 'year', 'kind', 'status',
                               'publication', 'has_body')

    def _article_clauses(self, query='', journal='', kind='', year=''):
        clauses, params = [], []
        if query:
            # All syntax characters are quoted as literal text; short Chinese
            # searches use instr because the trigram tokenizer needs 3 chars.
            if self.fts_available and re.fullmatch(r'[\w\s]{3,}', query, re.UNICODE):
                clauses.append('id IN (SELECT article_id FROM article_search WHERE article_search MATCH ?)')
                params.append('"' + query.replace('"', '""') + '"')
            clauses.append('instr(search_text,?) > 0')
            params.append(query)
        for field, value in (('journal', journal), ('kind', kind)):
            if value:
                clauses.append(field + '=?')
                params.append(value)
        if str(year).strip():
            clauses.append('year=?')
            params.append(int(year))
        return clauses, params

    def list_articles(self, query='', journal='', kind='', limit=200, year='', offset=0):
        clauses, params = self._article_clauses(query, journal, kind, year)
        sql = 'SELECT data FROM articles' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        sql += ' ORDER BY publication DESC,title ASC,id ASC LIMIT ? OFFSET ?'
        params += [max(0, int(limit)), max(0, int(offset))]
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(sql, params)]

    def list_article_summaries(self, query='', journal='', kind='', year='', limit=200, offset=0):
        """Scalar columns only, so a list view never parses an article body."""
        clauses, params = self._article_clauses(query, journal, kind, year)
        sql = 'SELECT ' + ','.join(self.ARTICLE_SUMMARY_COLUMNS) + ' FROM articles'
        sql += (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        sql += ' ORDER BY publication DESC,title ASC,id ASC LIMIT ? OFFSET ?'
        params += [max(0, int(limit)), max(0, int(offset))]
        result = []
        with self._connection() as conn:
            for row in conn.execute(sql, params):
                value = dict(zip(self.ARTICLE_SUMMARY_COLUMNS, row))
                value['has_body'] = bool(value['has_body'])
                result.append(value)
        return result

    def count_articles(self, query='', journal='', kind='', year=''):
        clauses, params = self._article_clauses(query, journal, kind, year)
        sql = 'SELECT count(*) FROM articles' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        with self._connection() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

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
