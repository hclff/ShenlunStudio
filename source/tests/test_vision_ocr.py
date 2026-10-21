"""Synthetic images and loopback HTTP only; never use a configured provider."""
import base64
import io
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image, PngImagePlugin

from shenlun.model import CancelledError, ModelClient, ModelError
from shenlun.storage import Store


def picture(size=(100, 60), mode='RGB', color='white', **save_options):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, 'PNG', **save_options)
    return output.getvalue()


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        status, requests, delay = 200, [], 0
        received = threading.Event()
        response = {'model': 'fixture-vision', 'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'text': '原文，数字123。\n第二段', 'uncertainties': ['末字待核对']})}}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150}}

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append((self.path, body, self.headers.get('Authorization')))
            self.received.set()
            time.sleep(self.delay)
            self.send_response(self.status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            try:
                self.wfile.write(json.dumps(self.response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f'http://127.0.0.1:{srv.server_port}/v1', 'model': 'fixture', 'timeout': 3}, Handler
    srv.shutdown()
    srv.server_close()
    thread.join()


def test_transport_sends_one_inline_image_with_compatibility_and_usage(server):
    config, handler = server
    image = picture()
    client = ModelClient({**config, 'max_input_chars': 100, 'token_parameter': 'max_completion_tokens',
                          'send_temperature': False}, 'fixture-secret')
    result = client.complete_image('转写', {'action': 'ocr'}, image)
    assert result['usage']['total_tokens'] == client.spent_tokens == 150
    path, body, auth = handler.requests[0]
    assert path == '/v1/chat/completions' and auth == 'Bearer fixture-secret'
    assert body['max_completion_tokens'] == 5000 and 'temperature' not in body
    content = body['messages'][1]['content']
    assert len(content) == 2 and json.loads(content[0]['text']) == {'action': 'ocr'}
    assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
    assert base64.b64decode(content[1]['image_url']['url'].split(',', 1)[1]) == image


@pytest.mark.parametrize('image', [b'not an image', b'x' * (4 * 1024 * 1024 + 1), 'https://example.com/private.png'], ids=['invalid', 'oversize', 'url'])
def test_invalid_image_rejected_before_network(server, image):
    config, handler = server
    with pytest.raises((ModelError, ValueError), match='图像|图片'):
        ModelClient(config, '').complete_image('s', {}, image)
    assert handler.requests == []


def test_image_reservation_and_text_limits_block_before_network(server):
    config, handler = server
    with pytest.raises(ModelError, match='预算'):
        ModelClient({**config, 'session_token_budget': 10000}, '').complete_image('s', {}, picture())
    with pytest.raises(ModelError, match='输入'):
        ModelClient({**config, 'max_input_chars': 1}, '').complete_image('s', {}, picture())
    assert handler.requests == []


def test_prepare_strips_metadata_flattens_white_and_keeps_full_input_dimensions():
    from shenlun.vision_ocr import prepare_image
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('private', 'synthetic-sensitive-metadata')
    result = prepare_image(picture((3000, 2000), 'RGBA', (5, 6, 7, 0), pnginfo=metadata))
    assert (result['original_width'], result['original_height']) == (3000, 2000)
    assert (result['width'], result['height']) == (2048, 1365)
    assert len(result['image']) <= 4 * 1024 * 1024
    with Image.open(io.BytesIO(result['image'])) as decoded:
        assert decoded.mode == 'RGB' and decoded.getpixel((0, 0)) == (255, 255, 255)
        assert not decoded.getexif() and 'private' not in decoded.info
    assert b'synthetic-sensitive-metadata' not in result['image']


def test_prepare_exif_orientation_and_crop_advice():
    from shenlun.vision_ocr import prepare_image
    image = Image.new('RGB', (60, 100), 'white')
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, 'JPEG', exif=exif)
    result = prepare_image(output.getvalue())
    assert (result['width'], result['height']) == (100, 60)
    with pytest.raises(ValueError, match='裁剪'):
        prepare_image(picture((100, 1100)))


@pytest.mark.parametrize('raw', [b'', b'bad', b'x' * (25 * 1024 * 1024 + 1)], ids=['empty', 'invalid', 'oversize'])
def test_prepare_rejects_bad_or_oversize_source(raw):
    from shenlun.vision_ocr import prepare_image
    with pytest.raises(ValueError):
        prepare_image(raw)


def test_estimate_matches_real_request_and_budget_reservation(server):
    from shenlun.vision_ocr import estimate_image, recognize_image
    config, handler = server
    image = picture((800, 400))
    estimate = estimate_image(image, config)
    assert estimate['estimated_input_tokens'] >= 8192
    assert estimate['estimated_total_tokens'] == estimate['estimated_input_tokens'] + 5000
    assert estimate['bytes'] <= 4 * 1024 * 1024
    with pytest.raises(ModelError, match='预算'):
        recognize_image(image, {**config, 'session_token_budget': estimate['estimated_total_tokens'] - 1}, '')
    assert handler.requests == []
    recognize_image(image, {**config, 'session_token_budget': estimate['estimated_total_tokens']}, '')
    assert len(handler.requests) == 1


def test_image_estimate_accepts_expanded_output_limit():
    from shenlun.vision_ocr import estimate_image
    estimate = estimate_image(picture(), {'max_output_tokens':9999999})
    assert estimate['estimated_total_tokens'] == estimate['estimated_input_tokens'] + 9999999
    with pytest.raises(ValueError):
        estimate_image(picture(), {'max_output_tokens':10000000})


def test_recognize_transcribes_without_review_or_image_storage(server, tmp_path):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    store = Store(tmp_path / 'isolated.db')
    result = recognize_image(picture(), config, 'fixture-secret', store=store)
    assert result['text'] == '原文，数字123。\n第二段'
    assert result['uncertainties'] == ['末字待核对']
    assert result['model'] == 'fixture-vision' and result['usage_estimated'] is False
    assert result['info']['width'] == 100 and result['info']['height'] == 60
    assert store.usage_summary()['total_tokens'] == 150 and store.stats()['reviews'] == 0
    with sqlite3.connect(store.path) as conn:
        stored = conn.execute('SELECT data FROM usage').fetchone()[0]
    assert 'base64' not in stored and 'fixture-secret' not in stored
    assert set(json.loads(stored)) == {'input_tokens', 'output_tokens', 'total_tokens', 'model',
                                       'prompt_version', 'created_at', 'estimated'}
    body = handler.requests[0][1]
    assert '不解题' in body['messages'][0]['content'] and '不润色' in body['messages'][0]['content']
    payload = json.loads(body['messages'][1]['content'][0]['text'])
    assert set(payload['output_schema']) == {'text', 'uncertainties'}


@pytest.mark.parametrize('data', [
    {'text': 3, 'uncertainties': []}, {'text': 'x' * 250001, 'uncertainties': []},
    {'text': 'x', 'uncertainties': ['x'] * 51}, {'text': 'x', 'uncertainties': [False]},
    {'text': 'x', 'uncertainties': ['x' * 2001]}, {'text': 'x'},
])
def test_semantic_failures_still_record_actual_usage(server, tmp_path, data):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.response['choices'][0]['message']['content'] = json.dumps(data)
    store = Store(tmp_path / 'rejected.db')
    with pytest.raises((ValueError, ModelError)):
        recognize_image(picture(), config, '', store=store)
    assert store.usage_summary()['total_tokens'] == 150 and store.stats()['reviews'] == 0


@pytest.mark.parametrize('content,finish', [('not json', 'stop'), ('{"text":"partial","uncertainties":[]}', 'length')])
def test_invalid_or_truncated_json_cannot_be_saved_as_success_but_records_usage(server, tmp_path, content, finish):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.response['choices'][0].update(finish_reason=finish, message={'content': content})
    store = Store(tmp_path / 'invalid.db')
    with pytest.raises(ModelError):
        recognize_image(picture(), config, '', store=store)
    assert store.usage_summary()['total_tokens'] == 150


@pytest.mark.parametrize('choice', [None, 'bad-shape', 7, True,
                                    {'message': None}, {'message': 'bad-shape'}],
                         ids=['null-choice', 'string-choice', 'integer-choice', 'boolean-choice',
                              'null-message', 'string-message'])
def test_malformed_choice_is_friendly_error_and_preserves_actual_usage(server, tmp_path, choice):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.response['choices'] = [choice]
    store = Store(tmp_path / 'malformed-choice.db')
    with pytest.raises(ModelError, match='JSON'):
        recognize_image(picture(), config, '', store=store)
    assert store.usage_summary() == {'requests': 1, 'input_tokens': 120,
                                     'output_tokens': 30, 'total_tokens': 150}
    assert store.stats()['reviews'] == 0


def test_no_text_is_honest_success(server):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.response['choices'][0]['message']['content'] = '{"text":"","uncertainties":[]}'
    assert recognize_image(picture(), config, '')['text'] == ''


@pytest.mark.parametrize('status', [400, 401, 302])
def test_image_errors_hide_server_body_credentials_and_give_model_advice(server, status):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.status = status
    handler.response = {'error': 'fixture-secret secret-user-answer base64'}
    with pytest.raises(ModelError) as error:
        recognize_image(picture(), config, 'fixture-secret')
    assert 'fixture-secret' not in str(error.value) and 'secret-user-answer' not in str(error.value)
    assert '图像' in str(error.value)
    assert len(handler.requests) == 1


def test_cancellation_before_upload_and_during_response_never_returns_success(server, tmp_path):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    cancel = threading.Event()
    cancel.set()
    store = Store(tmp_path / 'cancel.db')
    with pytest.raises(CancelledError):
        recognize_image(picture(), config, '', store=store, cancel=cancel)
    assert handler.requests == []
    cancel.clear()
    handler.delay = .4
    def cancel_after_request():
        if handler.received.wait(5): cancel.set()
    timer = threading.Thread(target=cancel_after_request)
    timer.start()
    try:
        with pytest.raises(CancelledError):
            recognize_image(picture(), config, '', store=store, cancel=cancel)
    finally:
        timer.join()
    assert len(handler.requests) == 1 and store.stats()['reviews'] == 0


def test_transport_rejects_truncated_jpeg_before_upload(server):
    config, handler = server
    output = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(output, 'JPEG')
    truncated = output.getvalue()[:-50]
    with pytest.raises(ModelError, match='图片'):
        ModelClient(config, '').complete_image('s', {}, truncated)
    assert handler.requests == []


def test_partial_usage_is_labeled_estimated_and_missing_usage_stays_bounded(server):
    from shenlun.vision_ocr import recognize_image
    config, handler = server
    handler.response['usage'] = {'prompt_tokens': 120}
    result = recognize_image(picture(), config, '')
    assert result['usage_estimated'] is True
    assert result['usage']['input_tokens'] == 120 and result['usage']['output_tokens'] > 0
    handler.response.pop('usage')
    result = recognize_image(picture(), config, '')
    assert result['usage_estimated'] is True and result['usage']['input_tokens'] >= 8192


def test_prepare_pixel_limit_before_decode():
    from shenlun.vision_ocr import prepare_image
    with pytest.raises(ValueError, match='像素|裁剪'):
        prepare_image(picture((5000, 5000)))


def test_inline_json_body_cap_is_distinct_from_image_and_text_caps(server):
    import random
    config, handler = server
    output = io.BytesIO()
    Image.frombytes('RGB', (1190, 1170), random.Random(23).randbytes(1190 * 1170 * 3)).save(output, 'PNG')
    image = output.getvalue()
    assert len(image) < 4 * 1024 * 1024
    with pytest.raises(ModelError, match='6MB'):
        ModelClient({**config, 'max_input_chars': 250000, 'session_token_budget': 2000000}, '').complete_image(
            's', {'text': '字' * 249000}, image)
    assert handler.requests == []


def test_received_image_cancel_records_known_usage_once_without_success(tmp_path,monkeypatch):
    from shenlun.vision_ocr import recognize_image
    cancel=threading.Event()
    def received(self,encoded):
        cancel.set()
        return {'model':'fixture-image','choices':[{'message':{'content':'{"text":"合成文字","uncertainties":[]}'}}],
                'usage':{'prompt_tokens':120,'completion_tokens':30,'total_tokens':150}}
    monkeypatch.setattr(ModelClient,'_post',received)
    store=Store(tmp_path/'received-cancel.db')
    with pytest.raises(CancelledError):
        recognize_image(picture(),{'base_url':'http://127.0.0.1:1/v1','model':'fixture-image'},'',store=store,cancel=cancel)
    assert store.usage_summary()=={'requests':1,'input_tokens':120,'output_tokens':30,'total_tokens':150}
    assert store.stats()['reviews']==0
