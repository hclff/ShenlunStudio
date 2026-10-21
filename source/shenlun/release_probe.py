"""Offline frozen-app acceptance in a separate, explicitly supplied fixture database."""
from copy import deepcopy
from pathlib import Path
import uuid


def probe_practice(directory):
    from PySide6.QtCore import QEventLoop,QTimer
    from PySide6.QtWidgets import QApplication
    from .practice import comparison
    from .domain import RUBRICS,prepare_submission
    from .rubrics import PROFILE_CHOICES,resolve_template
    from .storage import Store
    from .ui import MainWindow

    root=Path(directory)/('practice-probe-'+uuid.uuid4().hex)
    root.mkdir(parents=True)
    store=Store(root/'fixture.db')
    fields={'question':'【离线发布自测】概括便民做法','materials':'服务站开通电话报修，派单后回访。',
            'answer':'开通电话报修。','question_type':'归纳概括','max_score':20,'word_limit':150,
            'exam':'江苏省考','category':'B','year':'2026'}
    window=MainWindow(store)
    def check(value,message):
        if not value:raise RuntimeError('练习闭环验收失败：'+message)
    try:
        check(window.load_submission(fields),'载入题目')
        automatic=window.submission()
        check(automatic['rubric_template']['profile']=='jiangsu_b','实际考试类别量表匹配')
        check(prepare_submission(automatic)==automatic,'UI和服务重复处理保留实际模板')
        import json
        custom=json.dumps([{'id':'only','name':'离线自测维度','weight':100,'description':'只用于发布自测'}],ensure_ascii=False)
        window.rubric_edit.setPlainText(custom)
        window.rubric_profile.setCurrentIndex(window.rubric_profile.findData('national_law'))
        check(window.rubric_edit.toPlainText()==custom and window.submission()['rubric_template']['kind']=='custom','切换模板不覆盖自定义量表')
        window.rubric_edit.clear()
        window.rubric_profile.setCurrentIndex(window.rubric_profile.findData('generic'))
        count=0
        for profile,_ in PROFILE_CHOICES:
            if profile=='auto':continue
            for kind in RUBRICS:
                check(sum(row['weight'] for row in resolve_template(kind,selection=profile)['rubric'])==100,'打包模板权重')
                count+=1
        window.save_current_question();qid=window.current_question_id
        check(bool(qid),'保存题库')
        window.answer_edit.setPlainText('  自测未完成原答\n')
        window.rubric_edit.setPlainText('[{"id":')
        # Exercise the real debounce timer, not a direct call to the persistence function.
        loop=QEventLoop();QTimer.singleShot(1200,loop.quit);loop.exec()
        draft=store.load_workspace()
        check(draft['submission']['rubric_text']=='[{"id":','不完整量表自动保存')
        check(draft['submission']['answer']=='  自测未完成原答\n','原答空白与换行保留')
        check(window.close(),'首次关闭')
        window=MainWindow(Store(store.path))
        check(window.current_question_id==qid and window.rubric_edit.toPlainText()=='[{"id":','重新打开恢复')
        check(window.rubric_profile.currentData()=='generic','模板选择重开保留')
        check(window.answer_edit.toPlainText()=='  自测未完成原答\n','重新打开原答')
        window.answer_edit.setPlainText('【未等待自动保存】刚输入的最新原答')
        check(window.new_question_attempt(qid) and not window.answer_edit.toPlainText(),'新作答清空原答')
        check(any(item['submission']['answer']=='【未等待自动保存】刚输入的最新原答'
                  and item['submission']['rubric_text']=='[{"id":' for item in store.list_workspace_history()),'立即开始新作答保留最新草稿')
        fixture_submission=prepare_submission({**fields,'rubric_selection':'generic'})
        first={'title':'【离线合成记录】不是真实模型评测','submission':fixture_submission,
               'assessment':{'total_score':12,'max_score':20,'mode':'full','rubric_fingerprint':fixture_submission['rubric_fingerprint'],
                             'material_points':[],'annotations':[]},
               'model':'offline-release-fixture','prompt_version':'fixture-p1','revisions':{}}
        rid=store.save_review(first)
        second=deepcopy(first);second['submission']['answer']='开通电话报修，派单并回访。';second['assessment']['total_score']=15
        other_id=store.save_review(second)
        check(store.question_for_review(rid)==qid==store.question_for_review(other_id),'同题作答关联')
        saved=store.get_review(rid);saved['revisions']={'minimal':'【离线合成修改稿】'};store.save_review(saved)
        check(len(store.list_question_attempts(qid))==2,'修改稿不增加作答次数')
        window.refresh_questions();window.question_list.setCurrentRow(0)
        for index in range(window.attempt_list.count()):window.attempt_list.item(index).setSelected(True)
        window.compare_attempts()
        check('估分差：3' in window.comparison_browser.toPlainText(),'实际界面对比')
        incompatible=deepcopy(second);incompatible['model']='different-fixture-model'
        check(comparison(first,incompatible)['delta'] is None,'评分条件不一致不算进步')
        store.save_human_score(rid,0,reason='【离线发布自测】零分边界',independent=False)
        check(store.get_human_score(rid)['score']==0 and store.practice_summary()['independent_human_count']==0,'人工零分与独立评分标记')
        check(not window.workers,'没有模型任务')
        check(window.close(),'备份前关闭')
        backup=store.backup_to(root/'backup.db')
        store.set_setting('fixture_after_backup',True)
        safety=store.restore_from(backup)
        check(Store(safety).get_setting('fixture_after_backup') is True,'恢复前安全备份')
        check(store.get_setting('fixture_after_backup') is None and len(store.list_question_attempts(qid))==2,'恢复备份')
        window=MainWindow(Store(store.path))
        check(window.current_question_id==qid and not window.answer_edit.toPlainText(),'恢复后重新打开')
        return {'ok':True,'synthetic_offline_fixtures_only':True,'fixture_directory':str(root),
                'question_bank':True,'real_timer_autosave':True,'invalid_rubric_preserved':True,
                'restart_recovered':True,'new_attempt_archives_draft':True,'dirty_switch_archives_latest':True,'linked_attempts':2,
                'revision_keeps_attempt_count':True,'ui_comparison':True,'incompatible_gain_blocked':True,
                'human_zero_bound':True,'independent_human_scores':0,'backup_restore_safety':True,
                'rubric_templates':count,'template_selection_recovered':True,'custom_override_protected':True,
                'network_model_calls':0}
    finally:
        window.close();QApplication.processEvents()
