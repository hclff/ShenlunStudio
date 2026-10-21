"""Normal grading confirms the actual preset and sends the frozen approved list."""
from threading import Event

import pytest
from PySide6.QtWidgets import QApplication,QMessageBox

from shenlun.storage import Store
from shenlun.ui import MainWindow


@pytest.fixture
def host(tmp_path):
    app=QApplication.instance() or QApplication([])
    value=MainWindow(Store(tmp_path/'workflow.db'));value.load_example()
    value.question_type.setCurrentText('归纳概括')
    value.store.set_teaching_method_enabled('LM01',True,reviewer='合成核对',note='软件测试')
    preset=value.store.save_method_preset({'name':'本次指定组合','question_type':'归纳概括','teaching_ids':['LM01'],'article_ids':[]})
    value.store.set_setting('method_choice',{'mode':'preset','preset_id':preset['id']});value.refresh_method_presets()
    yield value
    value.workers.clear();value.close();app.processEvents()


def test_cancel_actual_method_confirmation_does_not_read_credentials(host,monkeypatch):
    calls=[]
    def no(*args,**kwargs):calls.append((args,kwargs));return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',no)
    monkeypatch.setattr(host,'get_service',lambda **kw:pytest.fail('credentials read before confirming actual methods'))
    host.begin_assessment()
    assert calls and calls[0][1]['defaultButton']==QMessageBox.StandardButton.No
    assert '本次指定组合' in calls[0][0][2] and 'LM01' in calls[0][0][2]


def test_confirmed_normal_grading_uses_frozen_methods_after_setting_changes(host,monkeypatch):
    captured=[]
    def yes(*args,**kwargs):
        host.store.set_setting('method_choice',{'mode':'off'})
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',yes)
    class Service:
        def assess(self,s,**kwargs):captured.append(kwargs['grade_context']);return 'synthetic'
    monkeypatch.setattr(host,'get_service',lambda **kw:Service())
    monkeypatch.setattr(host,'start_job',lambda title,operation,done,**kwargs:operation(None,Event()))
    host.begin_assessment()
    assert len(captured)==1
    assert captured[0]['payload']['teaching_guidance']['methods'][0]['id']=='LM01'
    assert captured[0]['method_choice_snapshot']['name']=='本次指定组合'


def test_method_preview_summary_follows_off_without_legacy_source_errors(host):
    host.store.set_setting('teaching_selection',{'mode':'source','owner_ids':['bailu']})
    host.store.set_setting('method_choice',{'mode':'off'});host.refresh_method_presets();host.refresh_teaching_summary()
    assert '关闭' in host.teaching_summary.text()
    assert host.preflight_teaching(host.submission()) is None


def test_saved_report_shows_combination_and_actual_article_method_names():
    from shenlun.reports import review_html
    from test_evaluation import fixture_review,submission
    review=fixture_review(submission(),10)
    review['method_choice_snapshot']={'name':'合成组合 <甲>','version':2,'id':'mp-synthetic'}
    review['method_snapshot']=[{'id':'article-synthetic','name':'合成行动方法'}]
    rendered=review_html(review)
    assert '合成组合 &lt;甲&gt;' in rendered and '合成行动方法' in rendered and 'article-synthetic' in rendered
