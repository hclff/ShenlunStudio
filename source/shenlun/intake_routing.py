"""Literal paragraph routing. No classification, rewriting or persistence."""
import hashlib
import json
import re

FIELDS = ('question', 'materials', 'answer', 'reference')
IMPORT_LIMIT = 250000


def split_routing_pages(pages):
    if not isinstance(pages, (list, tuple)):
        raise ValueError('导入页面格式不正确。')
    for page in pages:
        if (not isinstance(page, dict) or not isinstance(page.get('source'), str)
                or type(page.get('number')) is not int or page['number'] < 1
                or not isinstance(page.get('text'), str)):
            raise ValueError('导入页面格式不正确。')
    if len('\n\n'.join(page['text'] for page in pages)) > IMPORT_LIMIT:
        raise ValueError('合并文字不得超过 25 万字。')
    digest = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    segments = []
    for page in pages:
        for paragraph in re.split(r'\r?\n[^\S\r\n]*\r?\n(?:[^\S\r\n]*\r?\n)*', page['text']):
            if paragraph.strip():
                segments.append({'id': f'{digest}:{len(segments)}', 'source': page['source'],
                                 'number': page['number'], 'text': paragraph})
    return segments


def compose_routed_fields(segments, assignments, before, replace_targets):
    if (not isinstance(before, dict) or set(before) != set(FIELDS)
            or any(not isinstance(before[key], str) for key in FIELDS)
            or not isinstance(assignments, dict) or not isinstance(segments, (list, tuple))
            or not isinstance(replace_targets, (set, frozenset, list, tuple))
            or any(target not in FIELDS for target in replace_targets)):
        raise ValueError('内容分配格式不正确。')
    ids = set()
    for segment in segments:
        if (not isinstance(segment, dict) or not isinstance(segment.get('id'), str)
                or not segment['id'] or segment['id'] in ids or not isinstance(segment.get('text'), str)
                or not isinstance(segment.get('source'), str) or type(segment.get('number')) is not int
                or segment['number'] < 1):
            raise ValueError('分段格式不正确或已失效，请重新分配。')
        ids.add(segment['id'])
    if (any(key not in ids for key in assignments)
            or any(not isinstance(target, str) or target not in ('', *FIELDS) for target in assignments.values())):
        raise ValueError('分配目标无效或分段已失效。')
    if sum(len(segment['text']) for segment in segments) > IMPORT_LIMIT:
        raise ValueError('合并文字不得超过 25 万字。')
    result = before.copy()
    for field in FIELDS:
        parts = [segment['text'] for segment in segments
                 if assignments.get(segment['id']) == field and segment['text'].strip()]
        if parts:
            added = '\n\n'.join(parts)
            result[field] = (before[field] + '\n\n' + added
                             if before[field] and field not in replace_targets else added)
    imported_size = sum(len('\n\n'.join(segment['text'] for segment in segments
                        if assignments.get(segment['id']) == field and segment['text'].strip())) for field in FIELDS)
    if imported_size > IMPORT_LIMIT:
        raise ValueError('合并文字不得超过 25 万字。')
    if sum(map(len, result.values())) > 2000000:
        raise ValueError('自动草稿超过200万字符，请先导出或缩小内容。')
    return result
