"""Synthetic, isolated teaching integration checks shared by source and frozen EXE."""
import json
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import threading
import uuid


def probe_teaching(directory,screenshot=None):
    from .evaluation import run_evaluation
    from .model import DEFAULT_CONFIG
    from .service import AppService
    from .storage import Store
    from .reports import review_html
    from .teaching_catalog import builtin_teaching_methods
    root=Path(directory)/('teaching-probe-'+uuid.uuid4().hex);root.mkdir(parents=True)
    store=Store(root/'synthetic.db')
    initial=store.list_teaching_methods();enabled=store.list_teaching_methods(enabled_only=True)
    community = [r for r in initial if r['id'].startswith('GH_')]
    if len(initial)!=len(builtin_teaching_methods()) or enabled or len(community)!=12:
        raise RuntimeError('内置教学方法和社区方法必须保持未启用草案')
    if any(r['name'].startswith('社区整理·') or '/blob/' not in r['sources'][0]['url'] for r in community):
        raise RuntimeError('社区方法标识或固定版本来源缺失')
    raw={'question':'【合成发布自测】概括社区做法。','materials':'社区保留人工窗口。\n服务站开通电话预约。',
         'answer':'保留人工窗口，开通电话预约。','question_type':'归纳概括','max_score':20,'word_limit':100}
    methods=[]
    for owner in ('limengyuan','fenbi'):
        draft=store.save_teaching_method({'owner_id':owner,'name':'合成验收方法（非教师观点）',
            'purpose':'仅核验程序链路','steps':['从本题材料核对事实'],'checks':['不要捏造引文'],
            'applicable_types':['归纳概括'],'not_applicable':'不作真实教学或评分效果结论',
            'sources':[{'title':'合成来源占位（非教师资料）','url':'https://example.com/synthetic-teaching',
                        'author':'合成验收','published_at':'','accessed_at':'','access_scope':'full_text',
                        'locator':'合成段落1','excerpt':'合成测试原文：保留具体事实。','quote':'保留具体事实'}]})
        methods.append(store.set_teaching_method_enabled(draft['id'],True,reviewer='合成验收声明（非真实审核）',
                        note='仅用于离线软件链路验收',expected_version=draft['version']))
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<2*1024*1024:self.send_error(413);return
            body=json.loads(self.rfile.read(length));payload=json.loads(body['messages'][1]['content']);captured.append(payload)
            s=payload['submission']
            data={'summary':'合成接口固定输出','task_analysis':'概括做法',
                'dimensions':[{'id':d['id'],'score':70,'reason':'合成测试固定分，不作质量结论'} for d in s['rubric']],
                'annotations':[],'material_points':[{'point':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}],
                    'coverage':'covered','answer_quote':'保留人工窗口'}],
                'strengths':[],'next_action':'检查软件链路','uncertainties':['未验证真实模型语义质量'],
                'teaching_analysis':{'applications':[{'method_id':m['id'],'status':'applied','observation':'合成方法对应材料事实',
                    'material_refs':[{'id':'M1','quote':'保留人工窗口'}],'answer_quote':'保留人工窗口'} for m in payload['teaching_guidance']['methods']],
                    'consensus':['核对本题材料'],'differences':['合成接口不代表真实教师观点']}}
            response=json.dumps({'model':'teaching-loopback-fixture','choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}},ensure_ascii=False).encode('utf8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)))
            self.end_headers();self.wfile.write(response)
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    try:
        config={**DEFAULT_CONFIG,'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'teaching-loopback-fixture','timeout':5,'session_token_budget':1000000}
        service=AppService(store,config,'fixture-key-not-real')
        store.set_setting('teaching_selection',{'mode':'source','owner_ids':['limengyuan']})
        single=service.evaluate_submission(raw)
        if len(captured)!=1 or len(single['teaching_snapshot']['methods'])!=1:raise RuntimeError('指定方法额外发起批改请求')
        store.set_setting('teaching_selection',{'mode':'compare','owner_ids':['limengyuan','fenbi']})
        compared=service.evaluate_submission(raw)
        if len(captured)!=2 or len(compared['teaching_snapshot']['methods'])!=2:raise RuntimeError('方法对照额外发起批改请求')
        dataset=store.create_eval_set('合成教学发布验收');case=store.add_eval_case(dataset['id'],raw,origin='gpt_control')
        run=store.create_eval_run(dataset['id'],[case['id']],1,config)
        for method in methods:store.set_teaching_method_enabled(method['id'],False,expected_version=method['version'])
        store.set_setting('teaching_selection',{'mode':'off','owner_ids':[]})
        run_evaluation(store,service,run['id'])
    finally:server.shutdown();server.server_close();thread.join(2)
    frozen=store.list_eval_results(run['id'])[0]['review']['teaching_snapshot']
    if len(captured)!=3 or frozen!=compared['teaching_snapshot'] or store.usage_summary()['total_tokens']!=60 or store.list_reviews():
        raise RuntimeError('教学方法冻结、用量或独立评测状态错误')
    backup=store.backup_to(root/'backup.db');restored=Store(root/'restored.db');restored.restore_from(backup)
    if restored.list_eval_results(run['id'])[0]['review']['teaching_snapshot']!=frozen:raise RuntimeError('教学方法版本备份恢复失败')
    html=review_html(compared)
    if '名师公开方法' not in html or '合成验收声明' not in html or 'synthetic-teaching' not in html:raise RuntimeError('教学报告来源展示缺失')
    (root/'synthetic-teaching-report.html').write_text(html,encoding='utf8')
    if screenshot:
        from PySide6.QtWidgets import QApplication,QTabWidget
        from .ui import MainWindow
        host=MainWindow(store)
        try:
            host.resize(1050,720);host.navigation.setCurrentRow(2);host.show();QApplication.processEvents()
            # The methods workspace keeps publication and teaching sources separate.
            for tabs in host.findChildren(QTabWidget):
                for index in range(tabs.count()):
                    if '名师' in tabs.tabText(index) or '教学' in tabs.tabText(index):tabs.setCurrentIndex(index)
            host.teaching_panel.owner_filter.setCurrentIndex(host.teaching_panel.owner_filter.findData('bailu'))
            host.teaching_panel.select_method('GH_BL01')
            QApplication.processEvents()
            if not host.grab().save(str(screenshot.with_name(screenshot.stem+'-teaching-methods.png'))):raise OSError('无法保存方法库截图')
            host.show_review(compared);QApplication.processEvents();host.result_report.scrollToAnchor('teaching-methods');QApplication.processEvents()
            if not host.grab().save(str(screenshot.with_name(screenshot.stem+'-teaching-report.png'))):raise OSError('无法保存方法报告截图')
        finally:host.close()
    owner_management=probe_teaching_owners(root)
    return {'ok':True,'synthetic_fixtures_only':True,'catalog_drafts':len(initial),'community_drafts':len(community),'initial_enabled':0,
            'loopback_calls':3,'usage_tokens':60,'single_grade_calls':1,'compare_grade_calls':1,
            'frozen_after_disable':True,'backup_restore':True,'ordinary_reviews':0,'cloud_calls':0,
            'owner_management':owner_management}


def probe_teaching_owners(root):
    from .storage import Store
    from .teaching import resolve_teaching_guidance,validate_teaching_snapshot
    from .reports import teaching_review_html
    store=Store(root/'owners.db')
    owner=store.add_teaching_owner('合成发布主体')
    draft=store.save_teaching_method({'owner_id':owner['id'],'name':'主体管理发布测试',
        'purpose':'离线检查','steps':['核对原文'],'applicable_types':['归纳概括'],
        'not_applicable':'仅软件检查','sources':[{'title':'合成来源','url':'https://example.org/test',
        'access_scope':'full_text','locator':'第一段','excerpt':'保留窗口','quote':'保留窗口'}]})
    store.set_teaching_method_enabled(draft['id'],True,reviewer='合成核验',note='离线测试',expected_version=1)
    raw={'question_type':'归纳概括'}
    snapshot=resolve_teaching_guidance(store,raw,{'mode':'source','owner_ids':[owner['id']]})
    store.delete_teaching_owner(owner['id'])
    if any(o['id']==owner['id'] for o in store.list_teaching_owners()) or store.get_teaching_method(draft['id']):
        raise RuntimeError('主体或方法删除后仍可用')
    if draft['id'] not in {m['id'] for m in store.list_deleted_methods('teaching')}:
        raise RuntimeError('主体删除没有保留关联方法')
    validate_teaching_snapshot(snapshot,raw)
    if '合成发布主体' not in teaching_review_html({'teaching_snapshot':snapshot}):
        raise RuntimeError('历史主体名称未保留')
    backup=store.backup_to(root/'owners-backup.db')
    restored=Store(root/'owners-restored.db');restored.restore_from(backup)
    restored.restore_method('teaching',draft['id'])
    if not any(o['id']==owner['id'] for o in restored.list_teaching_owners()) or restored.get_teaching_method(draft['id'])['enabled']:
        raise RuntimeError('主体及停用方法恢复失败')
    return {'added':True,'archived':True,'restored_disabled':True,'historical_name':True,'backup_restore':True}
