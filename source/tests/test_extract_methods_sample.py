"""The sampling CLI must pick a stable slice and never touch the database."""
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))

from extract_methods_sample import _failed_ids, main, sample_rows, select_rows, summarize  # noqa: E402
from shenlun.storage import Store  # noqa: E402


def make_store(tmp_path, count=9):
    path = tmp_path / 'studio.db'
    store = Store(path)
    for index in range(count):
        text = '正文' * 200
        store.upsert_article({
            'id': f'a{index:03d}', 'title': f'第{index}篇', 'journal': '人民日报' if index % 2 else '求是',
            'kind': '评论', 'status': 'available', 'year': 2026, 'issue': index,
            'publication': f'2026-01-{index + 1:02d}', 'url': f'https://example.com/{index}',
            'body': text, 'paragraphs': [{'id': f'a{index:03d}:p1', 'text': text}]})
    return path


def test_sample_takes_a_stable_slice(tmp_path):
    path = make_store(tmp_path, 9)
    sample = sample_rows(path, every=3)
    assert sample['available'] == 9
    assert sample['sampled'] == 3
    ids = [item['id'] for item in sample['items']]
    # Same call, same slice: no randomness between runs or machines.
    assert ids == [item['id'] for item in sample_rows(path, every=3)['items']]
    # The slice follows the corpus order (journal, newest publication first).
    assert ids == ['a007', 'a001', 'a004']


def test_every_one_is_the_whole_corpus_and_limit_truncates(tmp_path):
    path = make_store(tmp_path, 5)
    assert sample_rows(path, every=1)['sampled'] == 5
    assert sample_rows(path, every=1, limit=2)['sampled'] == 2


def test_select_rows_follows_the_given_order_and_reports_missing(tmp_path):
    path = make_store(tmp_path, 3)
    sample = select_rows(path, ['a002', 'missing-id', 'a000'])
    assert [item['id'] for item in sample['items']] == ['a002', 'a000']
    assert sample['missing'] == ['missing-id']
    assert sample['sampled'] == 2


def test_failed_ids_reads_the_report(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text('{"failed": [{"id": "a001"}], "failed_ids": ["a001", "a002"]}',
                      encoding='utf8')
    assert _failed_ids(report) == ['a001', 'a002']
    report.write_text('{"failed": [{"id": "a003"}]}', encoding='utf8')
    assert _failed_ids(report) == ['a003']


def test_short_rows_are_dropped_before_stepping(tmp_path):
    path = make_store(tmp_path, 3)
    store = Store(path)
    store.upsert_article({'id': 'pic', 'title': '图片报道', 'journal': '人民日报', 'kind': '其他',
                          'status': 'available', 'year': 2026, 'issue': 9, 'publication': '2026-01-20',
                          'url': 'https://example.com/pic', 'body': '图片报道',
                          'paragraphs': [{'id': 'pic:p1', 'text': '图片报道'}]})
    sample = sample_rows(path, every=1)
    assert sample['available'] == 4 and sample['usable'] == 3
    assert [item['id'] for item in sample['items']] == ['a001', 'a002', 'a000']
    assert all(item['characters'] >= 300 for item in sample['items'])


def test_sampling_never_writes_or_migrates(tmp_path):
    path = make_store(tmp_path, 4)
    before = path.read_bytes()
    with sqlite3.connect(path) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
    sample_rows(path, every=2)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == version


def test_summary_counts_batches_like_the_service(tmp_path):
    path = make_store(tmp_path, 2)
    store = Store(path)
    long_paragraphs = [{'id': 'b:p1', 'text': '甲' * 9000}, {'id': 'b:p2', 'text': '乙' * 9000}]
    store.upsert_article({'id': 'b', 'title': '长文', 'journal': '求是', 'kind': '评论',
                          'status': 'available', 'year': 2026, 'issue': 1,
                          'publication': '2026-01-09', 'url': 'https://example.com/b',
                          'body': '\n'.join(p['text'] for p in long_paragraphs),
                          'paragraphs': long_paragraphs})
    stats = summarize(sample_rows(path, every=1))
    assert stats['articles'] == 3
    # Two short articles plus one that needs two 16000-char batches.
    assert stats['estimated_batches'] == 4


def test_dry_run_reports_and_exits_without_writing(tmp_path, capsys):
    path = make_store(tmp_path, 6)
    before = path.read_bytes()
    assert main(['--db', str(path), '--every', '2']) == 0
    output = capsys.readouterr().out
    assert '采样 3 篇' in output
    assert '试运行未写入' in output
    assert path.read_bytes() == before


def test_apply_refuses_without_a_key(tmp_path):
    path = make_store(tmp_path, 2)
    assert main(['--db', str(path), '--every', '1', '--apply', '--api-key', '',
                 '--base-url', 'https://example.com/v1', '--model', 'm']) == 2


def test_missing_database_is_reported(tmp_path):
    assert main(['--db', str(tmp_path / 'absent.db')]) == 1
