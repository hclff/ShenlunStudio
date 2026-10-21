"""The same offline workflow acceptance is bundled into the shipping EXE."""
import importlib.util


def test_actual_search_preset_and_paired_evaluation_workflow(tmp_path):
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    assert importlib.util.find_spec('shenlun.method_workflow_release_probe'), 'shipping method workflow probe required'
    from shenlun.method_workflow_release_probe import probe_method_workflow
    report=probe_method_workflow(tmp_path)
    for key in ('ok','synthetic_fixtures_only','filters_scoped','preset_saved_from_checks','exact_preview',
                'cancel_before_upload','paired_frozen_calls','missing_humans_not_accuracy','backup_restore'):
        assert report[key]
    assert report['loopback_calls']==8 and report['cloud_calls']==0 and report['human_accuracy_verified'] is False
