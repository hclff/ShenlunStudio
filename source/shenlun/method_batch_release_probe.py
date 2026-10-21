"""Synthetic real-widget batch acceptance shared by source and frozen EXE."""
from pathlib import Path
import uuid


def probe_method_batch(directory,screenshot=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox
    from .storage import Store
    from .ui import MainWindow
    from .ui.teaching import TeachingReviewDialog
    from .ui.method_batch import MethodBatchResultDialog
    root=Path(directory)/('method-batch-'+uuid.uuid4().hex);root.mkdir()
    store=Store(root/'synthetic.db')
    for identity,body in [('a','合成窗口帮助群众办事。'),('b','合成电话预约便于居民咨询。')]:
        store.upsert_article({'id':identity,'title':'合成批量测试'+identity,'body':body})
    draft={'name':'【合成验收】行动与作用','purpose':'仅验证批量程序链路','steps':['核对行动','核对作用'],
        'applicable_types':['归纳概括'],'not_applicable':'不代表实际教学质量',
        'refs':[{'id':'a:p1','quote':'窗口帮助群众'},{'id':'b:p1','quote':'电话预约'}]}
    good=store.save_manual_article_method(draft)
    bad=store.save_manual_article_method({**draft,'name':'【合成验收】无证据草稿','refs':[]})
    host=MainWindow(store);errors=[]
    def click(control,answer=QMessageBox.StandardButton.Yes):
        finished=False
        def handle():
            if finished:return
            modal=QApplication.activeModalWidget()
            if isinstance(modal,TeachingReviewDialog):
                modal.reviewer.setText('合成批量验收');modal.note.setPlainText('仅软件测试，不作教学质量结论');modal.accept()
            elif isinstance(modal,QMessageBox):
                if modal.defaultButton()!=modal.button(QMessageBox.StandardButton.No):errors.append('批量确认未默认取消')
                modal.button(answer).click()
            elif isinstance(modal,MethodBatchResultDialog):
                if screenshot:
                    target=screenshot.with_name(screenshot.stem+'-batch-result.png')
                    if not modal.grab().save(str(target)):errors.append('无法保存批量结果截图')
                modal.accept()
            QTimer.singleShot(0,handle)
        QTimer.singleShot(0,handle)
        control.click();finished=True
        if errors:raise RuntimeError(errors[-1])
    try:
        host.resize(1050,720);host.navigation.setCurrentRow(2);host.methods_tabs.setCurrentIndex(1)
        host.show();QApplication.processEvents();host.article_batch_select_all_button.click()
        if len(host.checked_article_methods())!=2:raise RuntimeError('文章批量勾选失败')
        click(host.article_batch_enable_button,QMessageBox.StandardButton.No)
        if store.list_cards(True) or len(host.checked_article_methods())!=2:raise RuntimeError('取消文章批量操作丢失选择或写入状态')
        click(host.article_batch_enable_button)
        if [r['id'] for r in store.list_cards(True)]!=[good['id']]:raise RuntimeError('文章批量启用绕过证据检查')
        if sorted(r['status'] for r in host.last_method_batch_result)!=['failed','success']:raise RuntimeError('文章批量缺少逐项结果')
        if screenshot:
            if not host.grab().save(str(screenshot.with_name(screenshot.stem+'-batch-articles.png'))):raise OSError('无法保存文章批量截图')
        click(host.article_batch_disable_button)
        if store.list_cards(True) or len(host.checked_article_methods())!=2:raise RuntimeError('文章批量停用或勾选保留失败')
        p=host.teaching_panel;host.methods_tabs.setCurrentIndex(0)
        p.owner_filter.setCurrentIndex(p.owner_filter.findData('limengyuan'));p.batch_select_all_button.click()
        if {r['id'] for r in p.checked_methods()}!={'LM01','LM02','GH_LM01','GH_LM02'}:raise RuntimeError('教学全选超出当前筛选范围')
        click(p.batch_enable_button)
        enabled=store.list_teaching_methods(True)
        if len(enabled)!=4 or any(r['reviewer']!='合成批量验收' for r in enabled):raise RuntimeError('教学批量审核记录缺失')
        if screenshot:
            if not host.grab().save(str(screenshot.with_name(screenshot.stem+'-batch-teaching.png'))):raise OSError('无法保存教学批量截图')
        click(p.batch_disable_button)
        if store.list_teaching_methods(True):raise RuntimeError('教学批量停用失败')
        p.batch_clear_button.click()
        if p.checked_methods() or p.batch_enable_button.isEnabled():raise RuntimeError('清空批量选择失败')
        p.batch_select_all_button.click();p.owner_filter.setCurrentIndex(p.owner_filter.findData(None))
        if p.checked_methods():raise RuntimeError('切换筛选未清空旧勾选')
        restored=Store(root/'restored.db');restored.restore_from(store.backup_to(root/'backup.db'))
        if restored.list_cards(True) or restored.list_teaching_methods(True):raise RuntimeError('备份恢复未保留批量停用状态')
        return {'ok':True,'synthetic_fixtures_only':True,'checkbox_selection':True,'filtered_select_all':True,
            'cancel_preserved':True,'article_eligibility_checked':True,'teaching_audit_saved':True,
            'batch_disable':True,'per_item_results':True,'backup_restore':True,'cloud_calls':0}
    finally:host.close();QApplication.processEvents()
