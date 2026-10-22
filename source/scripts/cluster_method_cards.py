"""Cluster candidate method cards by name similarity, read-only.

A single-article extraction can never clear the two-article evidence bar that
``validate_article_card`` enforces, so the candidates a sample run produces only
become usable after near-duplicate names are folded together. Exact name
matching finds a few dozen groups; this script reports how far fuzzy clustering
gets before anything is merged, so the grouping can be reviewed first.

Nothing here writes to the database: ``--apply`` lives in
``scripts/merge_method_cards.py``, which consumes the JSON printed here.
"""
import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

# Brackets hold qualifiers such as "（评论员观察）" that vary between articles
# while the method itself stays the same, so they are dropped for comparison.
BRACKETS = re.compile(r'[（(][^）)]*[）)]')
PUNCTUATION = re.compile(r'[\s，。、；：·—\-~"“”\'’‘()\[\]【】《》<>]')


def normalize(name):
    """Comparison form of a method name."""
    text = BRACKETS.sub('', str(name or ''))
    return PUNCTUATION.sub('', text).lower()


def similarity(left, right, max_len_diff=4):
    """Character-level ratio with a guard for very different lengths."""
    if not left or not right:
        return 0.0
    if abs(len(left) - len(right)) > max_len_diff:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def load_cards(path):
    """Live cards only: merged-away sources stay in ``cards`` but are purged."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'数据库不存在：{path}')
    uri = path.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        # Mirrors ``Store.list_cards``: purged ids are gone for good, ids in the
        # trash table are the sources a merge folded away (restorable).
        hidden = set()
        for table in ('method_library_purged', 'method_library_trash'):
            hidden.update(row[0] for row in conn.execute(
                f"SELECT method_id FROM {table} WHERE kind='article'"))
        rows = conn.execute('SELECT data FROM cards').fetchall()
    cards = []
    for (payload,) in rows:
        try:
            card = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if isinstance(card, dict) and card.get('id') and card['id'] not in hidden:
            cards.append(card)
    return cards


def cluster_cards(cards, threshold=0.72, max_len_diff=4):
    """Greedy clustering: biggest cards first, similarity against the seed."""
    groups = {}
    for card in cards:
        key = normalize(card.get('name'))
        groups.setdefault(key, []).append(card)
    seeds = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    clusters = []
    for key, members in seeds:
        best, best_score = None, 0.0
        for cluster in clusters:
            score = similarity(key, cluster['key'], max_len_diff)
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= threshold:
            best['members'].extend(members)
            best['names'].extend(name for name in {normalize(c.get('name')) for c in members}
                                 if name not in best['names'])
            best['scores'].append(round(best_score, 3))
            continue
        clusters.append({'key': key, 'names': [key], 'members': list(members), 'scores': []})
    return clusters


def describe(cluster):
    cards = cluster['members']
    articles = []
    refs = 0
    for card in cards:
        refs += len(card.get('refs') or [])
        for aid in card.get('source_article_ids') or []:
            if aid not in articles:
                articles.append(aid)
    names = sorted({str(card.get('name') or '').strip() for card in cards})
    purposes = sorted({str(card.get('purpose') or '').strip() for card in cards if card.get('purpose')})
    return {'id': cluster['key'], 'cards': len(cards), 'articles': len(articles),
            'article_ids': articles, 'refs': refs, 'names': names,
            'purposes': purposes[:6], 'card_ids': [card['id'] for card in cards]}


def summarize(cards, clusters):
    total = len(cards)
    clustered = sum(1 for cluster in clusters if len(cluster['members']) > 1)
    covered = sum(len(cluster['members']) for cluster in clusters if len(cluster['members']) > 1)
    eligible = sum(1 for cluster in clusters
                   if len({aid for card in cluster['members']
                           for aid in card.get('source_article_ids') or []}) >= 2)
    return {'cards': total, 'clusters': len(clusters), 'merged_clusters': clustered,
            'covered_cards': covered, 'singleton_cards': total - covered,
            'eligible_after_merge': eligible}


def render_markdown(report):
    lines = ['# 候选方法聚类清单', '',
             f"- 生成时间：{report['generated_at']}",
             f"- 数据库：`{report['db']}`",
             f"- 相似度阈值：{report['threshold']}",
             f"- 候选卡：{report['summary']['cards']} 张，聚类后 {report['summary']['clusters']} 簇；"
             f"可合并簇 {report['summary']['merged_clusters']} 个（覆盖 {report['summary']['covered_cards']} 张卡），"
             f"孤卡 {report['summary']['singleton_cards']} 张，"
             f"合并后可达两篇证据门槛 {report['summary']['eligible_after_merge']} 簇", '']
    lines += ['| 卡数 | 文章数 | 引用数 | 代表名 | 同簇名称 |', '|---|---|---|---|---|']
    for cluster in report['clusters']:
        if cluster['cards'] < 2:
            continue
        others = '、'.join(name for name in cluster['names'] if name != cluster['names'][0]) or '—'
        lines.append(f"| {cluster['cards']} | {cluster['articles']} | {cluster['refs']} | "
                     f"{cluster['names'][0]} | {others} |")
    singles = [cluster for cluster in report['clusters'] if cluster['cards'] == 1]
    if singles:
        lines += ['', f'## 孤卡（{len(singles)} 张，无法单独启用）', '',
                  '、'.join(cluster['names'][0] for cluster in singles[:120])]
    return '\n'.join(lines) + '\n'


def build_report(db, threshold=0.72, max_len_diff=4):
    cards = load_cards(db)
    clusters = cluster_cards(cards, threshold=threshold, max_len_diff=max_len_diff)
    described = [describe(cluster) for cluster in clusters]
    described.sort(key=lambda item: (-item['cards'], item['id']))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return {'db': str(Path(db)), 'generated_at': stamp, 'threshold': threshold,
            'max_len_diff': max_len_diff,
            'summary': summarize(cards, clusters), 'clusters': described}


def main(argv=None):
    parser = argparse.ArgumentParser(description='按名称相似度聚类候选方法卡（只读）')
    parser.add_argument('--db', type=Path, required=True, help='数据库路径')
    parser.add_argument('--threshold', type=float, default=0.72, help='相似度阈值，默认 0.72')
    parser.add_argument('--max-len-diff', type=int, default=4,
                        help='名称长度差超过该值不再比较，默认 4；放宽可抓到措辞长短不同的同义方法')
    parser.add_argument('--out', type=Path, default=None, help='JSON 输出路径，默认 validation/ 下')
    parser.add_argument('--markdown', type=Path, default=None, help='Markdown 输出路径，默认与 JSON 同名')
    args = parser.parse_args(argv)

    report = build_report(args.db, threshold=args.threshold, max_len_diff=args.max_len_diff)
    stamp = report['generated_at']
    out = args.out or PROJECT / 'validation' / f'method-clusters-{stamp}.json'
    markdown = args.markdown or out.with_suffix('.md')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    markdown.write_text(render_markdown(report), encoding='utf8')

    summary = report['summary']
    print(f"候选卡 {summary['cards']} 张 → {summary['clusters']} 簇（阈值 {report['threshold']}）")
    print(f"可合并簇 {summary['merged_clusters']} 个，覆盖 {summary['covered_cards']} 张卡；"
          f"孤卡 {summary['singleton_cards']} 张；合并后可达两篇门槛 {summary['eligible_after_merge']} 簇")
    print(f"清单：{out}")
    print(f"表格：{markdown}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
