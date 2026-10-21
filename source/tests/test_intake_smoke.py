"""Frozen smoke probe exercises local readers and review without touching an exercise."""
import importlib

import pytest
from PySide6.QtWidgets import QApplication


def test_import_probe_preserves_editors_and_generates_preview(tmp_path):
    from shenlun.storage import Store
    from shenlun.ui import MainWindow
    module=importlib.import_module('shenlun.__main__')
    assert hasattr(module,'probe_intake'), 'release needs actual packaged intake probe'
    app=QApplication.instance() or QApplication([])
    window=MainWindow(Store(tmp_path/'probe.db'))
    window.materials_edit.setPlainText('已有材料')
    source=tmp_path/'材料.txt';source.write_text('读入的新材料',encoding='utf8')
    screenshot=tmp_path/'preview.png'
    try:
        result=module.probe_intake(window,[source],screenshot)
        assert result['pages']==1 and result['errors']==[]
        assert '读入的新材料' in result['text']
        assert result['cancel_preserved_editors']
        assert window.materials_edit.toPlainText()=='已有材料'
        assert screenshot.stat().st_size>5000
    finally:
        window.close();app.processEvents()


def test_smoke_import_flag_requires_explicit_isolated_smoke_mode(tmp_path):
    from shenlun.__main__ import main
    with pytest.raises(SystemExit) as failure:
        main(['--smoke-import',str(tmp_path/'any.txt')])
    assert failure.value.code==2


def test_frozen_practice_probe_exercises_real_timer_restart_and_isolated_fixtures(tmp_path):
    from shenlun.release_probe import probe_practice
    app=QApplication.instance() or QApplication([])
    result=probe_practice(tmp_path)
    assert result['ok'] and result['real_timer_autosave'] and result['ui_comparison']
    assert result['backup_restore_safety'] and result['network_model_calls']==0
    assert result['independent_human_scores']==0
    assert list(tmp_path.glob('practice-probe-*/fixture.db'))
    assert not (tmp_path/'studio.db').exists()
