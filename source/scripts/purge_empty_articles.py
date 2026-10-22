"""Delete article rows that carry no body text, after a dry run and a backup.

The corpus keeps a row for every imported article, including the pure chart
pages whose body is an image. Those rows show up as ``has_body=0`` and can be
removed once the operator confirms the list. A candidate is only removed when
its stored JSON really has no text, so a mis-set flag never costs an article.
"""
import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from shenlun.storage import Store  # noqa: E402

# Records that still point at an article and would be left dangling. Frozen
# snapshots (reviews, eval runs, method comparisons) keep their own copy of the
# text and are deliberately not part of this guard.
REFERENCE_TABLES = (('cards', 'data'),)
CHUNK = 200


def default_database():
    base = os.environ.get('LOCALAPPDATA')
    root = Path(base) if base else Path.home() / 'AppData' / 'Local'
    return root / 'ShenlunStudio' / 'studio.db'


def _read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)


def _blank_body(article):
    """True when the stored JSON holds neither a body nor readable paragraphs."""
    if str(article.get('body') or '').strip():
        return False
    paragraphs = article.get('paragraphs') or []
    return not any(str(p.get('text') or '').strip() for p in paragraphs if isinstance(p, dict))


def _referenced(conn, ids):
    """Rows whose JSON still names one of the ids, so they must not be deleted."""
    if not ids:
        return []
    hits = []
    for table, column in REFERENCE_TABLES:
        for row in conn.execute(f'SELECT id,{column} FROM {table}'):
            payload = row[1] or ''
            for article_id in ids:
                if article_id in payload:
                    hits.append({'table': table, 'record': row[0], 'id': article_id})
    return hits


def collect(path):
    """Classify the rows without a body, without touching the database."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'数据库不存在：{path}')
    with closing(_read_only(path)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        rows = conn.execute('SELECT id,title,journal,kind,publication,status,data FROM articles '
                            'WHERE has_body=0 ORDER BY journal,title,id').fetchall()
        candidates, anomalous = [], []
        for row in rows:
            try:
                article = json.loads(row['data'])
            except (TypeError, ValueError):
                article = {}
            if not isinstance(article, dict):
                article = {}
            item = {'id': row['id'], 'title': row['title'], 'journal': row['journal'],
                    'kind': row['kind'], 'publication': row['publication'],
                    'status': row['status'], 'url': str(article.get('url') or '')}
            if _blank_body(article):
                candidates.append(item)
            else:
                anomalous.append(dict(item, reason='has_body=0 但正文不为空'))
        hits = _referenced(conn, [item['id'] for item in candidates])
    blocked = {hit['id'] for hit in hits}
    skipped = [item for item in candidates if item['id'] in blocked]
    return {'db': str(path), 'total': total,
            'candidates': [item for item in candidates if item['id'] not in blocked],
            'skipped': skipped, 'anomalous': anomalous, 'references': hits}


def _delete(conn, table, column, ids):
    for start in range(0, len(ids), CHUNK):
        batch = ids[start:start + CHUNK]
        conn.execute(f'DELETE FROM {table} WHERE {column} IN (' + ','.join('?' * len(batch)) + ')', batch)


def purge(path, *, apply=False, vacuum=False):
    """Collect candidates and, with ``apply``, back up and delete them."""
    path = Path(path)
    report = collect(path)
    report.update(deleted=0, backup='', remaining=report['total'])
    if not apply or not report['candidates']:
        return report
    store = Store(path)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = path.with_name(f'{path.stem}.empty-body-backup-{stamp}.db')
    report['backup'] = str(store.backup_to(backup, register=False, backup_kind='purge'))
    ids = [item['id'] for item in report['candidates']]
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        conn.execute('PRAGMA busy_timeout=30000')
        with conn:
            _delete(conn, 'analyses', 'article_id', ids)
            _delete(conn, 'articles', 'id', ids)
        conn.execute('PRAGMA optimize')
        report['remaining'] = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
    report['deleted'] = len(ids)
    if vacuum:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return report


def _print_human(report, apply):
    if report.get('error'):
        print(f"{report['db']}：{report['error']}")
        return
    print(f"数据库：{report['db']}")
    print(f"文章总数：{report['total']}；待删：{len(report['candidates'])}；"
          f"被引用跳过：{len(report['skipped'])}；异常保留：{len(report['anomalous'])}")
    for item in report['candidates']:
        print(f"  - {item['id']}  {item['journal']}/{item['kind']}  {item['title']}")
    for item in report['skipped']:
        print(f"  跳过 {item['id']}  {item['title']}：仍被方法卡等记录引用")
    for item in report['anomalous']:
        print(f"  保留 {item['id']}  {item['title']}：{item['reason']}")
    if apply:
        print(f"已删除 {report['deleted']} 条；剩余 {report['remaining']} 条；备份 {report['backup'] or '（无需备份）'}")
    else:
        print('试运行未写入；确认无误后加 --apply 执行删除。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='删除文章资料库中没有正文的条目（默认只试运行）')
    parser.add_argument('--db', type=Path, action='append',
                        help='数据库路径，可重复；默认本机 studio.db')
    parser.add_argument('--apply', action='store_true', help='实际删除，删除前自动备份')
    parser.add_argument('--vacuum', action='store_true', help='删除后重建数据库文件以回收空间')
    parser.add_argument('--json', action='store_true', help='输出机器可读结果')
    args = parser.parse_args(argv)
    reports, failed = [], False
    for target in args.db or [default_database()]:
        try:
            report = purge(target, apply=args.apply, vacuum=args.vacuum)
        except (OSError, sqlite3.Error, ValueError) as error:
            failed = True
            report = {'db': str(target), 'error': str(error)}
        reports.append(report)
        if not args.json:
            _print_human(report, args.apply)
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
