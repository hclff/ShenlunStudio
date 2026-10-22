"""Extract writing methods for a deterministic sample of the article corpus.

The GUI extract button buys every article in the current filter. For a first
taste of candidate quality that is too expensive, so this script walks the same
``extract_methods`` service path on a stable sample: sort by journal/publication
/id, then take every N-th row.

Two safety properties matter more than speed:

* ``--dry-run`` (the default) never opens ``Store`` and therefore never
  migrates or writes the database; it only reads rows and reports cost.
* A real run backs the database up before the schema upgrade that ``Store``
  performs, and it never asks for confirmation interactively, so it can be
  started once and left alone.

Interrupting is free: the service caches every batch under
``methods-<hash>-batchN``, so a rerun pays only for the missing batches.
"""
import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from shenlun.service import AppService  # noqa: E402
from shenlun.storage import Store  # noqa: E402

# Same order as ``list_article_summaries`` so the sample matches what the user
# sees in the corpus table, and stays identical between runs.
SAMPLE_ORDER = 'ORDER BY journal, publication DESC, id'
CHUNK_CHARS = 16000
SESSION_BATCH = 20


def default_database():
    import os
    base = os.environ.get('LOCALAPPDATA')
    root = Path(base) if base else Path.home() / 'AppData' / 'Local'
    return root / 'ShenlunStudio' / 'studio.db'


def _read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)


def _safe_article(payload):
    try:
        article = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return article if isinstance(article, dict) else {}


def _batches(article):
    """Batch count as ``service.text._chunks`` sees it, without importing Qt."""
    paragraphs = [p.get('text') or '' for p in article.get('paragraphs') or [] if isinstance(p, dict)]
    total, size = 1, 0
    for text in paragraphs:
        if size and size + len(text) > CHUNK_CHARS:
            total += 1
            size = 0
        size += len(text)
    return total


def sample_rows(path, every=10, limit=0, min_chars=300):
    """Rows of the sample, read-only; ``every=1`` means the whole corpus.

    Rows below ``min_chars`` are dropped before stepping, because picture pages
    and one-line notices cost a request while yielding nothing transferable.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'数据库不存在：{path}')
    every = max(1, int(every))
    with closing(_read_only(path)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        rows = conn.execute(
            'SELECT id,title,journal,kind,year,data FROM articles '
            "WHERE status='available' AND has_body>0 " + SAMPLE_ORDER).fetchall()
    usable = []
    for row in rows:
        article = _safe_article(row['data'])
        body = str(article.get('body') or '')
        if len(body) < int(min_chars):
            continue
        usable.append({'id': row['id'], 'title': row['title'], 'journal': row['journal'],
                       'kind': row['kind'], 'year': row['year'], 'characters': len(body),
                       'estimated_input_tokens': len(body.encode('utf8')) // 2,
                       'estimated_batches': _batches(article)})
    picked = usable[::every]
    if limit and int(limit) > 0:
        picked = picked[:int(limit)]
    return {'db': str(path), 'total': total, 'available': len(rows), 'usable': len(usable),
            'every': every, 'min_chars': int(min_chars), 'sampled': len(picked), 'items': picked}


def select_rows(path, ids):
    """Rows for an explicit id list, in the order given.

    Used to retry the articles that failed output validation; ids that no longer
    exist come back in ``missing`` so a retry never silently skips a failure.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'数据库不存在：{path}')
    wanted = [str(i).strip() for i in ids if str(i).strip()]
    with closing(_read_only(path)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        rows = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            marks = ','.join('?' * len(chunk))
            for row in conn.execute('SELECT id,title,journal,kind,year,data FROM articles '
                                    f'WHERE id IN ({marks})', chunk):
                rows[row['id']] = row
    items, missing = [], []
    for aid in wanted:
        row = rows.get(aid)
        if row is None:
            missing.append(aid)
            continue
        article = _safe_article(row['data'])
        body = str(article.get('body') or '')
        items.append({'id': row['id'], 'title': row['title'], 'journal': row['journal'],
                      'kind': row['kind'], 'year': row['year'], 'characters': len(body),
                      'estimated_input_tokens': len(body.encode('utf8')) // 2,
                      'estimated_batches': _batches(article)})
    return {'db': str(path), 'total': total, 'available': len(items), 'usable': len(items),
            'every': 1, 'min_chars': 0, 'sampled': len(items), 'missing': missing, 'items': items}


def _failed_ids(report_path):
    """Ids listed as failed in a previous ``--apply`` report."""
    value = json.loads(Path(report_path).read_text(encoding='utf8'))
    ids = value.get('failed_ids')
    if not ids:
        ids = [item.get('id', '') for item in value.get('failed') or []]
    return [str(i) for i in ids if str(i).strip()]


def _ids_from_file(path):
    """One article id per line; columns after the id are ignored.

    ``utf-8-sig`` because a list pasted from PowerShell's ``Set-Content`` carries
    a BOM, which would otherwise glue itself to the first id.
    """
    text = Path(path).read_text(encoding='utf-8-sig')
    return [line.strip().split()[0] for line in text.splitlines() if line.strip()]


def summarize(sample):
    return {'articles': sample['sampled'],
            'characters': sum(i['characters'] for i in sample['items']),
            'estimated_input_tokens': sum(i['estimated_input_tokens'] for i in sample['items']),
            'estimated_batches': sum(i['estimated_batches'] for i in sample['items'])}


def stored_config(path):
    """``model_config`` from the settings table, read without migrating."""
    with closing(_read_only(path)) as conn:
        try:
            row = conn.execute("SELECT data FROM settings WHERE key='model_config'").fetchone()
        except sqlite3.Error:
            return {}
    if not row:
        return {}
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve_config(path, args):
    config = stored_config(path)
    if args.base_url:
        config['base_url'] = args.base_url
    if args.model:
        config['model'] = args.model
    # One method card is a large JSON object; the app default of 5000 output
    # tokens truncates it for long articles, and the per-task budget resets only
    # when the client is recreated, so a 208-article run needs a bigger ceiling.
    config['max_output_tokens'] = max(int(config.get('max_output_tokens') or 0), args.max_output)
    config['session_token_budget'] = max(int(config.get('session_token_budget') or 0), args.budget)
    key = args.api_key or _environment_key()
    return config, key


def _environment_key():
    import os
    return os.environ.get('SHENLUN_API_KEY', '')


def _format_seconds(seconds):
    if seconds < 60:
        return f'{seconds:.0f} 秒'
    minutes = seconds / 60
    if minutes < 60:
        return f'{minutes:.0f} 分钟'
    return f'{minutes / 60:.1f} 小时'


def run(path, sample, config, key, *, progress_note=None):
    """Extract the sample through the real service, resumable by cache."""
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = path.with_name(f'{path.stem}.method-extraction-backup-{stamp}.db')
    # ``Store`` takes its own pre-migration backup when it upgrades the schema;
    # this extra snapshot is the state the extraction actually starts from and
    # is registered in the app's backup list.
    store = Store(path)
    backup_path = store.backup_to(backup, register=True, backup_kind='manual')
    report = {'db': str(path), 'backup': str(backup_path), 'started_at': stamp,
              'model': config.get('model', ''), 'base_url': config.get('base_url', ''),
              'requested': sample['sampled'], 'done': 0, 'processed': 0, 'skipped': 0,
              'failed': [], 'candidates': 0, 'batches': 0, 'cached_ids': [], 'failed_ids': []}
    service = AppService(store, config, key)
    started = time.monotonic()
    ids = [item['id'] for item in sample['items']]
    total = len(ids)
    for start in range(0, total, SESSION_BATCH):
        batch = ids[start:start + SESSION_BATCH]
        def progress(message, _start=start):
            if progress_note:
                progress_note(f'[{_start + 1}-{min(_start + SESSION_BATCH, total)}/{total}] {message}')
        try:
            result = service.extract_methods(batch, progress=progress)
        except KeyboardInterrupt:
            report['cancelled'] = True
            break
        except Exception as error:  # transport or JSON failure aborts the session
            report['error'] = f'{type(error).__name__}: {error}'
            break
        report['processed'] += result.get('processed', 0)
        report['skipped'] += result.get('skipped', 0)
        report['candidates'] += result.get('candidates', 0)
        report['batches'] += result.get('batches', 0)
        report['cached_ids'].extend(result.get('skipped_ids', []))
        for item in result.get('failed', []):
            report['failed'].append({'id': item.get('id', ''), 'title': item.get('title', ''),
                                     'error': item.get('error', '')})
            report['failed_ids'].append(item.get('id', ''))
        report['done'] = min(start + SESSION_BATCH, total)
        elapsed = time.monotonic() - started
        rate = elapsed / max(1, report['done'])
        remaining = rate * (total - report['done'])
        if progress_note:
            progress_note(f'进度 {report["done"]}/{total}，已用 {_format_seconds(int(elapsed))}，'
                          f'预计剩余 {_format_seconds(int(remaining))}')
    report['elapsed_seconds'] = round(time.monotonic() - started, 1)
    report['finished_at'] = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return report


def _print_human(sample, args, config, key):
    stats = summarize(sample)
    print(f"数据库：{sample['db']}")
    if args.retry_report or args.ids_file:
        print(f"按清单处理 {sample['sampled']} 篇，全文库 {sample['total']} 篇")
        if sample.get('missing'):
            print(f"  其中已不存在：{len(sample['missing'])} 篇")
    else:
        print(f"可用且有正文 {sample['available']} 篇，全文库 {sample['total']} 篇；"
              f"其中正文≥{sample['min_chars']} 字 {sample['usable']} 篇，"
              f"每 {sample['every']} 篇取 1 篇 → 采样 {sample['sampled']} 篇")
    print(f"采样字符 {stats['characters']:,}；估算输入 token {stats['estimated_input_tokens']:,}；"
          f"请求批次 {stats['estimated_batches']}")
    print(f"模型：{config.get('model') or '（未配置）'}；端点：{config.get('base_url') or '（未配置）'}；"
          f"密钥：{'已读取' if key else '缺失'}")
    if not args.verbose:
        print('前 5 篇：')
        for item in sample['items'][:5]:
            print(f"  - {item['id']}  {item['journal']}/{item['kind']}  {item['title']}（{item['characters']} 字）")
    else:
        for item in sample['items']:
            print(f"  - {item['id']}  {item['journal']}/{item['kind']}  {item['title']}（{item['characters']} 字）")


def main(argv=None):
    parser = argparse.ArgumentParser(description='按固定步长抽样提炼文章方法（默认只试运行）')
    parser.add_argument('--db', type=Path, default=None, help='数据库路径，默认本机 studio.db')
    parser.add_argument('--every', type=int, default=10, help='每 N 篇取 1 篇，1 表示全量')
    parser.add_argument('--limit', type=int, default=0, help='最多处理多少篇，0 表示不限')
    parser.add_argument('--min-chars', type=int, default=300, help='正文少于该字数不采样，默认 300')
    parser.add_argument('--apply', action='store_true', help='实际调用模型并写入方法库（默认只试运行）')
    parser.add_argument('--api-key', default='', help='临时覆盖密钥；更推荐 SHENLUN_API_KEY 环境变量')
    parser.add_argument('--base-url', default='', help='临时覆盖端点，如 https://api.deepseek.com/v1')
    parser.add_argument('--model', default='', help='临时覆盖模型名，如 deepseek-chat')
    parser.add_argument('--max-output', type=int, default=8000, help='单次输出 token 上限，默认 8000')
    parser.add_argument('--budget', type=int, default=9_900_000, help='整轮任务 token 预算，默认 990 万')
    parser.add_argument('--retry-report', type=Path, default=None,
                        help='按上一份报告里的失败篇目重跑，忽略 --every/--limit')
    parser.add_argument('--ids-file', type=Path, default=None,
                        help='按文本清单（每行一个 id）重跑，优先于 --retry-report')
    parser.add_argument('--report', type=Path, default=None, help='结果 JSON 路径，默认 validation/ 下')
    parser.add_argument('--json', action='store_true', help='只输出机器可读结果')
    parser.add_argument('--verbose', action='store_true', help='列出全部采样条目')
    args = parser.parse_args(argv)

    path = args.db or default_database()
    try:
        if args.ids_file:
            sample = select_rows(path, _ids_from_file(args.ids_file))
        elif args.retry_report:
            sample = select_rows(path, _failed_ids(args.retry_report))
        else:
            sample = sample_rows(path, every=args.every, limit=args.limit, min_chars=args.min_chars)
    except (OSError, sqlite3.Error) as error:
        print(f'{path}：{error}')
        return 1

    config, key = _resolve_config(path, args)
    if not args.json:
        _print_human(sample, args, config, key)

    if not args.apply:
        if not args.json:
            missing = [name for name, value in (('端点', config.get('base_url')), ('模型', config.get('model'))) if not value]
            note = '、'.join(missing)
            print('试运行未写入。' + (f'还缺：{note}；' if note else '')
                  + '确认后用 --apply 执行，首次执行会先备份再升级数据库。')
        else:
            print(json.dumps({'sample': sample, 'summary': summarize(sample), 'apply': False},
                             ensure_ascii=False, indent=2))
        return 0

    if not key:
        print('缺少 API 密钥：请设置 SHENLUN_API_KEY 环境变量，或用 --api-key 传入（不落盘）。')
        return 2
    if not config.get('base_url') or not config.get('model'):
        print('缺少模型配置：请在应用「设置 → 模型」中填写端点与模型，或用 --base-url/--model 传入。')
        return 2

    def note(message):
        if not args.json:
            print(message, flush=True)

    report = run(path, sample, config, key, progress_note=note)
    stamp = report['finished_at']
    target = args.report or PROJECT / 'validation' / f'method-extraction-sample-{stamp}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**report, 'sample': sample['items']}, ensure_ascii=False, indent=2),
                      encoding='utf8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report.get('error') else 1
    print(f"完成：请求 {report['requested']} 篇；处理 {report['processed']}；复用 {report['skipped']}；"
          f"失败 {len(report['failed'])}；候选卡 {report['candidates']}；计费批次 {report['batches']}")
    for item in report['failed']:
        print(f"  失败 {item['id']}  {item['title']}：{item['error']}")
    if report.get('error'):
        print(f"中止：{report['error']}（已完成的批次已缓存，重跑只补缺失批次）")
    print(f"备份：{report['backup']}")
    print(f"报告：{target}")
    return 0 if not report.get('error') else 1


if __name__ == '__main__':
    raise SystemExit(main())
