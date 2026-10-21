"""Real SQLite failures and restore/timer schedules at modal ownership boundaries."""
import importlib
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from shenlun.model import ModelClient
from shenlun.storage import Store
from shenlun.ui import MainWindow
from shenlun.ui.intake import IntakePreviewDialog
from shenlun.ui.review_plans import ReviewPlanDialog
from shenlun.ui.workspace_guard import ModalWorkspaceGuard
from test_review_goals import setup_report, add


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, 'initialize_backup_schedule', lambda self: None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    def forbidden(*args, **kwargs):
        pytest.fail('Modal lifecycle must not call model providers')
    monkeypatch.setattr(ModelClient, 'complete', forbidden)
    monkeypatch.setattr(ModelClient, 'complete_image', forbidden)
    w = MainWindow(Store(tmp_path / 'synthetic.db'))
    yield w
    w.autosave_timer.stop()
    w._workspace_dirty = False
    w.close()
    app.processEvents()


def stage(window, tmp_path):
    window.answer_edit.setPlainText('persisted-P')
    assert window.flush_workspace()
    backup = window.store.backup_to(tmp_path / 'opening.db')
    window.answer_edit.setPlainText('dirty-D')
    window.rubric_edit.setPlainText('unfinished raw rubric')
    assert window.autosave_timer.isActive()
    return backup


@pytest.fixture
def releases(monkeypatch):
    """Observe real release ownership; keep all protection/timer effects intact."""
    values = []
    original = ModalWorkspaceGuard.release
    def release(self, **kwargs):
        values.append(self)
        return original(self, **kwargs)
    monkeypatch.setattr(ModalWorkspaceGuard, 'release', release)
    return values


def preview(window, capacity=False):
    page = SimpleNamespace(source='synthetic.txt', number=1,
        text='x' * 250001 if capacity else 'synthetic paragraph',
        method='text', preview_png=b'', warnings=[], fields={})
    return IntakePreviewDialog(SimpleNamespace(pages=[page], errors=[]), window)


def assert_stale(window, monkeypatch):
    QTest.qWait(1100)
    assert window.store.load_workspace()['submission']['answer'] == 'persisted-P'
    assert window.answer_edit.toPlainText() == 'dirty-D'
    assert window.rubric_edit.toPlainText() == 'unfinished raw rubric'
    assert window._workspace_context_stale and window._autosave_paused
    assert not window.autosave_timer.signalsBlocked()
    assert not window.flush_workspace()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    def refuse(*args, **kwargs):
        assert kwargs.get('defaultButton', args[-1]) == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', refuse)
    window.load_draft()
    assert window._workspace_context_stale
    assert window.answer_edit.toPlainText() == 'dirty-D'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.load_draft()
    assert not window._workspace_context_stale and not window._autosave_paused
    assert window.answer_edit.toPlainText() == 'persisted-P'


@pytest.mark.parametrize('entry', ['library', 'builtin', 'routing-capacity', 'routing-context'])
def test_failed_opening_after_real_restore_cannot_overwrite_P(window, tmp_path, monkeypatch, releases, entry):
    backup = stage(window, tmp_path)
    p = preview(window, capacity=entry == 'routing-capacity') if entry.startswith('routing') else None
    generation = window.store.assessment_generation
    epoch = generation()
    def restore_once():
        monkeypatch.setattr(window.store, 'assessment_generation', generation)
        window.store.restore_from(backup)
        return epoch
    monkeypatch.setattr(window.store, 'assessment_generation', restore_once)
    if entry == 'routing-context':
        original_snapshot = window.workspace_snapshot
        def fail_snapshot():
            monkeypatch.setattr(window, 'workspace_snapshot', original_snapshot)
            raise OSError('context read failed')
        monkeypatch.setattr(window, 'workspace_snapshot', fail_snapshot)
    if entry == 'builtin':
        window.restore_rubric_template()
    else:
        with pytest.raises((ValueError, OSError)):
            window.build_rubric_library_dialog() if entry == 'library' else p.create_routing_dialog()
    assert len(releases) == 1 and releases[0].released
    assert_stale(window, monkeypatch)
    if p:
        p.reject()


@pytest.mark.parametrize('entry', ['library-read', 'library-ui', 'builtin', 'routing-context', 'routing-ui', 'routing-capacity'])
def test_ordinary_open_failure_restores_pending_timer(window, tmp_path, monkeypatch, releases, entry):
    stage(window, tmp_path)
    p = preview(window, capacity=entry == 'routing-capacity') if entry.startswith('routing') else None
    def fail(*args, **kwargs):
        raise OSError('opening failed')
    if entry == 'library-read':
        connect = sqlite3.connect
        pending = [True]
        class ReadFailure(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if pending[0] and sql.startswith('SELECT data FROM practice_workspace'):
                    pending[0] = False
                    raise sqlite3.OperationalError('opening failed')
                return super().execute(sql, *args, **kwargs)
        monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=ReadFailure))
    elif entry == 'library-ui':
        monkeypatch.setattr(importlib.import_module('shenlun.ui.rubric_library'), 'QVBoxLayout', fail)
    elif entry == 'routing-context':
        original = window.workspace_snapshot
        def once():
            monkeypatch.setattr(window, 'workspace_snapshot', original)
            return fail()
        monkeypatch.setattr(window, 'workspace_snapshot', once)
    elif entry == 'routing-ui':
        monkeypatch.setattr(importlib.import_module('shenlun.ui.intake_routing'), 'route_combo', fail)
    if entry == 'builtin':
        window.replace_rubric_draft(fail, 'synthetic', 'synthetic')
    else:
        with pytest.raises((ValueError, OSError, sqlite3.Error)):
            window.build_rubric_library_dialog() if entry.startswith('library') else p.create_routing_dialog()
    assert len(releases) == 1 and releases[0].released
    assert not window.autosave_timer.signalsBlocked()
    assert window.autosave_timer.isActive() and not window._autosave_paused
    QTest.qWait(1100)
    assert window.store.load_workspace()['submission']['answer'] == 'dirty-D'
    if p:
        p.reject()


def saved_goals(window):
    rid, qid, report = setup_report(window.store)
    add(window.store, rid, report)
    window.show_review(report)
    return rid, qid, report


def review_dialog(window, qid, kind):
    return window.build_review_goals_dialog() if kind == 'goals' else ReviewPlanDialog(window.store, qid, window)


@pytest.mark.parametrize('kind', ['goals', 'plan'])
@pytest.mark.parametrize('restored', [False, True])
def test_late_source_read_failure_unwinds_constructor(window, tmp_path, monkeypatch, releases, kind, restored):
    _, qid, _ = saved_goals(window)
    backup = stage(window, tmp_path)
    connect = sqlite3.connect
    did_restore = []
    restoring = []
    epoch = window.store.assessment_generation()
    class SourceFailure(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == 'SELECT data FROM reviews WHERE id=?' and not restoring:
                if restored and not did_restore:
                    restoring.append(True)
                    try:
                        window.store.restore_from(backup)
                        assert window.store.assessment_generation() != epoch
                        did_restore.append(True)
                    finally:
                        restoring.clear()
                raise sqlite3.OperationalError('late source read failed')
            return super().execute(sql, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=SourceFailure))
        with pytest.raises(sqlite3.OperationalError, match='late source'):
            review_dialog(window, qid, kind)
    assert len(releases) == 1 and releases[0].released
    assert not window.autosave_timer.signalsBlocked()
    if restored:
        assert did_restore
        assert_stale(window, monkeypatch)
    else:
        assert window.autosave_timer.isActive() and not window._autosave_paused
        # A subsequent modal must inherit a healthy signal state, not a leaked guard.
        d = review_dialog(window, qid, kind)
        d.reject()
        QTest.qWait(1100)
        assert window.store.load_workspace()['submission']['answer'] == 'dirty-D'


@pytest.mark.parametrize('kind', ['library', 'routing'])
def test_late_ui_failure_after_restore_preserves_P(window, tmp_path, monkeypatch, releases, kind):
    backup = stage(window, tmp_path)
    p = preview(window) if kind == 'routing' else None
    def fail(*args, **kwargs):
        window.store.restore_from(backup)
        raise OSError('late UI failed')
    module = importlib.import_module('shenlun.ui.' + ('rubric_library' if kind == 'library' else 'intake_routing'))
    monkeypatch.setattr(module, 'QVBoxLayout' if kind == 'library' else 'route_combo', fail)
    with pytest.raises(OSError, match='late UI'):
        window.build_rubric_library_dialog() if kind == 'library' else p.create_routing_dialog()
    assert len(releases) == 1 and releases[0].released
    assert_stale(window, monkeypatch)
    if p:
        p.reject()


@pytest.mark.parametrize('restored', [False, True])
def test_direct_routing_constructor_owns_failed_setup(window, tmp_path, monkeypatch, releases, restored):
    from shenlun.ui.intake_routing import IntakeRoutingDialog
    backup = stage(window, tmp_path)
    p = preview(window, capacity=True)
    context = {'generation': window.store.assessment_generation(),
               'before': window.workspace_snapshot(), 'workspace': window.store.load_workspace()}
    if restored:
        window.store.restore_from(backup)
    with pytest.raises(ValueError, match='25'):
        IntakeRoutingDialog(p, context)
    assert len(releases) == 1 and releases[0].released
    if restored:
        assert_stale(window, monkeypatch)
    else:
        assert window.autosave_timer.isActive() and not window.autosave_timer.signalsBlocked()
        QTest.qWait(1100)
        assert window.store.load_workspace()['submission']['answer'] == 'dirty-D'
    p.reject()


@pytest.mark.parametrize('kind', ['goals', 'plan'])
@pytest.mark.parametrize('blocked', [False, True])
def test_early_review_read_failure_restores_original_signal_state(
        window, tmp_path, monkeypatch, releases, kind, blocked):
    _, qid, _ = saved_goals(window)
    stage(window, tmp_path)
    window.autosave_timer.blockSignals(blocked)
    connect = sqlite3.connect
    pending = [True]
    class ReadFailure(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if pending[0] and sql.startswith('SELECT data FROM practice_workspace'):
                pending[0] = False
                raise sqlite3.OperationalError('first context read failed')
            return super().execute(sql, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=ReadFailure))
        with pytest.raises(sqlite3.OperationalError, match='first context'):
            review_dialog(window, qid, kind)
    assert len(releases) == 1 and releases[0].released
    assert window.autosave_timer.signalsBlocked() == blocked
    assert window.autosave_timer.isActive() and not window._autosave_paused
    window.autosave_timer.blockSignals(False)
    QTest.qWait(1100)
    assert window.store.load_workspace()['submission']['answer'] == 'dirty-D'


@pytest.mark.parametrize('kind', ['goals', 'plan'])
@pytest.mark.parametrize('error_type', [sqlite3.OperationalError, OSError])
@pytest.mark.parametrize('unreadable', [False, True])
def test_review_save_commit_failure_retains_inputs_rolls_back_and_retries(
        window, tmp_path, monkeypatch, kind, error_type, unreadable):
    rid, qid, report = saved_goals(window)
    stage(window, tmp_path)
    d = review_dialog(window, qid, kind)
    d.status.setCurrentText('复习中')
    d.scheduled.setChecked(True)
    d.due_date.setDate(QDate(2026, 11, 21))
    d.tags.setText('保留标签')
    if kind == 'goals':
        d.candidates.setCurrentRow(1)
        d.candidates.item(1).setCheckState(Qt.CheckState.Checked)
    opening_question = window.store.get_question(qid)
    opening_workspace = window.store.load_workspace()
    retained_goals = d.retained_goals.copy()
    connect = sqlite3.connect
    failed = []
    class CommitFailure(sqlite3.Connection):
        def commit(self):
            if self.total_changes and not failed:
                failed.append(True)
                raise error_type('synthetic commit failure')
            return super().commit()
    def connection(*args, **kwargs):
        if unreadable and failed:
            raise sqlite3.OperationalError('protection read unavailable')
        return connect(*args, **kwargs, factory=CommitFailure)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connection)
        d.save()
    assert failed
    assert window.store.get_question(qid) == opening_question
    assert window.store.get_review(rid) == report
    assert window.store.load_workspace() == opening_workspace
    assert d.result() != d.DialogCode.Accepted and d.message.text()
    assert d.value() == {'status': '复习中', 'due_date': '2026-11-21', 'tags': ['保留标签']}
    assert d.retained_goals == retained_goals and d.goals.currentRow() == 0
    if kind == 'goals':
        assert d.candidates.currentRow() == 1
        assert d.candidates.item(1).checkState() == Qt.CheckState.Checked
    assert window._autosave_paused == unreadable
    if unreadable:
        assert window._workspace_context_stale
    else:
        assert 'synthetic commit failure' in d.message.text()
        d.save()
        assert d.result() == d.DialogCode.Accepted
        saved = window.store.get_question(qid)['review_plan']
        assert saved['tags'] == ['保留标签'] and saved['due_date'] == '2026-11-21'
        assert len(saved['goals']) == (2 if kind == 'goals' else 1)
        assert window.store.get_review(rid) == report
    d.done(d.DialogCode.Rejected)
