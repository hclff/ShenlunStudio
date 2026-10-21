"""Read-only configuration guidance and deliberately small compatibility probes."""
from .model import DEFAULT_CONFIG, ModelClient, ModelError, CancelledError, check_cancel, text_request_estimate

PROBE_SYSTEM = '仅输出 JSON 对象 {"ok":true}，不要解释或附加内容。'
PROBE_PAYLOAD = {'action':'compatibility_test', 'expected':{'ok':True}}


def connection_identity(config):
    value = {**DEFAULT_CONFIG, **config}
    return (value['base_url'].rstrip('/'), value['model'].strip(), value['token_parameter'])


def config_guidance(config, output_limit=None):
    value = {**DEFAULT_CONFIG, **config}
    notes = ['输入按字符限制；单次输出按 token 限制；任务预算累计本次所有调用（含重试）。',
             ('模型实际输出上限：未知。可填写 9999999 不代表服务支持；小额检测不测极限。'
              if output_limit is None else f'当前服务本次声明输出上限：{output_limit} token；未修改你的设置。')]
    if output_limit is not None and value['max_output_tokens'] > output_limit:
        notes.append('单次输出设置超过服务声明范围，请降低后再调用。')
    if value['session_token_budget'] <= value['max_output_tokens']:
        notes.append('任务预算不足：须大于单次输出上限，并留出输入与后续修改稿额度。')
    if not value['json_mode']:
        notes.append('当前未请求 JSON 模式；批改仍要求返回完整 JSON 对象。')
    return '\n'.join(notes)


def preflight_request(config, system, payload, *, output_limit=None):
    value = {**DEFAULT_CONFIG, **config}
    if output_limit is not None and value['max_output_tokens'] > output_limit:
        raise ModelError(f'单次输出设置超出当前服务本次声明的 {output_limit} token 上限；请调整设置后再试。')
    characters, estimated = text_request_estimate(system, payload)
    if characters > value['max_input_chars']:
        raise ModelError(f'本次输入约 {characters:,} 字符，超过设置的 {value["max_input_chars"]:,} 字符上限；请缩小材料或提高输入字符上限。')
    reserved = estimated + value['max_output_tokens']
    if reserved > value['session_token_budget']:
        raise ModelError(f'任务预算不足：首个请求预留约 {reserved:,} token（输入 {estimated:,} + 单次输出 {value["max_output_tokens"]:,}），预算为 {value["session_token_budget"]:,}；请提高预算或降低单次输出上限，并为修改稿留出额度。')
    return {'characters':characters, 'estimated_input_tokens':estimated, 'reserved_tokens':reserved}


def check_compatibility(service, progress=None, cancel=None):
    """Never upload a submission, guess provider limits, or mutate the task client."""
    config = {**service.config, 'max_output_tokens':128, 'session_token_budget':8192,
              'max_input_chars':4000, 'timeout':min(service.config['timeout'], 30)}
    report = {'checks':[], 'output_limit':None, 'tested_output_tokens':128,
              'configured_output_tokens':service.config['max_output_tokens']}
    stop = False
    for name, label, options in (
        ('connection', '连接与基础响应', {'json_mode':False, 'send_temperature':False}),
        ('json', 'JSON 对象样例', {'json_mode':True, 'send_temperature':False}),
        ('parameters', '输出参数与温度', {'json_mode':False, 'send_temperature':service.config['send_temperature']})):
        check_cancel(cancel)
        if stop:
            report['checks'].append({'id':name, 'label':label, 'status':'skipped', 'detail':'连接或授权未通过，已停止后续测试。'})
            continue
        if progress: progress('兼容性检测：'+label+'…')
        client = ModelClient({**config, **options}, service.key)
        try:
            response = client.complete(PROBE_SYSTEM, PROBE_PAYLOAD, cancel)
            service._record_response_usage(response)
            # A JSON sample cannot prove server-side schema enforcement.
            valid = response['data'].get('ok') is True
            status = 'passed' if valid or name != 'json' else 'failed'
            detail = '已返回约定的 JSON 样例。' if valid else '请求已获得服务响应；样例未符合约定，格式能力请以 JSON 项检测为准。'
        except CancelledError as exc:
            metadata = getattr(exc, 'response_metadata', None)
            if metadata: service._record_response_usage(metadata)
            raise
        except ModelError as exc:
            metadata = getattr(exc, 'response_metadata', None)
            if metadata: service._record_response_usage(metadata)
            status, detail = 'failed', str(exc)
            if name != 'json' and metadata and getattr(exc, 'completion_received', False):
                status = 'passed'
                detail = '请求已获得服务响应，参数未被服务拒绝；输出未完成约定样例，格式能力请以 JSON 项检测为准。'
            elif name == 'json' and getattr(exc, 'output_incomplete', False):
                status = 'inconclusive'
                detail = '固定 128 token 检测额度内未生成完整最终 JSON（可能消耗在推理或发生截断）；本项未确认，不代表不支持 JSON，也不是连接失败。未自动扩大检测额度。'
            if name == 'connection':
                stop = not metadata and getattr(exc, 'http_status', None) not in (400, 422)
            if getattr(exc, 'parameter', None) in ('max_tokens', 'max_completion_tokens'):
                report['output_limit'] = getattr(exc, 'upper_limit', None)
        report['checks'].append({'id':name, 'label':label, 'status':status, 'detail':detail})
    return report
