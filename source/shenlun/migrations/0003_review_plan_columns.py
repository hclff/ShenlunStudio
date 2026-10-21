"""Review-plan projections kept in the same row as the authoritative JSON.

The plan lives inside ``question_bank.data``; duplicating status and due date in
the same row lets the review list filter and sort in SQL. Because both are
written by the same UPDATE statement as ``data``, they cannot drift apart.
"""


def apply(conn):
    existing = {row[1] for row in conn.execute('PRAGMA table_info(question_bank)')}
    if not existing:
        return
    added = []
    if 'plan_status' not in existing:
        conn.execute("ALTER TABLE question_bank ADD COLUMN plan_status TEXT NOT NULL DEFAULT '待复习'")
        added.append('plan_status')
    if 'plan_due' not in existing:
        conn.execute("ALTER TABLE question_bank ADD COLUMN plan_due TEXT NOT NULL DEFAULT ''")
        added.append('plan_due')
    if added and 'data' in existing:
        conn.execute("""UPDATE question_bank SET
            plan_status=coalesce(json_extract(data,'$.review_plan.status'),'待复习'),
            plan_due=coalesce(json_extract(data,'$.review_plan.due_date'),'')""")
    conn.execute('CREATE INDEX IF NOT EXISTS question_plan_due ON question_bank(plan_status,plan_due)')
