"""Grade-only mode: one model call, no revisions, and a later revision-only retry."""
import copy
import json

import pytest

from shenlun.evaluation import canonical_hash
from shenlun.service import ASSESSMENT_SYSTEM, PROMPT_VERSION, AppService
from shenlun.storage import Store
from test_service import CONFIG, ai_assessment, raw, service


def make_task(store, mode='grade_only'):
    offline = AppService(store, CONFIG, '')
    submission, payload, sources, methods, points = offline.prepare_grade(raw())
    context = {'payload': payload, 'sources': sources, 'method_snapshot': methods,
               'reference_points_snapshot': points, 'prompt_version': PROMPT_VERSION,
               'system_hash': canonical_hash(ASSESSMENT_SYSTEM),
               'method_choice_snapshot': offline._last_method_choice,
               'revision_mode': mode}
    return store.create_assessment_task(submission, context)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'revision-mode.db')


def test_grade_only_skips_the_revision_call_and_completes(store):
    tid = make_task(store)
    review = service(store, [ai_assessment]).run_assessment_task(tid)
    assert review['revisions']['minimal'] == '' and review['revisions']['model'] == ''
    assert '仅精批' in review['revisions']['status']
    assert store.get_assessment_task(tid)['state'] == 'completed'
    assert store.get_review(review['id'])['assessment'] == review['assessment']
    assert store.usage_summary()['requests'] == 1


def test_revision_can_be_generated_later_without_regrading(store):
    tid = make_task(store)
    review = service(store, [ai_assessment]).run_assessment_task(tid)
    before = copy.deepcopy(store.get_review(review['id']))
    updated = service(store, [{'minimal': '保留原意的改稿', 'model': '示范稿'}]).retry_revisions(
        review['id'], expected_review=copy.deepcopy(before))
    assert updated['revisions']['minimal'] == '保留原意的改稿'
    assert updated['assessment'] == before['assessment']
    assert updated['assessment']['total_score'] == before['assessment']['total_score']
    assert store.usage_summary()['requests'] == 2
    assert store.get_assessment_task(tid)['state'] == 'completed'


def test_with_revision_keeps_the_two_call_flow(store):
    tid = make_task(store, 'with_revision')
    review = service(store, [ai_assessment, {'minimal': '改稿', 'model': '示范'}]).run_assessment_task(tid)
    assert review['revisions']['minimal'] == '改稿'
    assert store.get_assessment_task(tid)['state'] == 'completed'
    assert store.usage_summary()['requests'] == 2


def test_interrupted_grade_only_task_completes_on_resume_without_recalling(store):
    tid = make_task(store)
    original = store.save_assessment_review
    states = []

    def crash_on_completed(task_id, token, review, *, state, expected_review=None):
        states.append(state)
        if state == 'completed':
            raise SystemExit('process exited')
        return original(task_id, token, review, state=state, expected_review=expected_review)

    store.save_assessment_review = crash_on_completed
    with pytest.raises(SystemExit):
        service(store, [ai_assessment]).run_assessment_task(tid)
    assert states == ['scored', 'completed']
    saved = Store(store.path).get_assessment_task(tid)
    assert saved['state'] == 'scored' and saved['review_id']
    review = service(Store(store.path), []).run_assessment_task(tid)
    assert '仅精批' in review['revisions']['status']
    assert Store(store.path).get_assessment_task(tid)['state'] == 'completed'


def test_unknown_revision_mode_in_snapshot_is_not_recoverable(store):
    tid = make_task(store)
    with store._connection() as conn:
        data = json.loads(conn.execute('SELECT data FROM assessment_tasks WHERE id=?', (tid,)).fetchone()[0])
        data['context']['revision_mode'] = 'grade_only_or_something'
        conn.execute('UPDATE assessment_tasks SET data=? WHERE id=?', (json.dumps(data), tid))
    assert store.list_assessment_tasks()[0]['recoverable'] is False


def test_grade_only_is_the_default_and_the_choice_is_remembered(app, tmp_path):
    from shenlun.ui import MainWindow

    path = tmp_path / 'mode-ui.db'
    first = MainWindow(Store(path))
    assert first.revision_mode.currentData() == 'grade_only'
    first.revision_mode.setCurrentIndex(first.revision_mode.findData('with_revision'))
    assert Store(path).get_setting('revision_mode') == 'with_revision'
    first.close()
    app.processEvents()
    second = MainWindow(Store(path))
    assert second.revision_mode.currentData() == 'with_revision'
    second.close()
    app.processEvents()
