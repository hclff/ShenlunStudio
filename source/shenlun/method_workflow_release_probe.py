"""Isolated synthetic end-to-end workflow through real Qt and loopback transport."""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import threading
import time
import uuid


def probe_method_workflow(directory,screenshot=None):
    from PySide6.QtCore import QTimer,Qt
    from PySide6.QtWidgets import QApplication,QMessageBox
    from .storage import Store
    from .service import AppService
    from .model import DEFAULT_CONFIG
    from .ui import MainWindow
    from .ui.method_presets import MethodPresetDialog
    from .ui.evaluation import EvaluationDialog
    from .method_comparison import method_comparison_report
    root=Path(directory)/('method-workflow-'+uuid.uuid4().hex);root.mkdir()
    store=Store(root/'synthetic.db')
    for identity,body in [('a','保留人工窗口，帮助群众办事。'),('b','提供电话预约，方便居民咨询。')]:
        store.upsert_article({'id':identity,'title':'【合成验收】'+identity,'body':body})
    draft={'name':'【合成验收】组合方法甲','purpose':'验证程序链路','steps':['核对具体行动','解释实际作用'],
        'applicable_types':['归纳概括'],'not_applicable':'仅合成软件测试，不代表教师观点',
        'refs':[{'id':'a:p1','quote':'保留人工窗口'},{'id':'b:p1','quote':'提供电话预约'}]}
    selected=store.save_manual_article_method(draft)
    other=store.save_manual_article_method({**draft,'name':'【合成验收】其他方法乙'})
    for card in (selected,other):AppService(store,{},'').enable_card(card['id'],True)
    for identity in ('LM01','GH_LM01'):
        store.set_teaching_method_enabled(identity,True,reviewer='合成软件验收',note='仅本地合成链路验证')
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<2*1024*1024:self.send_error(413);return
            body=json.loads(self.rfile.read(length));payload=json.loads(body['messages'][1]['content']);captured.append(payload)
            s=payload['submission'];teaching=payload.get('teaching_guidance')
            data={'summary':'合成固定返回，仅验证程序','task_analysis':'核对程序冻结输入',
                'dimensions':[{'id':d['id'],'score':70 if teaching else 60,'reason':'合成固定值，不作教学效果结论'} for d in s['rubric']],
                'annotations':[],'material_points':[{'point':'保留人工窗口','material_refs':[{'id':'M1','quote':'保留人工窗口'}],
                    'coverage':'covered','answer_quote':'保留人工窗口'}],
                'strengths':[],'next_action':'核对软件链路','uncertainties':['未验证真实模型语义质量']}
            if teaching:
                data['teaching_analysis']={'applications':[{'method_id':m['id'],'status':'not_applicable',
                    'observation':'合成链路测试，不作真实适用性结论','material_refs':[],'answer_quote':''} for m in teaching['methods']],
                    'consensus':[],'differences':[]}
            response=json.dumps({'model':'workflow-loopback-fixture','choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}},ensure_ascii=False).encode('utf8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)))
            self.end_headers();self.wfile.write(response)
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    host=MainWindow(store);dialog=None
    host.config={**DEFAULT_CONFIG,'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'workflow-loopback-fixture',
        'timeout':5,'session_token_budget':1000000}
    host.get_service=lambda require_config=False:AppService(store,dict(host.config),'local-fixture-no-real-key')
    def save(widget,suffix):
        if screenshot:
            QApplication.processEvents()
            if not widget.grab().save(str(screenshot.with_name(screenshot.stem+'-'+suffix+'.png'))):raise OSError('无法保存工作流截图')
    def confirm(control,answer):
        errors=[]
        def choose():
            modal=QApplication.activeModalWidget()
            if isinstance(modal,QMessageBox):
                if modal.defaultButton()!=modal.button(QMessageBox.StandardButton.No):errors.append('方法对照未默认取消')
                modal.button(answer).click()
            else:
                errors.append('缺少方法对照上传确认')
                if modal:modal.reject()
        QTimer.singleShot(0,choose);control.click()
        if errors:raise RuntimeError(errors[-1])
    try:
        host.resize(1050,720);host.navigation.setCurrentRow(2);host.methods_tabs.setCurrentIndex(0);host.show();QApplication.processEvents()
        p=host.teaching_panel;p.owner_filter.setCurrentIndex(p.owner_filter.findData('limengyuan'))
        p.method_search.setText('按所问对象');p.enabled_filter.setCurrentIndex(p.enabled_filter.findData(True))
        p.batch_select_all_button.click()
        if [r['id'] for r in p.checked_methods()]!=['LM01']:raise RuntimeError('教学筛选批量范围不正确')
        save(host,'workflow-filters')
        host.methods_tabs.setCurrentIndex(1);host.article_method_search.setText('组合方法甲');host.article_batch_select_all_button.click()
        if [r['id'] for r in host.checked_article_methods()]!=[selected['id']]:raise RuntimeError('文章筛选批量范围不正确')
        manager=MethodPresetDialog(store,host);manager.name.setText('【合成验收】概括方法组合');manager.question_type.setCurrentText('归纳概括')
        manager.load_current_checks();manager.save_current()
        presets=store.list_method_presets()
        if len(presets)!=1 or presets[0]['teaching_ids']!=['LM01'] or presets[0]['article_ids']!=[selected['id']]:
            raise RuntimeError('组合未准确保存当前勾选')
        manager.show();save(manager,'workflow-preset-manager');manager.reject();preset=presets[0]
        host.question_type.setCurrentText('归纳概括');host.refresh_method_presets()
        host.method_choice.setCurrentIndex(host.method_choice.findData(preset['id']))
        preview=host.method_preview()
        if ([r['id'] for r in preview['article_methods']]!=[selected['id']] or
            [r['id'] for r in preview['teaching_snapshot']['methods']]!=['LM01']):raise RuntimeError('实际方法预览含组合外成员')
        host.navigation.setCurrentRow(0);save(host,'workflow-training')
        dataset=store.create_eval_set('【合成验收】方法对照')
        raw={'question':'概括社区服务做法。','materials':'社区保留人工窗口。\n社区提供电话预约。','answer':'保留人工窗口。',
            'question_type':'归纳概括','max_score':20,'word_limit':100}
        for answer in ('保留人工窗口。','保留人工窗口，提供电话预约。'):
            store.add_eval_case(dataset['id'],{**raw,'answer':answer},origin='gpt_control',source='合成发布验收，不是考生数据')
        dialog=EvaluationDialog(host);dialog.show();dialog.tabs.setCurrentIndex(3);dialog.repeats.setValue(2)
        dialog.comparison_preset.setCurrentIndex(dialog.comparison_preset.findData(preset['id']))
        confirm(dialog.comparison_run_button,QMessageBox.StandardButton.No)
        if captured or store.list_method_comparisons(dataset['id']):raise RuntimeError('取消对照后仍上传或创建轮次')
        confirm(dialog.comparison_run_button,QMessageBox.StandardButton.Yes)
        deadline=time.monotonic()+45
        while dialog._jobs and time.monotonic()<deadline:
            QApplication.processEvents();time.sleep(.01)
        if dialog._jobs:raise RuntimeError('方法对照未在本机验收时限内结束')
        groups=store.list_method_comparisons(dataset['id'])
        if len(groups)!=1 or len(captured)!=8:raise RuntimeError('两组冻结评分的实际调用次数不正确')
        report=method_comparison_report(store,groups[0]['id'])
        if report['paired_cases']!=2 or report['human_pairs']!=0 or report['baseline_mae_percent'] is not None:
            raise RuntimeError('缺少独立人工对照时仍报告准确性')
        for payload in captured[:4]:
            if payload.get('teaching_guidance') or payload.get('method_guidance'):raise RuntimeError('关闭组包含方法指导')
        for payload in captured[4:]:
            if ([m['id'] for m in payload['teaching_guidance']['methods']]!=['LM01'] or len(payload['method_guidance'])!=1):
                raise RuntimeError('组合组实际输入超出指定名单')
        dialog.refresh_method_comparisons(selected=groups[0]['id']);save(dialog,'workflow-comparison')
        dialog.export_method_comparison(root/'comparison.json');dialog.reject()
        restored=Store(root/'restored.db');restored.restore_from(store.backup_to(root/'backup.db'))
        if method_comparison_report(restored,groups[0]['id'])['paired_cases']!=2 or restored.get_method_preset(preset['id'])!=preset:
            raise RuntimeError('组合与对照备份恢复失败')
        return {'ok':True,'synthetic_fixtures_only':True,'filters_scoped':True,'preset_saved_from_checks':True,
            'exact_preview':True,'cancel_before_upload':True,'paired_frozen_calls':True,'missing_humans_not_accuracy':True,
            'backup_restore':True,'loopback_calls':len(captured),'cloud_calls':0,'human_accuracy_verified':False}
    finally:
        if dialog and dialog._jobs:
            for job in list(dialog._jobs):job.cancel()
            deadline=time.monotonic()+8
            while dialog._jobs and time.monotonic()<deadline:QApplication.processEvents();time.sleep(.01)
        if dialog and not dialog._jobs:dialog.reject()
        host.close();QApplication.processEvents();server.shutdown();server.server_close();thread.join(2)
