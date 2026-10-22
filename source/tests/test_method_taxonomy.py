"""Method cards must be grouped by writing technique, not by wording.

The failure this guards against: "刚柔并济提出对策" contains 辩证, so a naive
keyword pass files a measure card under argument; and "回扣前文提出方向" contains
回扣, so a paragraph technique lands in the ending family.
"""
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))

from merge_method_cards import cluster_label  # noqa: E402
from method_taxonomy import (  # noqa: E402
    FAMILIES, classify_by_keywords, detect_stage, family_ids, parse_assignments,
    prompt_payload,
)


def classify(name, purpose=''):
    family = classify_by_keywords(name, purpose)
    return family.id if family else None


def test_stage_words_pick_the_article_part():
    assert detect_stage('总分总结构谋篇') == '结构'
    assert detect_stage('引权威论断开篇') == '开头'
    assert detect_stage('对策按思想制度行动分层') == '对策'
    assert detect_stage('提炼段首判断句') == '分论点'
    assert detect_stage('借比喻化抽象为具体') == ''


def test_technique_words_never_cross_stages():
    # 含“辩证”“刚柔”，但名称写明是对策，不能被论证族吸走
    assert classify('刚柔并济提出对策') in (None, 'measure.subject', 'measure.layer',
                                              'measure.problem_fit', 'measure.concrete',
                                              'measure.dimension', 'measure.mechanism',
                                              'measure.closure')


def test_paragraph_echo_is_not_a_ending():
    # “回扣”是段落回扣技法，不是首尾呼应
    assert classify('回扣前文提出方向') == 'thesis.keyword'
    assert classify('首尾呼应收束全文') == 'ending.echo'


def test_authority_evidence_lands_in_argument_family():
    assert classify('以权威论述支撑论点') == 'argument.authority'
    assert classify('引权威文献佐证观点') == 'argument.authority'


def test_technique_fallback_without_stage_word():
    assert classify('借比喻化抽象为具体') == 'argument.metaphor'
    assert classify('以今昔对照呈现变化') == 'argument.contrast'


def test_purpose_only_helps_when_name_has_no_stage_word():
    # 名称本身没环节词，purpose 提到对策：先试对策族，不中再全局兜底
    assert classify('引权威论述定调', '解决对策缺乏依据') == 'argument.authority'


def test_definition_and_explanation_are_split():
    assert classify('否定辨析深化概念') == 'concept.dialectic'
    assert classify('分层阐释概念内涵') == 'concept.explain'


def test_family_ids_are_unique_and_indexed():
    ids = family_ids()
    assert len(ids) == len(FAMILIES) == len(set(ids))


def test_parse_assignments_drops_unknown_ids():
    data = {'assignments': [
        {'key': 1, 'family': 'argument.data'},
        {'key': 2, 'family': 'not.a.family'},
        {'key': 'x', 'family': 'argument.data'},
        {'key': 3, 'family': None},
    ]}
    assert parse_assignments(data) == {1: 'argument.data'}


def test_prompt_payload_batches_keep_the_family_table():
    items = [{'key': index, 'name': f'方法{index}', 'purpose': ''} for index in range(25)]
    batches = prompt_payload(items, batch_size=10)
    assert [len(batch['items']) for batch in batches] == [10, 10, 5]
    assert all(len(batch['families']) == len(FAMILIES) for batch in batches)


def test_cluster_label_prefers_family_name():
    assert cluster_label({'family': '引用权威论证', 'names': ['以权威论述支撑论点']}) == '引用权威论证'
    assert cluster_label({'names': ['以权威论述支撑论点', '引权威佐证']}) == '以权威论述支撑论点'
    assert cluster_label({}) == ''
