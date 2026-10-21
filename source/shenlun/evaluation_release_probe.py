"""Frozen 0.7 fixture-only checks; all labels/answers here are synthetic."""
import json
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import threading
import uuid


def probe_evaluation(directory,screenshot=None):
    from .domain import prepare_submission
    from .evaluation import run_evaluation,eval_run_summary,blind_eval_rows
    from .model import DEFAULT_CONFIG
    from .service import AppService
    from .storage import Store
    root=Path(directory)/('evaluation-probe-'+uuid.uuid4().hex);root.mkdir(parents=True)
    store=Store(root/'synthetic.db')
    raw={'question':'【合成发布自测】概括社区做法。','materials':'社区保留人工窗口。\n服务站开通电话预约。',
         'answer':'保留人工窗口，开通电话预约。','question_type':'归纳概括','max_score':20,'word_limit':100}
    qid=store.save_question(raw,title='离线合成测试题')
    points=[{'id':'P1','text':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}]}]
    draft=store.save_reference_points(qid,points)
    if store.get_active_reference_points(qid) is not None:raise RuntimeError('参考要点草稿被误当确认')
    confirmed=store.save_reference_points(qid,points,reviewer='合成验收确认者（非真实阅卷）',confirm=True,expected_version=draft['version'])
    dataset=store.create_eval_set('离线合成验收，不作质量结论')
    case=store.add_eval_case(dataset['id'],raw,origin='gpt_control',source='软件离线合成用例')
    store.save_eval_label(case['id'],0,reviewer='合成验收标签',independent=False)
    rows=blind_eval_rows(store,dataset['id'])
    if 'gpt_control' in json.dumps(rows) or rows[0]['human'] is not None:raise RuntimeError('盲评导出泄漏控制标签或人工分')
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<2*1024*1024:self.send_error(413);return
            body=json.loads(self.rfile.read(length));payload=json.loads(body['messages'][1]['content'])
            captured.append(payload)
            s=payload['submission']
            data={'summary':'离线测试：对应材料','task_analysis':'概括社区做法','dimensions':[{'id':d['id'],'score':70,'reason':'合成接口固定分，用于校验链路'} for d in s['rubric']],
                  'annotations':[],'material_points':[{'point':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}],'coverage':'covered','answer_quote':'保留人工窗口'}],
                  'strengths':[],'next_action':'测试结果不用于真实评分','uncertainties':['合成接口未评估模型语义质量'],
                  'reference_point_coverage':[{'id':'P1','coverage':'covered','answer_quote':'保留人工窗口','reason':'原文对应'}]}
            response=json.dumps({'model':'evaluation-loopback-fixture','choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)}}],
                                 'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}},ensure_ascii=False).encode('utf8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    try:
        config={**DEFAULT_CONFIG,'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'evaluation-loopback-fixture','timeout':5,'session_token_budget':1000000}
        run=store.create_eval_run(dataset['id'],[case['id']],3,config)
        newer=store.save_reference_points(qid,points,reviewer='第二位合成验收确认者',confirm=True,expected_version=confirmed['version'])
        result=run_evaluation(store,AppService(store,config,'fixture-key-not-real'),run['id'])
    finally:server.shutdown();server.server_close();thread.join(2)
    summary=eval_run_summary(store,run['id'])
    if (result['status']!='completed' or len(captured)!=3 or any(payload['confirmed_reference_points']['version']!=confirmed['version'] for payload in captured)
            or summary['human_pairs']!=0 or summary['mae_percent'] is not None or summary['repeat_groups'][0]['spread']!=0
            or store.list_reviews() or store.usage_summary()['total_tokens']!=60):raise RuntimeError('冻结评测、用量或来源统计失败')
    backup=store.backup_to(root/'backup.db');restored=Store(root/'restored.db');restored.restore_from(backup)
    if len(restored.list_eval_results(run['id']))!=3 or restored.get_active_reference_points(qid)['version']!=newer['version'] or restored.get_eval_label(case['id'])['score']!=0:
        raise RuntimeError('新评测与参考要点备份恢复失败')
    if screenshot:
        from PySide6.QtWidgets import QApplication
        from .ui import MainWindow
        from .ui.reference_points import ReferencePointsDialog
        from .ui.evaluation import EvaluationDialog
        host=MainWindow(store)
        dialogs=[('reference-points',ReferencePointsDialog(store,qid,host)),('evaluation',EvaluationDialog(host))]
        try:
            for name,dialog in dialogs:
                dialog.resize(1050,720);dialog.show();QApplication.processEvents()
                target=screenshot.with_name(screenshot.stem+'-'+name+'.png')
                if not dialog.grab().save(str(target)):raise OSError('无法保存评测界面截图')
                dialog.reject()
        finally:
            for _,dialog in dialogs:dialog.reject()
            host.close()
    return {'ok':True,'synthetic_fixtures_only':True,'reference_versions':newer['version'],'draft_not_active':True,
            'frozen_reference_version':confirmed['version'],'repetitions':3,'loopback_calls':len(captured),
            'usage_tokens':60,'ordinary_reviews':0,'independent_real_human_pairs':0,'mae':None,
            'repeat_spread':0,'new_tables_backup_restore':True,'cloud_calls':0}
