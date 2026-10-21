from __future__ import annotations

import copy
import hashlib
import json
import re

from ..domain import RUBRICS, check_refs, corpus_paragraphs, require_list, require_text
from ..model import check_cancel

from .prompts import EXTRACTION_SYSTEM, EXTRACTION_VERSION
from .text import _chunks, _notify, _now


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
            for key in ('name','purpose','not_applicable'):require_text(method.get(key),'方法'+key)
            steps=require_list(method.get('steps'),'方法步骤',15)
            if not steps:raise ValueError('方法需要具体步骤')
            for step in steps:require_text(step,'方法步骤')
            types=require_list(method.get('applicable_types'),'适用题型',5)
            if not types or any(t not in RUBRICS for t in types):raise ValueError('候选方法题型不受支持')
            if not method.get('refs'):raise ValueError('候选方法缺少原文引用')
            check_refs(method['refs'],evidence,'候选方法')
        for limit in require_list(data.get('limitations',[]),'局限',30):require_text(limit,'局限')
        return copy.deepcopy(data)

    def _store_method(self,method,article):
        identity=re.sub(r'\s','',method['name']+'|'+method['purpose'])
        cid='method-'+hashlib.sha256(identity.encode('utf8')).hexdigest()[:24]
        if self.store.is_method_deleted('article',cid):return
        existing=next((c for c in self.store.list_cards() if c['id']==cid),None)
        curation=existing.get('curation') if existing else None
        if isinstance(curation,dict) and curation.get('origin')=='local_manual':return
        card={k:copy.deepcopy(method[k]) for k in ('name','purpose','steps','applicable_types','not_applicable','refs')}
        card.update(id=cid,enabled=False,source_article_ids=[article['id']],source_fingerprints=[article['fingerprint']],
                    source_versions={article['id']:article['fingerprint']},
                    status='候选方法，尚未启用',version=EXTRACTION_VERSION,created_at=_now())
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
            card['variants']=previous_variants+[{'article_id':article['id'],'steps':method['steps'],'not_applicable':method['not_applicable']}]
            # New evidence invalidates prior activation until the user reviews it.
            card['enabled']=False
        from ..method_library import MethodConflictError
        try:self.store.save_card(card,expected_card=existing)
        except MethodConflictError:pass  # A concurrent manual edit/deletion takes precedence.

    def extract(self,article_ids,progress=None,cancel=None):
        result={'processed':0,'skipped':0,'unavailable':0,'image_only':0,'batches':0}
        ids=list(dict.fromkeys(article_ids))
        for n,aid in enumerate(ids,1):
            check_cancel(cancel)
            article=self.store.get_article(aid)
            if not article or article.get('status')!='available':
                result['unavailable']+=1;continue
            if not article.get('training_eligible',True) or not article.get('body','').strip() or not article.get('paragraphs'):
                result['image_only']+=1;continue
            signature=hashlib.sha256(json.dumps({'prompt':EXTRACTION_VERSION,'model':self.config['model'],'base':self.config['base_url'],
                                'body':article['fingerprint']},sort_keys=True).encode()).hexdigest()[:24]
            version='extract-'+signature
            if self.store.get_analysis(aid,version):
                result['skipped']+=1;continue
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

    def _validate_card(self,card):
        actual={};paragraph_fingerprints={};paragraph_sources={};current_versions={}
        for aid in card.get('source_article_ids',[]):
            article=self.store.get_article(aid)
            if article and article.get('status')=='available' and article.get('training_eligible',True):
                paragraphs=corpus_paragraphs([article])
                actual.update(paragraphs)
                paragraph_fingerprints.update({pid:article['fingerprint'] for pid in paragraphs})
                paragraph_sources.update({pid:aid for pid in paragraphs})
                current_versions[aid]=article['fingerprint']
        check_refs(card.get('refs',[]),actual,'方法依据')
        fingerprints={paragraph_fingerprints[ref['id']] for ref in card.get('refs',[])}
        if len(fingerprints)<2:
            raise ValueError('通用方法至少需要两篇不同正文的文章证据；当前可作为单篇分析参考。')
        if card.get('source_fingerprints') and not fingerprints.issubset(set(card['source_fingerprints'])):
            raise ValueError('方法依据的原文已更新，请重新提炼并核对。')
        if card.get('source_versions'):
            cited_sources={paragraph_sources[ref['id']] for ref in card.get('refs',[])}
            if any(card['source_versions'].get(aid)!=current_versions[aid] for aid in cited_sources):
                raise ValueError('方法依据的原文版本已更新，请重新提炼并核对。')
