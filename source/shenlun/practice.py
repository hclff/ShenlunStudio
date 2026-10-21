"""Canonical question identity and evidence-conscious practice comparisons."""
import copy
import difflib
import hashlib
import html
import json
import math

from .domain import RUBRICS,finite_number,rubric_fingerprint,validate_rubric

TEXT_FIELDS=('question','materials','reference','exam','year','category')


def text(value):
    return str(value or '').replace('\r\n','\n').replace('\r','\n').strip()


def mapping(value):return value if isinstance(value,dict) else {}


def question_fingerprint(submission):
    identity=[text(submission.get('question')),text(submission.get('materials')),
              text(submission.get('question_type') or '归纳概括')]
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode('utf8')).hexdigest()


def normalize_question(submission):
    if not isinstance(submission,dict):raise ValueError('题目信息须为对象。')
    result={}
    for key in TEXT_FIELDS:
        value=submission.get(key,'')
        if key in ('year','category') and type(value) in (int,float):value=str(value)
        if not isinstance(value,str):raise ValueError(key+'须为文字。')
        result[key]=text(value)
    if not (result['question'] or result['materials']):raise ValueError('题库保存至少需要题干或给定材料。')
    if sum(len(v) for v in result.values())>250000:raise ValueError('题目超过25万字符，请拆分。')
    kind=submission.get('question_type') or '归纳概括'
    if kind not in RUBRICS:raise ValueError('题型不受支持。')
    result['question_type']=kind
    score=finite_number(submission.get('max_score',20),'题目满分',1,1000)
    if int(score)!=score:raise ValueError('当前题库表单的满分须为整数。')
    result['max_score']=int(score)
    limit=submission.get('word_limit',0)
    if type(limit) is not int or not 0<=limit<=10000:raise ValueError('字数上限须为0—10000的整数。')
    result['word_limit']=limit
    from .rubrics import validate_selection
    result['rubric_selection']=validate_selection(submission.get('rubric_selection','auto'))
    rubric=submission.get('rubric')
    rubric_text=submission.get('rubric_text','')
    if not isinstance(rubric_text,str):raise ValueError('量表草稿须为文字。')
    if rubric_text.strip():
        try:rubric=json.loads(submission['rubric_text'])
        except (ValueError,TypeError):raise ValueError('量表尚未写完；自动草稿已保留，请修正后保存到题库。') from None
    if rubric is not None:
        result['rubric']=[{key:row[key] for key in ('id','name','weight','description') if key in row}
                          for row in validate_rubric(rubric)]
    rubric_size=sum(len(str(value)) for row in result.get('rubric',[]) for value in row.values())
    if sum(len(result[key]) for key in TEXT_FIELDS)+rubric_size>250000:
        raise ValueError('题目及量表超过25万字符，请拆分。')
    if rubric is not None and submission.get('rubric_template'):
        from .rubrics import submission_rubric
        resolved=submission_rubric({**result,'rubric_template':submission['rubric_template']})
        result['rubric_template']=resolved['metadata']
    return copy.deepcopy(result)


def score_value(review):
    assessment=mapping(mapping(review).get('assessment'))
    value=assessment.get('total_score')
    if type(value) not in (int,float):return None
    try:number=float(value)
    except (ValueError,OverflowError):return None
    if not math.isfinite(number):return None
    return number


def scoring_conditions(review):
    review=mapping(review)
    submission=mapping(review.get('submission'))
    assessment=mapping(review.get('assessment'))
    config=review.get('config_snapshot',{})
    if not isinstance(config,dict):config={}
    fingerprint=assessment.get('rubric_fingerprint')
    if not fingerprint and submission.get('rubric'):
        try:fingerprint=rubric_fingerprint(validate_rubric(submission['rubric']))
        except (ValueError,TypeError,OverflowError):pass
    raw_max=assessment.get('max_score',submission.get('max_score'))
    try:maximum=finite_number(raw_max,'题目满分',0.01,1000)
    except (ValueError,TypeError,OverflowError):maximum=None
    mode=assessment.get('mode',submission.get('mode')) or ('full' if submission.get('question') and submission.get('materials') else 'diagnostic')
    if not (text(submission.get('question')) and text(submission.get('materials'))):mode='diagnostic'
    context={'exam':text(submission.get('exam')),'year':text(submission.get('year')),
             'category':text(submission.get('category')),'reference':text(submission.get('reference')),
             'paragraphs':[{'id':p.get('id'),'text':p.get('text')} for source in (review.get('sources') or []) if isinstance(source,dict)
                           for p in (source.get('paragraphs') or []) if isinstance(p,dict)]}
    context_id=hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
    result={'model':review.get('model') or config.get('model') or '',
            'rubric':fingerprint or '', 'prompt':review.get('prompt_version') or '',
            'endpoint':text(config.get('base_url')),'context':context_id,
            'max_score':maximum,'word_limit':submission.get('word_limit',0),'mode':mode,
            'parameters':{key:config[key] for key in ('temperature','send_temperature','max_input_chars','max_output_tokens','token_parameter','json_mode') if key in config},
            'methods':review.get('method_snapshot',[])}
    if submission.get('rubric_template'):result['template']=copy.deepcopy(submission['rubric_template'])
    if review.get('reference_points_snapshot'):
        from .reference_points import reference_conditions
        result['reference_points']=reference_conditions(review['reference_points_snapshot'])
    if review.get('teaching_snapshot'):
        from .teaching import teaching_conditions
        result['teaching_methods']=teaching_conditions(review['teaching_snapshot'])
    return result


def scoring_version(review):
    conditions=scoring_conditions(review)
    if not all(conditions[key] for key in ('model','rubric','prompt','max_score')) or conditions['mode']!='full':return None
    try:encoded=json.dumps(conditions,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
    except (ValueError,TypeError):return None
    return hashlib.sha256(encoded.encode('utf8')).hexdigest()[:20]


def answer_identity(review):
    submission=mapping(mapping(review).get('submission'))
    body=question_fingerprint(submission)+'\n'+text(submission.get('answer'))
    return hashlib.sha256(body.encode('utf8')).hexdigest()[:24]


def _answer_diff(left,right):
    # Bounded line opcodes avoid ndiff's potentially quadratic character matching.
    left_lines=text(left)[:100000].splitlines()[:500]
    right_lines=text(right)[:100000].splitlines()[:500]
    rows=[]
    for kind,a,b,c,d in difflib.SequenceMatcher(None,left_lines,right_lines,autojunk=True).get_opcodes():
        chunks=[('  ',left_lines[a:b])] if kind=='equal' else []
        if kind in ('delete','replace'):chunks.append(('- ',left_lines[a:b]))
        if kind in ('insert','replace'):chunks.append(('+ ',right_lines[c:d]))
        for mark,lines in chunks:
            color='#fff0ed' if mark=='- ' else '#e7f5ef' if mark=='+ ' else '#fff'
            for line in lines:rows.append('<div style="background:'+color+';padding:4px">'+html.escape(mark+line)+'</div>')
    if len(text(left))>100000 or len(text(right))>100000 or len(text(left).splitlines())>500 or len(text(right).splitlines())>500:
        rows.append('<p>差异摘要仅显示前10万字符、500行；上方为完整原答。</p>')
    return ''.join(rows) or '<p>暂无原答文字。</p>'


def comparison(left_review,right_review):
    left=mapping(left_review.get('submission'));right=mapping(right_review.get('submission'))
    if question_fingerprint(left)!=question_fingerprint(right):raise ValueError('请选择同一题目的两次作答。')
    lc,rc=scoring_conditions(left_review),scoring_conditions(right_review)
    reasons=[]
    a,b=score_value(left_review),score_value(right_review)
    if a is None or b is None or lc['mode']!='full' or rc['mode']!='full':reasons.append('有记录仅作诊断或没有有效总分。')
    for key,label in (('model','模型'),('rubric','量表版本'),('prompt','提示版本'),('max_score','满分')):
        if not lc[key] or not rc[key]:reasons.append(label+'信息缺失，评分条件无法确认。')
        elif lc[key]!=rc[key]:reasons.append(label+'不同。')
    for key,label in (('word_limit','字数要求'),('parameters','模型调用参数'),('methods','参考方法快照'),
                      ('endpoint','模型服务地址'),('context','考试信息或参考资料')):
        if lc[key]!=rc[key]:reasons.append(label+'不同。')
    if lc.get('template')!=rc.get('template'):reasons.append('训练量表模板或来源版本不同。')
    if lc.get('reference_points')!=rc.get('reference_points'):reasons.append('人工确认参考要点版本不同。')
    if lc.get('teaching_methods')!=rc.get('teaching_methods'):reasons.append('教学方法选择或版本不同。')
    if any(value is not None and maximum is not None and not 0<=value<=maximum for value,maximum in ((a,lc['max_score']),(b,rc['max_score']))):
        reasons.append('记录得分超出满分范围。')
    comparable=not reasons
    left_html=html.escape(text(left.get('answer'))).replace('\n','<br>')
    right_html=html.escape(text(right.get('answer'))).replace('\n','<br>')
    body='<table width="100%" cellpadding="12"><tr><th>第一次选中作答</th><th>第二次选中作答</th></tr><tr><td valign="top" width="50%">'+left_html+'</td><td valign="top" width="50%">'+right_html+'</td></tr></table>'
    return {'comparable':comparable,'reasons':reasons,'left_score':a,'right_score':b,
            'delta':round(b-a,6) if comparable else None,'left_conditions':lc,'right_conditions':rc,
            'answer_html':body+'<h3>文字变化（红色删除、绿色新增）</h3>'+_answer_diff(left.get('answer'),right.get('answer'))}
