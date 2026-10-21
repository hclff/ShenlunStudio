"""Short restore-safe transaction boundary for explicit local state writes."""
from contextlib import contextmanager
import json


@contextmanager
def state_write(store, expected_generation):
    # The OS restore guard is not reentrant: callers must not hold it already.
    with store.assessment_restore_guard():
        with store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT data FROM settings WHERE key='assessment_generation'").fetchone()
            if row is None or json.loads(row[0]) != expected_generation:
                raise ValueError('数据库已恢复，请重新载入后确认。')
            yield conn
