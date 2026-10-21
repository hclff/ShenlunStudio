"""Release acceptance uses real widgets, durable tasks and SQLite snapshots."""
import importlib.util
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialogButtonBox


def test_backup_manager_close_is_localized_without_system_translator(tmp_path):
    from shenlun.auto_backup import AutoBackupManager
    from shenlun.storage import Store
    from shenlun.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path/'button.db')
    AutoBackupManager(store).configure(enabled=False)
    window = MainWindow(store)
    try:
        dialog = window.build_backup_manager_dialog()
        close = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close)
        assert close.text() == '关闭'
        dialog.show()
        close.click()
        assert not dialog.isVisible()
    finally:
        window.wait_for_backup()
        window.close()
        app.processEvents()


def test_release_probe_covers_restart_rubric_and_registered_backup_boundaries(tmp_path):
    # Removing the probe or replacing persistence/UI work with optimistic flags
    # must fail the acceptance entry point and its independently visible artifacts.
    assert importlib.util.find_spec('shenlun.reliability_release_probe') is not None, '0.9.4 needs its offline reliability probe'
    from shenlun.reliability_release_probe import probe_reliability
    from shenlun.storage import Store
    from shenlun.auto_backup import AutoBackupManager
    app = QApplication.instance() or QApplication([])
    results = [probe_reliability(tmp_path, tmp_path/f'release-{index}.png') for index in range(2)]
    for index, result in enumerate(results):
        assert result['ok']
        assert set(result['checks']) == {'builtin_identity', 'table_affects_submission',
            'advanced_cancel_preserved', 'invalid_draft_restart', 'score_persisted_restart',
            'recovery_lists_task', 'resume_cancel_no_work', 'revisions_only',
            'original_grade_unchanged', 'completed_reopens_without_call', 'daily_dedupe',
            'registered_retention', 'manager_localized_registered_only',
            'backup_restore_roundtrip', 'registry_machine_local', 'zero_external_model_calls',
            'workers_finished'}
        assert all(result['checks'].values())
        assert result['external_model_calls'] == 0
        assert result['scoring_calls'] == 1 and result['resumed_revision_calls'] == 1
        root = Path(result['data_dir'])
        assert root.parent == tmp_path and root.name.startswith('reliability-probe-')
        store = Store(root/'synthetic.db')
        tasks = store.list_assessment_tasks()
        assert len(tasks) == 1 and tasks[0]['state'] == 'completed'
        assert store.get_review(tasks[0]['review_id'])['assessment']['total_score'] == 16
        assert store.list_review_plans()[0]['review_plan']['tags'] == ['合成验收']
        records = AutoBackupManager(store).records()
        assert any(row['kind'] == 'safety' for row in records)
        assert any(row['kind'] == 'manual' for row in records)
        assert (root/'backups'/'unregistered.db').read_bytes() == b'synthetic foreign file'
        for suffix in ('rubric-normal', 'rubric-minimum', 'backup-normal', 'backup-minimum',
                       'recovery-normal', 'recovery-minimum'):
            assert (tmp_path/f'release-{index}-reliability-{suffix}.png').stat().st_size > 1000
    assert results[0]['data_dir'] != results[1]['data_dir']
    assert not (tmp_path/'studio.db').exists()
    app.processEvents()


def test_accidental_provider_use_fails_closed_and_restores_guard(tmp_path, monkeypatch):
    import pytest
    from shenlun.model import ModelClient
    from shenlun.reliability_release_probe import _OfflineClient, probe_reliability
    app = QApplication.instance() or QApplication([])
    original = ModelClient.complete
    def accidental_live_use(self, system, payload, cancel=None):
        return ModelClient.complete(self, system, payload, cancel)
    monkeypatch.setattr(_OfflineClient, 'complete', accidental_live_use)
    with pytest.raises(RuntimeError, match='禁止外部模型请求'):
        probe_reliability(tmp_path)
    assert ModelClient.complete is original
    app.processEvents()
