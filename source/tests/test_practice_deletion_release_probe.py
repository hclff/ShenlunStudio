"""The distributed EXE exercises deletion through actual widgets and SQLite."""
import importlib.util


def test_shipping_record_and_question_deletion(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    assert importlib.util.find_spec('shenlun.practice_deletion_release_probe'), 'shipping deletion probe required'
    from shenlun.practice_deletion_release_probe import probe_practice_deletion
    report = probe_practice_deletion(tmp_path)
    for key in ('ok', 'synthetic_fixtures_only', 'cancel_preserved', 'single_and_bulk_reviews',
                'filtered_question_selection', 'single_and_bulk_questions', 'reports_preserved',
                'workspace_preserved', 'stale_confirmation_rejected', 'restart_and_backup'):
        assert report[key], key
    assert report['cloud_calls'] == 0
