"""Recheck records for contested annotations, kept outside the review payload.

The original review is never rewritten: a recheck appends one row here and the
window joins it back by annotation index.
"""


def apply(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS review_rechecks (id TEXT PRIMARY KEY, '
                 'review_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS review_rechecks_review ON review_rechecks(review_id)')
