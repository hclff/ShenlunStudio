"""Isolated, synthetic offline acceptance checks for source and frozen releases."""
from pathlib import Path
import uuid


def probe_learning(directory, screenshot=None):
    from PySide6.QtCore import QDate, QUrl
    from PySide6.QtWidgets import QApplication
    from .storage import Store
    from .ui import MainWindow
    from .ui.review_plans import ReviewPlanDialog, ReviewListDialog
    root = Path(directory)/('learning-probe-'+uuid.uuid4().hex); root.mkdir(parents=True)
    store = Store(root/'synthetic.db')
    review = {'title':'【合成验收】便民服务',
        'submission':{'question':'【合成验收】概括便民做法','materials':'社区开通电话预约。\n服务站保留人工窗口。',
            'answer':'开通电话。\n服务便民。','question_type':'归纳概括','max_score':20,'word_limit':100},
        'assessment':{'total_score':16,'max_score':20,'summary':'合成数据，仅验收程序，不验证评分效果',
            'annotations':[{'start':0,'end':5,'quote':'开通电话。','issue':'措施不具体','suggestion':'核对预约用途',
                'material_refs':[{'id':'M1','quote':'电话预约'}]}],
            'material_points':[{'point':'保留人工窗口','coverage':'missing','answer_quote':'',
                'material_refs':[{'id':'M2','quote':'保留人工窗口'}]}],
            'dimensions':[{'id':'content','name':'内容','score':80,'weight':100,'reason':'合成固定值'}]},
        'revisions':{'minimal':'开通电话预约。\n服务便民。','model':'开通电话预约，保留人工窗口。','status':'完成（合成离线检查）'}}
    rid = store.save_review(review); original = store.get_review(rid)
    qid = store.question_for_review(rid)
    window = MainWindow(store); window.show(); window.show_review(original)
    app = QApplication.instance()
    def capture(suffix, target):
        if screenshot is None:return
        path = Path(screenshot).with_name(Path(screenshot).stem+suffix+'.png')
        path.parent.mkdir(parents=True,exist_ok=True); app.processEvents()
        if not target.grab().save(str(path)):raise RuntimeError('复盘验收截图保存失败。')
    try:
        app.processEvents()
        summary = '优先改进' in window.result_summary.toPlainText() and '保留人工窗口' in window.result_summary.toPlainText()
        capture('-learning-summary',window)
        window.resize(1050,720); capture('-learning-minimum',window); window.resize(1360,900)
        window.open_result_link(QUrl('#learning-annotation-0'))
        annotation = window.result_original.textCursor().selectedText()=='开通电话。'
        window.result_tabs.setCurrentIndex(window.result_tabs.count()-1)
        window.revision_diff_list.setCurrentRow(0); app.processEvents()
        diff = window.revision_diff_list.count()==2 and '电话预约' in window.revision_diff_browser.toPlainText()
        capture('-learning-diff',window)
        editor = ReviewPlanDialog(store,qid,window)
        editor.status.setCurrentText('复习中'); editor.tags.setText('遗漏要点、概括过细')
        editor.scheduled.setChecked(True); editor.due_date.setDate(QDate.currentDate().addDays(3)); editor.save()
        saved = store.get_question(qid)['review_plan']
        plan = saved['version']==1 and saved['tags']==['遗漏要点','概括过细'] and saved['status']=='复习中'
        listing = ReviewListDialog(window); listing.show(); app.processEvents()
        list_ok = listing.items.count()==1
        capture('-review-list',listing); listing.reject()
        backup = store.backup_to(root/'backup.db')
        restored = Store(root/'restored.db'); restored.restore_from(backup)
        backup_ok = restored.get_question(qid)['review_plan']==saved
        snapshot = store.prepare_practice_deletion('questions',[qid])
        store.delete_practice_items('questions',[qid],expected_token=snapshot['token'])
        deleted = store.get_question(qid) is None and not store.list_review_plans()
        result = {'summary':summary,'annotation_navigation':annotation,'paragraph_diff':diff,
            'plan_saved':plan,'review_list':list_ok,'backup_restore':backup_ok,'delete_cleans_plan':deleted,
            'grade_unchanged':store.get_review(rid)==original,'no_model_calls':store.usage_summary()['requests']==0}
        if not all(result.values()):raise RuntimeError('复盘发布验收失败：'+str(result))
        return result
    finally:
        window.close(); app.processEvents()
