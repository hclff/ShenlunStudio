"""The same CRUD acceptance path runs in the shipping Windows executable."""
import importlib.util


def test_frozen_library_probe_checks_real_widgets_and_local_persistence(tmp_path):
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    assert importlib.util.find_spec('shenlun.method_library_release_probe'), 'shipping CRUD probe required'
    from shenlun.method_library_release_probe import probe_method_library
    report=probe_method_library(tmp_path)
    assert report['ok'] and report['synthetic_fixtures_only'] and report['cloud_calls']==0
    for key in ('create_and_edit','precise_evidence','delete_cancel_preserved',
                'article_and_teacher_delete','restored_disabled','backup_restore','historical_snapshot_preserved',
                'manual_edits_preserved','concurrent_enable_guarded','permanent_delete',
                'purge_cancel_preserved','purged_history_preserved'):
        assert report[key]
    batch=report['batch']
    assert batch['cloud_calls']==0
    for key in ('ok','synthetic_fixtures_only','checkbox_selection','filtered_select_all','cancel_preserved',
                'article_eligibility_checked','teaching_audit_saved','batch_disable','per_item_results','backup_restore'):
        assert batch[key]
