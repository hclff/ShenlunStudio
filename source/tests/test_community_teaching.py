"""Community summaries stay traceable without verbose UI-facing provenance copy."""
import json
from shenlun.storage import Store
from shenlun.teaching import resolve_teaching_guidance, validate_teaching_method, teaching_content_hash
from shenlun.teaching_catalog import builtin_teaching_methods


def test_new_community_cards_are_pinned_and_dormant_in_existing_library(tmp_path):
    store = Store(tmp_path / 'existing.db')
    legacy = store.get_teaching_method('LM01')
    store.save_teaching_method({**legacy, 'name': '本人修改的分类方法'}, expected_version=0)
    reopened = Store(store.path)
    community = [r for r in reopened.list_teaching_methods() if r['id'].startswith('GH_')]
    assert len(community) == 12
    assert {r['owner_id'] for r in community} == {
        'bailu','yuandong','limengyuan','xiaomage','zhanzhang','sihai_feiyang',
        'zhangxiaolong','zhongjun','shanshuling'}
    for row in community:
        validate_teaching_method(row)
        assert row['content_hash'] == teaching_content_hash(row)
        assert not row['name'].startswith('社区整理·')
        assert not row['enabled'] and row['status'] == 'draft' and row['version'] == 0
        assert 'GitHub' not in row['purpose'] and '社区' not in row['purpose']
        assert '教师本人' not in row['not_applicable'] and '仓库声称' not in row['not_applicable']
        assert row['note'] == ''
        for source in row['sources']:
            assert source['url'].startswith('https://github.com/') and '/blob/' in source['url']
            commit = source['url'].split('/blob/')[1].split('/')[0]
            assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
            assert '社区' not in source['author'] and '教师本人' not in source['author']
            assert commit in source['locator'] and '许可：' in source['locator']
            assert source['quote'] and source['quote'] in source['excerpt']
    assert reopened.list_teaching_methods(True) == []
    assert reopened.get_teaching_method('LM01')['name'] == '本人修改的分类方法'
    assert len(reopened.list_teaching_methods()) == 24


def test_builtin_teaching_catalog_has_no_pending_source_prompts():
    from shenlun.teaching_catalog import teaching_owners
    assert all(owner['description'] == '' for owner in teaching_owners())
    visible_text = json.dumps(builtin_teaching_methods(), ensure_ascii=False)
    assert '来源待补' not in visible_text
    assert all(method['note'] == '' for method in builtin_teaching_methods())
    assert '待核' not in visible_text


def test_local_review_can_use_summary_without_losing_origin_or_overwriting_it(tmp_path):
    store = Store(tmp_path / 'review.db')
    row = store.get_teaching_method('GH_BL01')
    assert row is not None, 'the community method must be built into the main software'
    store.set_teaching_method_enabled(row['id'], True, reviewer='本地核对人',
        note='只认可社区改写步骤，不声称老师认证', expected_version=0)
    submission = {'question_type':'归纳概括','question':'概括做法','materials':'社区开通电话。',
                  'answer':'开通电话。'}
    snapshot = resolve_teaching_guidance(store, submission,
        {'mode':'source','owner_ids':['bailu']})
    selected = snapshot['methods'][0]
    assert selected['id'] == row['id'] and selected['sources'] == row['sources']
    assert '教师本人' not in selected['not_applicable'] and not selected['name'].startswith('社区整理·')
    store.set_teaching_method_enabled(row['id'], False, expected_version=1)
    assert selected['enabled'] and not store.get_teaching_method(row['id'])['enabled']
    assert not next(r for r in builtin_teaching_methods() if r['id'] == row['id'])['enabled']
