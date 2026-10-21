"""Local synthetic deletion acceptance, also run inside the frozen Windows EXE."""
from copy import deepcopy
from pathlib import Path
import uuid


def probe_practice_deletion(directory, screenshot=None):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .storage import Store
    from .ui import MainWindow
    root = Path(directory) / ('practice-deletion-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    store = Store(root / 'synthetic.db')
    questions, reviews = [], []
    for suffix in ('甲', '乙', '丙'):
        raw = {'question': '【合成验收】' + suffix + '：概括社区做法。',
               'materials': '社区保留人工窗口，提供电话预约。', 'answer': '保留人工窗口。',
               'question_type': '归纳概括', 'max_score': 20, 'word_limit': 100}
        qid = store.save_question(raw, title='【合成验收】' + suffix)
        questions.append(qid)
        snapshot = store.save_reference_points(qid, [{'id': 'P1', 'text': '保留人工窗口',
            'material_refs': [{'id': 'M1', 'quote': '保留人工窗口'}]}],
            reviewer='合成软件验收', confirm=True)
        for number in (1, 2):
            rid = store.save_review({'title': '【合成验收】' + suffix + str(number),
                'submission': {**raw, 'answer': raw['answer'] + str(number)},
                'assessment': {'total_score': 12, 'max_score': 20, 'mode': 'full',
                    'rubric_fingerprint': 'synthetic', 'annotations': []},
                'model': 'synthetic-offline', 'prompt_version': 'synthetic',
                'reference_points_snapshot': snapshot, 'revisions': {}})
            store.save_human_score(rid, 11, reviewer='合成固定值', independent=False)
            reviews.append(rid)
    host = MainWindow(store)
    errors = []

    def select(widget, identity):
        for index in range(widget.count()):
            item = widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == identity:
                widget.setCurrentItem(item)
                return widget.currentItem()
        raise RuntimeError('验收目标未出现在列表中：' + identity)

    def confirm(control, answer, mutate=None):
        def choose():
            modal = QApplication.activeModalWidget()
            if not isinstance(modal, QMessageBox):
                errors.append('缺少删除确认窗口')
                if modal: modal.reject()
                return
            if modal.defaultButton() != modal.button(QMessageBox.StandardButton.No):
                errors.append('删除确认未默认取消')
            if mutate: mutate()
            modal.button(answer).click()
        QTimer.singleShot(0, choose)
        control.click()
        if errors: raise RuntimeError(errors[-1])

    def save(suffix):
        if screenshot:
            QApplication.processEvents()
            target = screenshot.with_name(screenshot.stem + '-' + suffix + '.png')
            if not host.grab().save(str(target)): raise OSError('无法保存删除功能截图')

    try:
        host.resize(1050, 720); host.show(); host.navigation.setCurrentRow(3)
        QApplication.processEvents()
        host.review_select_all_button.click()
        if set(host.checked_practice_ids('reviews')) != set(reviews):
            raise RuntimeError('训练记录勾选范围错误')
        confirm(host.review_bulk_delete_button, QMessageBox.StandardButton.No)
        if len(store.list_reviews()) != 6 or len(host.checked_practice_ids('reviews')) != 6:
            raise RuntimeError('取消删除改变了记录或勾选')
        select(host.review_list, reviews[0])
        if len(host.checked_practice_ids('reviews')) != 6:
            raise RuntimeError('查看单条详情改变了批量勾选')
        save('delete-records')
        host.review_clear_checks_button.click()
        select(host.review_list, reviews[0])
        confirm(host.review_delete_button, QMessageBox.StandardButton.Yes)
        if store.get_review(reviews[0]) or store.get_human_score(reviews[0]) or not store.get_question(questions[0]):
            raise RuntimeError('单条记录删除的关联范围错误')
        for rid in reviews[1:3]:
            select(host.review_list, rid).setCheckState(Qt.CheckState.Checked)
        confirm(host.review_bulk_delete_button, QMessageBox.StandardButton.Yes)
        if {r['id'] for r in store.list_reviews()} != set(reviews[3:]):
            raise RuntimeError('批量记录删除遗漏或越界')
        retained = {rid: deepcopy(store.get_review(rid)) for rid in reviews[3:]}
        host.navigation.setCurrentRow(5)
        host.question_search.setText('【合成验收】乙')
        host.question_select_all_button.click()
        if host.checked_practice_ids('questions') != [questions[1]]:
            raise RuntimeError('题库全选未限定当前筛选')
        select(host.question_list, questions[1])
        save('delete-questions')
        host.question_search.clear()
        if host.checked_practice_ids('questions'): raise RuntimeError('切换题库筛选保留了隐蔽勾选')
        select(host.question_list, questions[0])
        confirm(host.question_delete_button, QMessageBox.StandardButton.Yes)
        if store.get_question(questions[0]) or len(store.list_questions()) != 2:
            raise RuntimeError('单题删除失败')
        select(host.question_list, questions[1])
        def change_question():
            other = Store(store.path)
            value = other.get_question(questions[1])
            other.save_question(value['submission'], title='【合成验收】已并发修改')
        confirm(host.question_delete_button, QMessageBox.StandardButton.Yes, change_question)
        if len(store.list_questions()) != 2: raise RuntimeError('过期确认仍执行了题目删除')
        host.refresh_questions()
        if not host.open_question(questions[1]): raise RuntimeError('无法载入删除前草稿')
        host.answer_edit.setPlainText('【合成验收】编辑中的原答保持不变')
        if not host.flush_workspace(): raise RuntimeError('无法保存验收草稿')
        before_text = host.raw_submission()
        host.navigation.setCurrentRow(5)
        host.question_select_all_button.click()
        confirm(host.question_bulk_delete_button, QMessageBox.StandardButton.Yes)
        if store.list_questions() or host.current_question_id is not None:
            raise RuntimeError('批量删除未清空题库或未解除当前题目关联')
        if host.raw_submission() != before_text or store.load_workspace()['question_id'] is not None:
            raise RuntimeError('删除题目丢失了正在编辑的文字')
        if any(store.get_review(rid) != record or store.question_for_review(rid) is not None
               for rid, record in retained.items()):
            raise RuntimeError('删除题目改变了独立训练报告')
        if not store.get_human_score(reviews[3]): raise RuntimeError('删除题目移除了保留报告的人工评分')
        again = Store(store.path)
        if again.list_questions() or len(again.list_reviews()) != 3:
            raise RuntimeError('重启复活了已删除题目或记录')
        backup = again.backup_to(root / 'backup.db')
        restored = Store(root / 'restored.db'); restored.restore_from(backup)
        if restored.list_questions() or any(restored.get_review(rid) != value for rid, value in retained.items()):
            raise RuntimeError('删除后的备份恢复未保留历史报告')
        try: restored.save_review({'id': reviews[0], 'submission': {'answer': '迟到保存'}})
        except ValueError: pass
        else: raise RuntimeError('已删除记录被迟到写入重新创建')
        return {'ok': True, 'synthetic_fixtures_only': True, 'cancel_preserved': True,
            'single_and_bulk_reviews': True, 'filtered_question_selection': True,
            'single_and_bulk_questions': True, 'reports_preserved': True,
            'workspace_preserved': True, 'stale_confirmation_rejected': True,
            'restart_and_backup': True, 'cloud_calls': 0}
    finally:
        host.close(); QApplication.processEvents()
