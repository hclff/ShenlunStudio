"""Runtime release acceptance must leave independently inspectable artifacts."""
import importlib.util
import importlib
import json
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


def probe_api():
    assert importlib.util.find_spec('shenlun.workflow_release_probe'), '0.9.5 needs its runtime workflow probe'
    from shenlun.workflow_release_probe import probe_workflow
    return probe_workflow


def test_workflow_probe_persists_real_artifacts_and_unique_children(tmp_path):
    from shenlun.storage import Store
    app = QApplication.instance() or QApplication([])
    results = [probe_api()(tmp_path, tmp_path/f'run-{i}.png') for i in range(2)]
    for result in results:
        assert result['ok'] and all(result['checks'].values())
        assert set(result['checks']) >= {'routing_confirmation_cancel', 'routing_dialog_cancel',
            'routing_commit', 'routing_history', 'routing_reopen_four_fields', 'rubric_saved_exact',
            'rubric_copy_exact', 'rubric_rename_exact', 'rubric_load_cancel', 'rubric_load_exact',
            'rubric_delete_cancel', 'rubric_copy_isolation', 'old_report_stable',
            'goal_default_unchecked', 'goal_added_from_evidence', 'goal_deduplicated',
            'ordinary_plan_preserves_goals', 'source_deletion_retains_goals',
            'goals_restart_source_deleted', 'new_backup_restore', 'old_format_restore',
            'zero_external_model_calls', 'workers_finished'}
        assert result['external_model_calls'] == 0
        root = Path(result['data_dir'])
        assert root.parent == tmp_path and root.name.startswith('workflow-probe-')
        store = Store(root/'synthetic.db')
        fields = store.load_workspace()['submission']
        assert fields['question'] == '【合成】概括便民措施'
        assert fields['materials'] == '原材料\n\n社区开通电话预约。\n服务站保留人工窗口。'
        assert fields['answer'] == '开通电话。\n服务便民。'
        assert fields['reference'] == '参考：电话预约与人工窗口'
        assert store.list_workspace_history()[0]['submission']['answer'] == '开通电话。\n服务便民。'
        presets = store.list_rubric_presets()
        assert len(presets) == 1 and presets[0]['name'] == '合成备份模板'
        assert '70.000000000000001' in presets[0]['raw_json']
        assert store.list_reviews() == []  # Explicit source deletion must remain deleted.
        plan = store.list_review_plans()[0]['review_plan']
        assert plan['status'] == '已解决' and plan['tags'] == ['合成目标']
        assert len(plan['goals']) == 1 and plan['goals'][0]['refs'] == [{'id': 'M3', 'quote': '保留人工窗口'}]
        restored = Store(root/'restored.db')
        assert restored.list_rubric_presets() == presets
        assert restored.list_review_plans()[0]['review_plan'] == plan
        legacy = Store(root/'legacy-restored.db')
        assert legacy.list_rubric_presets() == [] and legacy.list_review_plans() == []
        with sqlite3.connect(root/'legacy-backup.db') as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='rubric_presets'").fetchone() is None
        assert len(result['screenshots']) == 9
        for shot in result['screenshots']:
            image = QImage(shot['path'])
            assert not image.isNull()
            assert (image.width(), image.height()) == tuple(shot['size'])
            assert Path(shot['path']).stat().st_size > 1000
    assert results[0]['data_dir'] != results[1]['data_dir']
    assert not (tmp_path/'studio.db').exists()
    app.processEvents()


@pytest.mark.parametrize('method', ['complete', 'complete_image'])
def test_guard_blocks_both_provider_methods_and_restores_after_error(tmp_path, monkeypatch, method):
    from shenlun.model import ModelClient
    from shenlun.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    probe = probe_api()
    originals = (ModelClient.complete, ModelClient.complete_image)
    def accidental_provider(self):
        return getattr(ModelClient, method)(None)
    monkeypatch.setattr(MainWindow, 'build_rubric_library_dialog', accidental_provider)
    with pytest.raises(RuntimeError, match='禁止外部模型请求'):
        probe(tmp_path)
    assert (ModelClient.complete, ModelClient.complete_image) == originals
    app.processEvents()


def test_observation_rejects_a_routing_writer_that_silently_skips_persistence(tmp_path, monkeypatch):
    from shenlun.storage import Store
    app = QApplication.instance() or QApplication([])
    probe = probe_api()
    monkeypatch.setattr(Store, 'commit_routed_workspace', lambda self, before, after, **kw: after)
    with pytest.raises(RuntimeError, match='routing_commit'):
        probe(tmp_path)
    app.processEvents()


def test_provider_guard_counts_each_blocked_attempt_even_when_caught():
    probe_api()
    from shenlun.model import ModelClient
    from shenlun.workflow_release_probe import _provider_guard
    originals = (ModelClient.complete, ModelClient.complete_image)
    with _provider_guard() as attempts:
        for method in ('complete', 'complete_image'):
            with pytest.raises(RuntimeError, match='禁止外部模型请求'):
                getattr(ModelClient, method)(None)
        assert len(attempts) == 2
    assert (ModelClient.complete, ModelClient.complete_image) == originals


def test_observation_rejects_rubric_load_that_accepts_without_copying(tmp_path, monkeypatch):
    from shenlun.ui.rubric_library import RubricLibraryDialog
    app = QApplication.instance() or QApplication([])
    probe = probe_api()
    original = RubricLibraryDialog.load_selected
    def skip_editor_copy(self):
        with monkeypatch.context() as scoped:
            scoped.setattr(self.host.rubric_edit, 'setPlainText', lambda text: None)
            return original(self)
    monkeypatch.setattr(RubricLibraryDialog, 'load_selected', skip_editor_copy)
    with pytest.raises(RuntimeError, match='rubric_load_exact'):
        probe(tmp_path)
    app.processEvents()


def test_actual_smoke_entry_preserves_previous_keys_and_appends_workflow(tmp_path, monkeypatch):
    from shenlun.auto_backup import AutoBackupManager
    from shenlun.storage import Store
    from shenlun.__main__ import main
    entry = importlib.import_module('shenlun.__main__')
    def tiny_store(directory):
        store = Store(Path(directory)/'synthetic.db')
        AutoBackupManager(store).configure(enabled=False)
        return store
    monkeypatch.setattr(entry, 'create_store', tiny_store)
    called = []
    previous = [('release_probe', 'practice', 'probe_practice'),
        ('ocr_release_probe', 'image_ocr', 'probe_ocr'),
        ('evaluation_release_probe', 'evaluation', 'probe_evaluation'),
        ('teaching_release_probe', 'teaching', 'probe_teaching'),
        ('method_library_release_probe', 'method_library', 'probe_method_library'),
        ('method_workflow_release_probe', 'method_workflow', 'probe_method_workflow'),
        ('practice_deletion_release_probe', 'practice_deletion', 'probe_practice_deletion'),
        ('learning_release_probe', 'learning', 'probe_learning'),
        ('reliability_release_probe', 'reliability', 'probe_reliability')]
    for module, key, function in previous:
        def observed(*args, key=key):
            called.append(key)
            return {'observed_previous': key}
        monkeypatch.setattr(importlib.import_module('shenlun.'+module), function, observed)
    report_path = tmp_path/'smoke.json'
    assert main(['--smoke-test', '--data-dir', str(tmp_path/'explicit'),
                 '--smoke-report', str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert called == [key for _, key, _ in previous]
    assert all(report[key] == {'observed_previous': key} for key in called)
    assert report['ok'] and report['workflow']['ok']
    assert report['workflow']['external_model_calls'] == 0
    assert Path(report['workflow']['data_dir']).parent == tmp_path
