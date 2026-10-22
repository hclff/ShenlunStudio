from __future__ import annotations

import copy
import hashlib
import json
import re

from ..domain import RUBRICS, check_refs, corpus_paragraphs, require_list, require_text
from ..model import check_cancel

from .prompts import (EXTRACTION_SYSTEM, EXTRACTION_VERSION, METHOD_EXTRACTION_SYSTEM,
                      METHOD_EXTRACTION_VERSION)
from .text import _chunks, _notify, _now


def estimate_method_extraction(store,article_ids):
    """Local-only cost preview; callable before any credential is read."""
    articles=[a for a in (store.get_article(i) for i in dict.fromkeys(article_ids))
              if a and a.get('status')=='available' and a.get('training_eligible',True)
              and a.get('body','').strip() and a.get('paragraphs')]
    return {'articles':len(articles),'characters':sum(len(a['body']) for a in articles),
            'estimated_input_tokens':sum(len(a['body'].encode('utf8'))//2 for a in articles),
            'estimated_batches':sum(len(list(_chunks(a['paragraphs']))) for a in articles)}


class ExtractionMixin:
    def estimate_extraction(self,article_ids):
        articles=[self.store.get_article(i) for i in dict.fromkeys(article_ids)]
        articles=[a for a in articles if a and a.get('status')=='available' and a.get('training_eligible',True) and a.get('body','').strip() and a.get('paragraphs')]
        chars=sum(len(a['body']) for a in articles)
        return {'articles':len(articles),'characters':chars,'estimated_input_tokens':sum(len(a['body'].encode('utf8'))//2 for a in articles),
                'estimated_batches':sum(len(list(_chunks(a['paragraphs']))) for a in articles)}

    def _validate_extraction(self,data,paragraphs):
        if not isinstance(data,dict):raise ValueError('文章分析须为JSON对象')
        require_text(data.get('thesis'),'文章主张')
        require_text(data.get('structure'),'文章结构')
        evidence={p['id']:p['text'] for p in paragraphs}
        for role in require_list(data.get('paragraph_roles',[]),'段落作用'):
            if not isinstance(role,dict) or role.get('paragraph_id') not in evidence:
                raise ValueError('文章分析引用了不存在的段落')
            require_text(role.get('role'),'段落作用');require_text(role.get('reason'),'作用理由')
        for chain in require_list(data.get('logic_chains',[]),'论证链',50):
            require_text(chain.get('claim'),'论证主张');require_text(chain.get('analysis'),'论证解释')
            refs=chain.get('evidence_refs',[])
            if not refs:raise ValueError('论证链缺少原文引用')
            check_refs(refs,evidence,'论证链')
        for feat in require_list(data.get('language_features',[]),'语言特点',50):
            require_text(feat.get('feature'),'语言特点');require_text(feat.get('applicability'),'适用情形')
            if not feat.get('refs'):raise ValueError('语言分析缺少原文引用')
            check_refs(feat['refs'],evidence,'语言分析')
        for method in require_list(data.get('methods',[]),'候选方法',15):
            self._validate_method(method,evidence)
        for limit in require_list(data.get('limitations',[]),'局限',30):require_text(limit,'局限')
        return copy.deepcopy(data)

    def _validate_method(self,method,evidence,*,strict=False):
        if not isinstance(method,dict):raise ValueError('候选方法格式不正确')
        for key in ('name','purpose','not_applicable'):require_text(method.get(key),'方法'+key)
        if strict and len(str(method['name']).strip())>100:raise ValueError('方法名称最多100字。')
        steps=require_list(method.get('steps'),'方法步骤',15)
        if not steps:raise ValueError('方法需要具体步骤')
        if strict and not 2<=len(steps)<=6:raise ValueError('方法步骤须为2至6条。')
        for step in steps:require_text(step,'方法步骤')
        types=require_list(method.get('applicable_types'),'适用题型',5)
        if not types or any(t not in RUBRICS for t in types):raise ValueError('候选方法题型不受支持')
        if not method.get('refs'):raise ValueError('候选方法缺少原文引用')
        check_refs(method['refs'],evidence,'候选方法')

    def _store_method(self,method,article,*,version=EXTRACTION_VERSION,strict=False):
        """Merge one extracted method into the library; returns (state, card id).

        ``None`` means the card is deleted or manually curated, so extraction must
        never resurrect or overwrite it.
        """
        identity=re.sub(r'\s','',method['name'].strip()+'|'+method['purpose'].strip())
        cid='method-'+hashlib.sha256(identity.encode('utf8')).hexdigest()[:24]
        if self.store.is_method_deleted('article',cid):return None,None
        existing=next((c for c in self.store.list_cards() if c['id']==cid),None)
        curation=existing.get('curation') if existing else None
        if isinstance(curation,dict) and curation.get('origin')=='local_manual':return None,None
        steps=[str(step).strip() for step in method['steps']]
        if strict and not 2<=len(steps)<=6:raise ValueError('方法步骤须为2至6条。')
        refs=[]
        for ref in method['refs']:
            item={'id':ref['id'],'quote':ref['quote']}
            if item not in refs:refs.append(item)
        card={'name':method['name'].strip(),'purpose':method['purpose'].strip(),'steps':steps,
              'applicable_types':list(dict.fromkeys(method['applicable_types'])),
              'not_applicable':method['not_applicable'].strip(),'refs':refs}
        card.update(id=cid,enabled=False,source_article_ids=[article['id']],source_fingerprints=[article['fingerprint']],
                    source_versions={article['id']:article['fingerprint']},
                    status='候选方法，尚未启用',version=version,created_at=_now())
        if existing:
            previous_refs=existing.get('refs',[])
            previous_variants=existing.get('variants',[])
            versions=dict(existing.get('source_versions',{}))
            if versions.get(article['id']) != article['fingerprint']:
                # Replace this source's obsolete version; retain other sources
                # and accumulate all batches for the same current body.
                previous_refs=[r for r in previous_refs if not r['id'].startswith(article['id']+':p')]
                previous_variants=[v for v in previous_variants if v.get('article_id')!=article['id']]
            card['refs']=previous_refs+[r for r in card['refs'] if r not in previous_refs]
            card['source_article_ids']=list(dict.fromkeys(existing.get('source_article_ids',[])+[article['id']]))
            versions[article['id']]=article['fingerprint']
            card['source_versions']=versions
            card['source_fingerprints']=list(dict.fromkeys(versions.values()))
            variant={'article_id':article['id'],'steps':steps,'not_applicable':card['not_applicable']}
            if not any(old.get('article_id')==article['id'] and old.get('steps')==steps
                       for old in previous_variants):
                previous_variants=previous_variants+[variant]
            card['variants']=previous_variants
            card['created_at']=existing.get('created_at',card['created_at'])
            # New evidence invalidates prior activation until the user reviews it.
            card['enabled']=False
        from ..method_library import MethodConflictError
        try:self.store.save_card(card,expected_card=existing)
        except MethodConflictError:return None,None  # A concurrent manual edit/deletion takes precedence.
        return ('updated' if existing else 'created'),cid

    def extract(self,article_ids,progress=None,cancel=None):
        # The *_ids lists let the caller name the articles it skipped instead of
        # only counting them; they are additive and safe for older callers.
        result={'processed':0,'skipped':0,'unavailable':0,'image_only':0,'batches':0,
                'skipped_ids':[],'unavailable_ids':[],'image_only_ids':[]}
        ids=list(dict.fromkeys(article_ids))
        for n,aid in enumerate(ids,1):
            check_cancel(cancel)
            article=self.store.get_article(aid)
            if not article or article.get('status')!='available':
                result['unavailable']+=1;result['unavailable_ids'].append(aid);continue
            if not article.get('training_eligible',True) or not article.get('body','').strip() or not article.get('paragraphs'):
                result['image_only']+=1;result['image_only_ids'].append(aid);continue
            signature=hashlib.sha256(json.dumps({'prompt':EXTRACTION_VERSION,'model':self.config['model'],'base':self.config['base_url'],
                                'body':article['fingerprint']},sort_keys=True).encode()).hexdigest()[:24]
            version='extract-'+signature
            if self.store.get_analysis(aid,version):
                result['skipped']+=1;result['skipped_ids'].append(aid);continue
            batches=list(_chunks(article['paragraphs']));analyses=[]
            for index,paragraphs in enumerate(batches,1):
                check_cancel(cancel)
                _notify(progress,f'提炼 {n}/{len(ids)}：{article["title"]}（段落批次{index}/{len(batches)}）')
                batch_version=version+f'-batch{index}'
                cached=self.store.get_analysis(aid,batch_version)
                if cached:
                    analyses.append(cached);continue
                response=self._complete(EXTRACTION_SYSTEM,{'title':article['title'],'paragraphs':paragraphs,
                                       'question_types':list(RUBRICS),'batch':index,'batches':len(batches)},cancel)
                analysis=self._validate_extraction(response['data'],paragraphs)
                check_cancel(cancel)
                self.store.save_analysis(aid,analysis,batch_version)
                analyses.append(analysis);result['batches']+=1
            combined={'article_id':aid,'article_title':article['title'],'thesis':'\n'.join(a['thesis'] for a in analyses),
                      'structure':'\n'.join(a['structure'] for a in analyses),'batches':analyses,'model':self.config['model'],
                      'version':version,'created_at':_now()}
            check_cancel(cancel)
            for analysis in analyses:
                for method in analysis.get('methods',[]):self._store_method(method,article)
            self.store.save_analysis(aid,combined,version)
            self.store.save_analysis(aid,combined,'1')
            result['processed']+=1
        _notify(progress,f'提炼完成：新处理{result["processed"]}篇，跳过已完成{result["skipped"]}篇。')
        return result

    def estimate_method_extraction(self,article_ids):
        return estimate_method_extraction(self.store,article_ids)

    def _validate_methods(self,data,paragraphs):
        if not isinstance(data,dict):raise ValueError('方法提取结果须为JSON对象')
        evidence={p['id']:p['text'] for p in paragraphs}
        methods=require_list(data.get('methods',[]),'候选方法',15)
        for method in methods:self._validate_method(method,evidence,strict=True)
        return copy.deepcopy(methods)

    def _cached_methods(self,record):
        """Method list of a cache row, or ``None`` when the row cannot be trusted.

        A truncated or hand-edited row must be treated as absent, otherwise the
        article would fail on every rerun and never heal.
        """
        if not isinstance(record,dict):return None
        methods=record.get('methods')
        return methods if isinstance(methods,list) else None

    def _reuse_full_analysis(self,article):
        """Free path: methods already produced by a full analysis for this body.

        Returns ``None`` when there is no usable analysis (so the method pass
        must run) and a list when it can answer, including an empty list. The
        combined row is preferred; when a full analysis was cancelled before the
        merge, the per-batch caches still count so the article is not paid twice.
        """
        signature=hashlib.sha256(json.dumps({'prompt':EXTRACTION_VERSION,'model':self.config['model'],
                            'base':self.config['base_url'],'body':article['fingerprint']},sort_keys=True).encode()).hexdigest()[:24]
        version='extract-'+signature
        methods=None
        combined=self.store.get_analysis(article['id'],version)
        if combined:methods=self._methods_from_combined(combined)
        if methods is None:methods=self._methods_from_batches(article,version)
        if methods is None:return None
        evidence={p['id']:p['text'] for p in article['paragraphs']}
        try:
            for method in methods:self._validate_method(method,evidence,strict=True)
        except ValueError:
            # The reusable summary is not strict enough; redo this article alone.
            return None
        return copy.deepcopy(methods)

    def _methods_from_combined(self,analysis):
        """Flatten a stored full analysis; ``None`` means the shape is unusable."""
        if not isinstance(analysis,dict):return None
        batches=analysis.get('batches')
        if not isinstance(batches,list):return None
        methods=[]
        for batch in batches:
            if not isinstance(batch,dict):return None
            found=batch.get('methods') or []
            if not isinstance(found,list):return None
            methods.extend(found)
        return methods

    def _methods_from_batches(self,article,version):
        """Rebuild methods from per-batch caches of an interrupted full analysis."""
        methods=[]
        for index,_ in enumerate(_chunks(article['paragraphs']),1):
            batch=self.store.get_analysis(article['id'],f'{version}-batch{index}')
            if not isinstance(batch,dict):return None
            found=batch.get('methods') or []
            if not isinstance(found,list):return None
            methods.extend(found)
        return methods

    def _method_version(self,article):
        signature=hashlib.sha256(json.dumps({'prompt':METHOD_EXTRACTION_VERSION,'model':self.config['model'],
                            'base':self.config['base_url'],'body':article['fingerprint']},sort_keys=True).encode()).hexdigest()[:24]
        return 'methods-'+signature

    def extract_methods(self,article_ids,progress=None,cancel=None):
        """Method-only pass: reusable writing methods become disabled candidates.

        Kept separate from ``extract`` so a long batch can stop after the methods
        without paying for the full analysis, and so each pass has its own cache.
        One unusable article is reported instead of aborting the whole batch.
        """
        result={'processed':0,'skipped':0,'unavailable':0,'image_only':0,'batches':0,'methods':0,
                'created':0,'updated':0,'candidates':0,'eligible':0,'failed':[],
                'skipped_ids':[],'unavailable_ids':[],'image_only_ids':[],'candidate_ids':[]}
        ids=list(dict.fromkeys(article_ids));touched=[]
        for n,aid in enumerate(ids,1):
            check_cancel(cancel)
            article=self.store.get_article(aid)
            if not article or article.get('status')!='available':
                result['unavailable']+=1;result['unavailable_ids'].append(aid);continue
            if not article.get('training_eligible',True) or not article.get('body','').strip() or not article.get('paragraphs'):
                result['image_only']+=1;result['image_only_ids'].append(aid);continue
            version=self._method_version(article)
            cached=self._cached_methods(self.store.get_analysis(aid,version))
            reused=None if cached is not None else self._reuse_full_analysis(article)
            if cached is not None or reused is not None:
                # Re-apply the cached methods so a library cleared by hand heals
                # without another paid request.
                methods=cached if cached is not None else reused
                if reused is not None:
                    self.store.save_analysis(aid,{'article_id':aid,'article_title':article['title'],
                        'methods':methods,'batches':0,'model':self.config['model'],
                        'version':version,'created_at':_now()},version)
                result['skipped']+=1;result['skipped_ids'].append(aid)
            else:
                batches=list(_chunks(article['paragraphs']));methods=[];failure=''
                for index,paragraphs in enumerate(batches,1):
                    check_cancel(cancel)
                    _notify(progress,f'提取方法 {n}/{len(ids)}：{article["title"]}（段落批次{index}/{len(batches)}）')
                    batch_version=version+f'-batch{index}'
                    part=self._cached_methods(self.store.get_analysis(aid,batch_version))
                    if part is None:
                        try:
                            response=self._complete(METHOD_EXTRACTION_SYSTEM,
                                {'title':article['title'],'paragraphs':paragraphs,'question_types':list(RUBRICS),
                                 'batch':index,'batches':len(batches)},cancel)
                            part=self._validate_methods(response['data'],paragraphs)
                        except ValueError as exc:
                            failure=str(exc);break
                        check_cancel(cancel)
                        self.store.save_analysis(aid,{'article_id':aid,'article_title':article['title'],
                            'methods':part,'model':self.config['model'],'version':batch_version,
                            'created_at':_now()},batch_version)
                        result['batches']+=1
                    methods.extend(part)
                if failure:
                    result['failed'].append({'id':aid,'title':article.get('title',''),'error':failure})
                    continue
                combined={'article_id':aid,'article_title':article['title'],'methods':methods,'batches':len(batches),
                          'model':self.config['model'],'version':version,'created_at':_now()}
                check_cancel(cancel)
                self.store.save_analysis(aid,combined,version)
            result['processed']+=1;result['methods']+=len(methods)
            for method in methods:
                try:
                    state,cid=self._store_method(method,article,version=METHOD_EXTRACTION_VERSION,strict=True)
                except ValueError as exc:
                    result['failed'].append({'id':aid,'title':article.get('title',''),'error':str(exc)})
                    continue
                if cid is None:continue
                result[state]+=1;touched.append(cid)
        result['candidate_ids']=list(dict.fromkeys(touched))
        result['candidates']=len(result['candidate_ids'])
        result['eligible']=self._eligible_candidates(result['candidate_ids'])
        _notify(progress,f'提取方法完成：新处理{result["processed"]}篇，复用缓存{result["skipped"]}篇，'
                         f'候选方法{result["candidates"]}条，其中{result["eligible"]}条可启用。')
        return result

    def _eligible_candidates(self,candidate_ids):
        from ..method_library import validate_article_card
        cards={c['id']:c for c in self.store.list_cards()}
        eligible=0
        for cid in candidate_ids:
            card=cards.get(cid)
            if not card or card.get('enabled'):continue
            try:validate_article_card(self.store,card)
            except ValueError:continue
            eligible+=1
        return eligible

    def _validate_card(self,card):
        from ..method_library import validate_article_card
        return validate_article_card(self.store,card)
