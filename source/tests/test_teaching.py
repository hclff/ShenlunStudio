"""Synthetic-only teaching provenance, selection and append-only persistence tests."""
import importlib
import importlib.util
import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy

import pytest


def domain():
    assert importlib.util.find_spec('shenlun.teaching'), 'teaching domain module required'
    return importlib.import_module('shenlun.teaching')


def method(identity='synthetic-1', owner='limengyuan', scope='full_text'):
    return {'id': identity, 'owner_id': owner, 'name': '合成分类检查', 'purpose': '核对材料分类',
            'steps': ['找出材料事实', '按所问对象整理'], 'checks': ['类别有依据'],
            'applicable_types': ['归纳概括'], 'not_applicable': '不得代替题干要求',
            'sources': [{'title': '合成测试来源', 'url': 'https://example.org/source',
                         'author': '合成作者', 'published_at': '', 'accessed_at': '2026-10-16',
                         'access_scope': scope, 'locator': '第二段',
                         'excerpt': '合成来源强调按对象分类。', 'quote': '按对象分类'}]}


def submission():
    return {'question': '概括做法', 'materials': ' 社区开通电话。\r\n\r\n居民随时咨询。 ',
            'answer': '开通电话服务。', 'question_type': '归纳概括'}


@pytest.fixture
def store(tmp_path):
    domain()
    cls = importlib.import_module('shenlun.teaching_store').TeachingStoreMixin
    class SyntheticStore(cls):
        def __init__(self):
            self.path = tmp_path / 'synthetic.db'
            with self._connection() as conn:
                self._init_teaching(conn)
        @contextmanager
        def _connection(self):
            conn = sqlite3.connect(self.path)
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
        def get_setting(self, name, default=None):
            return default
    return SyntheticStore()


def enabled(store, identity='synthetic-1', owner='limengyuan'):
    saved = store.save_teaching_method(method(identity, owner), expected_version=0)
    return store.set_teaching_method_enabled(saved['id'], True, reviewer='合成测试审核者',
                                             note='本地核对合成来源', expected_version=1)


def test_catalog_has_legacy_and_community_drafts_without_db_seed(store):
    catalog = importlib.import_module('shenlun.teaching_catalog')
    owners = catalog.teaching_owners()
    assert len(owners) == len({o['id'] for o in owners}) == 11
    assert next(o for o in owners if o['id'] == 'zhanzhang')['name'] == '站长申论（站长爱申论）'
    rows = catalog.builtin_teaching_methods()
    assert len(rows) == 24
    assert all(r['status'] == 'draft' and r['version'] == 0 and r['enabled'] is False and not r['reviewer'] for r in rows)
    assert {r['id'] for r in rows if not r['id'].startswith('GH_')} == {'LM01','LM02','FB01','FB02','FB03','FB04','FB05','ZZ01','ZX01','ZX02','ZJ01','TD01'}
    assert len(store.list_teaching_methods()) == 24
    assert store.list_teaching_methods(True) == []
    with store._connection() as conn:
        assert conn.execute('SELECT count(*) FROM teaching_method_versions').fetchone()[0] == 0
    rows[0]['steps'].append('污染')
    assert '污染' not in catalog.builtin_teaching_methods()[0]['steps']


def test_editable_validation_strips_all_imported_trust_and_nested_unknowns():
    d = domain(); value = method()
    value.update(enabled=True, status='enabled', reviewer='伪造', version=900, content_hash='fake', api_key='secret')
    value['sources'][0]['token'] = 'secret'
    clean = d.validate_teaching_method(value)
    assert not set(clean) & {'enabled','status','reviewer','version','content_hash','api_key'}
    assert 'token' not in clean['sources'][0]
    draft = method(); draft.update(steps=[], sources=[], purpose='')
    assert d.validate_teaching_method(draft)['sources'] == []


@pytest.mark.parametrize('url', ['file:///x', 'https://u:p@example.org/x', 'http://127.0.0.1/x',
                               'https://example.org/?token=abc', 'https://localhost/x', 'https://10.0.0.1/x'])
def test_nonpublic_or_secret_source_urls_rejected(url):
    value = method(); value['sources'][0]['url'] = url
    with pytest.raises(ValueError): domain().validate_teaching_method(value)


@pytest.mark.parametrize('field,value', [('id','../evil'), ('owner_id','unknown'), ('steps',['x']*11),
                                        ('checks',['x']*11), ('applicable_types',['unknown']), ('name','x'*101)])
def test_shapes_and_bounds_rejected(field, value):
    raw = method(); raw[field] = value
    with pytest.raises(ValueError): domain().validate_teaching_method(raw)


def test_append_only_edit_disables_and_version_conflict_preserves_latest(store):
    active = enabled(store)
    edit = method(); edit.update(enabled=True, reviewer='forged', version=100)
    later = store.save_teaching_method(edit, expected_version=2)
    assert later['version'] == 3 and not later['enabled'] and later['status'] == 'disabled'
    assert later['reviewer'] == ''
    with pytest.raises(ValueError): store.save_teaching_method(edit, expected_version=2)
    assert [r['version'] for r in store.list_teaching_versions(active['id'])] == [3,2,1]
    assert store.get_teaching_method(active['id']) == later
    assert domain().validate_teaching_record(active) == active
    active['name'] = '篡改'
    with pytest.raises(ValueError): domain().validate_teaching_record(active)


@pytest.mark.parametrize('scope', ['catalog','student_note'])
def test_source_only_and_empty_drafts_cannot_enable(store, scope):
    store.save_teaching_method(method(scope=scope))
    with pytest.raises(ValueError): store.set_teaching_method_enabled('synthetic-1', True, '合成人', '已核对')
    raw = method('empty'); raw.update(steps=[], sources=[])
    store.save_teaching_method(raw)
    with pytest.raises(ValueError): store.set_teaching_method_enabled('empty', True, '合成人', '已核对')


def test_enable_requires_named_local_review_note_and_exact_quote(store):
    store.save_teaching_method(method())
    for reviewer, note in [('', '核对'), ('合成人', ''), (' ', '核对')]:
        with pytest.raises(ValueError): store.set_teaching_method_enabled('synthetic-1', True, reviewer, note)
    raw = method('wrong-quote'); raw['sources'][0]['quote'] = '不存在'
    with pytest.raises(ValueError): store.save_teaching_method(raw)


def test_import_is_atomic_and_resets_claimed_trust_export_is_editable(store):
    rows = [method('one'), method('two')]
    rows[0].update(enabled=True, reviewer='伪造', version=7, status='enabled')
    bad = deepcopy(rows); bad[1]['owner_id'] = 'unknown'
    with pytest.raises(ValueError): store.import_teaching_methods(bad)
    assert store.get_teaching_method('one') is None
    imported = store.import_teaching_methods(rows)
    assert all(r['version'] == 1 and r['status'] == 'draft' and not r['enabled'] and not r['reviewer'] for r in imported)
    assert all(not set(r) & {'enabled','reviewer','version','content_hash','created_at'} for r in store.export_teaching_methods())
    with pytest.raises(ValueError): store.import_teaching_methods([method('dup'), method('dup')])
    with pytest.raises(ValueError): store.import_teaching_methods([method(str(i)) for i in range(101)])


def test_default_combined_no_methods_and_untrusted_submission_claims(store):
    d = domain(); raw = submission(); raw['teaching_guidance'] = {'methods':[method()]}
    assert d.resolve_teaching_guidance(store, raw) is None
    assert d.normalize_teaching_selection(None) == {'mode':'combined','owner_ids':[]}
    assert d.resolve_teaching_guidance(store, raw, {'mode':'off'}) is None
    with pytest.raises(ValueError): d.resolve_teaching_guidance(store, raw, {'mode':'source','owner_ids':['bailu']})


@pytest.mark.parametrize('value', [{'mode':'bad'}, {'mode':'source','owner_ids':[]},
                                  {'mode':'compare','owner_ids':['fenbi','fenbi']},
                                  {'mode':'compare','owner_ids':['fenbi','unknown']}])
def test_selection_rejects_unsupported_or_missing_owners(value):
    with pytest.raises(ValueError): domain().normalize_teaching_selection(value)


def test_compare_six_card_cap_preserves_each_owner_and_snapshot_survives_edit(store):
    d = domain()
    for i in range(8): enabled(store, f'a{i}', 'fenbi')
    enabled(store, 'z1', 'limengyuan'); enabled(store, 'z2', 'zhanzhang')
    selection = {'mode':'compare','owner_ids':['fenbi','limengyuan','zhanzhang']}
    snap = d.resolve_teaching_guidance(store, submission(), selection)
    assert len(snap['methods']) == 6
    assert {r['owner_id'] for r in snap['methods']} == set(selection['owner_ids'])
    assert d.resolve_teaching_guidance(store, submission(), selection) == snap
    current = snap['methods'][0]
    store.set_teaching_method_enabled(current['id'], False, note='合成停用', expected_version=current['version'])
    assert d.validate_teaching_snapshot(snap, submission()) == snap
    assert d.teaching_conditions(snap)['content_hash'] == snap['content_hash']
    forged = deepcopy(snap); forged['mode'] = 'source'
    with pytest.raises(ValueError): d.validate_teaching_snapshot(forged, submission())
    other = submission(); other['question_type'] = '提出对策'
    with pytest.raises(ValueError): d.validate_teaching_snapshot(snap, other)


def test_output_cites_actual_normalized_materials_and_each_method_exactly_once(store):
    d = domain(); enabled(store)
    snap = d.resolve_teaching_guidance(store, submission())
    value = {'applications':[{'method_id':'synthetic-1','status':'applied','observation':'合成观察',
                             'material_refs':[{'id':'M1','quote':'开通电话'}], 'answer_quote':'开通电话'}],
             'consensus':[], 'differences':[]}
    assert d.validate_teaching_output(value, snap, submission()) == value
    for key, change in [('material_refs', []), ('material_refs',[{'id':'M1','quote':'居民随时'}]),
                        ('method_id','unknown'), ('answer_quote','虚构')]:
        bad = deepcopy(value); bad['applications'][0][key] = change
        with pytest.raises(ValueError): d.validate_teaching_output(bad, snap, submission())
    bad = deepcopy(value); bad['applications'] *= 2
    with pytest.raises(ValueError): d.validate_teaching_output(bad, snap, submission())
    raw = submission(); raw['materials_paragraphs'] = [{'id':'M1','text':'虚构'}]
    bad = deepcopy(value); bad['applications'][0]['material_refs'][0]['quote'] = '虚构'
    with pytest.raises(ValueError): d.validate_teaching_output(bad, snap, raw)


def test_backup_checks_index_ids_hash_unknown_fields_and_legacy_absence(store):
    active = enabled(store)
    with store._connection() as conn:
        store._validate_teaching_restore(conn, {'teaching_method_versions'})
        store._validate_teaching_restore(conn, set())
        altered = deepcopy(active); altered['secret'] = 'never propagate'
        altered['content_hash'] = domain().teaching_content_hash(altered)
        conn.execute('UPDATE teaching_method_versions SET data=? WHERE version=2', (json.dumps(altered),))
        with pytest.raises(ValueError): store._validate_teaching_restore(conn, {'teaching_method_versions'})
        conn.execute('UPDATE teaching_method_versions SET data=? WHERE version=2', (json.dumps(active),))
        conn.execute("UPDATE teaching_method_versions SET id='forged' WHERE version=2")
        with pytest.raises(ValueError): store._validate_teaching_restore(conn, {'teaching_method_versions'})


def test_guidance_resolves_real_store_get_setting_api(store):
    enabled(store)
    store.get_setting = lambda name, default=None: {'mode':'off'}
    assert domain().resolve_teaching_guidance(store, submission()) is None


def test_import_collision_clones_instead_of_silently_disabling_active_method(store):
    active = enabled(store)
    imported = store.import_teaching_methods([method()])[0]
    assert imported['id'] != active['id'] and imported['status'] == 'draft'
    assert store.get_teaching_method(active['id']) == active
    assert len(store.list_teaching_versions(active['id'])) == 2
    builtin = store.get_teaching_method('LM01')
    clone = store.import_teaching_methods([builtin])[0]
    assert clone['id'] != 'LM01' and store.get_teaching_method('LM01') == builtin


def test_malformed_question_types_raise_valueerror_not_typeerror(store):
    raw = submission(); raw['question_type'] = []
    with pytest.raises(ValueError): domain().resolve_teaching_guidance(store,raw)


def test_draft_size_limit_applies_after_canonicalization():
    raw = method()
    raw['sources'] = [{**raw['sources'][0], 'excerpt':'甲'*4000, 'locator':'甲'*4000} for _ in range(8)]
    with pytest.raises(ValueError): domain().validate_teaching_method(raw)


def test_concurrent_edits_have_one_winner(store):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    store.save_teaching_method(method(),expected_version=0)
    barrier = threading.Barrier(2)
    def save(_):
        barrier.wait()
        try:
            return store.save_teaching_method(method(),expected_version=1)['version']
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2)),key=str) == [2,'conflict']
    assert len(store.list_teaching_versions('synthetic-1')) == 2


def test_compare_output_requires_explicit_comparison_lists(store):
    enabled(store,'a','fenbi'); enabled(store,'b','limengyuan')
    d=domain(); snap=d.resolve_teaching_guidance(store,submission(),{'mode':'compare','owner_ids':['fenbi','limengyuan']})
    output={'applications':[{'method_id':r['id'],'status':'uncertain','observation':'合成证据不足','material_refs':[]} for r in snap['methods']]}
    with pytest.raises(ValueError): d.validate_teaching_output(output,snap,submission())
    output.update(consensus=[],differences=[])
    assert d.validate_teaching_output(output,snap,submission())['consensus']==[]


def test_disabled_and_unknown_nested_fields_fail_strict_record_validation(store):
    d=domain(); active=enabled(store)
    tampered=deepcopy(active); tampered['sources'][0]['api_key']='never-export'
    tampered['content_hash']=d.teaching_content_hash(tampered)
    with pytest.raises(ValueError): d.validate_teaching_record(tampered)
    tampered=deepcopy(active); tampered['status']='disabled'
    tampered['content_hash']=d.teaching_content_hash(tampered)
    with pytest.raises(ValueError): d.validate_teaching_record(tampered)


def test_restore_rejects_trigger_and_view(store):
    with store._connection() as conn:
        conn.execute('CREATE TRIGGER teaching_poison AFTER INSERT ON teaching_method_versions BEGIN SELECT 1; END')
        with pytest.raises(ValueError): store._validate_teaching_restore(conn,{'teaching_method_versions'})
    other=sqlite3.connect(':memory:')
    try:
        other.execute('CREATE VIEW teaching_method_versions AS SELECT 1 AS id,1 AS version,\'{}\' AS data')
        with pytest.raises(ValueError): store._validate_teaching_restore(other,{'teaching_method_versions'})
    finally:
        other.close()



@pytest.mark.parametrize('url', ['https://127.1/x','https://../x','https://example.org/?authToken=secret',
                               'https://example.org/?sessionid=secret','https://example.org/?jwt=secret'])
def test_disguised_private_hosts_and_credential_query_names_are_rejected(url):
    raw=method(); raw['sources'][0]['url']=url
    with pytest.raises(ValueError): domain().validate_teaching_method(raw)


def test_public_ipv6_and_nondefault_port_are_public_urls():
    raw=method(); raw['sources'][0]['url']='https://[2606:4700:4700::1111]:8443/source?id=2'
    assert domain().validate_teaching_method(raw)['sources'][0]['url']==raw['sources'][0]['url']


def test_new_ids_and_version_upper_bound(store):
    raw=method(); raw.pop('id')
    assert store.save_teaching_method(raw)['id'].startswith('tm-')
    active=enabled(store)
    for value in [False,-1,'2',2147483648]:
        with pytest.raises(ValueError): store.save_teaching_method(method(),expected_version=value)
    extreme=deepcopy(active); extreme['version']=2147483647
    extreme['content_hash']=domain().teaching_content_hash(extreme)
    with store._connection() as conn:
        conn.execute('INSERT INTO teaching_method_versions VALUES(?,?,?)',
                     (extreme['id'],extreme['version'],json.dumps(extreme)))
    with pytest.raises(ValueError): store.set_teaching_method_enabled(extreme['id'],False,expected_version=2147483647)


def test_distinct_method_cap_is_atomic_and_includes_catalog(store):
    for offset in range(0, 488, 100):
        store.import_teaching_methods([method('cap-'+str(i)) for i in range(offset,min(offset+100,488))])
    # Preserve all 488 local slots from the 12-card catalog after adding 12 cards.
    assert len(store.list_teaching_methods())==512
    with pytest.raises(ValueError): store.import_teaching_methods([method('too-many')])
    assert store.get_teaching_method('too-many') is None
    # Updating the same local method does not consume another distinct slot.
    assert store.save_teaching_method(method('cap-0'),expected_version=1)['version']==2



@pytest.mark.parametrize('host', ['0x7f.0.0.1','0x7F.0.0.1','0x7f000001','0x7f.1',
                                '0x0a.0.0.1','0xac.16.0.1','0xc0.0xa8.1.1','0300.0250.1.1'])
def test_legacy_numeric_hosts_never_qualify_as_public_sources(host):
    raw=method(); raw['sources'][0]['url']='https://'+host+'/source'
    with pytest.raises(ValueError): domain().validate_teaching_method(raw)


@pytest.mark.parametrize('url', ['https://example.org/source','https://0x7f.example.org/source',
                               'https://1.2.example.org/source','https://[2606:4700:4700::1111]/source'])
def test_numeric_host_guard_preserves_dns_names_and_public_ipv6(url):
    raw=method(); raw['sources'][0]['url']=url
    assert domain().validate_teaching_method(raw)['sources'][0]['url']==url


@pytest.mark.parametrize('scope',['full_text','index_text','catalog','student_note'])
def test_each_secondary_nonempty_quote_requires_own_exact_excerpt(store,scope):
    active=enabled(store)
    bad=method('bad-secondary')
    bad['sources'].append({**bad['sources'][0], 'access_scope':scope,
                           'title':'合成第二来源','excerpt':'第二来源的实际文字','quote':'虚构引文'})
    d=domain()
    with pytest.raises(ValueError): d.validate_teaching_method(bad)
    with pytest.raises(ValueError): store.save_teaching_method(bad)
    with pytest.raises(ValueError): store.import_teaching_methods([method('atomic-first'),bad])
    assert store.get_teaching_method('atomic-first') is None
    assert store.get_teaching_method('bad-secondary') is None
    forged={**active,'sources':bad['sources']}
    forged['content_hash']=d.teaching_content_hash(forged)
    with pytest.raises(ValueError): d.validate_teaching_record(forged)
    snapshot=d.resolve_teaching_guidance(store,submission())
    snapshot['methods']=[forged]
    snapshot['content_hash']=d.teaching_content_hash(snapshot)
    with pytest.raises(ValueError): d.validate_teaching_snapshot(snapshot,submission())
    with store._connection() as conn:
        conn.execute('UPDATE teaching_method_versions SET data=? WHERE id=? AND version=2',
                     (json.dumps(forged),active['id']))
        with pytest.raises(ValueError): store._validate_teaching_restore(conn,{'teaching_method_versions'})
    with pytest.raises(ValueError): store.set_teaching_method_enabled(active['id'],True,'合成人','核对',expected_version=2)


def test_incomplete_empty_secondary_source_quote_remains_valid_draft_and_enable(store):
    raw=method()
    raw['sources'].append({'access_scope':'catalog','excerpt':'','quote':''})
    saved=store.save_teaching_method(raw)
    assert saved['sources'][1]['quote']==''
    assert store.set_teaching_method_enabled(saved['id'],True,'合成人','已核对',expected_version=1)['enabled']
