"""Escaped local reports and transparent human/AI comparison metrics."""
from __future__ import annotations

import copy
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from .domain import finite_number


def _e(value):
    return html.escape(str(value if value is not None else ""))


def _text(value):
    return _e(value).replace("\n", "<br>")


def _safe_url(value):
    value = str(value or "")
    u = urlsplit(value)
    return value if u.scheme in ("https","http") and not u.username and not u.password else ""


def _redacted(value):
    if isinstance(value, dict):
        return {k:_redacted(v) for k,v in value.items() if k.lower() not in ("api_key","authorization","password","secret","access_token")}
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    return value


def teaching_review_html(review):
    snapshot=review.get('teaching_snapshot')
    if not isinstance(snapshot,dict):return ''
    from .teaching_catalog import teaching_owners
    names={owner['id']:owner['name'] for owner in teaching_owners()}
    names.update(snapshot.get('owner_names',{}))
    analysis=(review.get('assessment') or {}).get('teaching_analysis') or {}
    applications={row['method_id']:row for row in analysis.get('applications',[]) if isinstance(row,dict) and isinstance(row.get('method_id'),str)}
    modes={'combined':'综合方法','source':'指定来源','compare':'方法对比'}
    parts=['<h2 id="teaching-methods">名师公开方法辅助</h2><p>'+_e(modes.get(snapshot.get('mode'),'方法辅助'))+
           '。以下为软件依公开方法生成的分析，不代表老师本人作答或评分；本地核验者不代表老师授权。评分仍按本题材料和适用量表。</p>']
    for method in snapshot.get('methods',[]):
        row=applications.get(method.get('id'),{})
        parts.append('<section><h3>'+_e(names.get(method.get('owner_id'),method.get('owner_id')))+' · '+_e(method.get('name'))+'</h3>')
        parts.append('<p>方法 '+_e(method.get('id'))+' · v'+_e(method.get('version'))+'；本地核验：'+_e(method.get('reviewer'))+'</p>')
        parts.append('<p>作用：'+_text(method.get('purpose'))+'<br>适用边界：'+_text(method.get('not_applicable'))+'</p>')
        for source in method.get('sources',[]):
            url=_safe_url(source.get('url'));title=_e(source.get('title'))
            parts.append('<p>来源：'+('<a href="'+_e(url)+'">'+title+'</a>' if url else title)+
                         '；访问范围：'+_e({'full_text':'已读正文','index_text':'索引正文','catalog':'课程目录','student_note':'学生笔记'}.get(source.get('access_scope'),source.get('access_scope')))+'；位置：'+_text(source.get('locator'))+
                         '<br>原文短锚点：'+_text(source.get('quote'))+'</p>')
        parts.append('<p>本题应用：'+_e({'applied':'已应用','not_applicable':'本题不适用','uncertain':'不确定'}.get(row.get('status'),'未记录'))+
                     '<br>'+_text(row.get('observation'))+'</p>')
        for ref in row.get('material_refs',[]):parts.append('<p>材料 ['+_e(ref.get('id'))+']：'+_text(ref.get('quote'))+'</p>')
        if row.get('answer_quote'):parts.append('<blockquote>'+_text(row['answer_quote'])+'</blockquote>')
        parts.append('</section>')
    for key,label in (('consensus','本题方法共识'),('differences','本题方法差异与边界')):
        if analysis.get(key):parts.append('<h3>'+label+'</h3>'+''.join('<p>'+_text(item)+'</p>' for item in analysis[key]))
    return ''.join(parts)


def review_html(review: dict) -> str:
    from .review_learning import summary_html
    s, a, revisions = review.get("submission",{}), review.get("assessment",{}), review.get("revisions",{})
    total = a.get("total_score")
    score = "仅作表达与结构诊断" if total is None else f"训练估分 {_e(total)} / {_e(a.get('max_score',100))}"
    result = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>申论训练批改报告</title>',
              '<style>body{font-family:"Microsoft YaHei","SimSun",sans-serif;line-height:1.8;color:#23313f;max-width:1000px;margin:32px auto;padding:20px}h1{font-size:26px}h2{font-size:19px;color:#235964;margin-top:28px}h3{font-size:16px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dbe2e5;padding:10px;vertical-align:top}th{background:#eff5f5}blockquote{margin:12px 0;padding:12px 18px;background:#f4f6f7;border-left:3px solid #76989b}a{color:#18606b}small,.muted{color:#65747b}section{break-inside:avoid}pre{white-space:pre-wrap} @media print{body{margin:0;padding:0;max-width:none}h2,h3{break-after:avoid}a{color:inherit}}</style></head><body>',
              '<p class="muted">申论研习 · 本地训练报告</p>', f'<h1>{_e(review.get("title","申论训练"))}</h1>',
              f'<h2>{score}</h2>', '<p class="muted">训练量表估评，尚未经过真实阅卷评分校准。字数按非空白字符含标点统计。</p>',
              f'<p>{_e(s.get("exam","通用训练"))} · {_e(s.get("year",""))} · {_e(s.get("category",""))} · {_e(s.get("question_type",""))}<br>模型：{_e(review.get("model",""))}　量表：{_e(a.get("rubric_version","training-1"))} / {_e(a.get("rubric_fingerprint","旧记录未标识"))}　字数：{_e(a.get("word_count",s.get("word_count","")))}</p>',
              f'<blockquote>{_text(a.get("summary",""))}</blockquote>']
    result.append(summary_html(review))
    template=a.get('rubric_template') or s.get('rubric_template')
    if isinstance(template,dict):
        result.extend(['<h2>实际使用的训练量表</h2>',
                       '<p>'+_e(template.get('name'))+' · '+_e(template.get('version'))+'</p>',
                       '<p>'+_text(template.get('notice'))+'</p><p>'+_text(template.get('focus'))+'</p>'])
        if template.get('source_title'):
            url=_safe_url(template.get('source_url'))
            title=_e(template.get('source_title'))
            result.append('<p>能力要求参考：'+('<a href="'+_e(url)+'">'+title+'</a>' if url else title)+
                          '；基线年度：'+_e(template.get('source_year'))+'。权重为软件训练设计。</p>')
            result.append('<p class="muted">来源核对范围：'+_text(template.get('source_access'))+'</p>')
    if s.get('rubric'):
        result.append('<h3>本次实际维度与检查项</h3><table><tr><th>维度</th><th>权重</th><th>检查说明</th></tr>')
        for dimension in s['rubric']:
            result.append('<tr><td>'+_e(dimension.get('name',dimension.get('id')))+'<br><small>'+_e(dimension.get('id'))+
                          '</small></td><td>'+_e(dimension.get('weight'))+'%</td><td>'+_text(dimension.get('description',''))+'</td></tr>')
        result.append('</table>')
    for warning in a.get("warnings",[]):
        result.append(f'<p><strong>提示：</strong>{_text(warning)}</p>')
    result.extend([f'<h2>审题与任务</h2><p>{_text(a.get("task_analysis",""))}</p>',
                   f'<h3>题干</h3><p>{_text(s.get("question","未提供"))}</p>',
                   f'<h3>给定材料</h3><p>{_text(s.get("materials","未提供"))}</p>',
                   f'<h2 id="answer-text">本人原答</h2><blockquote>{_text(s.get("answer",""))}</blockquote>'])
    if a.get("dimensions"):
        result.append('<h2>分项训练评价</h2><table><tr><th>维度</th><th>维度得分 / 100</th><th>权重</th><th>依据</th></tr>')
        for d in a["dimensions"]:
            result.append(f'<tr><td>{_e(d.get("name",d.get("id")))}</td><td>{_e(d.get("score"))}</td><td>{_e(d.get("weight"))}%</td><td>{_text(d.get("reason"))}</td></tr>')
        result.append('</table>')
    result.append('<h2>逐处批注</h2>')
    for n, an in enumerate(a.get("annotations",[]),1):
        result.append(f'<section id="learning-annotation-{n-1}"><h3>{n}. {_e(an.get("issue"))}</h3><blockquote>{_text(an.get("quote"))}</blockquote><p>{_text(an.get("suggestion"))}</p>')
        for key,label in (("material_refs","材料依据"),("corpus_refs","写作参考")):
            for ref in an.get(key,[]):
                result.append(f'<p><small>{label} [{_e(ref.get("id"))}]：{_text(ref.get("quote"))}</small></p>')
        result.append('</section>')
    points = a.get("material_points",[])
    if points:
        result.append('<h2>材料要点对照（AI推断，非官方采分点）</h2><table><tr><th>要点</th><th>覆盖情况</th><th>答案对应</th><th>材料证据</th></tr>')
        labels = {"covered":"已覆盖","partial":"部分覆盖","missing":"未覆盖","uncertain":"待确认"}
        for p in points:
            refs='；'.join(f'[{r.get("id","")}] {r.get("quote","")}' for r in p.get('material_refs',[]))
            result.append(f'<tr><td>{_e(p.get("point"))}</td><td>{_e(labels.get(p.get("coverage"),"待确认"))}</td><td>{_text(p.get("answer_quote"))}</td><td>{_text(refs)}</td></tr>')
        result.append('</table>')
    reference=review.get('reference_points_snapshot')
    if isinstance(reference,dict):
        result.append('<h2>人工确认参考要点（训练用途）</h2><p>确认人：'+_e(reference.get('reviewer'))+'；版本 '+_e(reference.get('version'))+'；时间 '+_e(reference.get('created_at'))+'。该快照保留本次实际参考，不是官方采分标准。</p>')
        coverage={row.get('id'):row for row in a.get('reference_point_coverage',[]) if isinstance(row,dict)}
        result.append('<table><tr><th>参考要点</th><th>材料原文</th><th>覆盖情况与原答</th></tr>')
        for point in reference.get('points',[]):
            row=coverage.get(point.get('id'),{});refs='；'.join('['+str(ref.get('id'))+'] '+str(ref.get('quote')) for ref in point.get('material_refs',[]))
            result.append('<tr><td>'+_e(point.get('id'))+' '+_text(point.get('text'))+'</td><td>'+_text(refs)+'</td><td>'+_e({'covered':'已覆盖','partial':'部分覆盖','missing':'未覆盖','uncertain':'待确认'}.get(row.get('coverage'),'待确认'))+'<br>'+_text(row.get('answer_quote'))+'<br>'+_text(row.get('reason'))+'</td></tr>')
        result.append('</table>')
    choice=review.get('method_choice_snapshot')
    articles=review.get('method_snapshot',[])
    if isinstance(choice,dict) or articles:
        result.append('<h2>本次实际方法</h2>')
        if isinstance(choice,dict) and choice.get('name'):
            result.append('<p>方法组合：'+_e(choice['name'])+' · v'+_e(choice.get('version'))+'（'+_e(choice.get('id'))+'）</p>')
        elif isinstance(choice,dict):result.append('<p>'+_e('关闭教学与文章方法' if choice.get('mode')=='off' else '自动匹配已启用方法')+'</p>')
        for method in articles:
            result.append('<p>文章方法：'+_e(method.get('name'))+'（'+_e(method.get('id'))+'）</p>')
        if not articles:result.append('<p>本次未使用文章提炼方法。</p>')
        result.append('<p class="muted">以上保留本次实际输入的方法快照；之后编辑、停用或删除不追溯改变本报告。</p>')
    result.append(teaching_review_html(review))
    result.append('<h2>修改对照</h2><p class="muted">修改稿是另外生成的练习参考，不改变原答得分。</p>')
    result.append(f'<h3>尽量保留原意的修改</h3><blockquote>{_text(revisions.get("minimal") or "尚无修改稿")}</blockquote>')
    result.append(f'<h3>按题目重新组织的示范稿</h3><blockquote>{_text(revisions.get("model") or "尚无示范稿")}</blockquote>')
    result.append(f'<p class="muted">修改状态：{_e(revisions.get("status","未生成"))}</p>')
    result.append(f'<h2>下一次练习</h2><p>{_text(a.get("next_action",""))}</p>')
    if a.get('strengths'):
        result.append('<h3>保留的优点</h3><ul>'+''.join('<li>'+_text(x)+'</li>' for x in a['strengths'])+'</ul>')
    if a.get('uncertainties'):
        result.append('<h3>需要人工确认</h3><ul>'+''.join('<li>'+_text(x)+'</li>' for x in a['uncertainties'])+'</ul>')
    result.append('<h2>写作参考来源</h2>')
    for source in review.get("sources",[]):
        url = _safe_url(source.get("url"))
        link = f'<a href="{_e(url)}">{_e(source.get("title","来源文章"))}</a>' if url else _e(source.get('title','来源文章'))
        result.append(f'<p>{link}　{_e(source.get("journal",""))} {_e(source.get("publication",""))}</p>')
    usage=review.get('usage',{})
    result.append(f'<hr><p class="muted">保存时间：{_e(review.get("created_at",""))}；本次记录用量：{_e(usage.get("total_tokens",0))} tokens。涉及模型调用的训练结论应结合材料和人工意见复核。</p></body></html>')
    return ''.join(result)


def export_review(review, path):
    path=Path(path)
    safe=_redacted(review)
    if path.suffix.lower()=='.json':
        text=json.dumps(safe,ensure_ascii=False,indent=2,allow_nan=False)
    elif path.suffix.lower() in ('.html','.htm'):
        text=review_html(safe)
    elif path.suffix.lower()=='.txt':
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self): super().__init__(); self.parts=[];self.skip=False
            def handle_starttag(self,tag,attrs):
                if tag in ('style','head'):self.skip=True
                if tag in ('p','h1','h2','h3','tr','blockquote','li','br'):self.parts.append('\n')
                if tag=='a':
                    for key,value in attrs:
                        if key=='href' and value.startswith(('https://','http://')):self.parts.append(' '+value+' ')
            def handle_endtag(self,tag):
                if tag in ('style','head'):self.skip=False
                if tag in ('p','h1','h2','h3','tr','blockquote','li'):self.parts.append('\n')
            def handle_data(self,data):
                if not self.skip:self.parts.append(data)
        parser=Text();parser.feed(review_html(safe));text=''.join(parser.parts)
    else:
        raise ValueError('报告支持 .html、.txt、.json；PDF请使用打印导出')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf8')


def _ranks(values):
    order=sorted(range(len(values)),key=values.__getitem__)
    ranks=[0.0]*len(values);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:j+=1
        rank=(i+j-1)/2+1
        for k in order[i:j]:ranks[k]=rank
        i=j
    return ranks


def calibration_metrics(pairs):
    if not isinstance(pairs,list):
        raise ValueError('请提供人工与AI分数对照列表')
    if not pairs:
        return {'n':0,'mae':None,'bias':None,'rmse':None,'spearman':None,'repeat_spread':{}}
    humans=[];ais=[];repeats=defaultdict(list);versions=defaultdict(int)
    for pair in pairs:
        max_score=finite_number(pair.get('max_score',100),'满分',.01,1000)
        human=finite_number(pair.get('human'),'人工分数',0,max_score)
        ai=finite_number(pair.get('ai'),'AI分数',0,max_score)
        humans.append(human*100/max_score);ais.append(ai*100/max_score)
        if pair.get('answer_id'):repeats[str(pair['answer_id'])].append(ai*100/max_score)
        versions[str(pair.get('version','未标注版本'))]+=1
    errors=[a-h for a,h in zip(ais,humans)]
    correlation=None
    if len(pairs)>=2 and len(set(humans))>1 and len(set(ais))>1:
        correlation=round(statistics.correlation(_ranks(humans),_ranks(ais)),6)
    return {'n':len(pairs),'mae':round(statistics.mean(abs(e) for e in errors),6),
            'bias':round(statistics.mean(errors),6),'rmse':round(math.sqrt(statistics.mean(e*e for e in errors)),6),
            'spearman':correlation,'repeat_spread':{k:round(max(v)-min(v),6) for k,v in repeats.items() if len(v)>1},
            'scale':'统一换算为百分制','versions':dict(versions),'calibrated':False}
