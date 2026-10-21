import json

import pytest

from shenlun import settings


class MemorySettings:
    def __init__(self): self.items={}
    def set_setting(self,k,v): self.items[k]=v
    def get_setting(self,k,default=None): return self.items.get(k,default)


def test_settings_cannot_persist_secret_fields():
    store=MemorySettings()
    settings.save_config(store,{'base_url':'https://api.example.com/v1','model':'m','api_key':'private-test-secret','Authorization':'private-test-secret'})
    assert 'private-test-secret' not in json.dumps(store.items)
    assert settings.load_config(store)['model']=='m'


def test_vault_failure_keeps_only_session_secret(monkeypatch):
    def blocked(*args): raise RuntimeError('vault unavailable')
    monkeypatch.setattr(settings.keyring,'set_password',blocked)
    assert settings.set_api_key('session-test-value') is False
    assert settings.get_api_key()=='session-test-value'
    settings.set_api_key('')
    assert settings.get_api_key()==''
