"""Question-bank projections: question type and the retry flag."""


def apply(conn):
    existing = {row[1] for row in conn.execute('PRAGMA table_info(question_bank)')}
    if not existing:
        return
    added = []
    if 'question_type' not in existing:
        conn.execute("ALTER TABLE question_bank ADD COLUMN question_type TEXT NOT NULL DEFAULT ''")
        added.append('question_type')
    if 'needs_retry' not in existing:
        conn.execute('ALTER TABLE question_bank ADD COLUMN needs_retry INTEGER NOT NULL DEFAULT 0')
        added.append('needs_retry')
    if added and 'data' in existing:
        conn.execute("""UPDATE question_bank SET
            question_type=coalesce(json_extract(data,'$.submission.question_type'),''),
            needs_retry=CASE WHEN json_extract(data,'$.needs_retry') IN (1,'true')
                THEN 1 ELSE 0 END""")
    conn.execute('CREATE INDEX IF NOT EXISTS question_type_index ON question_bank(question_type)')
    if 'updated_at' in existing:
        conn.execute('CREATE INDEX IF NOT EXISTS question_recent ON question_bank(updated_at DESC,id DESC)')
