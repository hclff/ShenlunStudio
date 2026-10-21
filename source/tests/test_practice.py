"""Question identity and honest same-question comparisons."""
from copy import deepcopy
import importlib
import importlib.util

import pytest


def module():
    assert importlib.util.find_spec('shenlun.practice'), 'practice comparison module required'
    return importlib.import_module('shenlun.practice')


def reviewed(answer='原答',score=12):
    return {'submission':{'question':'概括做法','materials':'社区开通电话。','answer':answer,
                          'question_type':'归纳概括','word_limit':200},
            'assessment':{'total_score':score,'max_score':20,'mode':'full','rubric_fingerprint':'r1'},
            'model':'model-a','prompt_version':'p1'}


def test_identity_ignores_answers_metadata_but_preserves_actual_material_changes():
    m=module();first=reviewed()['submission'];second={**first,'answer':'另一答','year':'2026','reference':'参考'}
    assert m.question_fingerprint(first)==m.question_fingerprint(second)
    assert m.question_fingerprint(first)!=m.question_fingerprint({**first,'materials':'社区取消电话。'})
    assert m.question_fingerprint(first)==m.question_fingerprint({**first,'question':'  概括做法\r\n'})


def test_comparable_score_delta_and_html_are_escaped():
    result=module().comparison(reviewed('<script>x</script>',12),reviewed('保留电话。',15))
    assert result['comparable'] and result['delta']==3
    assert '<script>' not in result['answer_html'] and '&lt;script&gt;' in result['answer_html']


@pytest.mark.parametrize('field,value',[('model','model-b'),('prompt_version','p2')])
def test_different_scoring_versions_never_claim_gain(field,value):
    other=reviewed(score=18);other[field]=value
    result=module().comparison(reviewed(),other)
    assert not result['comparable'] and result['delta'] is None and result['reasons']


def test_missing_rubric_or_diagnostic_cannot_claim_numeric_progress():
    other=reviewed();other['assessment'].pop('rubric_fingerprint')
    assert not module().comparison(reviewed(),other)['comparable']
    other=reviewed();other['assessment'].update(total_score=None,mode='diagnostic')
    assert module().comparison(reviewed(),other)['delta'] is None


def test_different_question_comparison_is_rejected():
    other=deepcopy(reviewed());other['submission']['materials']='另一套材料'
    with pytest.raises(ValueError):module().comparison(reviewed(),other)


def test_claimed_full_record_without_material_cannot_be_compared_as_full_score():
    first=reviewed();first['submission']['materials']=''
    result=module().comparison(first,first)
    assert not result['comparable'] and result['delta'] is None


def test_question_limit_also_counts_custom_rubric_text():
    fields=reviewed()['submission']
    fields['rubric']=[{'id':'one','name':'要点','weight':100,'description':'材'*250001}]
    with pytest.raises(ValueError):module().normalize_question(fields)


@pytest.mark.parametrize('changed',['endpoint','reference','exam','sources'])
def test_changed_service_or_scoring_input_context_never_claims_gain(changed):
    first=reviewed();second=deepcopy(first)
    if changed=='endpoint':
        first['config_snapshot']={'base_url':'https://one.example/v1'}
        second['config_snapshot']={'base_url':'https://two.example/v1'}
    elif changed=='sources':second['sources']=[{'id':'article','paragraphs':[{'id':'article:p1','text':'参考段落'}]}]
    else:second['submission'][changed]='不同的参考答案或考试类别'
    assert module().comparison(first,second)['delta'] is None
    assert module().scoring_version(first)!=module().scoring_version(second)
