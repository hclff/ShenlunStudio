"""Local rules for contested-annotation rechecks.

A recheck never touches the stored review. This module validates the selected
indices and the model verdicts, so an unverifiable quote downgrades to
``unclear`` instead of quietly becoming a new score.
"""
from __future__ import annotations

MAX_ANNOTATIONS = 12
DECISIONS = ("uphold", "revise", "unclear")


def select_indices(annotations, indices):
    count = len(annotations) if isinstance(annotations, list) else 0
    if not isinstance(indices, (list, tuple)) or not indices:
        raise ValueError('请先勾选需要复核的批注。')
    chosen = []
    for index in indices:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError('批注编号不正确。')
        if index < 0 or index >= count:
            raise ValueError('所选批注已不存在，请重新载入报告。')
        if index not in chosen:
            chosen.append(index)
    if len(chosen) > MAX_ANNOTATIONS:
        raise ValueError('单次最多复核 %d 条批注，请分批进行。' % MAX_ANNOTATIONS)
    return chosen


def material_map(submission):
    result = {}
    for item in submission.get('materials_paragraphs') or []:
        if isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('text'), str):
            if item['text'].strip():
                result[item['id']] = item['text']
    return result


def verified_refs(refs, materials):
    """Keep only quotes that really appear in the cited material paragraph."""
    result = []
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        identity, quote = ref.get('id'), ref.get('quote')
        if not isinstance(identity, str) or not isinstance(quote, str):
            continue
        if identity not in materials or not quote.strip() or quote not in materials[identity]:
            continue
        entry = {'id': identity, 'quote': quote}
        if entry not in result:
            result.append(entry)
    return result


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def validate_verdicts(data, review, indices, materials=None):
    review = review if isinstance(review, dict) else {}
    material_text = materials if isinstance(materials, dict) else material_map(
        review.get('submission') if isinstance(review.get('submission'), dict) else {})
    if not isinstance(data, dict):
        raise ValueError('复核结果格式不正确。')
    raw = data.get('verdicts')
    if not isinstance(raw, list):
        raise ValueError('复核结果缺少结论列表。')
    allowed = set(indices)
    verdicts = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('复核结论格式不正确。')
        index = entry.get('index')
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError('复核结论的批注编号不正确。')
        if index not in allowed:
            raise ValueError('复核结论包含了未勾选的批注。')
        if index in verdicts:
            raise ValueError('同一批注出现了两条复核结论。')
        decision = entry.get('decision')
        if decision not in DECISIONS:
            raise ValueError('复核判定必须是 uphold、revise 或 unclear。')
        reason = _text(entry.get('reason'))
        if not reason:
            raise ValueError('复核结论缺少理由。')
        issue = _text(entry.get('suggested_issue'))
        suggestion = _text(entry.get('suggested_suggestion'))
        if decision == 'revise' and not suggestion:
            raise ValueError('标记为建议修正的复核结论必须给出修改建议。')
        refs = verified_refs(entry.get('evidence_refs'), material_text)
        if entry.get('evidence_refs') and not refs:
            decision = 'unclear'
            reason += '；模型给出的材料引用无法在原文核实'
        verdicts[index] = {'index': index, 'decision': decision, 'reason': reason[:400],
                           'suggested_issue': issue[:400], 'suggested_suggestion': suggestion[:400],
                           'evidence_refs': refs}
    for index in indices:
        if index not in verdicts:
            verdicts[index] = {'index': index, 'decision': 'unclear',
                               'reason': '模型未返回该条批注的复核结论，需人工复核',
                               'suggested_issue': '', 'suggested_suggestion': '', 'evidence_refs': []}
    return verdicts
