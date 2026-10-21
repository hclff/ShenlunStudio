"""Pure teaching-method provenance, frozen guidance and exact-evidence validation.

Validation proves internal consistency; only the Store's named local enable action
establishes runtime trust. No function fetches a URL or invokes a model.
"""
import hashlib
import ipaddress
import json
import re
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlsplit

from .domain import RUBRICS, prepare_submission

OWNER_IDS = frozenset(('bailu','yuandong','limengyuan','fenbi','zhanzhang','sihai_feiyang',
                       'xiaomage','zhangxiaolong','zhongjun','tangdi','shanshuling'))
SOURCE_FIELDS = ('title','url','author','published_at','accessed_at','access_scope','locator','excerpt','quote')
EDITABLE_FIELDS = ('id','owner_id','name','purpose','steps','checks','applicable_types','not_applicable','sources')
RECORD_FIELDS = frozenset((*EDITABLE_FIELDS,'version','enabled','status','reviewer','note','created_at','content_hash'))
SCOPES = ('full_text','index_text','catalog','student_note')


def _text(value, label, maximum, empty=False, strip=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{label}须为不超过{maximum}字的文字' + ('。' if empty else '且不能为空。'))
    return value.strip() if strip else value


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('教学方法标识须为1—80位字母、数字、短横线或下划线。')
    return value


def _owner_identity(value):
    if not isinstance(value,str) or (value not in OWNER_IDS and not re.fullmatch(r'owner-[a-f0-9]{32}',value)):
        raise ValueError('请选择已登记的教学主体。')
    return value


def _strings(value, label, count, length, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= count:
        raise ValueError(f'{label}数量无效。')
    return [_text(item, label, length) for item in value]


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('教学方法须为有效JSON对象。') from None


def _bounded(value):
    if len(_encoded(value).encode('utf8')) > 65536:
        raise ValueError('单条教学方法不能超过64KB。')


def teaching_content_hash(value):
    return hashlib.sha256(_encoded({k:v for k,v in value.items() if k != 'content_hash'}).encode('utf8')).hexdigest()


def _public_url(value):
    value = _text(value, '来源URL', 4000, empty=True)
    if not value:
        return ''
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme not in ('http','https') or not host or parts.username is not None
                or parts.password is not None or any(c.isspace() or ord(c) < 32 for c in value)
                or '\\' in value or (parts.port is not None and not 1<=parts.port<=65535)):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                raise ValueError
        else:
            host = host.encode('idna').decode('ascii').lower().rstrip('.')
            if ('.' not in host or host.endswith(('.localhost','.local','.internal'))
                    # Reject legacy IPv4 literals (mixed decimal/octal/hex),
                    # whose address meaning differs across URL/Qt parsers.
                    or re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)(?:\.(?:[0-9]+|0x[0-9a-f]+))*',host)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in host.split('.'))):
                raise ValueError
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            compact = re.sub(r'[^a-z0-9]','',key.lower())
            if (re.search(r'(^|[_-])(token|key|secret|password|passwd|authorization|credential|signature|sig|auth)($|[_-])',key,re.I)
                    or any(word in compact for word in ('token','secret','password','credential','signature','apikey','accesskey'))
                    or compact in ('jwt','bearer','auth','session','sessionid','code')):
                raise ValueError
        if re.search(r'(token|secret|password|api.?key|authorization)\s*[:=]', unquote(parts.fragment), re.I):
            raise ValueError
    except (ValueError, OverflowError, UnicodeError):
        raise ValueError('来源URL须为公开HTTP(S)地址，不能含登录凭据或查询密钥。') from None
    return value


def validate_teaching_method(value):
    """Canonical editable fields only; never retain imported local trust claims."""
    if not isinstance(value, dict):
        raise ValueError('教学方法须为对象。')
    result = {}
    if value.get('id') is not None:
        result['id'] = _identity(value['id'])
    owner = value.get('owner_id')
    _owner_identity(owner)
    result.update(owner_id=owner, name=_text(value.get('name'), '名称',100),
                  purpose=_text(value.get('purpose',''), '目的',800,empty=True),
                  steps=_strings(value.get('steps',[]),'步骤',10,600),
                  checks=_strings(value.get('checks',[]),'检查项',10,600))
    types = _strings(value.get('applicable_types'), '适用题型',5,100,1)
    if len(set(types)) != len(types) or any(t not in RUBRICS for t in types):
        raise ValueError('适用题型须为1—5项已知且不重复的题型。')
    result['applicable_types'] = types
    result['not_applicable'] = _text(value.get('not_applicable',''),'适用边界',2000,empty=True)
    sources = value.get('sources',[])
    if not isinstance(sources,list) or len(sources)>8:
        raise ValueError('来源须为至多8项的列表。')
    result['sources'] = []
    for source in sources:
        if not isinstance(source,dict):
            raise ValueError('来源须为对象。')
        canonical = {k:_text(source.get(k,''),'来源'+k,4000,empty=True,strip=k not in ('excerpt','quote'))
                     for k in SOURCE_FIELDS if k not in ('url','access_scope')}
        canonical['url'] = _public_url(source.get('url',''))
        scope = source.get('access_scope')
        if not isinstance(scope,str) or scope not in SCOPES:
            raise ValueError('来源访问范围无效。')
        if canonical['quote'] and canonical['quote'] not in canonical['excerpt']:
            raise ValueError('每条来源的非空引文须原样出现在该来源摘录中。')
        canonical['access_scope'] = scope
        result['sources'].append(canonical)
    _bounded(result)
    return result


def _eligible(value):
    if not value['purpose'] or not value['steps'] or not value['not_applicable']:
        raise ValueError('启用前须填写目的、步骤和适用边界。')
    if not any(s['access_scope'] in ('full_text','index_text') and s['title'] and s['url']
               and s['locator'] and s['quote'].strip() and s['quote'] in s['excerpt'] for s in value['sources']):
        raise ValueError('启用须有正文或索引正文来源、精确定位和可核对的原文引文。')


def validate_teaching_record(value):
    if not isinstance(value,dict) or set(value) != RECORD_FIELDS:
        raise ValueError('教学方法版本字段缺失或含未知字段。')
    result = validate_teaching_method(value)
    _identity(result.get('id'))
    version = value['version']
    if type(version) is not int or not 1 <= version <= 2147483647:
        raise ValueError('教学方法版本号无效。')
    status, enabled = value['status'], value['enabled']
    if not isinstance(status,str) or status not in ('draft','enabled','disabled') or type(enabled) is not bool or enabled != (status == 'enabled'):
        raise ValueError('教学方法版本状态不一致。')
    result.update(version=version, enabled=enabled, status=status,
                  reviewer=_text(value['reviewer'],'本地审核者',200,empty=not enabled),
                  note=_text(value['note'],'本地审核说明',4000,empty=not enabled))
    timestamp = _text(value['created_at'],'版本时间',80,strip=False)
    try:
        instant = datetime.fromisoformat(timestamp)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
    except (ValueError,OverflowError):
        raise ValueError('版本时间须含时区。') from None
    result['created_at'] = timestamp
    # Canonical content must be byte-for-value stable: no unknown nested fields or
    # coercions can survive restore under a different hash.
    if any(value[k] != result[k] for k in result):
        raise ValueError('教学方法版本须为规范字段内容。')
    digest = value['content_hash']
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or digest != teaching_content_hash(result):
        raise ValueError('教学方法内容哈希不一致。')
    if enabled:
        _eligible(result)
    result['content_hash'] = digest
    _bounded(result)
    return result


def normalize_teaching_selection(value):
    if value is None:
        value = {'mode':'combined'}
    if not isinstance(value,dict):
        raise ValueError('教学模式须为对象。')
    mode = value.get('mode','combined')
    if not isinstance(mode,str) or mode not in ('off','combined','source','compare'):
        raise ValueError('教学模式无效。')
    if mode in ('off','combined'):
        return {'mode':mode,'owner_ids':[]}
    owners = value.get('owner_ids',[])
    if (not isinstance(owners,list) or not (len(owners)==1 if mode=='source' else 2<=len(owners)<=3)
            or len(set(o for o in owners if isinstance(o,str)))!=len(owners)):
        raise ValueError('指定来源须选一个主体；比较须选2—3个不同且已登记的主体。')
    for owner in owners:_owner_identity(owner)
    return {'mode':mode,'owner_ids':list(owners)}


def _question_type(submission):
    if (not isinstance(submission,dict) or not isinstance(submission.get('question_type','文章写作'),str)
            or submission.get('question_type','文章写作') not in RUBRICS):
        raise ValueError('题型无效。')
    return submission.get('question_type','文章写作')


def resolve_teaching_guidance(store,submission,selection=None):
    selection = normalize_teaching_selection(selection if selection is not None else store.get_setting('teaching_selection',None))
    if selection['mode']=='off':
        return None
    live_owners = {o['id'] for o in store.list_teaching_owners()}
    if any(owner not in live_owners for owner in selection['owner_ids']):
        raise ValueError('所选教学主体不存在或已删除，请重新选择。')
    question_type = _question_type(submission)
    rows = [validate_teaching_record(row) for row in store.list_teaching_methods(enabled_only=True)]
    rows = sorted((r for r in rows if r['enabled'] and question_type in r['applicable_types']), key=lambda r:r['id'])
    owners = selection['owner_ids']
    if owners:
        rows = [r for r in rows if r['owner_id'] in owners]
        if any(not any(r['owner_id']==o for r in rows) for o in owners):
            raise ValueError('所选教学主体没有适用于本题的已启用且有来源依据的方法。')
    if not rows:
        return None
    if selection['mode']=='compare':
        groups = {o:[r for r in rows if r['owner_id']==o] for o in owners}
        chosen = []
        for index in range(6):
            for owner in owners:
                if index < len(groups[owner]) and len(chosen)<6:
                    chosen.append(groups[owner][index])
        rows = chosen
    else:
        rows = rows[:6]
    snapshot = {'schema':'teaching-guidance-1',**selection,'methods':rows}
    attach_owner_names(snapshot,store)
    snapshot['content_hash'] = teaching_content_hash(snapshot)
    return validate_teaching_snapshot(snapshot,submission)


def attach_owner_names(snapshot,store):
    identities = {m['owner_id'] for m in snapshot['methods']}
    if identities-set(OWNER_IDS):
        names = {o['id']:o['name'] for o in store.list_teaching_owners(include_deleted=True)}
        snapshot['owner_names'] = {identity:names[identity] for identity in sorted(identities)}


def validate_teaching_snapshot(value,submission):
    fields = {'schema','mode','owner_ids','methods','content_hash'}
    if not isinstance(value,dict) or set(value) not in (fields,fields|{'owner_names'}) or value['schema']!='teaching-guidance-1':
        raise ValueError('教学指导快照结构无效。')
    selection = normalize_teaching_selection(value)
    if selection['mode']=='off' or selection['owner_ids']!=value['owner_ids']:
        raise ValueError('教学指导快照选择无效。')
    rows = value['methods']
    if not isinstance(rows,list) or not 1<=len(rows)<=6:
        raise ValueError('教学指导须包含1—6个方法。')
    question_type = _question_type(submission)
    methods = [validate_teaching_record(r) for r in rows]
    if len({r['id'] for r in methods})!=len(methods) or any(not r['enabled'] or question_type not in r['applicable_types'] for r in methods):
        raise ValueError('教学指导方法未启用、不匹配题型或重复。')
    if selection['owner_ids'] and {r['owner_id'] for r in methods} != set(selection['owner_ids']):
        raise ValueError('教学指导快照未覆盖所选主体。')
    result = {'schema':'teaching-guidance-1',**selection,'methods':methods}
    if 'owner_names' in value:
        names = value['owner_names']
        if not isinstance(names,dict) or set(names)!={m['owner_id'] for m in methods}:
            raise ValueError('教学主体名称快照无效。')
        result['owner_names'] = {key:_text(name,'主体名称',100) for key,name in names.items()}
        if result['owner_names']!=names:raise ValueError('教学主体名称须为规范文字。')
    if value['content_hash'] != teaching_content_hash(result):
        raise ValueError('教学指导快照哈希不一致。')
    result['content_hash'] = value['content_hash']
    return result


def teaching_conditions(snapshot):
    if snapshot is None:
        return None
    if not isinstance(snapshot,dict) or snapshot.get('schema')!='teaching-guidance-1':
        raise ValueError('教学条件快照无效。')
    selection = normalize_teaching_selection(snapshot)
    digest = snapshot.get('content_hash')
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or digest!=teaching_content_hash(snapshot):
        raise ValueError('教学条件哈希不一致。')
    return {**selection,'content_hash':digest}


def validate_teaching_output(value,snapshot,submission):
    snapshot = validate_teaching_snapshot(snapshot,submission)
    fields = prepare_submission(submission)
    if not isinstance(value,dict):
        raise ValueError('生成的方法辅助分析须为对象。')
    applications = value.get('applications')
    wanted = [r['id'] for r in snapshot['methods']]
    if not isinstance(applications,list) or len(applications)!=len(wanted):
        raise ValueError('每个选定方法须有且仅有一次应用判断。')
    materials = {p['id']:p['text'] for p in fields['materials_paragraphs']}
    by_id = {}
    for row in applications:
        if not isinstance(row,dict):
            raise ValueError('方法应用判断须为对象。')
        identity = row.get('method_id')
        if not isinstance(identity,str) or identity not in wanted or identity in by_id:
            raise ValueError('应用判断的方法标识未知或重复。')
        status = row.get('status')
        if not isinstance(status,str) or status not in ('applied','not_applicable','uncertain'):
            raise ValueError('方法应用状态无效。')
        refs = row.get('material_refs',[])
        if not isinstance(refs,list) or len(refs)>30 or (status=='applied' and fields['mode']=='full' and not refs):
            raise ValueError('完整模式的方法应用须有材料依据。')
        canonical_refs = []
        for ref in refs:
            if not isinstance(ref,dict):
                raise ValueError('材料引用须为对象。')
            pid = ref.get('id'); quote = _text(ref.get('quote'),'材料引文',4000,strip=False)
            if not isinstance(pid,str) or pid not in materials or quote not in materials[pid]:
                raise ValueError('方法分析引用无法在实际M段落原文核实。')
            canonical_refs.append({'id':pid,'quote':quote})
        answer_quote = _text(row.get('answer_quote',''),'答案引文',4000,empty=True,strip=False)
        if answer_quote and (not answer_quote.strip() or answer_quote not in fields['answer']):
            raise ValueError('方法分析的答案引文无法原样核实。')
        by_id[identity] = {'method_id':identity,'status':status,
                           'observation':_text(row.get('observation'),'方法观察',4000),
                           'material_refs':canonical_refs,'answer_quote':answer_quote}
    result = {'applications':[by_id[i] for i in wanted]}
    for key in ('consensus','differences'):
        if snapshot['mode']=='compare' and key not in value:
            raise ValueError('比较模式须显式返回共识和分歧列表。')
        result[key] = _strings(value.get(key,[]),'共识或分歧',10,2000)
    return result



