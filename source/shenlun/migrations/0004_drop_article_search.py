"""Drop the trigram index: ``instr`` alone decides every article search.

The index cost 141.5 MB to narrow a candidate set that the substring test then
re-checked, so the table is removed here and ``Store`` no longer creates it.
Dropping a virtual table also drops its shadow tables; the freed pages stay in
the file until ``Store.compact`` rebuilds it.
"""


def apply(conn):
    conn.execute('DROP TABLE IF EXISTS article_search')
