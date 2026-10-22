import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shenlun.model import ModelClient, ModelError, CancelledError, validate_endpoint


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        status, response, requests = 200, {}, []
        response_script = None
        status_script = None
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append((self.path, body, self.headers.get('Authorization')))
            response = self.response_script.pop(0) if self.response_script else self.response
            status = self.status_script.pop(0) if self.status_script else self.status
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(encoded)
    srv = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    Handler.response = {'model':'fixture-model','choices':[{'message':{'content':'{"ok":true}'}}], 'usage':{'prompt_tokens':12,'completion_tokens':4,'total_tokens':16}}
    yield f'http://127.0.0.1:{srv.server_port}/v1', Handler
    srv.shutdown(); srv.server_close(); thread.join()


def test_real_http_payload_json_response_and_usage(server):
    url, handler = server
    client = ModelClient({'base_url':url,'model':'example-model','timeout':5}, 'test-not-a-real-key')
    result = client.complete('Only output JSON', {'answer':'保留原答'})
    assert result['data'] == {'ok':True}
    assert result['usage'] == {'input_tokens':12,'output_tokens':4,'total_tokens':16}
    path, body, auth = handler.requests[0]
    assert path == '/v1/chat/completions'
    assert json.loads(body['messages'][1]['content'])['answer']=='保留原答'
    assert body['messages'][0]['role']=='system'
    assert body['model']=='example-model'
    assert auth == 'Bearer test-not-a-real-key'


@pytest.mark.parametrize('url',['http://example.com/v1','https://user:password@example.com/v1','https://example.com/v1?key=hello','https://example.com/v1#key','file:///tmp/a','https://example.com/../hidden'])
def test_unsafe_endpoint_rejected(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_http_error_does_not_reveal_server_body_or_key(server):
    url,h=server;h.status=401;h.response={'error':'test-secret-and-user-answer'}
    client=ModelClient({'base_url':url,'model':'m'},'private-api-key')
    with pytest.raises(ModelError) as e:
        client.complete('s',{'question':'secret-question'})
    assert '401' in str(e.value)
    assert 'test-secret' not in str(e.value) and 'private-api-key' not in str(e.value)


def test_bad_model_json_is_not_success(server):
    url,h=server;h.response={'choices':[{'message':{'content':'这不是JSON'}}]}
    with pytest.raises(ModelError,match='JSON'):
        ModelClient({'base_url':url,'model':'m'},'').complete('s',{})


def test_canceled_call_never_reaches_server(server):
    url,h=server; cancel=threading.Event();cancel.set()
    with pytest.raises(CancelledError):
        ModelClient({'base_url':url,'model':'m'},'').complete('s',{},cancel)
    assert h.requests==[]


def test_input_limit_and_request_budget_enforced_before_network(server):
    url,h=server
    client=ModelClient({'base_url':url,'model':'m','max_input_chars':10},'')
    with pytest.raises(ModelError,match='输入'):
        client.complete('s',{'text':'a'*100})
    assert h.requests==[]


def test_exhausted_budget_stops_next_request(server):
    url,h=server
    c=ModelClient({'base_url':url,'model':'m','session_token_budget':6000,'max_output_tokens':5000},'')
    c.complete('s',{})
    c.spent_tokens=6000
    with pytest.raises(ModelError,match='预算'):
        c.complete('s',{})
    assert len(h.requests)==1


def test_redirect_cannot_forward_authorization(server):
    url,h=server;h.status=302
    with pytest.raises(ModelError):
        ModelClient({'base_url':url,'model':'m'},'private').complete('s',{})
    assert len(h.requests)==1


def test_provider_parameter_compatibility_is_configurable(server):
    url,h=server
    c=ModelClient({'base_url':url,'model':'reasoning-provider','token_parameter':'max_completion_tokens','send_temperature':False},'')
    c.complete('s',{})
    body=h.requests[0][1]
    assert body['max_completion_tokens']==5000
    assert 'max_tokens' not in body and 'temperature' not in body


def test_unknown_token_parameter_name_retries_with_alias(server):
    url,h=server
    h.status_script=[400,200]
    h.response_script=[{'error':{'param':'max_tokens','message':"Unsupported parameter: 'max_tokens' is not supported with this model."}},
                       {'choices':[{'message':{'content':'{"ok":true}'}}]}]
    c=ModelClient({'base_url':url,'model':'reasoning-provider','token_parameter':'max_tokens'},'')
    assert c.complete('s',{})['data']=={'ok':True}
    assert h.requests[0][1]['max_tokens']==5000
    assert h.requests[1][1]['max_completion_tokens']==5000
    assert 'max_tokens' not in h.requests[1][1]
    assert c.config['token_parameter']=='max_completion_tokens'


def test_declared_token_limit_does_not_retry_other_parameter(server):
    url,h=server
    h.status=400
    h.response={'error':{'param':'max_tokens','message':'max_tokens must be less than or equal to 4096'}}
    c=ModelClient({'base_url':url,'model':'m','token_parameter':'max_tokens'},'')
    with pytest.raises(ModelError,match='4096'):
        c.complete('s',{})
    assert len(h.requests)==1


@pytest.mark.parametrize('content', [
    '\ufeff{"ok":true}', '```\n{"ok":true}\n```',
    '```JSON\n{"ok":true}\n```',
    [{'type':'text','text':'{"ok":'}, {'type':'text','text':'true}'}],
])
def test_complete_accepts_whole_json_with_provider_text_wrappers(server, content):
    url, h = server
    h.response['choices'][0]['message']['content'] = content
    result = ModelClient({'base_url':url,'model':'deepseek-fixture'}, '').complete('输出JSON', {})
    assert result['data'] == {'ok':True}
    assert len(h.requests) == 1
    assert h.requests[0][1]['response_format'] == {'type':'json_object'}


@pytest.mark.parametrize('message,finish,expected', [
    ({'content':'{"ok":true}'}, 'length', '输出上限'),
    ({'content':'{"ok":'}, 'max_tokens', '输出上限'),
    ({'content':'', 'reasoning_content':'private-reasoning'}, 'stop', '推理'),
    ({'content':None, 'reasoning_content':'private-reasoning'}, 'stop', '推理'),
    ({'content':'   '}, 'stop', '空'),
    ({'content':None, 'refusal':'private-refusal'}, 'stop', '拒绝'),
    ({'content':'{"ok":true}'}, 'content_filter', '拦截'),
])
def test_response_failures_are_specific_and_preserve_billable_usage(server, message, finish, expected):
    url, h = server
    h.response['choices'][0] = {'message':message, 'finish_reason':finish}
    client = ModelClient({'base_url':url,'model':'deepseek-fixture'}, '')
    with pytest.raises(ModelError, match=expected) as caught:
        client.complete('输出JSON', {})
    assert caught.value.response_metadata['usage']['total_tokens'] == 16
    assert client.spent_tokens == 16
    assert 'private-' not in str(caught.value)


@pytest.mark.parametrize('raw', ['{"ok":true} {"ok":false}', '[{"ok":true}]',
                                '{"ok":', '说明文字 {"ok":true}',
                                '```json\n{"ok":true}\n```\nextra'])
def test_parser_does_not_guess_or_repair_incomplete_or_ambiguous_objects(server, raw):
    url, h = server
    h.response['choices'][0]['message']['content'] = raw
    with pytest.raises(ModelError, match='JSON'):
        ModelClient({'base_url':url,'model':'fixture'}, '').complete('输出JSON', {})


@pytest.mark.parametrize('raw', ['', None, 'plain invalid output'])
def test_service_recovers_once_and_accounts_each_http_reply(server, tmp_path, raw):
    from shenlun.service import AppService
    from shenlun.storage import Store
    url, h = server
    first = {**h.response, 'choices':[{'finish_reason':'stop','message':{'content':raw}}]}
    h.response_script = [first, h.response]
    store = Store(tmp_path/'retry.db')
    service = AppService(store, {'base_url':url,'model':'deepseek-fixture'}, '')
    assert service.test_connection()['ok'] is True
    assert len(h.requests) == 2
    assert store.usage_summary() == {'requests':2,'input_tokens':24,'output_tokens':8,'total_tokens':32}
    assert service.client.spent_tokens == 32
    assert all(r[1]['response_format'] == {'type':'json_object'} for r in h.requests)
    assert 'JSON' in h.requests[1][1]['messages'][0]['content']
    assert not store.list_reviews()


def test_json_recovery_is_bounded_to_one_extra_request(server, tmp_path):
    from shenlun.service import AppService
    from shenlun.storage import Store
    url, h = server
    h.response['choices'][0]['message']['content'] = ''
    store = Store(tmp_path/'twice-empty.db')
    with pytest.raises(ModelError, match='空'):
        AppService(store, {'base_url':url,'model':'deepseek-fixture'}, '').test_connection()
    assert len(h.requests) == 2
    assert store.usage_summary()['total_tokens'] == 32
    assert not store.list_reviews()


@pytest.mark.parametrize('finish', ['length', 'content_filter'])
def test_service_does_not_retry_truncation_or_filtering(server, tmp_path, finish):
    from shenlun.service import AppService
    from shenlun.storage import Store
    url, h = server
    h.response['choices'][0]['finish_reason'] = finish
    store = Store(tmp_path/'no-retry.db')
    with pytest.raises(ModelError):
        AppService(store, {'base_url':url,'model':'fixture'}, '').test_connection()
    assert len(h.requests) == 1 and store.usage_summary()['total_tokens'] == 16


def test_recovery_respects_remaining_token_budget(server, tmp_path):
    from shenlun.service import AppService
    from shenlun.storage import Store
    url, h = server
    h.response['choices'][0]['message']['content'] = ''
    h.response['usage'] = {'prompt_tokens':400,'completion_tokens':90,'total_tokens':490}
    store = Store(tmp_path/'budget.db')
    service = AppService(store, {'base_url':url,'model':'fixture',
        'max_output_tokens':128,'session_token_budget':500}, '')
    with pytest.raises(ModelError, match='预算'):
        service.test_connection()
    assert len(h.requests) == 1 and store.usage_summary()['total_tokens'] == 490


@pytest.mark.parametrize('field', ['max_input_chars','max_output_tokens','session_token_budget'])
def test_configured_limits_accept_9999999_and_reject_larger_values(field):
    client = ModelClient({'base_url':'http://127.0.0.1:1/v1','model':'fixture',field:9999999}, '')
    assert client.config[field] == 9999999
    with pytest.raises(ValueError):
        ModelClient({'base_url':'http://127.0.0.1:1/v1','model':'fixture',field:10000000}, '')


def test_cancellation_after_empty_reply_prevents_recovery_upload(server, tmp_path, monkeypatch):
    from shenlun.service import AppService
    from shenlun.storage import Store
    url, h = server
    h.response['choices'][0]['message']['content'] = ''
    store = Store(tmp_path/'cancel-retry.db')
    service = AppService(store, {'base_url':url,'model':'fixture'}, '')
    cancel = threading.Event()
    record = service._record_response_usage
    def record_and_cancel(response, prompt_version=None):
        record(response, prompt_version)
        cancel.set()
    monkeypatch.setattr(service, '_record_response_usage', record_and_cancel)
    with pytest.raises(CancelledError):
        service.test_connection(cancel=cancel)
    assert len(h.requests) == 1 and store.usage_summary()['total_tokens'] == 16
