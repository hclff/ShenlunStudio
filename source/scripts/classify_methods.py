"""把候选方法卡归入申论写作方法族，产出一份可审阅的归族清单。

与 ``cluster_method_cards.py`` 的区别：那边看名称字符串像不像，这边看方法在
申论写作里是不是同一个技法。字符串相似度到 0.60 已经收敛，剩下的孤卡是
"同一技法换了说法"，只能靠方法论判断。

默认调模型归族（族表见 ``method_taxonomy.py``），失败或未返回的项退回关键词
兜底；``--offline`` 则完全离线。只读数据库，不写入任何方法卡。

输出与 ``merge_method_cards.py`` 的 ``--clusters`` 兼容：每个族就是一个簇，
``names[0]`` 是族的规范名，加 ``--rename-to-family`` 即可让合并后的方法改用
这个规范名。
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
sys.path.insert(0, str(PROJECT / 'scripts'))

from method_taxonomy import (  # noqa: E402
    FAMILIES, FAMILY_BY_ID, classify_by_keywords, family_name, parse_assignments,
)
from shenlun.model import ModelClient  # noqa: E402

SYSTEM = """你是申论写作教研员。下面给出"写作方法族"清单（每个族 = 写作环节 + 手法 + 说明），
请把每个待归类的候选方法名归入最贴近的一个族。

判断原则：
1. 看名称描述的写作动作落在哪个环节、用了什么手法，不要抠字面措辞。
2. 同一技法换了说法必须归同一族：例如"引权威原话支撑论点""引权威论断开篇"
   "引用政策文件佐证"都是引证，归 argument.authority。
3. 一个名称同时含多种手法时，按主要手法归族：先看它解决的问题 purpose，再看名称。
4. 名称只是泛泛而谈、看不出具体写作技法时才填 null，不要为凑族硬塞。
5. 拿不准就在 confidence 里标 low，仍然要给出你判断最接近的族。

只输出 JSON：{"assignments":[{"key":1,"family":"argument.data","confidence":"high"}]}
family 必须是清单中的 id 或 null。"""


def read_cards(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
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


def model_config(db, args):
    """Stored model settings, with CLI overrides; read without migrating."""
    uri = Path(db).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        try:
            row = conn.execute("SELECT data FROM settings WHERE key='model_config'").fetchone()
        except sqlite3.Error:
            row = None
    try:
        config = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    if args.base_url:
        config['base_url'] = args.base_url
    if args.model:
        config['model'] = args.model
    config['max_output_tokens'] = max(int(config.get('max_output_tokens') or 0), 8000)
    config['session_token_budget'] = max(int(config.get('session_token_budget') or 0), 3_000_000)
    config['max_input_chars'] = max(int(config.get('max_input_chars') or 0), 24_000)
    key = args.api_key or __import__('os').environ.get('SHENLUN_API_KEY', '')
    return config, key


def build_items(cards):
    items = []
    for index, card in enumerate(cards, start=1):
        items.append({'key': index, 'name': str(card.get('name') or '').strip(),
                      'purpose': str(card.get('purpose') or '').strip()[:70]})
    return items


def model_batches(client, items, batch_size, progress=None):
    """One reply per batch; failures fall back to keywords for that batch only."""
    assigned = {}
    table = [{'id': f.id, 'name': f.name, 'stage': f.stage, 'desc': f.desc} for f in FAMILIES]
    total = (len(items) + batch_size - 1) // batch_size
    for number, start in enumerate(range(0, len(items), batch_size), start=1):
        chunk = items[start:start + batch_size]
        payload = {'families': table, 'items': chunk}
        for attempt in range(2):
            try:
                reply = client.complete(SYSTEM, payload)
                assigned.update(parse_assignments(reply.get('data')))
                break
            except Exception as error:  # noqa: BLE001 - one bad batch must not abort the run
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                if progress:
                    progress(f'第 {number}/{total} 批失败，改用关键词兜底：{error}')
        if progress:
            progress(f'已归类 {min(start + batch_size, len(items))}/{len(items)}')
    return assigned


def classify(cards, assigned, progress=None):
    """Model assignment first, keyword fallback next; returns {card_id: family_id}."""
    by_key = {}
    for index, card in enumerate(cards, start=1):
        family = assigned.get(index)
        if not family:
            fallback = classify_by_keywords(card.get('name'), card.get('purpose') or '')
            family = fallback.id if fallback else ''
        by_key[card['id']] = family
    return by_key


def build_report(db, cards, mapping, source):
    groups = {}
    for card in cards:
        family = mapping.get(card['id']) or ''
        groups.setdefault(family, []).append(card)
    clusters, unclassified = [], []
    for family, members in groups.items():
        articles = []
        refs = 0
        for card in members:
            refs += len(card.get('refs') or [])
            for aid in card.get('source_article_ids') or []:
                if aid not in articles:
                    articles.append(aid)
        names = sorted({str(card.get('name') or '').strip() for card in members})
        row = {'id': family or 'unclassified', 'family': family_name(family),
               'stage': FAMILY_BY_ID[family].stage if family in FAMILY_BY_ID else '',
               'cards': len(members), 'articles': len(articles), 'refs': refs,
               'names': names, 'card_ids': [card['id'] for card in members]}
        if family:
            clusters.append(row)
        else:
            unclassified.append(row)
    clusters.sort(key=lambda item: (-item['cards'], item['id']))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    eligible = sum(1 for row in clusters if row['articles'] >= 2 and row['cards'] >= 1)
    return {'db': str(Path(db)), 'generated_at': stamp, 'source': source,
            'summary': {'cards': len(cards), 'families_used': len(clusters),
                        'merged_families': sum(1 for row in clusters if row['cards'] > 1),
                        'covered_cards': sum(row['cards'] for row in clusters if row['cards'] > 1),
                        'unclassified': sum(row['cards'] for row in unclassified),
                        'eligible_after_merge': eligible},
            'clusters': clusters, 'unclassified': unclassified}


def render_markdown(report):
    summary = report['summary']
    lines = ['# 方法族归族清单', '',
             f"- 生成时间：{report['generated_at']}",
             f"- 归族方式：{'模型判断' if report['source'] == 'model' else '关键词兜底'}",
             f"- 候选卡：{summary['cards']} 张；命中 {summary['families_used']} 个方法族",
             f"- 同族可合并：{summary['merged_families']} 族、覆盖 {summary['covered_cards']} 张卡；"
             f"未归族 {summary['unclassified']} 张", '',
             '| 方法族 | 环节 | 卡数 | 篇数 | 引用 | 归入的方法名 |', '|---|---|---|---|---|---|']
    for row in report['clusters']:
        names = '、'.join(row['names'][:12]) + ('…' if len(row['names']) > 12 else '')
        lines.append(f"| {row['family']} | {row['stage']} | {row['cards']} | "
                     f"{row['articles']} | {row['refs']} | {names} |")
    if report['unclassified']:
        lines += ['', f"## 未归族（{summary['unclassified']} 张）", '',
                  '、'.join(row['names'][0] for row in report['unclassified'])]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description='按申论写作方法族归类候选方法卡（只读）')
    parser.add_argument('--db', type=Path, required=True, help='数据库路径')
    parser.add_argument('--offline', action='store_true', help='不调模型，只用关键词兜底')
    parser.add_argument('--batch-size', type=int, default=60, help='每次请求归类的卡数，默认 60')
    parser.add_argument('--limit', type=int, default=0, help='只处理前 N 张卡，0 表示全部')
    parser.add_argument('--api-key', default='', help='临时覆盖密钥；推荐 SHENLUN_API_KEY 环境变量')
    parser.add_argument('--base-url', default='', help='临时覆盖端点')
    parser.add_argument('--model', default='', help='临时覆盖模型名')
    parser.add_argument('--out', type=Path, default=None, help='JSON 输出路径')
    parser.add_argument('--markdown', type=Path, default=None, help='Markdown 输出路径')
    args = parser.parse_args(argv)

    cards = read_cards(args.db)
    if args.limit:
        cards = cards[:args.limit]
    items = build_items(cards)
    assigned, source = {}, 'keywords'
    if not args.offline:
        config, key = model_config(args.db, args)
        if not config.get('base_url') or not config.get('model'):
            print('缺少模型配置：请在应用设置中填写，或用 --base-url/--model 传入；本次改用关键词兜底。')
        else:
            client = ModelClient(config, key)
            assigned = model_batches(client, items, max(10, args.batch_size),
                                     progress=lambda message: print(message, flush=True))
            source = 'model'
    mapping = classify(cards, assigned, progress=None)
    report = build_report(args.db, cards, mapping, source)
    out = args.out or PROJECT / 'validation' / f'method-taxonomy-{report["generated_at"]}.json'
    markdown = args.markdown or out.with_suffix('.md')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    markdown.write_text(render_markdown(report), encoding='utf8')
    summary = report['summary']
    print(f"候选卡 {summary['cards']} 张 → {summary['families_used']} 个方法族；"
          f"同族可合并 {summary['merged_families']} 族、覆盖 {summary['covered_cards']} 张；"
          f"未归族 {summary['unclassified']} 张")
    print(f"清单：{out}")
    print(f"表格：{markdown}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
