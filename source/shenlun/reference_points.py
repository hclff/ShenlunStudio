"""Pure validation of human-confirmed training points and exact-text evidence.

A valid snapshot is a reproducible value, not proof of human provenance by itself.
Runtime trust comes from the local Store's explicit confirmation operation.
"""
import hashlib
import json
import re
from datetime import datetime

from .practice import normalize_question, question_fingerprint


def _text(value, label, maximum, allow_empty=False, strip=True):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(label + '须为限定长度的文字。')
    if not allow_empty and not value.strip():
        raise ValueError(label + '不能为空。')
    return value.strip() if strip else value


def _identity(value, label):
    value = _text(value, label, 128, strip=False)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError(label + '须为本地字母、数字、短横线或下划线标识。')
    return value


def validate_reference_points(points, submission, allow_empty=False):
    """Return detached points citing actual normalized materials, never supplied IDs."""
    fields = normalize_question(submission)
    materials = {f'M{i}': paragraph.strip() for i, paragraph in enumerate(
        [p for p in fields['materials'].split('\n') if p.strip()], 1)}
    if not isinstance(points, list) or len(points) > 50 or (not allow_empty and not points):
        raise ValueError('参考要点须为1—50项；未确认草稿可为空。')
    result, seen = [], set()
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('参考要点须为对象。')
        identity = _identity(point.get('id'), '要点标识')
        if identity in seen:
            raise ValueError('参考要点标识重复。')
        seen.add(identity)
        point_text = _text(point.get('text'), '要点内容', 4000)
        refs = point.get('material_refs')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 30:
            raise ValueError('每个参考要点须附1—30条材料原文引用。')
        canonical_refs = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError('材料引用须为对象。')
            source_id = ref.get('id')
            quote = _text(ref.get('quote'), '材料引文', 4000, strip=False)
            if not isinstance(source_id, str) or source_id not in materials or quote not in materials[source_id]:
                raise ValueError('材料引用无法在对应M段落的实际原文核实。')
            canonical_refs.append({'id': source_id, 'quote': quote})
        result.append({'id': identity, 'text': point_text, 'material_refs': canonical_refs})
    return result


def reference_content_hash(record):
    """Hash the full canonical version record except its own content_hash field."""
    payload = {key: value for key, value in record.items() if key != 'content_hash'}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf8')).hexdigest()


def validate_reference_version(record, submission):
    """Validate any local version for storage/backup; this never confers trust."""
    if not isinstance(record, dict):
        raise ValueError('参考要点版本须为对象。')
    qid = _identity(record.get('question_id'), '题目标识')
    fields = normalize_question(submission)
    fingerprint = question_fingerprint(fields)
    if record.get('question_fingerprint') != fingerprint:
        raise ValueError('参考要点与本题实际题干、材料或题型不一致。')
    version = record.get('version')
    if type(version) is not int or not 1 <= version <= 2147483647:
        raise ValueError('参考要点版本号无效。')
    status = record.get('status')
    if status not in ('draft', 'confirmed', 'withdrawn'):
        raise ValueError('参考要点状态无效。')
    reviewer = _text(record.get('reviewer'), '确认人', 200, allow_empty=status == 'draft')
    note = _text(record.get('note'), '备注', 4000, allow_empty=status != 'withdrawn')
    timestamp = _text(record.get('created_at'), '版本时间', 80, strip=False)
    try:
        instant = datetime.fromisoformat(timestamp)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
    except (ValueError, OverflowError):
        raise ValueError('参考要点版本时间须为含时区的有效时间。') from None
    points = validate_reference_points(record.get('points'), fields, allow_empty=status != 'confirmed')
    result = {'question_id': qid, 'question_fingerprint': fingerprint, 'version': version,
              'status': status, 'points': points, 'reviewer': reviewer, 'note': note, 'created_at': timestamp}
    digest = record.get('content_hash')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or digest != reference_content_hash(result):
        raise ValueError('参考要点内容哈希不一致。')
    result['content_hash'] = digest
    return result


def validate_reference_snapshot(snapshot, submission):
    """Validate a frozen confirmed snapshot against actual question/material/type."""
    result = validate_reference_version(snapshot, submission)
    if result['status'] != 'confirmed':
        raise ValueError('仅人工明确确认的参考要点可用于批改。')
    return result


def validate_point_coverage(rows, snapshot, answer):
    """Require exactly one bounded judgment per point with verifiable answer quotes."""
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'confirmed':
        raise ValueError('需要已验证的人工确认参考要点快照。')
    points = snapshot.get('points')
    if not isinstance(points, list) or not 1 <= len(points) <= 50:
        raise ValueError('参考要点快照格式无效。')
    wanted = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('参考要点快照格式无效。')
        wanted.append(_identity(point.get('id'), '要点标识'))
    if len(set(wanted)) != len(wanted):
        raise ValueError('参考要点标识重复。')
    if not isinstance(answer, str) or len(answer) > 250000:
        raise ValueError('答案须为不超过25万字的文字。')
    if not isinstance(rows, list) or len(rows) != len(wanted):
        raise ValueError('覆盖判断须包含每一个参考要点且只出现一次。')
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('覆盖判断须为对象。')
        identity = row.get('id')
        if not isinstance(identity, str) or identity not in wanted or identity in by_id:
            raise ValueError('覆盖判断的要点标识未知或重复。')
        status = row.get('coverage')
        if status not in ('covered', 'partial', 'missing', 'uncertain'):
            raise ValueError('参考要点覆盖状态无效。')
        quote = _text(row.get('answer_quote', ''), '答案引文', 4000,
                      allow_empty=status in ('missing', 'uncertain'), strip=False)
        if quote and (not quote.strip() or quote not in answer):
            raise ValueError('参考要点覆盖判断的答案引文无法原样核实。')
        reason = _text(row.get('reason', ''), '覆盖说明', 4000, allow_empty=True)
        by_id[identity] = {'id': identity, 'coverage': status, 'answer_quote': quote, 'reason': reason}
    return [by_id[identity] for identity in wanted]


def reference_conditions(snapshot):
    """Return the small stable provenance included in scoring comparisons."""
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        raise ValueError('参考要点条件须为版本快照。')
    for key in ('question_fingerprint', 'content_hash'):
        value = snapshot.get(key)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('参考要点比较条件的哈希无效。')
    if type(snapshot.get('version')) is not int or not 1 <= snapshot['version'] <= 2147483647:
        raise ValueError('参考要点比较条件的版本号无效。')
    return {key: snapshot[key] for key in ('question_fingerprint', 'version', 'content_hash')}
