from PySide6.QtWidgets import QApplication


def test_release_probe_exercises_real_learning_ui_and_local_database(tmp_path):
    from shenlun.learning_release_probe import probe_learning
    app = QApplication.instance() or QApplication([])
    result = probe_learning(tmp_path, tmp_path/'release.png')
    assert all(result.values())
    assert (tmp_path/'release-learning-summary.png').is_file()
    assert (tmp_path/'release-learning-diff.png').is_file()
    assert (tmp_path/'release-review-list.png').is_file()
    app.processEvents()
