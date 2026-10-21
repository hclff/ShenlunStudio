from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone




def _now():
    return datetime.now(timezone.utc).isoformat()


def _notify(progress, message):
    if progress:
        progress(message)


def _answer_segments(answer):
    return [{"start":m.start(),"end":m.end(),"text":m.group()}
            for m in re.finditer(r"[^。！？\n]+[。！？]?|[。！？]",answer)]


def _keyword_list(text):
    themes = ["服务","群众","治理","生态","环境","文化","创新","科技","教育","人才","发展","经济","产业","乡村","基层","法治","消费","改革","就业","民生","公平","效率","数字","保护","传统","城市","安全","协同","青年"]
    found=[w for w in themes if w in text]
    if found:return found[:8]
    grams=Counter(text[i:i+2] for i in range(len(text)-1) if re.fullmatch(r'[\u4e00-\u9fff]{2}',text[i:i+2]))
    stop={"我们","一个","作为","结合","材料","概括","根据","要求","分析","问题","提出","答案","以下"}
    return [w for w,n in grams.most_common(20) if w not in stop][:6]


def _chunks(paragraphs,limit=16000):
    chunk=[];size=0
    for p in paragraphs:
        text=p['text']
        for start in range(0,max(1,len(text)),limit):
            part=text[start:start+limit]
            if size+len(part)>limit and chunk:
                yield chunk;chunk=[];size=0
            chunk.append({"id":p['id'],"text":part,"source_start":start,"source_end":start+len(part)})
            size+=len(part)
    if chunk:yield chunk
