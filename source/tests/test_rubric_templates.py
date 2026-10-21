"""Exam-aware training rubrics, authoritative provenance and durable selection."""
from copy import deepcopy
import importlib
import importlib.util

import pytest

from shenlun.domain import RUBRICS,prepare_submission,rubric_fingerprint,validate_assessment
from shenlun.practice import comparison,normalize_question,scoring_conditions
from shenlun.storage import Store


def templates():
    assert importlib.util.find_spec('shenlun.rubrics'), 'exam/type rubric resolver required'
    return importlib.import_module('shenlun.rubrics')


def raw(**changes):
    return {'question':'概括社区服务做法','materials':'社区保留电话报修，服务老人。','answer':'保留电话报修。',
            'question_type':'归纳概括','exam':'江苏省考','category':'B','year':'2026','max_score':20,**changes}


@pytest.mark.parametrize('profile',['generic','national_provincial','national_local','national_law','jiangsu_a','jiangsu_b','jiangsu_c'])
@pytest.mark.parametrize('kind',list(RUBRICS))
def test_all_profiles_and_question_types_have_complete_bounded_training_weights(profile,kind):
    result=templates().resolve_template(kind,selection=profile,year='2026')
    rows=result['rubric'];meta=result['metadata']
    assert sum(row['weight'] for row in rows)==100
    assert len({row['id'] for row in rows})==len(rows)
    assert all(row['description'] and 0<=row['weight']<=100 for row in rows)
    assert meta['profile']==profile and meta['name'] and meta['version']
    assert '训练' in meta['notice'] and '官方' in meta['notice']


@pytest.mark.parametrize('exam,category,expected',[
    ('江苏省考','a类','jiangsu_a'),('江苏省考','B','jiangsu_b'),('江苏','C类','jiangsu_c'),
    ('国考','省部级','national_provincial'),('国考','市（地）级及以下直属机构综合管理类','national_local'),
    ('国家公务员考试','行政执法类','national_law'),('江苏省考','未知','generic'),('浙江省考','B','generic'),
])
def test_auto_match_is_explicit_and_never_confuses_different_provinces(exam,category,expected):
    result=templates().resolve_template('提出对策',exam=exam,category=category,year='2026')
    assert result['metadata']['profile']==expected
    if expected=='generic':assert result['metadata']['warnings']


def test_profile_changes_actual_criteria_and_weights_not_just_label():
    m=templates()
    first=m.resolve_template('综合分析',exam='江苏省考',category='A',year='2026')
    second=m.resolve_template('综合分析',exam='江苏省考',category='B',year='2026')
    assert rubric_fingerprint(first['rubric'])!=rubric_fingerprint(second['rubric'])
    assert [d['weight'] for d in first['rubric']]!=[d['weight'] for d in second['rubric']]
    assert '权限' in ''.join(d['description'] for d in second['rubric'])


def test_unknown_year_uses_honest_baseline_reference_and_manual_mismatch_warns():
    m=templates()
    result=m.resolve_template('应用写作',exam='江苏省考',category='C',year='2027')
    assert result['metadata']['source_year']=='2026'
    assert not result['metadata']['annual_verified'] and result['metadata']['warnings']
    result=m.resolve_template('应用写作',exam='江苏省考',category='A',year='2026',selection='national_law')
    assert result['metadata']['profile']=='national_law' and result['metadata']['warnings']
    with pytest.raises(ValueError):m.resolve_template('提出对策',selection='untrusted-profile')


def test_builtin_survives_ui_then_service_preparation_and_assessment_copies_local_provenance():
    templates();first=prepare_submission(raw());second=prepare_submission(first)
    assert first==second
    assert first['rubric_template']['profile']=='jiangsu_b'
    result={'summary':'训练评价','task_analysis':'概括做法','dimensions':[{'id':d['id'],'score':80,'reason':'材料有依据'} for d in first['rubric']],
            'annotations':[],'material_points':[{'point':'保留电话渠道','material_refs':[{'id':'M1','quote':'保留电话报修'}],
                                               'coverage':'covered','answer_quote':'保留电话报修'}],
            'strengths':[],'uncertainties':[],'next_action':'补全服务对象',
            'rubric_template':{'kind':'official','name':'伪造官方阅卷'}}
    assessment=validate_assessment(result,second,[])
    assert assessment['total_score']==16
    assert assessment['rubric_template']==first['rubric_template']
    assert assessment['rubric_template']['kind']=='builtin'


def test_custom_rubric_priority_and_forged_builtin_metadata_cannot_mislabel_custom_weights():
    templates();prepared=prepare_submission(raw())
    altered=deepcopy(prepared);altered['rubric'][0]['description']+='用户增加限制'
    result=prepare_submission(altered)
    assert result['rubric']==altered['rubric']
    assert result['rubric_template']['kind']=='custom'
    assert not result['rubric_template']['source_url']
    assert prepare_submission(result)==result
    custom=[{'id':'only','name':'指定要点','weight':100,'description':'按用户量表核对'}]
    assert prepare_submission(raw(rubric=custom))['rubric']==custom


def test_selection_roundtrips_templates_raw_drafts_and_structured_import(tmp_path):
    templates();store=Store(tmp_path/'rubrics.db')
    fields=raw(rubric_selection='generic',rubric_text='[{unfinished')
    store.save_workspace({'submission':fields,'question_id':None})
    assert store.load_workspace()['submission']['rubric_selection']=='generic'
    assert store.load_workspace()['submission']['rubric_text']=='[{unfinished'
    fields.pop('rubric_text');qid=store.save_question(fields)
    assert store.get_question(qid)['submission']['rubric_selection']=='generic'
    assert normalize_question(fields)['rubric_selection']=='generic'
    from shenlun.intake import read_inputs
    import json
    file=tmp_path/'questions.json'
    file.write_text(json.dumps([fields],ensure_ascii=False),encoding='utf8')
    batch=read_inputs([file])
    assert not batch.errors
    assert batch.pages[0].fields['rubric_selection']=='generic'
    fields['rubric_selection']='invalid'
    file.write_text(json.dumps([fields],ensure_ascii=False),encoding='utf8')
    invalid=read_inputs([file])
    assert invalid.errors and not invalid.pages


def test_template_metadata_participates_in_new_comparisons_without_relabeling_legacy():
    templates();s=prepare_submission(raw())
    legacy={'submission':{'question':'Q','materials':'M','answer':'A','word_limit':0},
            'assessment':{'total_score':10,'max_score':20,'mode':'full','rubric_fingerprint':'legacy-r1'},
            'model':'m','prompt_version':'p'}
    assert 'template' not in scoring_conditions(legacy)
    first={'submission':s,'assessment':{'total_score':12,'max_score':20,'mode':'full','rubric_fingerprint':s['rubric_fingerprint']},'model':'m','prompt_version':'p'}
    second=deepcopy(first);second['submission']['rubric_template']['version']='next-template-version'
    assert comparison(first,second)['delta'] is None


def test_reports_export_actual_template_source_and_criteria_without_recomputing(tmp_path):
    templates();from shenlun.reports import review_html,export_review
    s=prepare_submission(raw());record={'submission':s,'assessment':{'total_score':12,'max_score':20,'rubric_template':s['rubric_template']}}
    report=review_html(record)
    assert s['rubric_template']['name'] in report
    assert s['rubric_template']['source_url'] in report
    assert '训练' in report and s['rubric'][0]['description'] in report
    target=tmp_path/'report.txt';export_review(record,target)
    assert s['rubric_template']['name'] in target.read_text('utf8')


def test_banked_builtin_keeps_verified_provenance_and_edited_weights_become_custom(tmp_path):
    templates();s=prepare_submission(raw());store=Store(tmp_path/'bank.db')
    rid=store.save_review({'submission':s,'assessment':{'total_score':12,'max_score':20}})
    question=store.get_question(store.question_for_review(rid))
    assert question['submission']['rubric_template']==s['rubric_template']
    assert prepare_submission({**question['submission'],'answer':'新一份原答'})['rubric_template']['kind']=='builtin'
    forged=deepcopy(s);forged['rubric'][0]['description']+='改过实际检查项'
    assert normalize_question(forged)['rubric_template']['kind']=='custom'


def test_custom_notice_attributes_the_actual_weights_to_the_user():
    templates();s=prepare_submission(raw(rubric=[{'id':'only','name':'自定','weight':100}]))
    notice=s['rubric_template']['notice']
    assert '用户' in notice and '维度、权重及分档由软件设计' not in notice


def test_ignored_picker_does_not_change_the_actual_custom_template_or_comparability():
    templates();rows=[{'id':'mine','name':'指定标准','weight':100,'description':'用户标准'}]
    first=prepare_submission(raw(rubric=rows,rubric_selection='auto'))
    second=prepare_submission(raw(rubric=rows,rubric_selection='generic'))
    assert first['rubric_template']==second['rubric_template']
    def record(s):
        return {'submission':s,'assessment':{'total_score':10,'max_score':20,'mode':'full','rubric_fingerprint':s['rubric_fingerprint']},
                'model':'same-model','prompt_version':'same-prompt'}
    assert comparison(record(first),record(second))['comparable']
