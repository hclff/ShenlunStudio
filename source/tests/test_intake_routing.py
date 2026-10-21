"""Literal routing and real SQLite atomic/stale-write boundaries."""
from copy import deepcopy
import importlib
import importlib.util
import sqlite3

import pytest

from shenlun.storage import Store


def routing():
    assert importlib.util.find_spec('shenlun.intake_routing'), 'paragraph routing is required'
    return importlib.import_module('shenlun.intake_routing')


def workspace(answer='原答'):
    return {'submission': {'question': '旧题', 'materials': '旧材料', 'answer': answer,
                           'reference': '参考', 'rubric_text': '{未完成'}, 'question_id': None}


def test_split_preserves_literal_paragraphs_and_source_order():
    split = routing().split_routing_pages
    pages = [{'source': '甲.txt', 'number': 2, 'text': '  第一段\n段内行 \n\n \n第二段\n'},
             {'source': '空.txt', 'number': 1, 'text': ' \n\n'},
             {'source': '乙.txt', 'number': 3, 'text': '第三段'}]
    result = split(pages)
    assert [(p['source'], p['number'], p['text']) for p in result] == [
        ('甲.txt', 2, '  第一段\n段内行 '), ('甲.txt', 2, '第二段\n'), ('乙.txt', 3, '第三段')]
    assert result == split(pages)
    assert len({p['id'] for p in result}) == 3
    changed = deepcopy(pages); changed[0]['text'] += '变化'
    assert result[0]['id'] != split(changed)[0]['id']


def test_four_fields_compose_without_empty_target_clearing():
    segments = [{'id': 'a', 'source': 'x', 'number': 1, 'text': '新题'},
                {'id': 'b', 'source': 'x', 'number': 1, 'text': '新材料'},
                {'id': 'c', 'source': 'x', 'number': 2, 'text': '末段'}]
    before = {'question': '旧题', 'materials': '旧材料', 'answer': '原答', 'reference': '参考'}
    assert routing().compose_routed_fields(segments, {'c': 'materials', 'b': 'materials', 'a': 'question'},
                                          before, {'question', 'answer'}) == {
        'question': '新题', 'materials': '旧材料\n\n新材料\n\n末段', 'answer': '原答', 'reference': '参考'}


@pytest.mark.parametrize('assignments,replace', [({'unknown': 'answer'}, set()), ({'a': 'exam'}, set()),
                                                ({'a': None}, set()), ({}, {'exam'})])
def test_invalid_routes_are_rejected(assignments, replace):
    with pytest.raises(ValueError):
        routing().compose_routed_fields([{'id': 'a', 'source': 'x', 'number': 1, 'text': '段'}],
            assignments, dict.fromkeys(('question', 'materials', 'answer', 'reference'), ''), replace)


def test_import_limit_rejects_without_truncation():
    with pytest.raises(ValueError):
        routing().split_routing_pages([{'source': 'x', 'number': 1, 'text': '字' * 250001}])


def test_join_separators_count_toward_import_limit():
    with pytest.raises(ValueError):
        routing().split_routing_pages([{'source': 'x', 'number': 1, 'text': '字' * 125000},
                                      {'source': 'x', 'number': 2, 'text': '字' * 125000}])
    with pytest.raises(ValueError):
        routing().compose_routed_fields([
            {'id': 'a', 'source': 'x', 'number': 1, 'text': '字' * 125000},
            {'id': 'b', 'source': 'x', 'number': 2, 'text': '字' * 125000}],
            {'a': 'answer', 'b': 'answer'}, dict.fromkeys(('question', 'materials', 'answer', 'reference'), ''), set())


def require_commit(store):
    assert hasattr(store, 'commit_routed_workspace'), 'atomic routed commit is required'
    return store.commit_routed_workspace


def test_latest_dirty_before_is_archived_and_after_survives_reopen(tmp_path):
    store = Store(tmp_path / 'test.db'); commit = require_commit(store)
    opening = store.save_workspace(workspace('磁盘原答'))
    before = workspace('尚未自动保存的原答'); after = deepcopy(before)
    after['submission']['materials'] = '新材料'
    saved = commit(before, after, expected_workspace=opening, expected_generation=store.assessment_generation())
    assert saved == Store(store.path).load_workspace()
    assert saved['submission']['materials'] == '新材料'
    assert store.list_workspace_history()[0]['submission']['answer'] == '尚未自动保存的原答'


def test_second_internal_write_failure_rolls_back_all_tables(tmp_path, monkeypatch):
    store = Store(tmp_path / 'test.db'); commit = require_commit(store)
    opening = store.save_workspace(workspace()); after = workspace('新答')
    with store._connection() as conn:
        baseline = {name: list(conn.execute('SELECT * FROM ' + name)) for name in
                    ('practice_workspace', 'workspace_history', 'question_drafts')}
    writer = store._save_workspace; calls = []
    def fail_second(conn, snapshot, archive_previous):
        result = writer(conn, snapshot, archive_previous); calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError('injected disk error')
        return result
    monkeypatch.setattr(store, '_save_workspace', fail_second)
    with pytest.raises(sqlite3.OperationalError):
        commit(workspace('dirty'), after, expected_workspace=opening, expected_generation=store.assessment_generation())
    with store._connection() as conn:
        assert {name: list(conn.execute('SELECT * FROM ' + name)) for name in baseline} == baseline


def test_cross_store_write_and_restore_reject_old_context(tmp_path):
    store = Store(tmp_path / 'test.db'); commit = require_commit(store)
    opening = store.save_workspace(workspace()); generation = store.assessment_generation()
    backup = store.backup_to(tmp_path / 'copy.db', register=False)
    other = Store(store.path); other.save_workspace(workspace('其他窗口'))
    with pytest.raises(ValueError):
        commit(workspace(), workspace('新答'), expected_workspace=opening, expected_generation=generation)
    store.restore_from(backup)
    with pytest.raises(ValueError):
        commit(workspace(), workspace('新答'), expected_workspace=opening, expected_generation=generation)
    assert store.load_workspace()['submission']['answer'] == '原答'


def test_only_four_fields_can_change_and_capacity_is_checked(tmp_path):
    store = Store(tmp_path / 'test.db'); commit = require_commit(store)
    opening = store.save_workspace(workspace())
    for key, value in [('exam', '改变设置'), ('materials', '字' * 2000001)]:
        after = workspace(); after['submission'][key] = value
        with pytest.raises(ValueError):
            commit(workspace(), after, expected_workspace=opening, expected_generation=store.assessment_generation())
    assert store.load_workspace() == opening


def test_question_change_detaches_without_touching_question_or_reports(tmp_path):
    store = Store(tmp_path / 'test.db'); commit = require_commit(store)
    before = workspace(); qid = store.save_question({**before['submission'], 'rubric_text': ''}); before['question_id'] = qid
    opening = store.save_workspace(before); question = store.get_question(qid)
    after = deepcopy(before); after['submission']['question'] = '新题'
    saved = commit(before, after, expected_workspace=opening, expected_generation=store.assessment_generation())
    assert saved['question_id'] is None
    assert store.get_question(qid) == question
    assert store.load_question_draft(qid)['submission']['question'] == '旧题'
