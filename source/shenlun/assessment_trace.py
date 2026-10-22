"""Per-dimension evidence chains: score, answer quote, material, issue, advice.

Pure data assembly with no Qt and no model calls, so the desktop tree and the
exported HTML report show exactly the same judgement, and the "needs review"
rules stay unit-testable without a window.

A score exists only on a rubric dimension. An annotation never carries its own
deduction; it explains how one problem affected the dimension it belongs to.
"""
from __future__ import annotations

from html import escape

from .domain import MATERIAL_ISSUE_TYPES, normalise_issue_type

UNASSIGNED_ID = ""
DECISION_LABELS = {"uphold": "维持原判断", "revise": "建议修正", "unclear": "存疑"}
_MATERIAL_CLAIMS = ("材料", "原文", "给定资料", "要点")
_PENDING_COMMON = 4


def _text(value): return value if isinstance(value, str) else ""
def _rows(value): return value if isinstance(value, list) else []
def _mapping(value): return value if isinstance(value, dict) else {}
def _html(value): return escape(_text(value)).replace("\n", "<br>")


def _refs(items):
    result = []
    for item in _rows(items):
        identity, quote = _text(_mapping(item).get("id")), _text(_mapping(item).get("quote"))
        if identity and quote:
            ref = {"id": identity, "quote": quote}
            if ref not in result:
                result.append(ref)
    return result


def _common_fragment(text, other, size=_PENDING_COMMON):
    text, other = _text(text), _text(other)
    if len(other) < size:
        return ""
    for start in range(len(other) - size + 1):
        piece = other[start:start + size]
        if piece.strip() and piece in text:
            return piece
    return ""


def _shared_uncertainty(review, annotation):
    haystack = " ".join(_text(annotation.get(key)) for key in ("issue", "suggestion", "quote"))
    for item in _rows(_mapping(review.get("assessment")).get("uncertainties")):
        if isinstance(item, str) and len(item.strip()) >= _PENDING_COMMON:
            piece = _common_fragment(haystack, item)
            if piece:
                return piece
    return ""


def needs_review_reasons(review, annotation, recheck=None):
    """Locally decide why one annotation is not safe to treat as settled."""
    if not isinstance(annotation, dict):
        return []
    reasons = []
    issue_type = normalise_issue_type(annotation.get("issue_type"))
    if issue_type in MATERIAL_ISSUE_TYPES and not _refs(annotation.get("material_refs")):
        reasons.append("属于" + issue_type + "判断，但没有附材料原文引用")
    shared = _shared_uncertainty(review, annotation)
    if shared:
        reasons.append("与待确认事项中的“" + shared + "”相关")
    if _text(_mapping(recheck).get("decision")) == "unclear":
        reasons.append("复核结论为存疑")
    return reasons


def dimension_review_reason(dimension, items):
    """A dimension that talks about 材料 but shows no quote cannot be verified."""
    reason = _text(_mapping(dimension).get("reason"))
    if not reason or not any(word in reason for word in _MATERIAL_CLAIMS):
        return ""
    if any(_refs(_mapping(item).get("material_refs")) for item in _rows(items)):
        return ""
    return "该维度提到材料判断，但没有列出可核对的材料原文引用"


def _recheck_for(rechecks, index):
    if not isinstance(rechecks, dict):
        return None
    for key in (index, str(index)):
        verdict = rechecks.get(key)
        if isinstance(verdict, dict):
            return verdict
    return None


def _recheck_view(verdict):
    verdict = _mapping(verdict)
    decision = _text(verdict.get("decision"))
    if decision not in DECISION_LABELS:
        return None
    return {"decision": decision, "label": DECISION_LABELS[decision],
            "reason": _text(verdict.get("reason")),
            "suggested_issue": _text(verdict.get("suggested_issue")),
            "suggested_suggestion": _text(verdict.get("suggested_suggestion")),
            "evidence_refs": _refs(verdict.get("evidence_refs")),
            "created_at": _text(verdict.get("created_at"))}


def dimension_score_text(dimension):
    dimension = _mapping(dimension)
    score = dimension.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return ""
    text = "得分 %g / 100" % score
    weight = dimension.get("weight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        text += " · 权重 %g%%" % weight
    return text


def trace_rows(review, rechecks=None):
    """Group annotations under the dimensions they affected.

    ``rechecks`` maps an annotation index to the latest verdict. Every row keeps
    its own pending reasons so the window and the report never disagree.
    """
    review = _mapping(review)
    assessment = _mapping(review.get("assessment"))
    diagnostic = assessment.get("mode") == "diagnostic"
    rows, by_id = [], {}
    for item in _rows(assessment.get("dimensions")):
        item = _mapping(item)
        identity = _text(item.get("id"))
        row = {"dimension": {"id": identity, "name": _text(item.get("name")) or identity or "未命名维度",
                            "score": item.get("score"), "weight": item.get("weight"),
                            "reason": _text(item.get("reason"))},
               "unassigned": False, "items": [], "pending": False, "pending_reasons": []}
        rows.append(row)
        if identity:
            by_id[identity] = row
    fallback = {"dimension": {"id": UNASSIGNED_ID,
                              "name": "表达与结构批注" if diagnostic else "未归属维度",
                              "score": None, "weight": None, "reason": ""},
                "unassigned": True, "items": [], "pending": False, "pending_reasons": []}
    added = False
    for index, annotation in enumerate(_rows(assessment.get("annotations"))):
        annotation = _mapping(annotation)
        if not annotation:
            continue
        identity = _text(annotation.get("dimension"))
        row = by_id.get(identity)
        if row is None:
            if not added:
                rows.append(fallback)
                added = True
            row = fallback
        verdict = _recheck_for(rechecks, index)
        issue_type = normalise_issue_type(annotation.get("issue_type"))
        reasons = needs_review_reasons(review, annotation, verdict)
        row["items"].append({
            "annotation_index": index, "start": annotation.get("start"), "end": annotation.get("end"),
            "quote": _text(annotation.get("quote")), "issue": _text(annotation.get("issue")),
            "issue_type": issue_type, "issue_type_label": issue_type,
            "suggestion": _text(annotation.get("suggestion")),
            "material_refs": _refs(annotation.get("material_refs")),
            "corpus_refs": _refs(annotation.get("corpus_refs")),
            "dimension_id": row["dimension"]["id"], "needs_review": bool(reasons),
            "review_reasons": reasons, "recheck": _recheck_view(verdict)})
    for row in rows:
        reasons = [] if row["unassigned"] else [dimension_review_reason(row["dimension"], row["items"])]
        reasons.extend(reason for item in row["items"] for reason in item["review_reasons"])
        row["pending_reasons"] = list(dict.fromkeys(reason for reason in reasons if reason))
        row["pending"] = bool(row["pending_reasons"])
    return rows


def trace_counts(rows):
    items = [item for row in _rows(rows) for item in _rows(_mapping(row).get("items"))]
    return {"items": len(items),
            "pending_items": sum(1 for item in items if item.get("needs_review")),
            "pending_rows": sum(1 for row in _rows(rows) if _mapping(row).get("pending")),
            "pending_total": sum(len(_rows(_mapping(row).get("pending_reasons"))) for row in _rows(rows))}


def verification_text(item):
    """One short line for the report table and the desktop tree."""
    item = _mapping(item)
    recheck = item.get("recheck")
    if recheck:
        return recheck["label"] + ("：" + recheck["reason"] if recheck["reason"] else "")
    if item.get("needs_review"):
        return "待核实：" + "；".join(_rows(item.get("review_reasons")))
    return "已附材料引用" if item.get("material_refs") else "未附材料引用"


def _refs_html(refs):
    if not refs:
        return '<span class="muted">未附材料引用</span>'
    return "<br>".join("[" + _html(ref["id"]) + "] " + _html(ref["quote"]) for ref in refs)


def trace_html(rows):
    """Evidence chains as report sections; one table per dimension."""
    parts = ['<section id="score-basis"><h2>评分依据</h2>',
             '<p class="muted">按维度说明分数来源：原答句、材料依据、问题类型与改法。'
             '分数只在维度层面给出并本地按权重计算，单条批注不单独扣分。</p>']
    if not _rows(rows):
        parts.append('<p>本次没有可展开的维度依据。</p>')
    for row in _rows(rows):
        row, dimension = _mapping(row), _mapping(_mapping(row).get("dimension"))
        title = _html(dimension.get("name"))
        score = dimension_score_text(dimension)
        if score:
            title += "　" + _html(score)
        if row.get("unassigned"):
            title += ' <small>（批注未能对应到本次量表维度）</small>'
        parts.append("<h3>" + title + "</h3>")
        if dimension.get("reason"):
            parts.append("<p>为什么是这个分：" + _html(dimension["reason"]) + "</p>")
        items = _rows(row.get("items"))
        if not items:
            parts.append('<p class="muted">该维度没有逐句批注，分数依据见上面的说明。</p>')
        else:
            parts.append('<table><tr><th>原答句</th><th>问题类型</th><th>材料依据</th>'
                         '<th>修改建议</th><th>核实</th></tr>')
            for item in items:
                item = _mapping(item)
                issue = '<strong>' + _html(item.get("issue_type_label")) + '</strong>'
                if item.get("issue"):
                    issue += "<br><small>" + _html(item.get("issue")) + "</small>"
                verify = _html(verification_text(item))
                if item.get("recheck"):
                    verify += "<br><small>复核：" + _html(item["recheck"].get("reason")) + "</small>"
                parts.append("<tr><td>" + _html(item.get("quote")) + "</td><td>" + issue +
                             "</td><td>" + _refs_html(item.get("material_refs")) + "</td><td>" +
                             _html(item.get("suggestion")) + "</td><td>" + verify + "</td></tr>")
            parts.append("</table>")
        if row.get("pending_reasons"):
            parts.append("<p><strong>待核实：</strong>" +
                         "；".join(_html(reason) for reason in row["pending_reasons"]) + "</p>")
    parts.append("</section>")
    return "".join(parts)
