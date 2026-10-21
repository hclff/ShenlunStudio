"""Real HTTP round-trip integration; fixture scores are not product scoring."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from shenlun.service import AppService
from shenlun.storage import Store
from shenlun.reports import export_review


def test_http_to_validated_review_and_independent_revision(tmp_path):
    class Provider(BaseHTTPRequestHandler):
        calls=0
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data=json.loads(payload['messages'][1]['content'])
            if 'answer_segments' in data:
                s=data['submission'];m=s['materials_paragraphs'][0];seg=data['answer_segments'][0]
                output={'summary':'本地接口测试结果，不是语义评分验证','task_analysis':'测试材料映射','dimensions':[{'id':d['id'],'score':75,'reason':'固定测试夹具，不用于产品评分'} for d in s['rubric']],
                        'annotations':[{'start':seg['start'],'end':seg['end'],'quote':seg['text'],'issue':'测试位置绑定','suggestion':'核查成效概括','material_refs':[{'id':m['id'],'quote':m['text']}],'corpus_refs':[]}],
                        'material_points':[{'point':'报修可及性','material_refs':[{'id':m['id'],'quote':m['text']}],'coverage':'covered','answer_quote':seg['text']}],
                        'strengths':['保留原答'],'next_action':'核查原文定位','uncertainties':[]}
            else:
                output={'minimal':'老人报修更便捷。','model':'人工报修渠道降低使用门槛，便利老年群体。'}
            Provider.calls+=1
            body=json.dumps({'model':'fixture-model','choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)}}],
                             'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}}).encode('utf8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        store=Store(tmp_path/'reviews.db')
        service=AppService(store,{'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'fixture-model'},'')
        result=service.assess({'question':'概括服务改进成效','materials':'社区增设电话报修，老人可以顺利报修。','answer':'老人报修更便捷。','question_type':'归纳概括','max_score':20})
        assert Provider.calls==2
        assert result['assessment']['total_score']==15
        assert result['assessment']['annotations'][0]['quote']=='老人报修更便捷。'
        assert store.get_review(result['id'])['revisions']['model']=='人工报修渠道降低使用门槛，便利老年群体。'
        assert store.usage_summary()['total_tokens']==300
        export_review(result,tmp_path/'报告.html')
        assert '原答' in (tmp_path/'报告.html').read_text(encoding='utf8')
    finally:
        server.shutdown();server.server_close();worker.join()
