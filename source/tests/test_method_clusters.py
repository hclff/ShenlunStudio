"""Clustering and merge planning must group synonyms without fusing unrelated methods."""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))

from cluster_method_cards import cluster_cards, describe, normalize, similarity  # noqa: E402
from merge_method_cards import load_plan, pick_target, summarize, trim_refs  # noqa: E402


def card(cid, name, articles=1, refs=2):
    return {'id': cid, 'name': name, 'refs': [{'id': f'{cid}:p{i}', 'quote': 'q'} for i in range(refs)],
            'source_article_ids': [f'article-{cid}-{i}' for i in range(articles)]}


def test_normalize_drops_qualifiers_and_punctuation():
    assert normalize('引权威论述立论（评论员观察）') == '引权威论述立论'
    assert normalize(' 并列、分论点 展开 ') == '并列分论点展开'
    assert normalize('') == ''


def test_similarity_rejects_very_different_lengths():
    assert similarity('引权威论述立论', '引权威论述立论') == 1.0
    assert similarity('并列展开', '并列展开论证并补充对策') == 0.0


def test_synonyms_merge_and_unrelated_names_stay_apart():
    cards = [card('c1', '引权威论述支撑观点'), card('c2', '引用权威论述支撑论点'),
             card('c3', '正反对比强化论点'), card('c4', '按时间轴梳理政策演进')]
    clusters = cluster_cards(cards, threshold=0.72)
    sizes = sorted(len(cluster['members']) for cluster in clusters)
    assert sizes == [1, 1, 2]


def test_describe_counts_distinct_articles():
    cluster = cluster_cards([card('c1', '引权威论述立论', articles=2),
                             card('c2', '引用权威论述立论', articles=1)], threshold=0.72)[0]
    described = describe(cluster)
    assert described['cards'] == 2
    assert described['articles'] == 3
    assert described['refs'] == 4
    assert described['card_ids'] == ['c1', 'c2']


def test_plan_skips_clusters_below_the_evidence_bar(tmp_path):
    report = {'threshold': 0.72, 'clusters': [
        {'id': 'ok', 'cards': 3, 'articles': 3, 'card_ids': ['c1', 'c2', 'c3']},
        {'id': 'thin', 'cards': 2, 'articles': 1, 'card_ids': ['c4', 'c5']},
        {'id': 'single', 'cards': 1, 'articles': 1, 'card_ids': ['c6']}]}
    path = tmp_path / 'clusters.json'
    path.write_text(json.dumps(report), encoding='utf8')
    _, actions, skipped = load_plan(path, min_cards=2, min_articles=2)
    assert [item['id'] for item in actions] == ['ok']
    assert [item['id'] for item in skipped] == ['thin']


def test_pick_target_prefers_more_refs_then_shorter_name():
    cards = [card('c1', '引用权威论述支撑观点', refs=2), card('c2', '引权威论述支撑论点', refs=5)]
    assert pick_target(cards)['id'] == 'c2'
    tie = [card('c3', '用统一句式领起分论点标题', refs=3), card('c4', '统一句式领起分论点', refs=3)]
    assert pick_target(tie)['id'] == 'c4'


def test_trim_refs_keeps_one_per_article_first():
    refs = [{'id': f'a{i}:p1', 'quote': 'q'} for i in range(5)] + [{'id': 'a0:p2', 'quote': 'q2'}]
    trimmed = trim_refs(refs, limit=5)
    assert [ref['id'] for ref in trimmed] == ['a0:p1', 'a1:p1', 'a2:p1', 'a3:p1', 'a4:p1']
    # Under the ceiling nothing is dropped, and order is untouched.
    assert trim_refs(refs[:3], limit=5) == refs[:3]


def test_trim_refs_fills_the_ceiling_when_articles_run_out():
    refs = [{'id': 'a0:p1'}, {'id': 'a0:p2'}, {'id': 'a0:p3'}]
    assert [ref['id'] for ref in trim_refs(refs, limit=2)] == ['a0:p1', 'a0:p2']


def test_summary_separates_ready_and_blocked():
    records = [{'result': 'merged', 'folded': 2, 'enable_ready': True},
               {'result': 'merged', 'folded': 1, 'enable_ready': False},
               {'result': 'missing', 'reason': 'gone'}]
    summary = summarize(records)
    assert summary['merged_clusters'] == 2
    assert summary['folded_cards'] == 3
    assert summary['enable_ready'] == 1
    assert summary['still_blocked'] == 1
    assert len(summary['failed']) == 1
