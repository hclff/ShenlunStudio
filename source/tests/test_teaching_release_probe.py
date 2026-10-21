"""The shipping executable exercises teaching through an isolated real HTTP fixture."""
def test_teaching_probe_uses_synthetic_local_frozen_methods_only(tmp_path):
    from shenlun.teaching_release_probe import probe_teaching
    value=probe_teaching(tmp_path)
    assert value['ok'] and value['synthetic_fixtures_only']
    assert value['catalog_drafts']==24 and value['community_drafts']==12 and value['initial_enabled']==0
    assert value['loopback_calls']==3 and value['usage_tokens']==60
    assert value['single_grade_calls']==1 and value['compare_grade_calls']==1
    assert value['frozen_after_disable'] and value['backup_restore']
    assert value['ordinary_reviews']==0 and value['cloud_calls']==0
    assert value['owner_management'] == {'added':True,'archived':True,'restored_disabled':True,
                                         'historical_name':True,'backup_restore':True}
