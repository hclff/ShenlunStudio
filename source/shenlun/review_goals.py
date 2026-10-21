"""Allowlisted historical excerpts from the existing local learning summary."""
import hashlib
import json
import re

from .review_learning import learning_summary, _annotations

ALGORITHM_VERSION = 'learning-summary-1'
GOAL_FIELDS = {'id','review_id','basis','algorithm_version','title','action','refs','annotation'}


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def review_goal_basis(review):
    return canonical_hash({key:review.get(key) for key in ('submission','assessment','prompt_version')})


def goal_identity(goal):
    return 'goal-'+canonical_hash({key:value for key,value in goal.items() if key != 'id'})


def _text(value, maximum=2_000_000, *, nonempty=True):
    if not isinstance(value,str) or len(value)>maximum or (nonempty and not value.strip()):
        raise ValueError('训练目标文字不正确或过长。')
    return value


def normalize_goals(values):
    if not isinstance(values,list) or len(values)>1000:
        raise ValueError('训练目标最多1000项。')
    result, seen = [], set()
    for value in values:
        if not isinstance(value,dict) or set(value) != GOAL_FIELDS:
            raise ValueError('训练目标字段不正确。')
        _text(value['review_id'],200)
        if not isinstance(value['basis'],str) or not re.fullmatch('[0-9a-f]{64}',value['basis']):
            raise ValueError('训练目标评分依据不正确。')
        if value['algorithm_version'] != ALGORITHM_VERSION:
            raise ValueError('训练目标摘要版本不正确。')
        _text(value['title']); _text(value['action'])
        refs = value['refs']
        if not isinstance(refs,list) or len(refs)>1000:
            raise ValueError('训练目标材料引用不正确。')
        for ref in refs:
            if (not isinstance(ref,dict) or set(ref) != {'id','quote'} or not isinstance(ref['id'],str)
                    or not re.fullmatch(r'M[1-9][0-9]{0,8}',ref['id'])):
                raise ValueError('训练目标材料引用标识不正确。')
            _text(ref['quote'])
        if len({(ref['id'],ref['quote']) for ref in refs}) != len(refs):
            raise ValueError('训练目标材料引用重复。')
        annotation = value['annotation']
        if annotation is not None:
            if not isinstance(annotation,dict) or set(annotation) != {'index','start','end','quote'}:
                raise ValueError('训练目标批注摘录不正确。')
            if any(type(annotation[k]) is not int for k in ('index','start','end')):
                raise ValueError('训练目标批注位置不正确。')
            if not 0 <= annotation['index'] < 2_000_000 or not 0 <= annotation['start'] < annotation['end'] <= 2_000_000:
                raise ValueError('训练目标批注位置不正确。')
            if len(_text(annotation['quote'])) != annotation['end']-annotation['start']:
                raise ValueError('训练目标批注摘录与位置不一致。')
        if value['id'] != goal_identity(value) or value['id'] in seen:
            raise ValueError('训练目标标识不正确或重复。')
        seen.add(value['id'])
        # Return independent JSON values rather than aliases into caller input.
        result.append(json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False)))
    if sum(len(json.dumps(v,ensure_ascii=False)) for v in result)>2_000_000:
        raise ValueError('训练目标摘录合计超过200万字符。')
    return result


def review_goal_candidates(review):
    if not isinstance(review,dict) or not isinstance(review.get('id'),str) or not review['id']:
        return []
    basis = review_goal_basis(review)
    annotations = {item['index']:item for item in _annotations(review)}
    result = []
    for summary in learning_summary(review):
        annotation = annotations.get(summary['annotation_index'])
        goal = {'review_id':review['id'],'basis':basis,'algorithm_version':ALGORITHM_VERSION,
            'title':summary['title'],'action':summary['action'],'refs':summary['refs'],
            'annotation':{k:annotation[k] for k in ('index','start','end','quote')} if annotation else None}
        goal['id'] = goal_identity(goal)
        result.append(goal)
    return normalize_goals(result)


def validate_goal_sources(conn, goals):
    """Recheck available matching sources; deleted/changed sources remain historical."""
    cache = {}
    for goal in goals:
        rid = goal['review_id']
        if rid not in cache:
            row = conn.execute('SELECT data FROM reviews WHERE id=?',(rid,)).fetchone()
            report = json.loads(row[0]) if row else None
            cache[rid] = report
        report = cache[rid]
        if report and review_goal_basis(report) == goal['basis']:
            if goal not in review_goal_candidates(report):
                raise ValueError('训练目标摘录与来源证据不一致。')
