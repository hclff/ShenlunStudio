"""Build the distributable seed from the actual downloaded source manifest."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from shenlun.importers import import_manifest
from shenlun.storage import Store


def main():
    parser = argparse.ArgumentParser(description='构建实际官方语料种子数据库，不调用模型API')
    parser.add_argument('--manifest', type=Path, default=PROJECT.parent / 'tmp/pdfs/qstheory-source/manifest.json')
    parser.add_argument('--output', type=Path, default=PROJECT / 'assets/seed.db')
    args = parser.parse_args()
    store = Store(args.output)
    result = import_manifest(args.manifest, store)
    store.set_setting('seed_source', {'manifest': args.manifest.name, 'processed': result['processed']})
    stats = store.stats()
    articles = store.list_articles(limit=1_000_000)
    summary = dict(stats, paragraphs=sum(len(article['paragraphs']) for article in articles),
                   characters=sum(len(article['body']) for article in articles),
                   text_articles=sum(bool(article['body']) for article in articles),
                   image_only=sum(article.get('text_status') == 'image_only' for article in articles),
                   fts5_trigram=store.fts_available)
    with sqlite3.connect(args.output) as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.execute('VACUUM')
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
