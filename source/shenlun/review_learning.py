"""Evidence-bound, local-only summaries and bounded paragraph revision diffs."""
import difflib
from html import escape
from itertools import zip_longest


def _mapping(value): return value if isinstance(value, dict) else {}
def _list(value): return value if isinstance(value, list) else []
def _text(value): return value if isinstance(value, str) else ''
def _html(value): return escape(_text(value)).replace('\n', '<br>')


def _preview(value, length):
    value = ' '.join(_text(value).split())
    return escape(value[:length]+('…' if len(value)>length else ''))


def _materials(review):
    text = _text(_mapping(review.get('submission')).get('materials'))
    return {f'M{i}':p.strip() for i,p in enumerate((p for p in text.split('\n') if p.strip()),1)}


def _refs(items, materials):
    result = []
    for item in _list(items):
        item = _mapping(item); identity, quote = item.get('id'), item.get('quote')
        if isinstance(identity,str) and isinstance(quote,str) and quote.strip() and quote in materials.get(identity,''):
            ref = {'id':identity,'quote':quote}
            if ref not in result: result.append(ref)
    return result


def _annotations(review):
    answer = _text(_mapping(review.get('submission')).get('answer'))
    materials = _materials(review); result = []
    for index, item in enumerate(_list(_mapping(review.get('assessment')).get('annotations'))):
        item = _mapping(item); start, end = item.get('start'), item.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(answer): continue
        if answer[start:end] != item.get('quote'): continue
        result.append({'index':index,'start':start,'end':end,'quote':answer[start:end],
            'issue':_text(item.get('issue')),'suggestion':_text(item.get('suggestion')),
            'refs':_refs(item.get('material_refs'),materials)})
    return result


def learning_summary(review):
    """Order existing missing points, annotations, then dimensions; never infer points lost."""
    assessment = _mapping(review.get('assessment')); materials = _materials(review)
    points = []
    for point in _list(assessment.get('material_points')):
        point = _mapping(point); refs = _refs(point.get('material_refs'), materials)
        if refs and point.get('coverage') in ('missing','partial') and _text(point.get('point')).strip():
            points.append({'title':('未覆盖：' if point['coverage']=='missing' else '部分覆盖：')+point['point'],
                'action':'核对并补充该材料要点。','refs':refs,'annotation_index':None,
                'priority':0 if point['coverage']=='missing' else 2})
    candidates = sorted(points,key=lambda p:p['priority'])
    annotations = [{'title':a['issue'],'action':a['suggestion'],'refs':a['refs'],'annotation_index':a['index']}
                   for a in _annotations(review) if a['issue'] and a['suggestion']]
    candidates = [p for p in candidates if p['priority']==0]+annotations+[p for p in candidates if p['priority']==2]
    dims = [d for d in _list(assessment.get('dimensions')) if isinstance(d,dict)
            and type(d.get('score')) in (int,float) and 0 <= d['score'] < 100 and _text(d.get('reason')).strip()]
    for d in sorted(dims,key=lambda d:d['score']):
        candidates.append({'title':'维度参考：'+_text(d.get('name') or d.get('id')), 'action':d['reason'], 'refs':[], 'annotation_index':None})
    if _text(assessment.get('next_action')).strip():
        candidates.append({'title':'下一步训练','action':assessment['next_action'],'refs':[], 'annotation_index':None})
    result, seen = [], set()
    for item in candidates:
        key = (item['title'],item['action'])
        if key not in seen:
            seen.add(key); result.append({k:v for k,v in item.items() if k!='priority'})
        if len(result)==3: break
    return result


def summary_html(review):
    rows = learning_summary(review)
    parts = ['<section id="learning-summary"><h2>优先改进</h2><p class="muted">按本次报告整理，最多三项；不重新评分。</p>']
    if not rows: parts.append('<p>暂无可整理的改进项，可查看下方完整报告。</p>')
    for n,row in enumerate(rows,1):
        title = _preview(row['title'],60)
        if row['annotation_index'] is not None:
            title = f'<a href="#learning-annotation-{row["annotation_index"]}">{title}</a>'
        parts.append(f'<p><b>{n}. {title}</b><br>{_preview(row["action"],140)}')
        for ref in row['refs'][:1]: parts.append('<br><small>材料片段 ['+_html(ref['id'])+']：'+_preview(ref['quote'],70)+'</small>')
        parts.append('</p>')
    parts.append('</section>')
    return ''.join(parts)


def _paragraphs(text):
    result, offset = [], 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        if content.strip(): result.append({'text':content,'start':offset,'end':offset+len(content)})
        offset += len(line)
    return result


def _inline(before, after, force_coarse=False):
    if before == after: return _html(before),_html(after),False
    coarse = force_coarse or max(len(before),len(after)) > 5000 or len(before)*len(after) > 1_000_000
    if coarse:
        return '<del style="background:#fff0ee">'+_html(before)+'</del>', '<ins style="background:#e4f5ee">'+_html(after)+'</ins>', True
    old, new = [], []
    for kind,i,j,k,l in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes():
        a,b = _html(before[i:j]),_html(after[k:l])
        old.append(a if kind=='equal' else '<del style="background:#fff0ee">'+a+'</del>')
        new.append(b if kind=='equal' else '<ins style="background:#e4f5ee">'+b+'</ins>')
    return ''.join(old),''.join(new),False


def paragraph_diff(review, revision='minimal'):
    if revision not in ('minimal','model'): raise ValueError('修改稿类型不正确。')
    before = _text(_mapping(review.get('submission')).get('answer'))
    after = _text(_mapping(review.get('revisions')).get(revision))
    if not after.strip(): return []
    old, new = _paragraphs(before),_paragraphs(after)
    if len(old)*len(new) > 1_000_000:
        operations = [('replace',0,len(old),0,len(new))]
    else:
        operations = difflib.SequenceMatcher(None,[p['text'] for p in old],[p['text'] for p in new],autojunk=True).get_opcodes()
    annotations = _annotations(review); result = []
    remaining_work = 200_000
    for _,i,j,k,l in operations:
        for left,right in zip_longest(range(i,j),range(k,l)):
            a = old[left] if left is not None else None
            b = new[right] if right is not None else None
            at,bt = a['text'] if a else '', b['text'] if b else ''
            kind = 'insert' if a is None else 'delete' if b is None else 'equal' if at==bt else 'replace'
            cost = len(at)*len(bt) if at!=bt else 0
            old_html,new_html,coarse = _inline(at,bt,cost>remaining_work)
            if not coarse: remaining_work -= cost
            linked = [v for v in annotations if a and kind!='equal' and v['start']<a['end'] and v['end']>a['start']]
            result.append({'kind':kind,'before':at,'after':bt,'before_index':left,'after_index':right,
                'before_html':old_html,'after_html':new_html,'coarse':coarse,'annotations':linked})
    return result
