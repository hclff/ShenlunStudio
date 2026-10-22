"""Local review presentation never invents evidence or changes a saved assessment."""
import copy
import importlib


def module():
    return importlib.import_module('shenlun.review_learning')


def reviewed():
    return {'title':'便民服务', 'submission':{'question':'概括做法', 'materials':'社区开通电话预约。\n服务站保留人工窗口。',
        'answer':'开通电话。\n服务便民。'}, 'assessment':{'total_score':16, 'max_score':20,
        'summary':'措施需要具体化', 'next_action':'核对服务对象和措施',
        'annotations':[{'start':0,'end':5,'quote':'开通电话。','issue':'措施不具体','suggestion':'补充预约用途',
                        'material_refs':[{'id':'M1','quote':'电话预约'}]}],
        'material_points':[{'point':'保留人工窗口','coverage':'missing','answer_quote':'',
                            'material_refs':[{'id':'M2','quote':'保留人工窗口'}]}],
        'dimensions':[{'id':'content','name':'内容','weight':70,'score':80,'reason':'措施不全'}]},
        'revisions':{'minimal':'开通电话预约。\n服务便民。','model':'开通电话预约，保留人工窗口。'}}


def test_summary_prioritizes_evidenced_missing_points_and_never_rescores_or_mutates():
    value = reviewed(); before = copy.deepcopy(value)
    summary = module().learning_summary(value)
    assert summary[0]['refs'] == [{'id':'M2','quote':'保留人工窗口'}]
    assert summary[1]['action'] == '补充预约用途'
    assert len(summary) <= 10
    assert value == before


def test_summary_rechecks_material_refs_even_in_legacy_or_modified_reports():
    value = reviewed()
    value['assessment']['material_points'][0]['material_refs'] = [{'id':'M2','quote':'并不存在的事实'}]
    value['assessment']['annotations'][0]['material_refs'] = [{'id':'M999','quote':'电话预约'}]
    summary = module().learning_summary(value)
    assert all(not row['refs'] for row in summary)
    assert '并不存在的事实' not in module().summary_html(value)


def test_summary_caps_at_ten_and_legacy_empty_report_stays_honest():
    value = reviewed()
    template = value['assessment']['annotations'][0]
    value['assessment']['annotations'] = [
        {**template, 'issue': f'问题{index}', 'suggestion': f'建议{index}'} for index in range(12)]
    summary = module().learning_summary(value)
    assert len(summary) == 10
    assert summary[-1]['action'] == '建议8'
    assert module().learning_summary({}) == []
    assert '暂无' in module().summary_html({})


def test_summary_previews_stay_compact_without_truncating_underlying_report():
    value = reviewed()
    value['assessment']['annotations'][0]['suggestion'] = '很长的建议。'*2000
    before = copy.deepcopy(value)
    assert len(module().summary_html(value)) < 3000
    assert value == before


def test_diff_pairs_changed_and_equal_paragraphs_and_links_only_existing_annotations():
    rows = module().paragraph_diff(reviewed(), 'minimal')
    assert [r['kind'] for r in rows] == ['replace','equal']
    assert rows[0]['before'] == '开通电话。'
    assert rows[0]['after'] == '开通电话预约。'
    assert rows[0]['annotations'][0]['suggestion'] == '补充预约用途'
    assert rows[0]['annotations'][0]['refs'] == [{'id':'M1','quote':'电话预约'}]
    assert not rows[1]['annotations']
    assert '<ins' in rows[0]['after_html']


def test_insert_delete_and_unanchored_annotations_never_gain_invented_explanations():
    value = reviewed()
    value['revisions']['minimal'] = '新段落。\n开通电话。'
    value['assessment']['annotations'][0]['start'] = 100
    rows = module().paragraph_diff(value, 'minimal')
    assert any(r['kind']=='insert' for r in rows)
    assert any(r['kind']=='delete' for r in rows)
    assert all(not r['annotations'] for r in rows)


def test_diff_and_summary_escape_html_and_missing_revision_is_not_a_total_deletion():
    value = reviewed(); value['revisions']['minimal'] = '<img src="file:///secret"><script>x</script>'
    rows = module().paragraph_diff(value, 'minimal')
    assert '<img' not in ''.join(r['after_html'] for r in rows)
    assert '&lt;' in rows[0]['after_html']
    value['assessment']['annotations'][0]['issue'] = '<script>x</script>'
    assert '<script>' not in module().summary_html(value)
    value['revisions']['minimal'] = ''
    assert module().paragraph_diff(value, 'minimal') == []


def test_long_paragraph_has_bounded_diff_work_and_preserves_full_text():
    value = reviewed(); value['submission']['answer'] = '甲'*20000
    value['revisions']['minimal'] = '甲'*19999+'乙'
    rows = module().paragraph_diff(value, 'minimal')
    assert rows[0]['before'] == '甲'*20000
    assert rows[0]['after'].endswith('乙')
    assert rows[0]['coarse'] is True


def test_many_repetitive_paragraphs_share_a_total_fine_diff_budget():
    value = reviewed()
    value['submission']['answer'] = '\n'.join('甲'*998+str(i)+'乙' for i in range(40))
    value['revisions']['minimal'] = '\n'.join('甲'*998+str(i)+'丙' for i in range(40))
    rows = module().paragraph_diff(value, 'minimal')
    assert len(rows) == 40
    assert sum(not row['coarse'] for row in rows) < 5
    assert all(row['after'].endswith('丙') for row in rows)


def test_exported_report_places_summary_before_long_template_details(tmp_path):
    from shenlun.reports import review_html, export_review
    value = reviewed()
    html = review_html(value)
    assert html.index('优先改进') < html.index('审题与任务')
    assert '保留人工窗口' in html
    target = tmp_path/'review.txt'; export_review(value, target)
    assert '优先改进' in target.read_text(encoding='utf8')
    assert '#learning-annotation' not in target.read_text(encoding='utf8')
