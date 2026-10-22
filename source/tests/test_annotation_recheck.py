"""Rechecks are local, verdicts are validated, and the review never changes."""
import copy

import pytest

from shenlun.recheck import MAX_ANNOTATIONS, select_indices, validate_verdicts, verified_refs
from shenlun.service import AppService
from shenlun.storage import Store

CONFIG = {'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture', 'session_token_budget': 150000}
MATERIAL = '社区增设电话报修，老人能顺利报修。'


def review():
    return {'submission': {'question': '概括成效', 'question_type': '归纳概括', 'max_score': 20,
                           'materials': MATERIAL,
                           'materials_paragraphs': [{'id': 'M1', 'text': MATERIAL}],
                           'answer': '社区增设电话报修。\n服务便民。'},
            'assessment': {'total_score': 12, 'max_score': 20,
                'dimensions': [{'id': 'coverage', 'name': '材料覆盖', 'score': 60, 'weight': 40,
                                'reason': '覆盖不全'}],
                'material_points': [{'point': '老人能报修'}],
                'annotations': [
                    {'start': 0, 'end': 8, 'quote': '社区增设电话报修', 'issue': '未写清可及性',
                     'suggestion': '补出可及性', 'issue_type': '要点遗漏', 'dimension': 'coverage',
                     'material_refs': [{'id': 'M1', 'quote': '社区增设电话报修'}], 'corpus_refs': []},
                    {'start': 9, 'end': 13, 'quote': '服务便民', 'issue': '结论空泛',
                     'suggestion': '写出对象', 'issue_type': '表述空泛', 'dimension': 'coverage',
                     'material_refs': [], 'corpus_refs': []}]},
            'revisions': {'minimal': '', 'model': '', 'status': '仅精批完成，未生成修改稿'}}


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, payload, cancel=None):
        self.calls.append((system, payload))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {'data': reply(payload) if callable(reply) else reply,
                'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20},
                'model': 'fixture'}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'recheck.db')


def verdict(decision='uphold', **kw):
    item = {'index': 0, 'decision': decision, 'reason': '材料引用成立', 'evidence_refs': [],
            'suggested_issue': '', 'suggested_suggestion': ''}
    item.update(kw)
    return item


def test_select_indices_rejects_empty_and_out_of_range():
    annotations = review()['assessment']['annotations']
    for bad in ([], [2], [-1], ['0'], [True]):
        with pytest.raises(ValueError):
            select_indices(annotations, bad)


def test_select_indices_deduplicates_and_caps_one_batch():
    annotations = [{} for _ in range(MAX_ANNOTATIONS + 1)]
    assert select_indices(annotations, [0, 0, 1]) == [0, 1]
    with pytest.raises(ValueError, match='分批'):
        select_indices(annotations, list(range(MAX_ANNOTATIONS + 1)))


def test_verified_refs_keeps_only_quotes_present_in_the_material():
    materials = {'M1': MATERIAL}
    refs = verified_refs([{'id': 'M1', 'quote': '老人能顺利报修'}, {'id': 'M1', 'quote': '原文没有'},
                          {'id': 'M9', 'quote': '社区增设电话报修'}, 'bad'], materials)
    assert refs == [{'id': 'M1', 'quote': '老人能顺利报修'}]


def test_validate_verdicts_accepts_all_three_decisions():
    verdicts = validate_verdicts({'verdicts': [
        verdict('uphold'), verdict('revise', index=1, suggested_issue='结论空泛',
                                   suggested_suggestion='写出服务对象')]}, review(), [0, 1])
    assert verdicts[0]['decision'] == 'uphold'
    assert verdicts[1]['suggested_suggestion'] == '写出服务对象'


def test_validate_verdicts_rejects_unselected_index_and_bad_decision():
    with pytest.raises(ValueError, match='未勾选'):
        validate_verdicts({'verdicts': [verdict(index=1)]}, review(), [0])
    with pytest.raises(ValueError, match='判定'):
        validate_verdicts({'verdicts': [verdict('maybe')]}, review(), [0])
    with pytest.raises(ValueError, match='缺少理由'):
        validate_verdicts({'verdicts': [verdict(reason=' ')]}, review(), [0])


def test_revise_without_suggestion_is_refused():
    with pytest.raises(ValueError, match='修改建议'):
        validate_verdicts({'verdicts': [verdict('revise')]}, review(), [0])


def test_unverifiable_quote_downgrades_to_unclear():
    verdicts = validate_verdicts({'verdicts': [verdict(
        evidence_refs=[{'id': 'M1', 'quote': '捏造的引用'}])]}, review(), [0])
    assert verdicts[0]['decision'] == 'unclear'
    assert verdicts[0]['evidence_refs'] == []
    assert '无法在原文核实' in verdicts[0]['reason']


def test_missing_verdict_becomes_unclear_instead_of_being_dropped():
    verdicts = validate_verdicts({'verdicts': [verdict(index=0)]}, review(), [0, 1])
    assert verdicts[1]['decision'] == 'unclear' and '需人工复核' in verdicts[1]['reason']


def test_payload_carries_only_the_selected_annotations(tmp_path):
    svc = AppService(Store(tmp_path / 'payload.db'), CONFIG, '')
    payload = svc.recheck_payload(review(), [1])
    assert [item['index'] for item in payload['annotations']] == [1]
    assert payload['annotations'][0]['quote'] == '服务便民'
    assert payload['materials'] == MATERIAL and payload['answer'].startswith('社区增设')
    assert payload['output_schema']['verdicts'][0]['decision'] == 'uphold/revise/unclear'


def test_recheck_calls_the_model_once_and_leaves_the_review_untouched(store):
    svc = AppService(store, CONFIG, '')
    rid = store.save_review(review())
    current = store.get_review(rid)
    before = copy.deepcopy(current)
    svc.client = ScriptedClient([{'verdicts': [verdict('uphold', index=0)]}])
    result = svc.recheck_annotations(current, [0])
    assert len(svc.client.calls) == 1
    assert result['verdicts']['0']['decision'] == 'uphold'
    assert result['review_id'] == rid and result['usage']['total_tokens'] == 20
    assert store.get_review(rid) == before


def test_recheck_keeps_budget_usage_books_on_its_own_version(store):
    svc = AppService(store, CONFIG, '')
    rid = store.save_review(review())
    svc.client = ScriptedClient([{'verdicts': [verdict()]}])
    svc.recheck_annotations(store.get_review(rid), [0])
    usage = store.usage_details()
    assert usage[0]['data']['prompt_version'] == 'recheck-1'


def test_latest_recheck_wins_and_records_are_newest_first(store):
    rid = store.save_review(review())
    store.save_review_recheck(rid, {'verdicts': {'0': {'decision': 'uphold', 'reason': '成立'}},
                                    'created_at': '2026-10-20 09:00'})
    store.save_review_recheck(rid, {'verdicts': {'0': {'decision': 'revise', 'reason': '建议改写'}},
                                    'created_at': '2026-10-22 09:00'})
    records = store.list_review_rechecks(rid)
    assert [record['created_at'] for record in records] == ['2026-10-22 09:00', '2026-10-20 09:00']
    latest = store.latest_recheck_map(rid)
    assert latest[0]['decision'] == 'revise'
    assert latest[0]['created_at'] == '2026-10-22 09:00'


def test_saving_a_recheck_requires_an_existing_review_id(store):
    with pytest.raises(ValueError, match='训练记录'):
        store.save_review_recheck('', {'verdicts': {}})


def test_deleting_a_review_removes_its_rechecks(store):
    rid = store.save_review(review())
    store.save_review_recheck(rid, {'verdicts': {'0': {'decision': 'uphold', 'reason': '成立'}}})
    plan = store.prepare_practice_deletion('reviews', [rid])
    store.delete_practice_items('reviews', [rid], expected_token=plan['token'])
    assert store.list_review_rechecks(rid) == []


def test_backup_restore_keeps_rechecks_with_their_review(store, tmp_path):
    rid = store.save_review(review())
    store.save_review_recheck(rid, {'verdicts': {'0': {'decision': 'unclear', 'reason': '材料不足'}}})
    backup = store.backup_to(tmp_path / 'recheck-backup.db')
    restored = Store(tmp_path / 'restored.db')
    restored.restore_from(backup)
    assert restored.latest_recheck_map(rid)[0]['decision'] == 'unclear'
