"""Synthetic, offline CRUD acceptance shared by source and frozen desktop builds."""
from copy import deepcopy
from pathlib import Path
import uuid


def probe_method_library(directory,screenshot=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QDialog,QMessageBox
    from .storage import Store
    from .service import AppService
    from .method_library import MethodConflictError
    from .ui import MainWindow
    from .ui.article_method_editor import ArticleMethodEditorDialog,MethodTrashDialog
    root=Path(directory)/('method-library-probe-'+uuid.uuid4().hex);root.mkdir(parents=True)
    store=Store(root/'synthetic.db')
    for identity,title,body in [('a','合成甲','人工窗口帮助居民办事。'),('b','合成乙','电话预约方便群众咨询。')]:
        store.upsert_article({'id':identity,'title':title,'body':body})
    host=MainWindow(store)
    try:
        host.resize(1050,720);host.navigation.setCurrentRow(2);host.methods_tabs.setCurrentIndex(1)
        host.show();QApplication.processEvents()
        if not host.new_method_button.isEnabled() or not host.deleted_methods_button.isEnabled():
            raise RuntimeError('空方法库缺少新建或已删除入口')
        dialog=ArticleMethodEditorDialog(store,parent=host)
        dialog.name.setText('【合成验收】解释做法的作用');dialog.purpose.setPlainText('测试手动方法保存')
        dialog.steps.setPlainText('核对做法\n核对作用');dialog.type_checks['文章写作'].setChecked(True)
        dialog.boundary.setPlainText('仅合成软件测试，不作教学质量结论')
        for title,quote in [('合成甲','人工窗口'),('合成乙','电话预约')]:
            dialog.article_search.setText(title);dialog.search_articles();dialog.article_list.setCurrentRow(0)
            dialog.quote.setPlainText(quote);dialog.add_evidence()
        dialog.accept()
        if dialog.result()!=QDialog.DialogCode.Accepted:raise RuntimeError(dialog.message.text())
        card=dialog.saved_card
        if len(card['refs'])!=2 or card['enabled']:raise RuntimeError('手动方法证据或草稿状态错误')
        edited=ArticleMethodEditorDialog(store,card,host);edited.name.setText('【合成验收】编辑后的方法');edited.accept()
        if edited.result()!=QDialog.DialogCode.Accepted:raise RuntimeError(edited.message.text())
        AppService(store,{},'').enable_card(card['id'],True)
        frozen=deepcopy(store.list_cards(True)[0])
        rid=store.save_review({'title':'合成方法历史保存测试','submission':{'answer':'合成原答'},
                               'assessment':{},'method_snapshot':[frozen]})
        host.refresh_methods();host.method_table.selectRow(0)
        errors=[]
        def click_after_confirmation(control,answer):
            def choose():
                modal=QApplication.activeModalWidget()
                if isinstance(modal,QMessageBox):modal.button(answer).click()
                else:
                    errors.append('没有显示删除确认')
                    if modal:modal.reject()
            QTimer.singleShot(0,choose);control.click()
            if errors:raise RuntimeError(errors[-1])
        click_after_confirmation(host.delete_method_button,QMessageBox.StandardButton.No)
        if not store.list_cards(True) or host.selected_card()['id']!=card['id']:
            raise RuntimeError('取消删除未保留内容与选择')
        click_after_confirmation(host.delete_method_button,QMessageBox.StandardButton.Yes)
        if store.list_cards() or host.selected_card() is not None:raise RuntimeError('删除文章方法未即时刷新')
        trash=MethodTrashDialog(store,'article',host);trash.method_list.setCurrentRow(0);trash.restore_button.click()
        if trash.result()!=QDialog.DialogCode.Accepted or store.list_cards(True):
            raise RuntimeError('恢复文章方法未保持停用')
        p=host.teaching_panel;p.new_method();p.name.setText('【合成验收】自行新建的教学方法')
        p.purpose.setPlainText('测试新建与删除');p.steps.setPlainText('读题\n核对材料');p.save_current()
        teacher_id=p.current.get('id')
        if not teacher_id:raise RuntimeError('教学方法新建失败')
        click_after_confirmation(p.delete_button,QMessageBox.StandardButton.Yes)
        if store.get_teaching_method(teacher_id) is not None:raise RuntimeError('删除教学方法未生效')
        store.delete_method('article',card['id'])
        backup=store.backup_to(root/'backup.db');restored=Store(root/'restored.db');restored.restore_from(backup)
        if len(restored.list_deleted_methods())!=2 or restored.list_cards() or restored.get_teaching_method(teacher_id):
            raise RuntimeError('备份恢复未保留已删除状态')
        if restored.get_review(rid)['method_snapshot']!=[frozen]:raise RuntimeError('删除改写了历史方法快照')
        restored.restore_method('article',card['id']);restored.restore_method('teaching',teacher_id)
        if restored.list_cards(True) or restored.get_teaching_method(teacher_id)['enabled']:
            raise RuntimeError('恢复误继承启用状态')
        automatic=Store(root/'automatic-synthetic.db')
        for identity in ('a','b'):automatic.upsert_article(store.get_article(identity))
        api=AppService(automatic,{},'')
        raw={**frozen,'name':'【合成验收】自动方法','refs':[frozen['refs'][0]]}
        api._store_method(raw,automatic.get_article('a'))
        generated=automatic.list_cards()[0]
        manual=automatic.save_manual_article_method({**frozen,'name':'【合成验收】保留人工改法'},identity=generated['id'])
        api._store_method(raw,automatic.get_article('b'))
        if automatic.list_cards()!=[manual]:raise RuntimeError('自动提炼覆盖了人工修改')
        actual_enable=automatic.set_card_enabled
        def edit_during_enable(identity,flag,**kwargs):
            automatic.save_manual_article_method({**manual,'refs':[]},identity=identity)
            return actual_enable(identity,flag,**kwargs)
        automatic.set_card_enabled=edit_during_enable
        try:
            try:api.enable_card(manual['id'],True)
            except MethodConflictError:pass
            else:raise RuntimeError('并发修改证据后仍错误启用')
        finally:automatic.set_card_enabled=actual_enable
        if automatic.list_cards(True):raise RuntimeError('并发草稿未保持停用')
        if screenshot:
            store.restore_method('article',card['id']);host.refresh_methods();host.method_table.selectRow(0)
            host.methods_tabs.setCurrentIndex(1);QApplication.processEvents()
            target=screenshot.with_name(screenshot.stem+'-method-library-crud.png')
            if not host.grab().save(str(target)):raise OSError('无法保存方法管理截图')
            preview=ArticleMethodEditorDialog(store,store.list_cards()[0],host)
            preview.resize(760,600);preview.tabs.setCurrentIndex(1);preview.show();QApplication.processEvents()
            target=screenshot.with_name(screenshot.stem+'-method-editor.png')
            if not preview.grab().save(str(target)):raise OSError('无法保存方法编辑器截图')
            preview.reject()
        # Exercise the actual shared trash widgets on a second isolated store.
        # This leaves the screenshot fixture intact and makes no model calls.
        from .teaching import resolve_teaching_guidance
        from .domain import prepare_submission
        restored.set_teaching_method_enabled('GH_BL01',True,reviewer='合成验收',note='仅软件测试')
        submission=prepare_submission({'question':'概括做法','materials':'人工窗口服务居民。','answer':'保留人工窗口。',
                                      'question_type':'归纳概括','max_score':20,'word_limit':100})
        snapshot=resolve_teaching_guidance(restored,submission,{'mode':'source','owner_ids':['bailu']})
        history=restored.save_review({'submission':submission,'teaching_snapshot':snapshot,
            'assessment':{'teaching_analysis':{'applications':[{'method_id':'GH_BL01','status':'not_applicable',
                'observation':'合成验收历史保留','material_refs':[],'answer_quote':''}],
                'consensus':[],'differences':[]}}})
        restored.delete_method('article',card['id']);restored.delete_method('teaching','GH_BL01')
        for kind,identity in [('article',card['id']),('teaching','GH_BL01')]:
            trash=MethodTrashDialog(restored,kind,host)
            index=next(i for i in range(trash.method_list.count())
                       if trash.method_list.item(i).data(256)==identity)
            trash.method_list.setCurrentRow(index)
            click_after_confirmation(trash.purge_button,QMessageBox.StandardButton.No)
            if trash.action or not any(r['id']==identity for r in restored.list_deleted_methods(kind)):
                raise RuntimeError('取消彻底删除丢失内容或选择')
            click_after_confirmation(trash.purge_button,QMessageBox.StandardButton.Yes)
            if trash.action!='purged' or any(r['id']==identity for r in restored.list_deleted_methods(kind)):
                raise RuntimeError('彻底删除未即时移出已删除列表')
            try:restored.restore_method(kind,identity)
            except ValueError:pass
            else:raise RuntimeError('彻底删除后仍可恢复')
        purge_backup=restored.backup_to(root/'purged-backup.db')
        final=Store(root/'purged-restored.db');final.restore_from(purge_backup)
        if (final.list_cards(include_deleted=True) or final.get_teaching_method('GH_BL01',include_deleted=True)
            or final.get_review(history)['teaching_snapshot']!=snapshot):
            raise RuntimeError('彻底删除备份恢复或历史快照保留失败')
        from .method_batch_release_probe import probe_method_batch
        batch=probe_method_batch(root,screenshot)
        return {'ok':True,'synthetic_fixtures_only':True,'create_and_edit':True,'precise_evidence':True,
                'delete_cancel_preserved':True,'article_and_teacher_delete':True,'restored_disabled':True,
                'backup_restore':True,'historical_snapshot_preserved':True,
                'manual_edits_preserved':True,'concurrent_enable_guarded':True,
                'permanent_delete':True,'purge_cancel_preserved':True,'purged_history_preserved':True,
                'batch':batch,'cloud_calls':0}
    finally:host.close();QApplication.processEvents()
