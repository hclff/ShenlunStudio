from pathlib import Path
from shenlun.importers import classify_kind,parse_official_article


def test_editor_notice_is_not_opinion_training_text():
    assert classify_kind('本版责编：张三 李四','人民日报','评论')=='其他'
    assert classify_kind('欢迎赐稿','人民日报','评论')=='其他'


def test_empty_editor_page_is_available_metadata_not_a_failed_article():
    page=(Path(__file__).parent/'fixtures'/'people-real-empty-editor.html').read_text(encoding='utf8')
    a=parse_official_article(page,'https://paper.people.com.cn/rmrb/pc/content/202610/14/content_30185488.html')
    assert a['title'].startswith('本版责编')
    assert a['status']=='available' and a['body']==''
    assert a['training_eligible'] is False
