"""Ordered, idempotent SQLite upgrades stamped with ``PRAGMA user_version``.

Each step is a small module with ``apply(conn)``; steps only add columns, indexes
and backfill derived values, so an interrupted upgrade can simply be retried.
``ensure_schema`` takes the pre-migration backup, runs every pending step in one
transaction and refuses a database written by a newer application.
"""
import importlib
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 3


class MigrationError(RuntimeError):
    """An upgrade could not be completed; the database is left untouched."""


class MigrationTooNewError(MigrationError):
    def __init__(self, found, supported):
        self.found = found
        self.supported = supported
        super().__init__('本机数据库版本 ' + str(found) + ' 高于当前程序支持的 ' + str(supported) +
                         '；请升级程序后再打开，原有数据未被修改。')


def _step(name):
    return importlib.import_module('.' + name, __name__).apply


STEPS = (
    (1, 'article_index', _step('0001_article_index')),
    (2, 'question_columns', _step('0002_question_columns')),
    (3, 'review_plan_columns', _step('0003_review_plan_columns')),
)


def migrate(conn, *, db_path=None):
    """Apply pending steps on an already open connection; caller owns the transaction."""
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise MigrationTooNewError(version, SCHEMA_VERSION)
    applied = []
    for number, name, step in STEPS:
        if number > version:
            try:
                step(conn)
            except Exception as exc:
                # The caller rolls back; name the step so the failure is actionable.
                raise MigrationError('数据库升级第 ' + str(number) + ' 步（' + name +
                                     '）失败：' + str(exc) +
                                     ('；数据库：' + str(db_path) if db_path else '')) from exc
            applied.append(number)
    if applied:
        # user_version cannot be parameterised.
        conn.execute('PRAGMA user_version=%d' % SCHEMA_VERSION)
    return applied


def _has_user_data(conn):
    for table in ('articles', 'question_bank', 'reviews'):
        try:
            if conn.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                return True
        except sqlite3.Error:
            continue
    return False


def _write_backup(conn, path, version):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = path.with_name(path.stem + '-迁移前备份-v' + str(version) + '-' + stamp + '.db')
    suffix = 1
    while target.exists():
        target = path.with_name(path.stem + '-迁移前备份-v' + str(version) + '-' + stamp +
                                '-' + str(suffix) + '.db')
        suffix += 1
    with sqlite3.connect(target) as destination:
        conn.backup(destination)
        destination.execute('PRAGMA journal_mode=DELETE')
    return target


def ensure_schema(path, *, backup=True):
    """Upgrade ``path`` to ``SCHEMA_VERSION`` and report what happened."""
    path = Path(path)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        conn.execute('PRAGMA busy_timeout=30000')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise MigrationTooNewError(version, SCHEMA_VERSION)
        if version == SCHEMA_VERSION:
            return {'from': version, 'to': version, 'applied': [], 'backup': None}
        backup_path = None
        if backup and _has_user_data(conn):
            backup_path = _write_backup(conn, path, version)
        conn.execute('BEGIN IMMEDIATE')
        try:
            applied = migrate(conn, db_path=path)
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        return {'from': version, 'to': SCHEMA_VERSION, 'applied': applied,
                'backup': backup_path}
    finally:
        conn.close()
