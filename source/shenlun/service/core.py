from __future__ import annotations


from ..model import DEFAULT_CONFIG, JSONOutputError, ModelClient, ModelError, check_cancel

from .prompts import PROMPT_VERSION
from .text import _keyword_list, _now


class CoreService:
    def recognize_image(self,data,progress=None,cancel=None):
        """Transcribe one explicitly approved image without creating an assessment."""
        from ..vision_ocr import recognize_image
        return recognize_image(data,self.config,self.key,store=self.store,progress=progress,cancel=cancel)

    def __init__(self,store,config,key):
        self.store=store
        self.config={**DEFAULT_CONFIG,**{k:v for k,v in config.items() if k in DEFAULT_CONFIG}}
        self.key=key
        self.client=None

    def _complete(self,system,payload,cancel,*,retry_json=True):
        check_cancel(cancel)
        if self.client is None:self.client=ModelClient(self.config,self.key)
        for attempt in range(2):
            check_cancel(cancel)
            prompt = system if attempt == 0 else system + '\n请重新完成同一任务，直接输出完整的 JSON 对象，勿留空、勿附加 Markdown 或解释文字。'
            try:
                response=self.client.complete(prompt,payload,cancel)
            except ModelError as exc:
                metadata=getattr(exc,'response_metadata',None)
                if metadata is not None:self._record_response_usage(metadata)
                if retry_json and attempt == 0 and isinstance(exc, JSONOutputError):
                    continue
                raise
            self._record_response_usage(response)
            check_cancel(cancel)
            return response

    def _record_response_usage(self,response):
        usage={**response['usage'],'model':response.get('model',self.config['model']),
               'prompt_version':PROMPT_VERSION,'created_at':_now(),'estimated':response.get('usage_estimated',False)}
        # Usage is recorded even if semantic validation later rejects the output.
        self.store.record_usage(usage)

    def _retrieve(self,s):
        keywords=_keyword_list(s['question']+' '+s['answer'])
        found={}
        for keyword in keywords:
            for article in self.store.list_articles(query=keyword,limit=6):
                if article['status']=='available' and article.get('training_eligible',True) and article['kind'] not in ('导读','推荐','统计','人物'):
                    found[article['id']]=article
        scored=[]
        for article in found.values():
            for paragraph in article.get('paragraphs',[]):
                if len(paragraph['text'])<25:continue
                score=sum(paragraph['text'].count(k) for k in keywords)
                if score:scored.append((score,article,paragraph))
        scored.sort(key=lambda x:(-x[0],x[1]['id'],x[2]['id']))
        contexts=[];sources={};size=0
        for score,article,p in scored:
            if len(contexts)>=8:break
            # Use exact text; truncation retains the same paragraph id and is
            # validated against only the excerpt actually sent to the model.
            excerpt={"id":p['id'],"text":p['text'][:1200]}
            if size+len(excerpt['text'])>6500:continue
            contexts.append(excerpt);size+=len(excerpt['text'])
            source=sources.setdefault(article['id'],{k:article.get(k,'') for k in ('id','title','journal','url','publication','year','issue')})
            source.setdefault('paragraphs',[]).append(excerpt)
        return contexts,list(sources.values())
