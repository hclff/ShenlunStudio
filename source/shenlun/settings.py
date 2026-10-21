"""User preferences and OS credential storage."""
import keyring
from .model import DEFAULT_CONFIG, validate_endpoint

_SESSION_KEY = None

def load_config(store):
    return {**DEFAULT_CONFIG, **{k:v for k,v in store.get_setting('model_config',{}).items() if k in DEFAULT_CONFIG}}

def save_config(store, config):
    clean = {k:v for k,v in config.items() if k in DEFAULT_CONFIG}
    if clean.get('base_url'):
        clean['base_url']=validate_endpoint(clean['base_url'])
    store.set_setting('model_config', {**DEFAULT_CONFIG, **clean})

def get_api_key():
    if _SESSION_KEY is not None:
        return _SESSION_KEY
    try:
        return keyring.get_password('ShenlunStudio','model-api-key') or ''
    except Exception:
        return ''

def set_api_key(key):
    global _SESSION_KEY
    _SESSION_KEY = str(key or '')
    try:
        if key:
            keyring.set_password('ShenlunStudio','model-api-key',key)
        else:
            try:
                keyring.delete_password('ShenlunStudio','model-api-key')
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception:
        return False
