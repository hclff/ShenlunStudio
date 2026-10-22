import copy

import pytest

from shenlun.assessment_trace import (
    dimension_score_text,
    needs_review_reasons,
    trace_counts,
    trace_html,
    trace_rows,
)
from shenlun.domain import normalise_issue_type, prepare_submission, validate_assessment

ANSWER = "社区增设电话报修，老人能顺利报修。"
MATERIAL = "社区增设电话报修，老人能顺利报修。"


def annotation(**kw):
    item = {"start": 0, "end": 4, "quote": "社区增设", "dimension": "coverage",
            "issue_type": "要点遗漏", "issue": "没有说明渠道带来的可及性改善",
            "suggestion": "补出渠道与可及性的关系",
            "material_refs": [{"id": "M1", "quote": "社区增设电话报修"}], "corpus_refs": []}
    item.update(kw)
    return item


def make_review(annotations=None, dimensions=None, uncertainties=None, mode="full"):
    dims = dimensions if dimensions is not None else [
        {"id": "coverage", "name": "材料覆盖", "score": 60, "weight": 40, "reason": "材料要点覆盖不全"},
        {"id": "expression", "name": "组织表达", "score": 40, "weight": 20, "reason": "表述不够准确"},
    ]
    return {"submission": {"answer": ANSWER, "materials": MATERIAL, "mode": mode},
            "assessment": {"mode": mode, "dimensions": dims,
                           "annotations": annotations if annotations is not None else [annotation()],
                           "uncertainties": uncertainties or []}}


def test_annotations_group_under_their_dimension():
    second = annotation(start=5, end=9, quote="电话报修", dimension="expression",
                        issue_type="表述空泛", issue="表达空泛", suggestion="直接写清渠道",
                        material_refs=[])
    rows = trace_rows(make_review([annotation(), second]))
    assert [row["dimension"]["id"] for row in rows] == ["coverage", "expression"]
    assert [row["items"][0]["annotation_index"] for row in rows] == [0, 1]
    assert rows[0]["unassigned"] is False
    assert not rows[0]["pending"] and not rows[1]["pending"]


def test_unknown_dimension_falls_back_to_unassigned_row():
    rows = trace_rows(make_review([annotation(dimension="missing")]))
    assert rows[0]["items"] == []
    assert rows[-1]["unassigned"] is True
    assert rows[-1]["dimension"]["name"] == "未归属维度"
    assert rows[-1]["items"][0]["annotation_index"] == 0


def test_legacy_annotations_without_trace_fields_still_render():
    legacy = annotation()
    legacy.pop("dimension")
    legacy.pop("issue_type")
    rows = trace_rows(make_review([legacy]))
    item = rows[-1]["items"][0]
    assert rows[-1]["unassigned"] is True
    assert item["issue_type"] == "其他"
    assert item["needs_review"] is False


def test_material_issue_without_quote_needs_review():
    review = make_review([annotation(material_refs=[])])
    reasons = needs_review_reasons(review, review["assessment"]["annotations"][0])
    assert reasons and "材料" in reasons[0]
    assert trace_rows(review)[0]["items"][0]["needs_review"] is True


def test_material_issue_with_quote_is_settled():
    review = make_review()
    assert needs_review_reasons(review, review["assessment"]["annotations"][0]) == []


def test_dimension_claiming_material_without_any_quote_is_pending():
    quiet = annotation(issue_type="表述空泛", issue="表达空泛", material_refs=[])
    rows = trace_rows(make_review([quiet]))
    assert rows[0]["pending"] is True
    assert "材料" in rows[0]["pending_reasons"][0]


def test_dimension_with_material_quote_is_not_pending():
    rows = trace_rows(make_review())
    assert rows[0]["pending"] is False


def test_uncertainty_fragment_marks_annotation():
    review = make_review(uncertainties=["是否包含报修渠道信息待确认"])
    review["assessment"]["annotations"][0].update(
        issue_type="表述空泛", issue="未写清报修渠道", material_refs=[])
    reasons = needs_review_reasons(review, review["assessment"]["annotations"][0])
    assert any("待确认" in reason for reason in reasons)


def test_recheck_verdict_is_shown_and_unclear_is_pending():
    review = make_review()
    upheld = trace_rows(review, {0: {"decision": "uphold", "reason": "依据成立"}})[0]["items"][0]
    assert upheld["recheck"]["label"] == "维持原判断"
    assert upheld["needs_review"] is False

    unclear = trace_rows(review, {"0": {"decision": "unclear", "reason": "材料引用不充分"}})[0]["items"][0]
    assert unclear["recheck"]["decision"] == "unclear"
    assert unclear["needs_review"] is True


def test_diagnostic_mode_names_fallback_row_for_expression():
    review = make_review([annotation(dimension="", material_refs=[])], dimensions=[], mode="diagnostic")
    rows = trace_rows(review)
    assert rows[-1]["dimension"]["name"] == "表达与结构批注"
    assert rows[-1]["items"][0]["dimension_id"] == ""


def test_trace_html_escapes_text_and_reports_pending():
    review = make_review([annotation(issue="<script>alert(1)</script>", material_refs=[])])
    html = trace_html(trace_rows(review))
    assert "评分依据" in html and "材料覆盖" in html and "待核实" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_trace_counts_summarise_pending_work():
    review = make_review([annotation(), annotation(start=5, end=9, quote="电话报修",
                                                   material_refs=[], issue="漏点")])
    counts = trace_counts(trace_rows(review))
    assert counts["items"] == 2
    assert counts["pending_items"] == 1
    assert counts["pending_total"] >= 1


def test_dimension_score_text_skips_unknown_score():
    assert dimension_score_text({"score": 82.5, "weight": 40}) == "得分 82.5 / 100 · 权重 40%"
    assert dimension_score_text({"score": None}) == ""


@pytest.mark.parametrize("raw,expected", [
    ("要点遗漏", "要点遗漏"), ("漏点", "要点遗漏"), ("材料运用不当", "材料未用"),
    ("偏题", "审题偏差"), ("格式混乱", "格式不符"), ("没有对应标签", "其他"), (None, "其他")])
def test_issue_type_normalisation(raw, expected):
    assert normalise_issue_type(raw) == expected


def submission(**kw):
    return prepare_submission({"question": "概括服务改进的成效，100字内。", "materials": MATERIAL,
                               "answer": "方便老人报修。", "question_type": "归纳概括",
                               "max_score": 20, **kw})


def valid_result(s):
    return {"summary": "对象准确", "task_analysis": "概括成效",
            "dimensions": [{"id": d["id"], "score": 80, "reason": "准确使用材料"} for d in s["rubric"]],
            "annotations": [{"start": 0, "end": 7, "quote": "方便老人报修。", "issue": "可明确渠道带来的可及性改善",
                             "suggestion": "突出报修服务可及性。",
                             "material_refs": [{"id": "M1", "quote": "老人能顺利报修"}], "corpus_refs": []}],
            "material_points": [{"point": "老人能报修", "material_refs": [{"id": "M1", "quote": "老人能顺利报修"}],
                                 "coverage": "covered", "answer_quote": "老人报修"}],
            "strengths": ["对象准确"], "next_action": "区分做法与成效", "uncertainties": []}


def test_domain_keeps_valid_dimension_and_labels_unknown_ones():
    s = submission()
    r = valid_result(s)
    r["annotations"][0].update(dimension=s["rubric"][1]["id"], issue_type="漏点")
    out = validate_assessment(r, s, [])
    assert out["annotations"][0]["dimension"] == s["rubric"][1]["id"]
    assert out["annotations"][0]["issue_type"] == "要点遗漏"


def test_domain_clears_dimension_outside_this_rubric():
    s = submission()
    r = valid_result(s)
    r["annotations"][0].update(dimension="not-a-dimension", issue_type="不认识的类型")
    out = validate_assessment(r, s, [])
    assert out["annotations"][0]["dimension"] == ""
    assert out["annotations"][0]["issue_type"] == "其他"


def test_domain_accepts_annotations_without_new_fields():
    s = submission()
    out = validate_assessment(valid_result(s), s, [])
    assert out["annotations"][0]["issue_type"] == "其他"
    assert out["annotations"][0]["dimension"] == ""


def test_diagnostic_mode_clears_dimension_attribution():
    s = submission(materials="")
    r = valid_result(s)
    r["annotations"][0].update(material_refs=[], dimension=s["rubric"][0]["id"])
    r["material_points"] = []
    out = validate_assessment(r, s, [])
    assert out["annotations"][0]["dimension"] == ""


def test_trace_does_not_mutate_review():
    review = make_review()
    before = copy.deepcopy(review)
    trace_rows(review, {0: {"decision": "revise", "reason": "建议改写"}})
    assert review == before
