"""Article tags as a derived column: lower-case, de-duplicated, newline joined.

The ``data`` JSON stays authoritative; the column only exists so filtering is a
single ``instr`` test instead of parsing every body. Values are joined with
newlines and tested as ``char(10) || tag || char(10)``, so "理论" never matches
a "理论评论" tag. The covering index is rebuilt with the new trailing column.
"""
import json
import re


def normalize(tags):
    if tags is None:
        return ''
    if isinstance(tags, str):
        items = re.split(r'[\s,，、;；]+', tags)
    else:
        items = list(tags)
    return '\n'.join(sorted({str(item).strip().lower() for item in items if str(item).strip()}))


def apply(conn):
    existing = {row[1] for row in conn.execute('PRAGMA table_info(articles)')}
    if not existing:
        return
    if 'tags' not in existing:
        # Appended as the last column: restore validation accepts exactly
        # ``required + ('year', 'has_body', 'tags')`` for older backups.
        conn.execute("ALTER TABLE articles ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        rows = conn.execute("SELECT id,data FROM articles WHERE "
                            "json_type(data,'$.tags')='array'").fetchall()
        for article_id, data in rows:
            tags = normalize(json.loads(data).get('tags'))
            if tags:
                conn.execute('UPDATE articles SET tags=? WHERE id=?', (tags, article_id))
    # A covering index must list every projected column, or the list view falls
    # back to a table scan (the reason 0001 exists at all).
    needed = {'id', 'title', 'journal', 'kind', 'status', 'publication',
              'year', 'has_body', 'tags'}
    current = {row[1] for row in conn.execute('PRAGMA table_info(articles)')}
    if needed <= current:
        conn.execute('DROP INDEX IF EXISTS article_summary_scan')
        conn.execute("""CREATE INDEX article_summary_scan ON articles(
            publication DESC,title ASC,id ASC,journal,kind,status,year,has_body,tags)""")
