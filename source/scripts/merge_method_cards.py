"""Fold near-duplicate candidate method cards listed by the cluster report.

Each candidate card carries evidence from a single article, so none of them can
clear the two-article bar. Merging same-meaning cards is what turns a sampling
run into a usable method library, and it costs nothing: ``merge_article_methods``
is the same call the candidate pool's merge button makes, and it moves sources
into the restorable deleted list instead of dropping them.

Dry-run by default. ``--apply`` snapshots the database first and reports which
merged cards would now pass ``validate_article_card`` — enabling stays a manual
step in the candidate pool.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from shenlun.method_library import merge_article_methods, validate_article_card  # noqa: E402
from shenlun.service import AppService  # noqa: E402
from shenlun.storage import Store  # noqa: E402

# ``domain.check_refs`` rejects more than 30 refs, so a 23-card cluster would
# otherwise produce a card that can never be enabled. Trimming keeps one quote
# per article first, which preserves the evidence spread the bar cares about.
MAX_REFS = 30


def trim_refs(refs, limit=MAX_REFS):
    """Keep one ref per article, then fill remaining slots in original order."""
    refs = list(refs)
    if len(refs) <= limit:
        return refs
    grouped = {}
    for ref in refs:
        article = str(ref.get('id', '')).rsplit(':p', 1)[0]
        grouped.setdefault(article, []).append(ref)
    kept = [group[0] for group in grouped.values()][:limit]
    if len(kept) < limit:
        rest = [ref for group in grouped.values() for ref in group[1:]]
        kept.extend(rest[:limit - len(kept)])
    return kept[:limit]


def load_plan(report_path, min_cards=2, min_articles=2, limit=0):
    """Clusters worth merging, plus the ones rejected before any write."""
    report = json.loads(Path(report_path).read_text(encoding='utf8'))
    actions, skipped = [], []
    for cluster in report.get('clusters') or []:
        if int(cluster.get('cards', 0)) < min_cards:
            continue
        if int(cluster.get('articles', 0)) < min_articles:
            skipped.append({'id': cluster.get('id', ''), 'cards': cluster.get('cards', 0),
                            'articles': cluster.get('articles', 0),
                            'reason': f'合并后仍不足 {min_articles} 篇证据'})
            continue
        actions.append(cluster)
    actions.sort(key=lambda item: (-item.get('cards', 0), item.get('id', '')))
    if limit:
        actions = actions[:int(limit)]
    return report, actions, skipped


def pick_target(cards):
    """Keep the card with the most refs; ties go to the shorter, more generic name."""
    return sorted(cards, key=lambda c: (-len(c.get('refs') or []),
                                        len(str(c.get('name') or '')),
                                        str(c.get('id'))))[0]


def describe_target(card):
    return {'id': card.get('id', ''), 'name': card.get('name', ''),
            'refs': len(card.get('refs') or []),
            'articles': len(card.get('source_article_ids') or [])}


def repair_refs(store, limit=MAX_REFS):
    """Trim cards that already exceed the ref ceiling; report what changed."""
    fixed = []
    for card in store.list_cards():
        refs = card.get('refs') or []
        if len(refs) <= limit:
            continue
        trimmed = trim_refs(refs, limit)
        store.save_card({**card, 'refs': trimmed}, expected_card=card)
        fixed.append({'id': card['id'], 'name': card.get('name', ''),
                      'before': len(refs), 'after': len(trimmed)})
    return fixed


def cluster_label(cluster):
    """The method name a merged cluster should carry when renaming.

    Taxonomy clusters carry ``family`` (the writing-technique name); string
    clusters only have ``names`` and fall back to the first one.
    """
    candidates = cluster.get('family') or (cluster.get('names') or [''])[0]
    return str(candidates or '').strip()


def merge_plan(store, actions, progress=None, max_refs=MAX_REFS, rename=False):
    """Apply the plan; return one record per cluster.

    ``rename`` takes the cluster's ``family`` (or ``names[0]``) as the merged card
    name. Taxonomy clusters are named after the writing technique, so the merged
    card should carry that name rather than whichever wording had the most refs.
    """
    records = []
    cards = {card['id']: card for card in store.list_cards()}
    for cluster in actions:
        members = [cards[cid] for cid in cluster.get('card_ids') or [] if cid in cards]
        if len(members) < 2:
            records.append({'cluster': cluster.get('id', ''), 'result': 'missing',
                            'cards': len(members), 'reason': '清单中的候选卡已不存在或已被合并'})
            continue
        target = pick_target(members)
        sources = [card['id'] for card in members if card['id'] != target['id']]
        try:
            result = merge_article_methods(store, target['id'], sources)
        except (ValueError, KeyError) as error:
            records.append({'cluster': cluster.get('id', ''), 'result': 'failed',
                            'cards': len(members), 'reason': str(error)})
            continue
        merged = next((card for card in store.list_cards() if card['id'] == target['id']), None)
        label = cluster_label(cluster) if rename else ''
        if merged:
            updated = dict(merged)
            if len(updated.get('refs') or []) > max_refs:
                updated['refs'] = trim_refs(updated['refs'], max_refs)
            if label and updated.get('name') != label:
                updated['name'] = label
            if updated != merged:
                store.save_card(updated, expected_card=merged)
                merged = updated
        try:
            reason = validate_article_card(store, merged) if merged else '合并后的方法不存在'
        except ValueError as error:
            reason = str(error)
        record = {'cluster': cluster.get('id', ''), 'result': 'merged',
                  'keep': describe_target(target), 'folded': result['merged'],
                  'refs': len((merged or {}).get('refs') or []),
                  'articles': len((merged or {}).get('source_article_ids') or []),
                  'enable_ready': reason is None, 'blocked_reason': reason or ''}
        if result.get('failed'):
            record['delete_failed'] = result['failed']
        records.append(record)
        if progress:
            progress(f"合并 {record['keep']['name']}：{result['merged'] + 1} 张 → 1 张，"
                     f"引用 {result['refs']} 条，{'可启用' if reason is None else '仍不可启用'}")
        # ``cards`` is stale after each write; refresh lazily for later clusters.
        if len(records) % 20 == 0:
            cards = {card['id']: card for card in store.list_cards()}
    return records


def summarize(records):
    merged = [record for record in records if record['result'] == 'merged']
    return {'clusters': len(records), 'merged_clusters': len(merged),
            'folded_cards': sum(record.get('folded', 0) for record in merged),
            'enable_ready': sum(1 for record in merged if record.get('enable_ready')),
            'still_blocked': sum(1 for record in merged if not record.get('enable_ready')),
            'failed': [record for record in records if record['result'] != 'merged']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='合并聚类清单里的同义候选方法（默认只试运行）')
    parser.add_argument('--db', type=Path, required=True, help='数据库路径')
    parser.add_argument('--clusters', type=Path, default=None,
                        help='cluster_method_cards.py 产出的 JSON（--repair-refs 时不需要）')
    parser.add_argument('--min-cards', type=int, default=2, help='每簇至少几张卡才合并，默认 2')
    parser.add_argument('--min-articles', type=int, default=2, help='合并后至少几篇证据，默认 2')
    parser.add_argument('--limit', type=int, default=0, help='最多处理多少簇，0 表示不限')
    parser.add_argument('--max-refs', type=int, default=MAX_REFS,
                        help=f'单张方法卡的引用上限，默认 {MAX_REFS}（产品校验的硬上限）')
    parser.add_argument('--repair-refs', action='store_true',
                        help='只修复已超引用上限的卡，不做合并')
    parser.add_argument('--enable-ready', action='store_true',
                        help='合并后直接把通过校验的卡启用（默认留给候选池人工确认）')
    parser.add_argument('--disable-first', action='store_true',
                        help='合并前先停用全部已启用卡（产品规则不允许合并启用态的方法），完成后可用 --enable-ready 统一恢复')
    parser.add_argument('--rename-to-family', action='store_true',
                        help='合并后把方法名改成清单里的族名（classify_methods.py 的清单带 family 字段）')
    parser.add_argument('--apply', action='store_true', help='真正写入（默认只列出计划）')
    parser.add_argument('--report', type=Path, default=None, help='结果 JSON 路径，默认 validation/ 下')
    args = parser.parse_args(argv)

    if args.enable_ready:
        store = Store(args.db)
        pending = []
        for card in store.list_cards():
            if card.get('enabled'):
                continue
            try:
                if validate_article_card(store, card) is None:
                    pending.append(card)
            except ValueError:
                continue
        print(f"通过校验、可启用的未启用卡：{len(pending)} 张")
        if not args.apply:
            print('试运行未写入。确认后加 --apply 执行，会先备份数据库。')
            return 0
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = args.db.with_name(f'{args.db.stem}-批量启用前-{stamp}.db')
        backup_path = store.backup_to(backup, register=True, backup_kind='manual')
        service = AppService(store, {}, '')
        enabled = 0
        for card in pending:
            try:
                service.enable_card(card['id'], True)
                enabled += 1
            except ValueError as error:
                print(f"  启用失败 {card.get('name') or card['id']}：{error}")
        print(f"已启用 {enabled} 张（原可启用 {len(pending)} 张）")
        print(f"备份：{backup_path}")
        return 0

    if args.repair_refs:
        if not args.apply:
            store = Store(args.db)
            over = [card for card in store.list_cards() if len(card.get('refs') or []) > args.max_refs]
            print(f"超出引用上限 {args.max_refs} 的卡 {len(over)} 张；加 --apply 执行裁剪。")
            return 0
        store = Store(args.db)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = args.db.with_name(f'{args.db.stem}-修复引用前-{stamp}.db')
        backup_path = store.backup_to(backup, register=True, backup_kind='manual')
        fixed = repair_refs(store, args.max_refs)
        print(f"已裁剪 {len(fixed)} 张卡的引用")
        for item in fixed:
            print(f"  - {item['name']}：{item['before']} → {item['after']}")
        print(f"备份：{backup_path}")
        return 0

    if not args.clusters:
        parser.error('需要 --clusters，或改用 --repair-refs')
    report, actions, skipped = load_plan(args.clusters, min_cards=args.min_cards,
                                         min_articles=args.min_articles, limit=args.limit)
    print(f"清单：{args.clusters}（阈值 {report.get('threshold')}）")
    print(f"待合并 {len(actions)} 簇，覆盖 {sum(item.get('cards', 0) for item in actions)} 张卡；"
          f"跳过 {len(skipped)} 簇")
    for cluster in actions[:5]:
        names = cluster.get('names') or []
        print(f"  - {cluster.get('cards')} 张 / {cluster.get('articles')} 篇：{names[0] if names else ''}"
              f"（含 {'、'.join(names[1:4])}）" if len(names) > 1 else f"  - {names[0] if names else ''}")

    if not args.apply:
        print('试运行未写入。确认后加 --apply 执行，会先备份数据库。')
        return 0

    store = Store(args.db)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup = args.db.with_name(f'{args.db.stem}-合并候选方法前-{stamp}.db')
    backup_path = store.backup_to(backup, register=True, backup_kind='manual')
    if args.disable_first:
        live = store.list_cards(enabled_only=True)
        for card in live:
            store.set_card_enabled(card['id'], False, expected_card=card)
        print(f"已停用 {len(live)} 张启用卡，合并后可用 --enable-ready 统一恢复")
    records = merge_plan(store, actions, progress=lambda message: print(message, flush=True),
                         max_refs=args.max_refs, rename=args.rename_to_family)
    summary = summarize(records)
    result = {'db': str(args.db), 'clusters': str(args.clusters), 'backup': str(backup_path),
              'started_at': stamp, 'threshold': report.get('threshold'),
              'summary': summary, 'records': records, 'skipped': skipped,
              'finished_at': datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}
    target = args.report or PROJECT / 'validation' / f'method-merge-{result["finished_at"]}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(f"完成：合并 {summary['merged_clusters']} 簇，折叠 {summary['folded_cards']} 张卡；"
          f"其中 {summary['enable_ready']} 张达到启用条件，{summary['still_blocked']} 张仍不可启用")
    for record in summary['failed']:
        print(f"  未合并 {record.get('cluster')}：{record.get('reason')}")
    print(f"备份：{backup_path}")
    print(f"报告：{target}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
