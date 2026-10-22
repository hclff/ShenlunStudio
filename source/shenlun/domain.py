"""Local scoring arithmetic and exact-text evidence checks (no AI calls)."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

RUBRIC_VERSION = "training-1"
# Annotation labels are a closed set so the evidence chain stays groupable and
# the "needs material quote" rule below cannot drift with model wording.
ISSUE_TYPE_LABELS = ("审题偏差", "要点遗漏", "材料未用", "表述空泛",
                     "结构松散", "论证不足", "格式不符", "其他")
MATERIAL_ISSUE_TYPES = frozenset({"审题偏差", "要点遗漏", "材料未用"})
_ISSUE_TYPE_ALIASES = {"跑题": "审题偏差", "偏题": "审题偏差", "审题": "审题偏差",
                       "漏点": "要点遗漏", "遗漏要点": "要点遗漏", "覆盖不足": "要点遗漏",
                       "未用材料": "材料未用", "材料": "材料未用",
                       "空泛": "表述空泛", "套话": "表述空泛", "口语": "表述空泛",
                       "结构": "结构松散", "条理": "结构松散",
                       "论证": "论证不足", "逻辑": "论证不足",
                       "格式": "格式不符", "文体": "格式不符"}


def normalise_issue_type(value):
    """Fold model wording onto the closed label set; unknown input becomes 其他."""
    text = value.strip() if isinstance(value, str) else ""
    if text in ISSUE_TYPE_LABELS:
        return text
    for alias, label in _ISSUE_TYPE_ALIASES.items():
        if text and alias in text:
            return label
    return "其他"
RUBRICS = {
    "归纳概括": [("task", "任务对象", 15), ("coverage", "材料覆盖", 40), ("accuracy", "概括准确", 25), ("expression", "组织表达", 15), ("constraints", "字数与形式", 5)],
    "综合分析": [("task", "任务契合", 20), ("evidence", "材料依据", 25), ("reasoning", "关系与推理", 30), ("expression", "组织表达", 20), ("constraints", "字数与形式", 5)],
    "提出对策": [("alignment", "问题对应", 25), ("evidence", "材料依据", 20), ("action", "行动与可行条件", 30), ("expression", "组织表达", 20), ("constraints", "字数与形式", 5)],
    "应用写作": [("task", "任务与受众", 25), ("evidence", "材料信息", 30), ("action", "内容组织与可执行性", 25), ("expression", "语言格式", 15), ("constraints", "字数", 5)],
    "文章写作": [("task", "扣题立意", 25), ("evidence", "材料与论据", 20), ("reasoning", "论证分析", 25), ("structure", "结构", 15), ("expression", "语言", 10), ("constraints", "字数与文体", 5)],
}
DESCRIPTIONS = {
    "task": "依据题干对象、关系、身份和受众判断；不以宏大表述代替扣题。",
    "coverage": "核对指定材料中的实质信息，允许准确的同义概括，不按词面相似度评分。",
    "accuracy": "保留主体、对象、动作、结果与限定，区分问题、原因、做法和成效。",
    "expression": "准确、简洁、连贯且符合文体；小题不强求排比和政策术语。",
    "constraints": "检查本题字数、材料范围与形式要求，不虚构统一的官方扣分标准。",
    "evidence": "材料事实与主张对应；外部案例须有可靠出处，不能虚构数据或政策。",
    "reasoning": "解释证据为何支持主张，检查因果、比较、条件和关系是否成立。",
    "action": "检查措施对应的问题、实施主体、对象、行动及可行条件；应用文须完成沟通任务。",
    "alignment": "措施具体对应材料的问题和成因，避免泛化成宣传、重视的套话。",
    "structure": "论点之间有区分和关系，各段服务主张，不预设固定五段三分式。",
}


def rubric_for(question_type: str) -> list[dict]:
    if question_type not in RUBRICS:
        raise ValueError("请选择支持的题型")
    return [{"id": key, "name": name, "weight": weight, "description": DESCRIPTIONS[key]}
            for key, name, weight in RUBRICS[question_type]]


def finite_number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}必须是有限数值")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name}超出允许范围")
    return value


def validate_rubric(rubric):
    if not isinstance(rubric, list) or not 1 <= len(rubric) <= 20:
        raise ValueError("量表须包含 1—20 个维度，权重合计为100")
    ids = set()
    for d in rubric:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"].strip() or d["id"] in ids:
            raise ValueError("量表维度标识缺失或重复")
        if not isinstance(d.get("name"), str) or not d["name"].strip():
            raise ValueError("量表维度需要名称")
        ids.add(d["id"])
        finite_number(d.get("weight"), "维度权重", 0, 100)
        if "description" in d and not isinstance(d["description"], str):
            raise ValueError("量表描述必须是文字")
    if not math.isclose(sum(d["weight"] for d in rubric), 100, abs_tol=1e-8):
        raise ValueError("量表权重合计必须为100")
    return copy.deepcopy(rubric)


def prepare_submission(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("题目信息格式不正确")
    result = {}
    for key in ("question", "materials", "answer", "reference", "exam", "year", "category"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key}必须是文字")
        result[key] = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result["answer"]:
        raise ValueError("请先输入本人答案")
    if sum(len(result[k]) for k in ("question", "materials", "answer", "reference")) > 250000:
        raise ValueError("本题文字过长，请拆分为单题或缩小材料范围")
    result["exam"] = result["exam"] or "通用训练"
    result["question_type"] = raw.get("question_type", "文章写作")
    from .rubrics import submission_rubric
    resolved = submission_rubric({**raw, **result})
    result["rubric"] = resolved['rubric']
    result['rubric_selection'] = raw.get('rubric_selection','auto')
    result['rubric_template'] = resolved['metadata']
    result["max_score"] = finite_number(raw.get("max_score", 100), "题目满分", 0.01, 1000)
    limit = raw.get("word_limit", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100000:
        raise ValueError("字数上限须为非负整数，0 表示未设置")
    result["word_limit"] = limit
    result["word_count"] = len(re.sub(r"\s", "", result["answer"]))
    result["materials_paragraphs"] = [{"id": f"M{n}", "text": t.strip()}
                                       for n, t in enumerate([p for p in result["materials"].split("\n") if p.strip()], 1)]
    result["mode"] = "full" if result["question"] and result["materials"] else "diagnostic"
    result["rubric_version"] = RUBRIC_VERSION
    result["rubric_fingerprint"] = rubric_fingerprint(result['rubric'])
    return result


def rubric_fingerprint(rubric):
    canonical = [{**d, 'weight':float(d['weight'])} for d in sorted(rubric,key=lambda d:d['id'])]
    encoded = json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(encoded.encode('utf8')).hexdigest()[:16]


def require_text(value, field, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"模型结果的{field}须为文字且不能为空")
    return value


def require_list(value, field, maximum=200):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"模型结果的{field}必须为列表且不超过{maximum}项")
    return value


def corpus_paragraphs(corpus):
    mapping = {}
    for item in corpus:
        if "paragraphs" in item:
            for p in item["paragraphs"]:
                mapping[p["id"]] = p["text"]
        elif "id" in item and "text" in item:
            mapping[item["id"]] = item["text"]
    return mapping


def check_refs(refs, mapping, label):
    for ref in require_list(refs, label, 30):
        if not isinstance(ref, dict):
            raise ValueError(f"{label}引用格式不正确")
        quote = require_text(ref.get("quote"), label+"引用")
        if not isinstance(ref.get("id"), str) or ref["id"] not in mapping or quote not in mapping[ref["id"]]:
            raise ValueError(f"{label}引用无法在原文核实，请重新批改")


def validate_assessment(result: dict, submission: dict, corpus: list) -> dict:
    if not isinstance(result, dict):
        raise ValueError("模型批改结果不是结构化对象")
    out = copy.deepcopy(result)
    # Traceability metadata: a dimension id must belong to this rubric, and the
    # issue label must be one of the closed labels. Both are optional so reports
    # written before the fields existed still validate.
    rubric_ids = {d["id"] for d in submission["rubric"]} if submission.get("mode") == "full" else set()
    for field in ("summary", "task_analysis", "next_action"):
        require_text(out.get(field), field)
    for field in ("strengths", "uncertainties"):
        for item in require_list(out.get(field, []), field, 30):
            require_text(item, field)
    materials = {p["id"]: p["text"] for p in submission["materials_paragraphs"]}
    evidence = corpus_paragraphs(corpus)
    annotations = require_list(out.get("annotations"), "批注")
    seen = set()
    for a in annotations:
        if not isinstance(a, dict):
            raise ValueError("批注格式不正确")
        start, end = a.get("start"), a.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(submission["answer"]):
            raise ValueError("批注原文位置无效")
        quote = require_text(a.get("quote"), "批注原文")
        if submission["answer"][start:end] != quote:
            raise ValueError("批注原文与所指位置不一致")
        require_text(a.get("issue"), "问题说明")
        require_text(a.get("suggestion"), "修改建议")
        dimension = a.get("dimension")
        a["dimension"] = dimension if isinstance(dimension, str) and dimension in rubric_ids else ""
        a["issue_type"] = normalise_issue_type(a.get("issue_type"))
        signature = (start, end, a["issue"])
        if signature in seen:
            raise ValueError("存在重复批注，请重新批改")
        seen.add(signature)
        check_refs(a.get("material_refs", []), materials, "材料")
        check_refs(a.get("corpus_refs", []), evidence, "语料")
    points = require_list(out.get("material_points", []), "材料要点")
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("材料要点格式不正确")
        require_text(point.get("point"), "材料要点")
        if point.get("coverage") not in ("covered", "partial", "missing", "uncertain"):
            raise ValueError("材料要点覆盖状态无效")
        refs = point.get("material_refs", [])
        check_refs(refs, materials, "材料要点")
        if not refs:
            raise ValueError("材料要点必须附可核实的材料引用")
        aq = require_text(point.get("answer_quote", ""), "答案对应内容", True)
        if aq and aq not in submission["answer"]:
            raise ValueError("材料要点所指答案原文无法核实")
        if point["coverage"] in ("covered", "partial") and not aq:
            raise ValueError("已覆盖的材料要点须对应答案原文")
    warnings = []
    if submission["mode"] == "diagnostic":
        if points or any(a.get("material_refs") for a in annotations):
            raise ValueError("缺少题干或材料时不能判断材料漏点或作材料评分")
        out["dimensions"], out["total_score"], out["material_points"] = [], None, []
        warnings.append("缺少题干或给定材料，仅作表达与结构诊断，不提供完整题目分数。")
    else:
        if not points:
            raise ValueError("完整训练评分必须提供可核对的材料要点与证据对应")
        dims = require_list(out.get("dimensions"), "分项评分", 20)
        ids = [d.get("id") for d in dims if isinstance(d, dict)]
        wanted = [d["id"] for d in submission["rubric"]]
        if len(ids) != len(dims) or len(ids) != len(set(ids)) or set(ids) != set(wanted):
            raise ValueError("评分维度缺失、重复或与本题量表不一致")
        by_id = {d["id"]: d for d in dims}
        ordered = []
        for d in submission["rubric"]:
            scored = by_id[d["id"]]
            finite_number(scored.get("score"), "维度得分", 0, 100)
            require_text(scored.get("reason"), "分项评分依据")
            ordered.append({**scored, "name": d["name"], "weight": d["weight"]})
        out["dimensions"] = ordered
        out["total_score"] = round(sum(d["score"] * d["weight"] for d in ordered) / 10000 * submission["max_score"], 2)
    if submission["word_limit"] and submission["word_count"] > submission["word_limit"]:
        warnings.append(f"字数超限：{submission['word_count']}字，上限{submission['word_limit']}字（非空白字符含标点计数）。")
    template = submission.get('rubric_template')
    if template:
        warnings.extend(template.get('warnings',[]))
        out['rubric_template'] = copy.deepcopy(template)
    else:
        out.pop('rubric_template',None)
    out.update(mode=submission["mode"], max_score=submission["max_score"], word_count=submission["word_count"],
               rubric_version=RUBRIC_VERSION, rubric_fingerprint=rubric_fingerprint(submission['rubric']), warnings=warnings)
    return out
