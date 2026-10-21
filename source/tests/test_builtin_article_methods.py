import copy
import json
import sqlite3
from pathlib import Path

import pytest

from shenlun.__main__ import create_store
from shenlun.service import AppService
from shenlun.storage import Store


def fixture_catalog(tmp_path):
    seed = tmp_path / 'seed.db'
    source = Store(seed)
    for identity, body in [('a', '保留电话渠道，便于不会操作手机的老人报修。'),
                           ('b', '窗口提供人工协助，避免复杂手续阻碍群众办事。')]:
        source.upsert_article({'id': identity, 'title': identity, 'body': body, 'journal': '求是'})
    articles = [source.get_article(i) for i in ('a', 'b')]
    card = {'id': 'builtin-test', 'name': '说明渠道怎样降低门槛', 'purpose': '补足动作到结果的解释',
            'steps': ['指出障碍', '解释人工渠道的作用'], 'applicable_types': ['文章写作'],
            'not_applicable': '材料不支持因果时不臆造。',
            'source_article_ids': ['a', 'b'], 'source_versions': {a['id']: a['fingerprint'] for a in articles},
            'source_fingerprints': [a['fingerprint'] for a in articles],
            'refs': [{'id': 'a:p1', 'quote': '保留电话渠道'}, {'id': 'b:p1', 'quote': '窗口提供人工协助'}],
            'enabled': True, 'status': '外来启用声明不得继承'}
    catalog = tmp_path / 'methods.json'
    catalog.write_text(json.dumps({'catalog_id': 'test-catalog', 'version': 1, 'cards': [card]}, ensure_ascii=False), encoding='utf-8')
    return seed, catalog, card


def test_fresh_install_contains_methods_without_alternate_launcher(tmp_path):
    seed, catalog, card = fixture_catalog(tmp_path)
    store = create_store(tmp_path / 'new', seed, method_catalog=catalog)
    installed = store.list_cards()
    assert len(installed) == 1 and installed[0]['id'] == card['id']
    assert not installed[0]['enabled']
    assert installed[0]['status'] == '软件内置文章方法'
    assert installed[0]['builtin_catalog'] == 'test-catalog'
    AppService(store, {}, '')._validate_card(installed[0])


def test_upgrade_adds_only_missing_methods_and_sources_preserving_user_records(tmp_path):
    seed, catalog, _ = fixture_catalog(tmp_path)
    directory = tmp_path / 'existing'
    store = Store(directory / 'studio.db')
    own = store.upsert_article({'id': 'own', 'body': '我的本地文章。'})
    store.set_setting('user-preference', {'value': '保留'})
    review_id = store.save_review({'submission': {'answer': '原答'}, 'assessment': {'total_score': None}})
    store.save_card({'id': 'user-card', 'name': '自建方法', 'enabled': True})
    again = create_store(directory, seed, method_catalog=catalog)
    assert again.get_article(own)['body'] == '我的本地文章。'
    assert again.get_setting('user-preference') == {'value': '保留'}
    assert again.get_review(review_id)['submission']['answer'] == '原答'
    assert {c['id'] for c in again.list_cards()} == {'user-card', 'builtin-test'}
    assert next(c for c in again.list_cards() if c['id'] == 'user-card')['enabled']
    assert len(list(directory.glob('内置文章方法导入前备份-*.db'))) == 1
    assert again.list_articles('人工协助')[0]['id'] == 'b'


def test_repeated_start_preserves_edits_and_enabled_or_disabled_state(tmp_path):
    seed, catalog, _ = fixture_catalog(tmp_path)
    directory = tmp_path / 'repeat'
    store = create_store(directory, seed, method_catalog=catalog)
    card = store.list_cards()[0]
    card.update(name='本地修改过的方法名称', enabled=True)
    store.save_card(card)
    again = create_store(directory, seed, method_catalog=catalog)
    assert again.list_cards() == [card]
    again.set_card_enabled(card['id'], False)
    expected = again.list_cards()
    assert create_store(directory, seed, method_catalog=catalog).list_cards() == expected


def test_same_id_user_card_is_never_overwritten(tmp_path):
    seed, catalog, _ = fixture_catalog(tmp_path)
    directory = tmp_path / 'collision'
    store = Store(directory / 'studio.db')
    original = {'id': 'builtin-test', 'name': '同编号本地内容', 'enabled': True}
    store.save_card(original)
    assert create_store(directory, seed, method_catalog=catalog).list_cards() == [original]
    assert store.stats()['articles'] == 0


def test_changed_local_article_is_preserved_and_method_cannot_enable(tmp_path):
    seed, catalog, _ = fixture_catalog(tmp_path)
    directory = tmp_path / 'changed-source'
    store = Store(directory / 'studio.db')
    store.upsert_article({'id': 'a', 'body': '这是用户改写过的正文。'})
    again = create_store(directory, seed, method_catalog=catalog)
    card = again.list_cards()[0]
    assert again.get_article('a')['body'] == '这是用户改写过的正文。'
    assert not card['enabled'] and card['status'] == '内置方法与本机文章不一致，已停用'
    with pytest.raises(ValueError):
        AppService(again, {}, '').enable_card(card['id'], True)


def test_invalid_catalog_cannot_partially_import(tmp_path):
    seed, catalog, card = fixture_catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding='utf-8'))
    broken = copy.deepcopy(card)
    broken['id'] = 'broken'
    broken['refs'][0]['quote'] = '凭空编造的引文'
    data['cards'].append(broken)
    catalog.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    directory = tmp_path / 'invalid'
    store = Store(directory / 'studio.db')
    before = store.stats()
    with pytest.raises(ValueError):
        create_store(directory, seed, method_catalog=catalog)
    assert store.stats() == before


def test_normal_entry_uses_bundled_catalog_by_default(tmp_path, monkeypatch):
    from shenlun import __main__ as entry
    seed, catalog, _ = fixture_catalog(tmp_path)
    monkeypatch.setattr(entry, 'bundled_seed', lambda: seed)
    monkeypatch.setattr(entry, 'bundled_article_methods', lambda: catalog, raising=False)
    assert len(entry.create_store(tmp_path / 'default').list_cards()) == 1


def test_real_bundled_catalog_has_twenty_evidence_valid_methods(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # An existing empty database exercises upgrade without copying the full corpus.
    Store(tmp_path / 'real' / 'studio.db')
    store = create_store(tmp_path / 'real', root / 'assets/seed.db',
                         method_catalog=root / 'assets/builtin_article_methods.json')
    assert len(store.list_cards()) == 20 and not store.list_cards(True)
    service = AppService(store, {}, '')
    for card in store.list_cards():
        service._validate_card(card)
    assert store.usage_summary()['requests'] == 0


def test_failed_upgrade_rolls_back_sources_cards_and_search_index(tmp_path):
    seed, catalog, _ = fixture_catalog(tmp_path)
    data = json.loads(catalog.read_text(encoding='utf-8'))
    second = copy.deepcopy(data['cards'][0])
    second['id'] = 'second'
    data['cards'].append(second)
    catalog.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    directory = tmp_path / 'rollback'
    store = Store(directory / 'studio.db')
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TRIGGER fail_import BEFORE INSERT ON cards WHEN NEW.id='second' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    before = store.stats()
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        create_store(directory, seed, method_catalog=catalog)
    assert store.stats() == before
    assert store.list_articles('人工协助') == []
