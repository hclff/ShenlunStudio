"""Paged list APIs and the question/plan projection columns they rely on."""
import pytest

from shenlun.storage import Store


def fields(question='概括做法', question_type='归纳概括', answer='我的原答'):
    return {'question': question, 'materials': '社区开通电话服务。', 'answer': answer,
            'question_type': question_type, 'max_score': 20, 'word_limit': 200,
            'exam': '江苏省考', 'year': '2024', 'category': 'B', 'reference': ''}


def review(answer='我的原答', question='概括做法'):
    return {'submission': fields(question, answer=answer),
            'assessment': {'total_score': 12, 'max_score': 20, 'mode': 'full',
                           'rubric_fingerprint': 'r1'},
            'model': 'm1', 'prompt_version': 'p1', 'title': '社区做法'}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'paging.db')


def test_review_pages_do_not_overlap_and_match_the_total(store):
    ids = [store.save_review(review(answer='答案' + str(index))) for index in range(5)]
    first = store.list_reviews_page(limit=2, offset=0)
    second = store.list_reviews_page(limit=2, offset=2)
    assert [item['id'] for item in first] == [ids[4], ids[3]]
    assert [item['id'] for item in second] == [ids[2], ids[1]]
    assert store.count_reviews() == 5
    assert store.list_reviews_page(limit=2, offset=9) == []
    assert [item['id'] for item in store.list_reviews(limit=5)] == ids[::-1]


def test_question_pages_filter_by_projected_status(store):
    store.save_question(fields(), title='未练题')
    store.save_question(fields(question='概括另一做法'), title='已练题')
    store.save_review(review(question='概括另一做法'))
    retry = store.save_question(fields(question='第三题'), title='待重练题')
    store.mark_question_retry(retry, True)
    assert [item['title'] for item in store.list_questions_page(status='未练')] == ['未练题']
    assert [item['title'] for item in store.list_questions_page(status='已练')] == ['已练题']
    assert [item['title'] for item in store.list_questions_page(status='待重练')] == ['待重练题']
    assert store.count_questions() == 3
    assert store.count_questions(status='已练') == 1
    page = store.list_questions_page(limit=1, offset=1)
    assert len(page) == 1
    assert [item['title'] for item in store.list_questions(status='已练')] == ['已练题']


def test_question_type_filter_uses_projection(store):
    store.save_question(fields(), title='概括题')
    store.save_question(fields(question='写一篇文章', question_type='文章写作'), title='作文题')
    assert [item['title'] for item in store.list_questions_page(question_type='文章写作')] == ['作文题']
    assert store.count_questions(question_type='文章写作') == 1
    assert store.count_questions(query='概括') == 1


def test_usage_details_page_newest_first(store):
    store.record_usage({'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15, 'model': 'm1'})
    store.record_usage({'input_tokens': 20, 'output_tokens': 7, 'total_tokens': 27, 'model': 'm2'})
    newest = store.usage_details(limit=1)
    assert newest[0]['total_tokens'] == 27 and newest[0]['input_tokens'] == 20
    assert newest[0]['data']['model'] == 'm2'
    assert [item['data']['model'] for item in store.usage_details()] == ['m2', 'm1']
    assert store.usage_details(offset=1)[0]['data']['model'] == 'm1'


def test_review_plan_projection_follows_plan_writes(store):
    question_id = store.save_question(fields(), title='计划题')
    store.save_review_plan(question_id, {'status': '复习中', 'due_date': '2026-11-01',
                                         'tags': ['材料']}, expected_version=0)
    assert [item['id'] for item in store.list_review_plans(status='复习中')] == [question_id]
    assert [item['id'] for item in store.list_review_plans(due='due', today='2026-11-02')] == [question_id]
    assert store.list_review_plans(due='due', today='2026-10-21') == []
    store.save_review_plan(question_id, {'status': '已解决', 'due_date': '2026-11-01'},
                           expected_version=1)
    assert store.list_review_plans(due='due', today='2026-11-02') == []
    assert [item['id'] for item in store.list_review_plans(status='已解决')] == [question_id]
    assert store.list_questions_page(status='待重练') == []


def test_review_plans_keep_order_query_and_decoration(store):
    later = store.save_question(fields(question='题目甲'), title='乙题')
    sooner = store.save_question(fields(question='题目乙'), title='甲题')
    store.save_review_plan(later, {'status': '待复习', 'due_date': '2026-11-02'}, expected_version=0)
    store.save_review_plan(sooner, {'status': '待复习', 'due_date': '2026-11-01',
                                    'tags': ['基层']}, expected_version=0)
    assert [item['id'] for item in store.list_review_plans()] == [sooner, later]
    assert [item['id'] for item in store.list_review_plans(query='基层')] == [sooner]
    assert store.list_review_plans(query='基层')[0]['attempt_count'] == 0
    assert store.list_review_plans(query='基层')[0]['review_plan']['status'] == '待复习'
    assert store.list_review_plans(due='unscheduled') == []


def test_review_plans_include_retry_flag_without_plan(store):
    question_id = store.save_question(fields(), title='仅重练')
    store.mark_question_retry(question_id, True)
    plans = store.list_review_plans()
    assert [item['id'] for item in plans] == [question_id]
    assert plans[0]['review_plan']['status'] == '待复习'
    assert plans[0]['review_plan']['due_date'] == ''
    assert store.list_review_plans(status='复习中') == []
