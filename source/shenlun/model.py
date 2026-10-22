"""Bounded chat-completions transport; credentials never enter diagnostics."""
from __future__ import annotations

import json
import base64
import io
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG = {"base_url": "", "model": "", "timeout": 90, "max_output_tokens": 5000,
                  "session_token_budget": 150000, "max_input_chars": 60000, "temperature": .2,
                  "json_mode": True, "token_parameter": "max_tokens", "send_temperature": True}
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_REQUEST_BYTES = 6 * 1024 * 1024
MAX_CONFIG_LIMIT = 9_999_999


def text_request_estimate(system, payload):
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return len(system) + len(content), max(1, (len(system.encode('utf8')) + len(content.encode('utf8'))) // 2)


def _safe_http_error(exc):
    """Extract only a whitelisted parameter and explicit numeric bound; discard body."""
    code, parameter, upper = exc.code, None, None
    try:
        if code in (400, 422):
            body = json.loads(exc.read(8192).decode('utf8'))
            error = body.get('error', {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = error.get('message', '')
                message = message if isinstance(message, str) else ''
                allowed = ('max_tokens', 'max_completion_tokens', 'temperature', 'response_format')
                param = error.get('param')
                parameter = param if param in allowed else next((p for p in allowed if re.search(r'\b'+p+r'\b', message)), None)
                if parameter in ('max_tokens', 'max_completion_tokens'):
                    bound = re.search(r'(?:less than or equal to|<=|at most|不超过|上限为)\s*(\d{1,7})\b', message, re.I)
                    if not bound:
                        bound = re.search(r'(?:range|范围)\s*[:：]?\s*[\[(]\s*\d+\s*,\s*(\d{1,7})\s*[\])]', message, re.I)
                    if bound and 1 <= int(bound[1]) <= MAX_CONFIG_LIMIT:
                        upper = int(bound[1])
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        pass
    finally:
        exc.close()
    if parameter:
        detail = f'模型服务未接受 {parameter} 参数。'
        if upper is not None: detail += f'服务本次声明上限为 {upper}；请降低单次输出上限。'
        elif parameter == 'response_format': detail += '请检测 JSON 兼容性或核对模型。'
        elif parameter == 'temperature': detail += '可在设置中取消发送 temperature 后重新检测。'
        elif parameter in ('max_tokens', 'max_completion_tokens'):
            alternative = 'max_completion_tokens' if parameter == 'max_tokens' else 'max_tokens'
            detail += f'部分模型只接受另一个参数名；可在设置把“输出上限参数名”改为 {alternative} 后重试。'
        else: detail += '请核对输出参数名称和额度后重新检测。'
    elif code in (401, 403): detail = '模型服务拒绝访问；请核对 API 密钥、模型权限和账户状态。'
    else: detail = '请核对连接、参数、模型权限或稍后重试。'
    result = ModelError(f'模型服务返回 HTTP {code}。' + detail)
    result.http_status, result.parameter, result.upper_limit = code, parameter, upper
    return result


def image_input_estimate(system, payload):
    """Conservative reservation, not a provider-specific price quote."""
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return 8192 + max(1, (len(system.encode('utf8')) + len(content.encode('utf8'))) // 2)


class ModelError(RuntimeError):
    pass

class JSONOutputError(ModelError):
    """A completed reply that can be regenerated once within the task budget."""
    pass

class CancelledError(ModelError):
    pass

def validate_endpoint(url: str) -> str:
    if not isinstance(url, str) or not url.strip() or any(c.isspace() for c in url):
        raise ValueError("请填写有效的模型接口地址")
    try:
        u = urllib.parse.urlsplit(url)
        host, port = u.hostname, u.port
    except ValueError:
        raise ValueError("模型接口地址无效") from None
    if not host or u.username is not None or u.password is not None or u.query or u.fragment:
        raise ValueError("接口地址不能包含登录信息、查询参数或片段")
    if u.scheme != "https" and not (u.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("远程模型接口必须使用 HTTPS，本机服务可以使用 HTTP")
    if ".." in urllib.parse.unquote(u.path).split("/") or "\\" in u.path:
        raise ValueError("模型接口路径无效")
    return url.rstrip("/")


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError("任务已取消；已发送的请求可能仍在服务端处理。")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name}应为{low}—{high}之间的整数")
    return value


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
            isinstance(part, dict) and part.get('type') == 'text'
            and isinstance(part.get('text'), str) for part in content):
        return ''.join(part['text'] for part in content)
    raise ValueError()


def _json_object(content, reasoning=False):
    raw = ('' if content is None else _content_text(content)).strip().lstrip('\ufeff').strip()
    if not raw:
        if reasoning:
            raise ModelError('模型仅返回推理内容，没有最终结果；请提高输出上限或使用非推理模式。')
        raise JSONOutputError('模型返回空内容；可稍后重试。')
    fence = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n\s*```', raw, re.IGNORECASE | re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, RecursionError):
        raise JSONOutputError('模型返回的 JSON 对象不完整或格式错误；请重试并确认设置中已开启 JSON 输出。') from None

class ModelClient:
    def __init__(self, config, key):
        self.config = {**DEFAULT_CONFIG, **config}
        self.base_url = validate_endpoint(self.config["base_url"])
        self.endpoint = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        if not isinstance(self.config["model"], str) or not self.config["model"].strip():
            raise ValueError("请填写模型名称")
        self.model = self.config["model"].strip()
        if self.config['token_parameter'] not in ('max_tokens','max_completion_tokens'):
            raise ValueError('输出参数应选择 max_tokens 或 max_completion_tokens')
        self.key = str(key or "")
        local = urllib.parse.urlsplit(self.base_url).hostname in ("localhost", "127.0.0.1", "::1")
        if not self.key and not local:
            raise ValueError("请在设置中填写 API 密钥")
        self.timeout = _integer(self.config["timeout"], "响应超时秒数", 1, 300)
        self.max_output = _integer(self.config["max_output_tokens"], "输出上限", 128, MAX_CONFIG_LIMIT)
        self.budget = _integer(self.config["session_token_budget"], "任务 token 预算", 128, MAX_CONFIG_LIMIT)
        self.max_input = _integer(self.config["max_input_chars"], "输入字符上限", 1, MAX_CONFIG_LIMIT)
        temp = self.config["temperature"]
        if isinstance(temp, bool) or not isinstance(temp, (int,float)) or not 0 <= temp <= 2:
            raise ValueError("温度须在0—2之间")
        self.spent_tokens = 0

    def _post(self, encoded):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.endpoint, data=encoded, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=self.timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ModelError("模型响应超过2MB，请缩小本次任务")
        return json.loads(raw.decode("utf-8"))

    def complete(self, system, payload, cancel=None):
        return self._complete(system, payload, cancel)

    def complete_image(self, system, payload, image_bytes, cancel=None):
        """Send exactly one bounded inline PNG/JPEG; never resolve image URLs."""
        check_cancel(cancel)
        if not isinstance(image_bytes, bytes) or not image_bytes or len(image_bytes) > MAX_INLINE_IMAGE_BYTES:
            raise ModelError("图片须为不超过4MB的 PNG/JPEG 字节")
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.format not in ('PNG', 'JPEG') or image.width * image.height > 24_000_000:
                    raise ValueError()
                mime = 'image/png' if image.format == 'PNG' else 'image/jpeg'
                image.verify()
            # JPEG verify() checks headers only; decode once to reject a cut-off
            # pixel stream before any upload or budget reservation.
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ModelError("图片不是有效且尺寸受限的 PNG/JPEG，请重新选择图片") from None
        url = 'data:' + mime + ';base64,' + base64.b64encode(image_bytes).decode('ascii')
        return self._complete(system, payload, cancel, image_url=url)

    def _complete(self, system, payload, cancel=None, image_url=None):
        check_cancel(cancel)
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(system) + len(content) > self.max_input:
            raise ModelError("本次输入超过设置的字符上限，请缩小材料或提高输入上限")
        _, estimate = text_request_estimate(system, payload)
        if image_url is not None:
            estimate = image_input_estimate(system, payload)
        reservation = estimate + self.max_output
        if self.spent_tokens + reservation > self.budget:
            raise ModelError("本次任务的估算 token 预算不足，已停止后续调用")
        user_content = content if image_url is None else [
            {'type': 'text', 'text': content}, {'type': 'image_url', 'image_url': {'url': image_url}}]
        token_parameter = self.config['token_parameter']

        def encode(parameter):
            body = {"model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                    parameter: self.max_output}
            if self.config.get('send_temperature', True):
                body['temperature'] = self.config['temperature']
            if self.config.get("json_mode", True):
                body["response_format"] = {"type": "json_object"}
            return json.dumps(body, ensure_ascii=False).encode("utf8")

        encoded = encode(token_parameter)
        if image_url is not None and len(encoded) > MAX_IMAGE_REQUEST_BYTES:
            raise ModelError("图像请求超过6MB，请裁剪图片或减少文字说明")
        self.spent_tokens += reservation
        # The daemon owns only HTTP bytes, not Qt widgets or the database. This lets
        # local cancellation return promptly even while a remote inference is pending.
        outcome = queue.Queue(maxsize=1)
        def send():
            nonlocal token_parameter, encoded
            transient_retried = parameter_retried = False
            for attempt in range(3):
                try:
                    result = self._post(encoded)
                    self.config['token_parameter'] = token_parameter
                    outcome.put((True, result)); return
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    error = _safe_http_error(exc)
                    if code in (429, 503) and not transient_retried:
                        transient_retried = True
                        time.sleep(.5)
                        if cancel is not None and cancel.is_set():
                            outcome.put((False, CancelledError("任务已取消"))); return
                        continue
                    alternative = 'max_completion_tokens' if token_parameter == 'max_tokens' else 'max_tokens'
                    if (code in (400, 422) and error.parameter == token_parameter
                            and error.upper_limit is None and not parameter_retried):
                        # Some OpenAI-compatible models renamed the output cap.
                        # A rejection of the name (not the value) is safe to retry
                        # once without double-charging: the service returned no usage.
                        parameter_retried = True
                        token_parameter = alternative
                        encoded = encode(alternative)
                        continue
                    outcome.put((False, error)); return
                except (TimeoutError, socket.timeout):
                    outcome.put((False, ModelError("模型响应超时，结果未记为成功；可检查服务状态后重试。"))); return
                except (urllib.error.URLError, OSError):
                    outcome.put((False, ModelError("无法连接模型服务，请检查地址、网络与证书。"))); return
                except (ValueError, KeyError, TypeError, UnicodeError):
                    outcome.put((False, ModelError("模型服务未返回有效的 JSON 结构。"))); return
                except ModelError as exc:
                    outcome.put((False, exc)); return
                except Exception:
                    outcome.put((False, ModelError("模型调用失败，未保存为成功结果。"))); return
        threading.Thread(target=send, name="model-http", daemon=True).start()
        deadline = time.monotonic() + self.timeout * 2 + 2
        while True:
            try:
                ok, value = outcome.get(timeout=.1)
                break
            except queue.Empty:
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ModelError("模型响应超时，任务已结束")
        if not ok:
            check_cancel(cancel)
            raise value
        # Account any received response before cancellation or output validation.
        # An unusable or cancelled reply may still carry actual billable usage.
        response_metadata = self._response_usage(value, estimate, reservation)
        completion_received, output_incomplete = False, False
        try:
            check_cancel(cancel)
            choice = value['choices'][0]
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                raise ValueError()
            message = choice['message']
            completion_received = True
            output_incomplete = (choice.get('finish_reason') in ('length', 'max_tokens')
                                 or bool(message.get('reasoning_content')) and not message.get('content'))
            if choice.get('finish_reason') in ('length', 'max_tokens'):
                if image_url is not None:
                    raise ModelError("图像转写达到输出上限，结果已截断；请裁剪后分区识别或提高输出上限。")
                raise ModelError('模型达到输出上限，JSON 结果已截断；请在设置中提高单次输出 token 上限或缩小材料。')
            if choice.get('finish_reason') == 'content_filter':
                raise ModelError('模型服务拦截了本次输出，未生成可用结果。')
            if message.get('refusal'):
                raise ModelError('模型拒绝了本次请求，未生成可用结果。')
            data = _json_object(message.get('content'), bool(message.get('reasoning_content')))
            return {'data': data, **response_metadata}
        except (ValueError, KeyError, TypeError, IndexError, ModelError) as exc:
            error = exc if isinstance(exc, ModelError) else ModelError('模型服务返回的 JSON 响应结构异常，请核对接口地址与兼容性。')
            error.response_metadata = response_metadata
            error.completion_received = completion_received
            error.output_incomplete = output_incomplete
            raise error from None

    def _response_usage(self, value, estimate, reservation):
        value = value if isinstance(value, dict) else {}
        usage = value.get('usage')
        usage = usage if isinstance(usage, dict) else {}
        def amount(key, default):
            n = usage.get(key, default)
            return n if type(n) is int and n >= 0 else default
        inp = amount('prompt_tokens', estimate)
        try:
            raw = _content_text(value['choices'][0]['message']['content'])
        except (ValueError, KeyError, TypeError, IndexError):
            raw = ''
        raw = raw if isinstance(raw, str) else ''
        out = amount('completion_tokens', max(1, len(raw.encode('utf8')) // 2))
        total = max(inp + out, amount('total_tokens', inp + out))
        self.spent_tokens += total - reservation
        estimated = any(type(usage.get(k)) is not int or usage[k] < 0
                        for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
        return {'usage': {'input_tokens': inp, 'output_tokens': out, 'total_tokens': total},
                'model': str(value.get('model') or self.model), 'usage_estimated': estimated}
