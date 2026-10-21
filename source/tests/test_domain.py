import copy
import math

import pytest

from shenlun.domain import prepare_submission, rubric_for, validate_assessment


def submission(**kw):
    return prepare_submission({"question": "概括服务改进的成效，100字内。", "materials": "社区增设电话报修，老人能顺利报修。", "answer": "方便老人报修。", "question_type": "归纳概括", "max_score": 20, **kw})


def valid_result(s):
    return {"summary": "对象准确", "task_analysis": "概括成效", "dimensions": [{"id": d["id"], "score": 80, "reason": "准确使用材料"} for d in s["rubric"]],
            "annotations": [{"start": 0, "end": 7, "quote": "方便老人报修。", "issue": "可明确渠道带来的可及性改善", "suggestion": "突出报修服务可及性。", "material_refs": [{"id": "M1", "quote": "老人能顺利报修"}], "corpus_refs": []}],
            "material_points": [{"point": "老人能报修", "material_refs": [{"id": "M1", "quote": "老人能顺利报修"}], "coverage": "covered", "answer_quote": "老人报修"}], "strengths": ["对象准确"], "next_action": "区分做法与成效", "uncertainties": []}


def test_missing_material_has_no_full_scoring():
    s = submission(materials="")
    assert s["mode"] == "diagnostic"
    r = valid_result(s)
    r["annotations"][0]["material_refs"] = []
    r["material_points"] = []
    out = validate_assessment(r, s, [])
    assert out["total_score"] is None
    assert out["dimensions"] == []


def test_total_is_locally_computed_on_question_scale():
    s = submission()
    r = valid_result(s)
    r["total_score"] = 99
    out = validate_assessment(r, s, [])
    assert out["total_score"] == 16
    assert out["word_count"] == 7


def test_rubric_content_has_stable_distinct_version():
    original = submission()
    changed = copy.deepcopy(original['rubric'])
    changed[0]['description'] += '自定义任务限制。'
    custom = submission(rubric=changed)
    assert original['rubric_fingerprint'] != custom['rubric_fingerprint']
    assert submission(rubric=copy.deepcopy(changed))['rubric_fingerprint'] == custom['rubric_fingerprint']
    result = validate_assessment(valid_result(custom), custom, [])
    assert result['rubric_version'] == custom['rubric_version']
    assert result['rubric_fingerprint'] == custom['rubric_fingerprint']


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 101, True, "80"])
def test_bad_dimension_cannot_become_successful_score(bad):
    s = submission()
    r = valid_result(s)
    r["dimensions"][0]["score"] = bad
    with pytest.raises(ValueError):
        validate_assessment(r, s, [])


def test_invented_material_quote_rejected():
    s = submission()
    r = valid_result(s)
    r["annotations"][0]["material_refs"][0]["quote"] = "投诉下降90%"
    with pytest.raises(ValueError, match="引用"):
        validate_assessment(r, s, [])


def test_wrong_answer_span_rejected():
    s = submission()
    r = valid_result(s)
    r["annotations"][0]["end"] = 6
    with pytest.raises(ValueError, match="原文"):
        validate_assessment(r, s, [])


def test_unrelated_corpus_ref_rejected():
    s = submission()
    r = valid_result(s)
    r["annotations"][0]["corpus_refs"] = [{"id": "fake:p1", "quote": "增强温度"}]
    with pytest.raises(ValueError, match="引用"):
        validate_assessment(r, s, [])


def test_missing_or_duplicate_rubric_dimensions_rejected():
    s = submission()
    r = valid_result(s)
    r["dimensions"][0]["id"] = r["dimensions"][1]["id"]
    with pytest.raises(ValueError):
        validate_assessment(r, s, [])


def test_invalid_custom_rubric_not_silently_applied():
    with pytest.raises(ValueError, match="100"):
        submission(rubric=[{"id": "one", "name": "内容", "weight": 90, "description": "准确"}])


def test_word_count_and_line_offsets_preserve_answer():
    s = submission(answer="  服务\r\n 有温度。  ")
    assert s["answer"] == "服务\n 有温度。"
    assert s["word_count"] == 6


def test_zero_full_score_stays_zero_and_word_limit_reported():
    s = submission(word_limit=5)
    r = valid_result(s)
    for d in r["dimensions"]:
        d["score"] = 0
    out = validate_assessment(r, s, [])
    assert out["total_score"] == 0
    assert any("字数" in w for w in out["warnings"])


def test_diagnostic_cannot_assert_material_missing_points():
    s = submission(materials="")
    with pytest.raises(ValueError):
        validate_assessment(valid_result(s), s, [])


def test_material_missing_point_must_have_evidence():
    s = submission()
    r = valid_result(s)
    r["material_points"][0].update(coverage="missing", material_refs=[], answer_quote="")
    with pytest.raises(ValueError):
        validate_assessment(r, s, [])


def test_full_score_requires_material_evidence_mapping():
    s=submission();r=valid_result(s);r['material_points']=[]
    with pytest.raises(ValueError,match='材料'):
        validate_assessment(r,s,[])
