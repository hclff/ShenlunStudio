"""Add explicitly sampled official People's Daily editions to a clean seed."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shenlun.storage import Store
from shenlun.importers import import_people_range,is_auxiliary_title


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default=str(ROOT/'assets'/'seed.db'))
    p.add_argument('--dates',nargs='+',default=['2024-10-14','2025-10-14','2026-10-14'])
    args=p.parse_args()
    store=Store(Path(args.db))
    results=[]
    for day in args.dates:
        result=import_people_range(day,day,store,progress=lambda s:print(s,flush=True))
        results.append({'date':day,**{k:v for k,v in result.items() if k!='article_ids'}})
    for article in store.list_articles(journal='人民日报',limit=100000):
        if is_auxiliary_title(article['title']):
            article.update(kind='其他',training_eligible=False)
            store.upsert_article(article)
    store.set_setting('bundled_corpus_scope',{'qstheory':'2024—2026年截至2026-10-15，134期目录条目',
                       'people_daily_sample_dates':args.dates,'people_daily_full_coverage':False,
                       'note':'人民日报仅采样日期的评论/理论版；可在界面按日期继续导入。'})
    with sqlite3.connect(args.db) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        integrity=db.execute('PRAGMA quick_check').fetchone()[0]
    all_samples=store.list_articles(journal='人民日报',limit=100000)
    report={'samples':results,'sample_article_counts':{d:sum(a['publication']==d for a in all_samples) for d in args.dates},
            'eligible_text_samples':sum(bool(a['body']) and a.get('training_eligible',True) for a in all_samples),
            'stats':store.stats(),'integrity':integrity}
    (ROOT/'validation').mkdir(exist_ok=True)
    (ROOT/'validation'/'people-seed.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
