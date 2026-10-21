"""Real, synthetic 0.9.5 workflow acceptance for source and frozen smoke.

This exercises ordinary workflows; it does not certify every concurrency edge.
No seed, user database, provider, test module or pytest dependency is used.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def _provider_guard():
    """Block and count both provider entry points before any network traffic."""
    from .model import ModelClient
    attempts = []
    originals = (ModelClient.complete, ModelClient.complete_image)
    def blocked(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('离线发布验收禁止外部模型请求。')
    ModelClient.complete = ModelClient.complete_image = blocked
    try:
        yield attempts
    finally:
        ModelClient.complete, ModelClient.complete_image = originals


def probe_workflow(directory, screenshot=None):
    """Run against a fresh child of the caller's explicit smoke directory."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton
    from .auto_backup import AutoBackupManager
    from .intake import ImportBatch, InputPage
    from .reliability_release_probe import _answer_modal, _drain
    from .review_goals import review_goal_basis, review_goal_candidates
    from .storage import Store
    from .ui import MainWindow
    from .ui.intake import IntakePreviewDialog
    from .ui.review_plans import ReviewPlanDialog

    app = QApplication.instance()
    if app is None:
        raise RuntimeError('离线工作流验收需要实际 QApplication。')
    parent = Path(directory).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='workflow-probe-', dir=parent))
    windows, dialogs, shots, checks = [], [], [], {}

    def require(name, observed):
        checks[name] = bool(observed)
        if not checks[name]:
            raise RuntimeError('0.9.5 工作流发布验收失败：' + name)

    def open_window(store):
        # Settings and the disabled schedule belong only to this synthetic DB.
        AutoBackupManager(store).configure(enabled=False)
        window = MainWindow(store)
        windows.append(window)
        window.show()
        app.processEvents()
        _drain(app, lambda: window._backup_job is None)
        window.backup_timer.stop()
        return window

    def close_window(window):
        window.wait_for_backup()
        _drain(app, lambda: window._backup_job is None)
        window.close()
        app.processEvents()

    def own(dialog):
        dialogs.append(dialog)
        return dialog

    def captures(window, dialog, name, normal, main_minimum):
        if screenshot is None:
            return
        for suffix, host_size, size in (
                ('normal', (1360, 900), normal),
                ('main-minimum', (1050, 720), main_minimum),
                ('effective-minimum', (1050, 720),
                 (dialog.minimumWidth(), dialog.minimumHeight()))):
            window.resize(*host_size)
            dialog.resize(*size)
            dialog.show()
            app.processEvents()
            controls = [control for control in dialog.findChildren(QPushButton) if control.isVisible()]
            require(name+'_'+suffix+'_controls', bool(controls) and all(
                control.width() > 24 and control.height() > 18
                and dialog.rect().contains(QRect(control.mapTo(dialog, control.rect().topLeft()), control.size()))
                for control in controls))
            path = Path(screenshot).with_name(Path(screenshot).stem+'-workflow-'+name+'-'+suffix+'.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(path)):
                raise OSError('无法保存工作流验收截图。')
            shots.append({'path': str(path.resolve()), 'dialog': name, 'mode': suffix,
                          'size': [dialog.width(), dialog.height()],
                          'minimum': [dialog.minimumWidth(), dialog.minimumHeight()]})
            if suffix == 'effective-minimum':
                require(name+'_effective_minimum', [dialog.width(), dialog.height()] == list(size))

    def routing(window):
        texts = ['【合成】概括便民措施', '社区开通电话预约。\n服务站保留人工窗口。',
                 '开通电话。\n服务便民。', '参考：电话预约与人工窗口']
        preview = own(IntakePreviewDialog(ImportBatch(pages=[
            InputPage('synthetic-'+str(i)+'.txt', i+1, text, '文本')
            for i, text in enumerate(texts)]), window))
        dialog = own(preview.create_routing_dialog())
        require('routing_literal_source', len(dialog.segments) == len(texts) and all(
            dialog.table.cellWidget(i, 1).toPlainText() == text for i, text in enumerate(texts)))
        for combo, field in zip(dialog.target_combos, ('question', 'materials', 'answer', 'reference')):
            combo.setCurrentIndex(combo.findData(field))
        dialog.replace_checks['question'].setChecked(True)
        dialog.replace_checks['answer'].setChecked(True)
        return preview, dialog

    with _provider_guard() as external_attempts:
        try:
            store = Store(root/'synthetic.db')
            window = open_window(store)
            window.question_edit.setPlainText('旧题干')
            window.materials_edit.setPlainText('原材料')
            window.answer_edit.setPlainText('磁盘原答')
            require('initial_workspace_saved', window.flush_workspace())
            window.answer_edit.setPlainText('开通电话。\n服务便民。')
            opening = store.load_workspace()
            editor = window.workspace_snapshot()
            preview, dialog = routing(window)
            captures(window, dialog, 'routing', (1050, 720), (900, 600))
            _answer_modal(app, dialog.accept, '确认替换内容', QMessageBox.StandardButton.No)
            require('routing_confirmation_cancel', store.load_workspace() == opening
                    and window.workspace_snapshot() == editor and dialog.saved is None)
            dialog.reject(); preview.reject()
            require('routing_dialog_cancel', store.load_workspace() == opening
                    and window.workspace_snapshot() == editor)
            preview, dialog = routing(window)
            _answer_modal(app, dialog.accept, '确认替换内容', QMessageBox.StandardButton.Yes)
            expected = {'question': '【合成】概括便民措施',
                'materials': '原材料\n\n社区开通电话预约。\n服务站保留人工窗口。',
                'answer': '开通电话。\n服务便民。', 'reference': '参考：电话预约与人工窗口'}
            persisted = store.load_workspace()
            require('routing_commit', dialog.result() == QDialog.DialogCode.Accepted
                    and persisted is not None
                    and all(persisted['submission'][key] == value for key, value in expected.items())
                    and all(getattr(window, key+'_edit').toPlainText() == value for key, value in expected.items()))
            require('routing_history', store.list_workspace_history()[0]['submission'] == editor['submission'])
            preview.reject()
            close_window(window)
            store = Store(root/'synthetic.db')
            window = open_window(store)
            require('routing_reopen_four_fields', all(
                getattr(window, key+'_edit').toPlainText() == value for key, value in expected.items()))

            raw = ' [ {"id":"stable-a","name":"甲","weight":70.000000000000001,"extra":{"literal":"<b>合成</b>"}},\n {"id":"stable-b","name":"乙","weight":29.999999999999999} ] '
            window.rubric_edit.setPlainText(raw)
            require('rubric_workspace_saved', window.flush_workspace())
            library = own(window.build_rubric_library_dialog())
            library.name_edit.setText('合成原模板'); library.save_current()
            source = library.selected
            require('rubric_saved_exact', source is not None and source['raw_json'] == raw)
            library.name_edit.setText('合成复制模板'); library.copy_selected()
            copy = library.selected
            require('rubric_copy_exact', copy['id'] != source['id'] and copy['raw_json'] == raw)
            library.name_edit.setText('合成备份模板'); library.rename_selected()
            retained = library.selected
            require('rubric_rename_exact', retained['name'] == '合成备份模板' and retained['raw_json'] == raw)
            library.refresh(select_id=source['id'])
            unfinished = ' [ {"unfinished":'
            window.rubric_edit.setPlainText(unfinished)
            captures(window, library, 'templates', (900, 620), (900, 600))
            _answer_modal(app, library.load_selected, '覆盖自定义量表？', QMessageBox.StandardButton.No)
            require('rubric_load_cancel', window.rubric_edit.toPlainText() == unfinished
                    and library.result() != QDialog.DialogCode.Accepted)
            _answer_modal(app, library.load_selected, '覆盖自定义量表？', QMessageBox.StandardButton.Yes)
            require('rubric_load_exact', library.result() == QDialog.DialogCode.Accepted
                    and window.rubric_edit.toPlainText() == raw)
            require('rubric_loaded_workspace_saved', window.flush_workspace())

            # A tiny literal report supplies real, validated summary evidence.
            review = {'title': '【合成验收】便民服务', 'prompt_version': 'synthetic-workflow-1',
                'submission': window.submission(),
                'assessment': {'total_score': 16, 'max_score': 20, 'summary': '合成验收，不验证评分效果',
                    'next_action': '核对服务对象和措施',
                    'annotations': [{'start': 0, 'end': 5, 'quote': '开通电话。', 'issue': '措施不具体',
                        'suggestion': '补充预约用途', 'material_refs': [{'id': 'M2', 'quote': '电话预约'}]}],
                    'material_points': [{'point': '保留人工窗口', 'coverage': 'missing', 'answer_quote': '',
                        'material_refs': [{'id': 'M3', 'quote': '保留人工窗口'}]}],
                    'dimensions': [{'id': 'stable-a', 'name': '甲', 'weight': 70, 'score': 80, 'reason': '合成固定值'}]},
                'revisions': {'minimal': '开通电话预约。', 'model': '电话预约与人工窗口。'}}
            rid = store.save_review(review)
            original = store.get_review(rid)
            qid = store.question_for_review(rid)
            library = own(window.build_rubric_library_dialog())
            library.refresh(select_id=source['id'])
            _answer_modal(app, library.delete_selected, '删除模板？', QMessageBox.StandardButton.No)
            require('rubric_delete_cancel', store.get_rubric_preset(source['id']) == source)
            _answer_modal(app, library.delete_selected, '删除模板？', QMessageBox.StandardButton.Yes)
            require('rubric_copy_isolation', store.get_rubric_preset(source['id']) is None
                    and store.get_rubric_preset(retained['id']) == retained
                    and window.rubric_edit.toPlainText() == raw)
            require('old_report_stable', store.get_review(rid) == original)
            library.reject()

            window.show_review(original)
            goals = own(window.build_review_goals_dialog())
            require('goal_default_unchecked', goals.candidates.count() > 0 and all(
                goals.candidates.item(i).checkState() == Qt.CheckState.Unchecked
                for i in range(goals.candidates.count())))
            goals.candidates.item(0).setCheckState(Qt.CheckState.Checked)
            captures(window, goals, 'goals', (760, 620), (760, 600))
            goals.save()
            first = store.get_question(qid)['review_plan']
            require('goal_added_from_evidence', goals.result() == QDialog.DialogCode.Accepted
                    and first['goals'] == [review_goal_candidates(original)[0]]
                    and first['goals'][0]['refs'] == [{'id': 'M3', 'quote': '保留人工窗口'}])
            repeated = store.add_review_goals(rid, [first['goals'][0]['id']], goals.value(),
                expected_version=first['version'], expected_basis=review_goal_basis(original),
                expected_generation=store.assessment_generation(), expected_question_id=qid)
            require('goal_deduplicated', repeated['goals'] == first['goals'])
            plan_dialog = own(ReviewPlanDialog(store, qid, window))
            plan_dialog.status.setCurrentText('已解决'); plan_dialog.tags.setText('合成目标')
            plan_dialog.save()
            plan = store.get_question(qid)['review_plan']
            require('ordinary_plan_preserves_goals', plan['status'] == '已解决'
                    and plan['tags'] == ['合成目标'] and plan['goals'] == first['goals']
                    and not store.get_question(qid)['needs_retry'] and store.get_review(rid) == original)
            deletion = store.prepare_practice_deletion('reviews', [rid])
            store.delete_practice_items('reviews', [rid], expected_token=deletion['token'])
            require('source_deletion_retains_goals', store.get_review(rid) is None
                    and store.get_question(qid)['review_plan'] == plan)
            close_window(window)
            store = Store(root/'synthetic.db')
            window = open_window(store)
            plan_dialog = own(ReviewPlanDialog(store, qid, window))
            require('goals_restart_source_deleted', plan_dialog.retained_goals == plan['goals']
                    and '来源报告已删除' in plan_dialog.goal_detail.toPlainText()
                    and not plan_dialog.open_source_button.isEnabled()
                    and store.get_review(rid) is None)
            plan_dialog.reject()

            backup = store.backup_to(root/'workflow-backup.db')
            restored = Store(root/'restored.db')
            restored.restore_from(backup)
            require('new_backup_restore', restored.list_rubric_presets() == [retained]
                    and restored.get_question(qid)['review_plan'] == plan
                    and restored.get_review(rid) is None and restored.load_workspace() == store.load_workspace())
            legacy = Store(root/'legacy.db')
            legacy.set_setting('synthetic_legacy', 'old-format')
            with legacy._connection() as conn:
                conn.execute('DROP TABLE rubric_presets')
            old_backup = legacy.backup_to(root/'legacy-backup.db', register=False)
            legacy_restored = Store(root/'legacy-restored.db')
            legacy_restored.restore_from(old_backup)
            require('old_format_restore', legacy_restored.list_rubric_presets() == []
                    and legacy_restored.list_review_plans() == []
                    and legacy_restored.get_setting('synthetic_legacy') == 'old-format')
            require('zero_external_model_calls', len(external_attempts) == 0)
            for owner in windows:
                owner.wait_for_backup()
                _drain(app, lambda owner=owner: owner._backup_job is None)
            require('workers_finished', all(not owner.workers and owner._backup_job is None for owner in windows))
            return {'ok': all(checks.values()), 'checks': checks,
                    'external_model_calls': len(external_attempts), 'data_dir': str(root), 'screenshots': shots}
        finally:
            # Keep every Qt owner alive until its owned background copy has exited,
            # also when a provider guard or observable acceptance check raises.
            for owner in windows:
                owner.wait_for_backup()
                _drain(app, lambda owner=owner: owner._backup_job is None)
            for dialog in reversed(dialogs):
                # Bypass discard prompts only during teardown of owned fixtures.
                QDialog.done(dialog, QDialog.DialogCode.Rejected)
                guard = getattr(dialog, 'workspace_guard', None)
                if guard is not None:
                    guard.release()
            for owner in windows:
                owner.autosave_timer.stop()
                owner._workspace_dirty = False
                close_window(owner)
