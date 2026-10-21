"""Small real HTTP probes; no provider credentials or user submissions involved."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shenlun.model import ModelClient, ModelError
from shenlun.service import AppService
from shenlun.storage import Store


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        status = 200
        response = {'model':'fixture', 'choices':[{'finish_reason':'stop', 'message':{'content':'{"ok":true}'}}],
                    'usage':{'prompt_tokens':10, 'completion_tokens':4, 'total_tokens':14}}

        def log_message(self, *args): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append(body)
            response = self.response
            if callable(response):
                status, response = response(body)
            else:
                status = self.status
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', Handler
    server.shutdown(); server.server_close(); thread.join()


def test_diagnostics_caps_probes_records_usage_and_does_not_change_config(endpoint, tmp_path):
    url, handler = endpoint
    config = {'base_url':url, 'model':'fixture', 'max_output_tokens':9999999,
              'session_token_budget':128, 'max_input_chars':1,
              'json_mode':False, 'token_parameter':'max_completion_tokens', 'temperature':.4}
    store = Store(tmp_path/'diagnostics.db')
    service = AppService(store, config, '')
    report = service.check_compatibility()
    assert [c['status'] for c in report['checks']] == ['passed', 'passed', 'passed']
    assert report['output_limit'] is None  # Tiny probe cannot prove a model's maximum.
    assert report['tested_output_tokens'] == 128
    assert len(handler.requests) == 3
    assert all(body.get('max_completion_tokens') == 128 for body in handler.requests)
    assert handler.requests[0].get('response_format') is None
    assert handler.requests[1]['response_format'] == {'type':'json_object'}
    assert handler.requests[2]['temperature'] == .4
    assert store.usage_summary()['requests'] == 3
    assert service.config['max_output_tokens'] == 9999999
    assert service.config['json_mode'] is False
    assert service.client is None  # A diagnostic never consumes a future task client.


def test_json_incompatibility_does_not_disguise_connection_or_parameter_success(endpoint, tmp_path):
    url, handler = endpoint
    def respond(body):
        if 'response_format' in body:
            return 400, {'error':{'param':'response_format', 'message':'unsupported response_format private-answer'}}
        return 200, {'choices':[{'message':{'content':'{"ok":true}'}}],
                     'usage':{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}}
    handler.response = staticmethod(respond)
    report = AppService(Store(tmp_path/'d.db'), {'base_url':url,'model':'fixture'}, '').check_compatibility()
    assert [c['status'] for c in report['checks']] == ['passed', 'failed', 'passed']
    assert 'private-answer' not in json.dumps(report)


def test_auth_failure_stops_further_paid_probes(endpoint, tmp_path):
    url, handler = endpoint
    handler.status = 401
    handler.response = {'error':{'message':'secret-key-should-not-be-shown'}}
    report = AppService(Store(tmp_path/'d.db'), {'base_url':url,'model':'fixture'}, 'fake-key').check_compatibility()
    assert [c['status'] for c in report['checks']] == ['failed', 'skipped', 'skipped']
    assert len(handler.requests) == 1
    assert 'secret-key' not in json.dumps(report)


def test_parameter_rejection_exposes_only_whitelisted_parameter_and_numeric_limit(endpoint):
    url, handler = endpoint
    handler.status = 400
    handler.response = {'error':{'param':'max_tokens',
        'message':'max_tokens must be less than or equal to 8192; key=private-secret; answer=private-answer'}}
    with pytest.raises(ModelError) as caught:
        ModelClient({'base_url':url,'model':'fixture'}, 'fake-key').complete('JSON', {})
    error = caught.value
    assert error.parameter == 'max_tokens'
    assert error.upper_limit == 8192
    assert '8192' in str(error)
    assert 'private' not in str(error)


def test_preflight_checks_exact_reservation_before_network():
    from shenlun.model_diagnostics import preflight_request
    with pytest.raises(ModelError, match='预算'):
        preflight_request({'max_output_tokens':5000,'session_token_budget':5000}, 'JSON', {'answer':'原答'})
    with pytest.raises(ModelError, match='字符'):
        preflight_request({'max_input_chars':1}, 'JSON', {'answer':'原答'})
    # Even an empty JSON object needs an input reservation; equality cannot fit.
    with pytest.raises(ModelError, match='预算'):
        preflight_request({'max_output_tokens':9999999,'session_token_budget':9999999}, '', {})
    result = preflight_request({'max_output_tokens':128,'session_token_budget':1000}, 'JSON', {})
    assert result['reserved_tokens'] == 131


def test_connection_probe_does_not_confuse_non_json_output_with_network_failure(endpoint, tmp_path):
    url, handler = endpoint
    handler.response = {'choices':[{'message':{'content':'普通文本'}}],
                        'usage':{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}}
    report = AppService(Store(tmp_path/'d.db'), {'base_url':url,'model':'fixture'}, '').check_compatibility()
    assert [c['status'] for c in report['checks']] == ['passed', 'failed', 'passed']


def test_preflight_rejects_explicitly_reported_limit_without_changing_user_value():
    from shenlun.model_diagnostics import preflight_request
    config = {'max_output_tokens':10000}
    with pytest.raises(ModelError, match='8192'):
        preflight_request(config, 'JSON', {}, output_limit=8192)
    assert config['max_output_tokens'] == 10000


@pytest.mark.parametrize('finish,content', [('length', ''), ('stop', '')])
def test_reasoning_exhausts_small_probe_without_being_misreported_as_network_failure(endpoint, tmp_path, finish, content):
    url, handler = endpoint
    handler.response = {'choices':[{'finish_reason':finish, 'message':{'content':content,'reasoning_content':'推理内容'}}],
                        'usage':{'prompt_tokens':10,'completion_tokens':128,'total_tokens':138}}
    report = AppService(Store(tmp_path/'d.db'), {'base_url':url,'model':'fixture'}, '').check_compatibility()
    assert [c['status'] for c in report['checks']] == ['passed', 'inconclusive', 'passed']
    assert '128' in report['checks'][1]['detail']


def test_wrong_sample_value_is_not_a_connection_failure(endpoint, tmp_path):
    url, handler = endpoint
    handler.response = {'choices':[{'message':{'content':'{"ok":false}'}}],
                        'usage':{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}}
    report = AppService(Store(tmp_path/'d.db'), {'base_url':url,'model':'fixture'}, '').check_compatibility()
    assert [c['status'] for c in report['checks']] == ['passed', 'failed', 'passed']
