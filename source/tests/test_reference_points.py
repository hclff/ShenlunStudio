"""Reference versions bind human actions and exact evidence to saved questions."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import importlib
import threading

import pytest

from shenlun.storage import Store


def submission():
    return {'question': '概括社区做法', 'materials': ' 社区开通电话服务。\r\n\r\n居民可以随时咨询。 ',
            'question_type': '归纳概括', 'answer': '开通电话服务，方便居民咨询。', 'max_score': 20}


def points():
    return [{'id': 'P1', 'text': '开通电话服务',
             'material_refs': [{'id': 'M1', 'quote': '开通电话服务'}]}]


@pytest.fixture
def store(tmp_path):
    module = importlib.import_module('shenlun.reference_store')
    if issubclass(Store, module.ReferenceStoreMixin):
        return Store(tmp_path / 'reference.db')
    class ReferenceStore(module.ReferenceStoreMixin, Store):
        def __init__(self, path):
            super().__init__(path)
            with self._connection() as conn:
                self._init_reference_points(conn)
    return ReferenceStore(tmp_path / 'reference.db')


def test_reference_feature_is_available():
    assert importlib.util.find_spec('shenlun.reference_store'), 'reference store mixin required'


def test_versions_are_append_only_and_drafts_preserve_last_confirmation(store):
    qid = store.save_question(submission())
    first = store.save_reference_points(qid, [], expected_version=0)
    assert (first['version'], first['status']) == (1, 'draft')
    assert store.get_active_reference_points(qid) is None
    confirmed = store.save_reference_points(qid, points(), reviewer='本人', confirm=True, expected_version=1)
    later = points(); later[0]['text'] = '尚未确认的新措辞'
    draft = store.save_reference_points(qid, later, expected_version=2)
    assert draft['version'] == 3 and draft['status'] == 'draft'
    assert store.get_active_reference_points(qid) == confirmed
    assert store.get_reference_points(qid, version=1) == first
    assert [row['version'] for row in store.list_reference_versions(qid)] == [3, 2, 1]
    result = store.get_reference_points(qid); result['points'][0]['text'] = '篡改返回值'
    assert store.get_reference_points(qid) == draft
    assert store.get_reference_points(qid, version=999) is None


def test_explicit_confirmation_and_withdrawal_require_identity_and_preserve_history(store):
    qid = store.save_question(submission())
    for kwargs in ({'confirm': True}, {'confirm': True, 'reviewer': '  '}, {'reviewer': 1}, {'confirm': 'yes'}):
        with pytest.raises(ValueError): store.save_reference_points(qid, points(), **kwargs)
    with pytest.raises(ValueError): store.save_reference_points(qid, [], reviewer='本人', confirm=True)
    confirmed = store.save_reference_points(qid, points(), reviewer='本人', confirm=True)
    for reviewer, reason in [('', '撤回'), ('本人', ''), (False, '撤回')]:
        with pytest.raises(ValueError): store.withdraw_reference_points(qid, reviewer, reason)
    withdrawn = store.withdraw_reference_points(qid, '本人', '需重新核验', expected_version=1)
    assert withdrawn['status'] == 'withdrawn' and withdrawn['version'] == 2
    assert withdrawn['points'] == confirmed['points'] and withdrawn['note'] == '需重新核验'
    store.save_reference_points(qid, points(), expected_version=2)
    assert store.get_active_reference_points(qid) is None
    assert store.get_reference_points(qid, 1) == confirmed


def test_stale_writers_fail_atomically_including_simultaneous_connections(store):
    qid = store.save_question(submission())
    store.save_reference_points(qid, points(), expected_version=0)
    for expected in (0, False, -1, '1'):
        with pytest.raises(ValueError): store.save_reference_points(qid, points(), expected_version=expected)
    with pytest.raises(ValueError): store.withdraw_reference_points(qid, '本人', '过期窗口', expected_version=0)
    barrier = threading.Barrier(2)
    def save():
        barrier.wait()
        try:
            return store.save_reference_points(qid, points(), expected_version=1)['version']
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))
    assert sorted(results, key=str) == [2, 'conflict']
    assert len(store.list_reference_versions(qid)) == 2


@pytest.mark.parametrize('change', ['question', 'materials', 'question_type'])
def test_submission_lookup_uses_actual_content_never_claimed_ids_or_paragraphs(store, change):
    qid = store.save_question(submission())
    confirmed = store.save_reference_points(qid, points(), reviewer='本人', confirm=True)
    actual = submission(); actual.update(question_id='forged', question_fingerprint='forged')
    assert store.reference_points_for_submission(actual) == confirmed
    actual[change] = '提出对策' if change == 'question_type' else '被换掉的内容'
    actual['materials_paragraphs'] = [{'id': 'M1', 'text': '社区开通电话服务。'}]
    assert store.reference_points_for_submission(actual) is None


@pytest.mark.parametrize('bad', [
    None, {}, [], [{'id': 'P1', 'text': '做法', 'material_refs': []}],
    [{'id': 'P1', 'text': '做法', 'material_refs': [{'id': 'M2', 'quote': '开通电话服务'}]}],
    [{'id': 'P1', 'text': '做法', 'material_refs': [{'id': 'M1', 'quote': '电话热线服务'}]}],
    [{'id': 'P1', 'text': '做法', 'material_refs': [{'id': 'M1', 'quote': ' '}]}],
    [{'id': 'P1', 'text': 'x' * 4001, 'material_refs': [{'id': 'M1', 'quote': '电话'}]}],
])
def test_reference_preview_rejects_invalid_or_fabricated_cites(bad):
    module = importlib.import_module('shenlun.reference_points')
    with pytest.raises(ValueError): module.validate_reference_points(bad, submission())


def test_point_ids_unique_bounded_and_external_flags_do_not_assert_confirmation(store):
    module = importlib.import_module('shenlun.reference_points')
    qid = store.save_question(submission())
    bad = points() * 2
    with pytest.raises(ValueError): module.validate_reference_points(bad, submission())
    with pytest.raises(ValueError): module.validate_reference_points(points() * 51, submission())
    forged = points(); forged[0].update(status='confirmed', human_verified=True)
    result = store.save_reference_points(qid, forged)
    assert result['status'] == 'draft' and result['reviewer'] == ''
    assert result['points'] == points()
    forged[0]['material_refs'][0]['quote'] = '后来修改'
    assert store.get_reference_points(qid)['points'] == points()
    with pytest.raises(ValueError): store.save_reference_points('missing', points())


def test_snapshots_verify_hash_cites_fingerprint_confirmation_and_are_detached(store):
    module = importlib.import_module('shenlun.reference_points')
    qid = store.save_question(submission())
    confirmed = store.save_reference_points(qid, points(), reviewer='本人', confirm=True)
    result = module.validate_reference_snapshot(confirmed, submission())
    assert result == confirmed and len(result['content_hash']) == 64
    conditions = module.reference_conditions(confirmed)
    assert conditions == {'question_fingerprint': confirmed['question_fingerprint'],
                          'version': 1, 'content_hash': confirmed['content_hash']}
    assert module.reference_conditions(None) is None
    result['points'][0]['text'] = '修改'
    assert confirmed['points'] == points()
    for field, value in [('status', 'draft'), ('reviewer', ''), ('content_hash', '0' * 64),
                         ('version', True), ('created_at', 'not-a-time'), ('question_id', '')]:
        bad = deepcopy(confirmed); bad[field] = value
        with pytest.raises(ValueError): module.validate_reference_snapshot(bad, submission())
    bad = deepcopy(confirmed); bad['points'][0]['material_refs'][0]['quote'] = '伪造'
    with pytest.raises(ValueError): module.validate_reference_snapshot(bad, submission())
    changed = submission(); changed['question'] = '另一题'
    with pytest.raises(ValueError): module.validate_reference_snapshot(confirmed, changed)


def test_coverage_requires_every_known_point_once_and_exact_answer_evidence(store):
    module = importlib.import_module('shenlun.reference_points')
    qid = store.save_question(submission())
    extra = points() + [{'id': 'P2', 'text': '方便咨询', 'material_refs': [{'id': 'M2', 'quote': '随时咨询'}]}]
    snapshot = store.save_reference_points(qid, extra, reviewer='本人', confirm=True)
    rows = [{'id': 'P2', 'coverage': 'uncertain', 'answer_quote': '', 'reason': '证据不足'},
            {'id': 'P1', 'coverage': 'covered', 'answer_quote': '开通电话服务', 'reason': '已提及'}]
    canonical = module.validate_point_coverage(rows, snapshot, submission()['answer'])
    assert [row['id'] for row in canonical] == ['P1', 'P2']
    rows[0]['reason'] = '改写'
    assert canonical[1]['reason'] == '证据不足'
    bad_rows = [canonical[:1], canonical + canonical[:1], [{**canonical[0], 'id': 'P99'}, canonical[1]],
                [{**canonical[0], 'answer_quote': '无中生有'}, canonical[1]],
                [{**canonical[0], 'answer_quote': ''}, canonical[1]],
                [{**canonical[0], 'coverage': 'partial', 'answer_quote': ''}, canonical[1]],
                [{**canonical[0], 'coverage': 'invented'}, canonical[1]],
                [canonical[0], {**canonical[1], 'answer_quote': '无中生有'}],
                [canonical[0], {**canonical[1], 'reason': 'x' * 4001}]]
    for bad in bad_rows:
        with pytest.raises(ValueError): module.validate_point_coverage(bad, snapshot, submission()['answer'])
    missing = deepcopy(canonical); missing[1]['coverage'] = 'missing'
    assert module.validate_point_coverage(missing, snapshot, submission()['answer'])[1]['answer_quote'] == ''


def test_snapshot_rejects_forged_paragraph_maps_even_with_recomputed_hash(store):
    module = importlib.import_module('shenlun.reference_points')
    qid = store.save_question(submission())
    confirmed = store.save_reference_points(qid, points(), reviewer='本人', confirm=True)
    forged = deepcopy(confirmed)
    forged['points'][0]['material_refs'] = [{'id': 'M1', 'quote': '不存在的材料内容'}]
    forged['content_hash'] = module.reference_content_hash(forged)
    raw = submission(); raw['materials_paragraphs'] = [{'id': 'M1', 'text': '不存在的材料内容'}]
    with pytest.raises(ValueError): module.validate_reference_snapshot(forged, raw)


def test_draft_after_withdrawal_can_be_reconfirmed_without_mutating_previous_snapshot(store):
    module = importlib.import_module('shenlun.reference_points')
    qid = store.save_question(submission())
    first = store.save_reference_points(qid, points(), reviewer='甲', confirm=True)
    first_conditions = module.reference_conditions(first)
    store.withdraw_reference_points(qid, '甲', '重新核验', expected_version=1)
    draft = store.save_reference_points(qid, points(), expected_version=2)
    second = store.save_reference_points(qid, draft['points'], reviewer='乙', confirm=True, expected_version=3)
    assert store.get_active_reference_points(qid) == second
    assert module.reference_conditions(second) != first_conditions
    assert module.validate_reference_snapshot(first, submission()) == store.get_reference_points(qid, 1)
    assert module.reference_content_hash(dict(reversed(list(first.items())))) == first['content_hash']


@pytest.mark.parametrize('snapshot', [{}, {'question_fingerprint': [], 'version': {}, 'content_hash': []}])
def test_comparison_provenance_rejects_incomplete_or_mutable_claims(snapshot):
    module = importlib.import_module('shenlun.reference_points')
    with pytest.raises(ValueError): module.reference_conditions(snapshot)


def test_selected_reference_version_rejects_out_of_range_before_sqlite_binding(store):
    qid = store.save_question(submission())
    for version in (2 ** 80, 2147483648):
        with pytest.raises(ValueError): store.get_reference_points(qid, version=version)
