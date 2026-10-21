"""Article list projections: derived year/has_body columns and the covering index."""


def apply(conn):
    existing = {row[1] for row in conn.execute('PRAGMA table_info(articles)')}
    if not existing:
        return
    added = [name for name in ('year', 'has_body') if name not in existing]
    for name in added:
        conn.execute('ALTER TABLE articles ADD COLUMN ' + name + ' INTEGER NOT NULL DEFAULT 0')
    if added and 'data' in existing:
        # One SQL pass; SQLite parses each stored JSON once in C.
        conn.execute("""UPDATE articles SET
            year=CAST(coalesce(json_extract(data,'$.year'),0) AS INTEGER),
            has_body=CASE WHEN length(trim(coalesce(json_extract(data,'$.body'),'')))>0
                THEN 1 ELSE 0 END""")
    columns = existing | set(added)
    if 'year' in columns:
        conn.execute('CREATE INDEX IF NOT EXISTS article_year ON articles(year)')
    if {'id', 'title', 'journal', 'kind', 'status', 'publication', 'year', 'has_body'} <= columns:
        conn.execute("""CREATE INDEX IF NOT EXISTS article_summary_scan ON articles(
            publication DESC,title ASC,id ASC,journal,kind,status,year,has_body)""")
