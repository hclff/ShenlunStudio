import json
from pathlib import Path
import pytest
from shenlun.reports import review_html, export_review, calibration_metrics


def review():
    return {'title':'服务<script>alert(1)</script>','submission':{'question':'概括成效','materials':'老人可电话报修。','answer':'<img src=x onerror=alert(1)>原答','question_type':'归纳概括','exam':'通用训练'},
            'assessment':{'mode':'diagnostic','total_score':None,'max_score':20,'summary':'诊断','task_analysis':'材料对象','annotations':[],'dimensions':[],'material_points':[],'next_action':'准确概括','strengths':[],'uncertainties':[],'warnings':[]},
            'revisions':{'minimal':'修改后的答案','model':'另一个示范','status':'完成'},'sources':[],'model':'fixture'}


def test_html_escapes_untrusted_answer_and_title():
    text=review_html(review())
    assert '<script>' not in text
    assert '<img src=x' not in text
    assert '&lt;img' in text and '修改后的答案' in text and '原答' in text


def test_uncertain_teaching_application_uses_plain_status_text():
    value = review()
    value['teaching_snapshot'] = {'mode':'combined','methods':[{
        'id':'fixture-method','owner_id':'bailu','name':'合成方法','version':1,
        'reviewer':'','purpose':'','not_applicable':'','sources':[]}]}
    value['assessment']['teaching_analysis'] = {'applications':[{
        'method_id':'fixture-method','status':'uncertain','observation':'证据不足',
        'material_refs':[]}]}
    text = review_html(value)
    assert '本题应用：不确定' in text
    assert '待核' not in text


def test_json_export_preserves_original_and_revision(tmp_path):
    path=tmp_path/'报告.json'
    export_review(review(),path)
    data=json.loads(path.read_text(encoding='utf8'))
    assert data['submission']['answer']=='<img src=x onerror=alert(1)>原答'
    assert data['revisions']['minimal']=='修改后的答案'


def test_calibration_hand_calculated_errors():
    m=calibration_metrics([{'human':10,'ai':12},{'human':20,'ai':18}])
    assert m['n']==2 and m['mae']==2 and m['bias']==0 and m['rmse']==2


def test_empty_calibration_does_not_claim_zero_error():
    m=calibration_metrics([])
    assert m['n']==0 and m['mae'] is None


@pytest.mark.parametrize('score',[float('nan'),float('inf'),True,-2,110])
def test_invalid_calibration_not_silently_accepted(score):
    with pytest.raises(ValueError):
        calibration_metrics([{'human':score,'ai':10}])


def test_repeat_spread_only_for_same_answer():
    m=calibration_metrics([{'human':10,'ai':12,'answer_id':'a'},{'human':10,'ai':15,'answer_id':'a'},{'human':10,'ai':30,'answer_id':'b'}])
    assert m['repeat_spread']['a']==3
    assert 'b' not in m['repeat_spread']


def test_text_report_has_source_attribution(tmp_path):
    r=review();r['sources']=[{'title':'来源文章','url':'https://www.qstheory.cn/a','paragraphs':[]}]
    p=tmp_path/'报告.txt';export_review(r,p)
    assert 'https://www.qstheory.cn/a' in p.read_text(encoding='utf8')
