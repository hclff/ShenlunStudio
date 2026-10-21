"""Consent, no-credential estimates and saved comparison reports in actual Qt UI."""
import importlib.util
import json

import pytest
from PySide6.QtWidgets import QApplication,QLabel,QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow
from shenlun.ui.evaluation import EvaluationDialog
from test_method_comparison import setup,CONFIG


@pytest.fixture
def host(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    value=MainWindow(Store(tmp_path/'ui.db'));value.config=CONFIG
    yield value
    value.workers.clear();value.close();app.processEvents()


def test_comparison_page_uses_compact_guidance(host):
    dialog=EvaluationDialog(host)
    text='\n'.join(widget.text() for widget in dialog.findChildren(QLabel))
    assert '使用同一批作答对比“关闭方法”和指定组合；分数变化仅供参考。' in text
    assert dialog.comparison_detail.toPlainText() == '尚无对照。请先保存适用的方法组合。'
    assert '固定同一批完整作答' not in text
    dialog.reject()


def test_comparison_upload_confirmation_defaults_no_and_does_not_read_credentials(host,monkeypatch):
    assert importlib.util.find_spec('shenlun.ui.method_comparison'), 'paired evaluation tab is required'
    dataset,cases,preset=setup(host.store);dialog=EvaluationDialog(host)
    assert dialog.tabs.count()==4
    dialog.comparison_preset.setCurrentIndex(dialog.comparison_preset.findData(preset['id']))
    calls=[]
    def no(*args,**kwargs):calls.append((args,kwargs));return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',no)
    monkeypatch.setattr(host,'get_service',lambda **kw:pytest.fail('credentials read before explicit upload consent'))
    dialog.start_method_comparison()
    assert calls and calls[0][1]['defaultButton']==QMessageBox.StandardButton.No
    assert '两组' in calls[0][0][2] and 'token' in calls[0][0][2]
    assert not host.store.list_method_comparisons(dataset['id']) and not host.store.list_eval_runs(dataset['id'])
    dialog.reject()


def test_saved_comparison_display_and_export_say_no_human_accuracy(host,tmp_path):
    assert importlib.util.find_spec('shenlun.ui.method_comparison'), 'paired evaluation tab is required'
    dataset,cases,preset=setup(host.store)
    group=host.store.create_method_comparison(dataset['id'],[c['id'] for c in cases],1,CONFIG,preset['id'])
    dialog=EvaluationDialog(host);dialog.refresh_method_comparisons(selected=group['id'])
    text=dialog.comparison_detail.toPlainText()
    assert '人工对照样本不足' in text and '关闭方法' in text and preset['name'] in text
    path=tmp_path/'comparison.json';dialog.export_method_comparison(path)
    data=json.loads(path.read_text(encoding='utf8'))
    assert data['comparison']['id']==group['id'] and data['human_pairs']==0
    dialog.reject()
