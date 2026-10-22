"""Strict exact-method choices; no network calls or implicit activation."""
import copy

from .teaching import (_identity, _question_type, resolve_teaching_guidance,
                       attach_owner_names,
                       teaching_content_hash, validate_teaching_record,
                       validate_teaching_snapshot)


def normalize_method_choice(value):
    if value is None:
        return {'mode':'auto'}
    if not isinstance(value,dict):
        raise ValueError('方法组合选择须为对象。')
    mode=value.get('mode')
    if not isinstance(mode,str) or mode not in ('auto','off','preset'):
        raise ValueError('方法组合模式无效。')
    fields={'mode','preset_id'} if mode=='preset' else {'mode'}
    if set(value)!=fields:
        raise ValueError('方法组合选择字段缺失或含未知字段。')
    return {'mode':mode,'preset_id':_identity(value['preset_id'])} if mode=='preset' else {'mode':mode}


def resolve_method_choice(store,submission,choice=None,retire_invalid=False):
    """Resolve full live versions without dropping any exact preset member."""
    choice=normalize_method_choice(store.get_setting('method_choice') if choice is None else choice)
    result={'choice':choice,'preset':None,'article_methods':[],'teaching_snapshot':None}
    if choice['mode']=='off':
        return result
    question_type=_question_type(submission)
    # Import locally: the service also consumes this resolver.
    from .service import AppService
    from .method_library import MethodConflictError
    validator=AppService(store,{},'')
    if choice['mode']=='auto':
        for card in store.list_cards(enabled_only=True):
            applicable=question_type in card.get('applicable_types',[])
            # Preview only needs applicable methods. Real grading still checks
            # every card once so stale evidence is retired even when its type no
            # longer matches the submission.
            if not applicable and not retire_invalid:
                continue
            try:
                validator._validate_card(card)
            except ValueError:
                if retire_invalid:
                    try:
                        store.save_card({**card,'enabled':False,
                            'status':'来源已变化或证据不足，请重新提炼并核对'},expected_card=card)
                    except MethodConflictError:
                        pass
                continue
            if not applicable:
                continue
            if len(result['article_methods'])<6:
                result['article_methods'].append(copy.deepcopy(card))
            elif not retire_invalid:
                break
        result['teaching_snapshot']=resolve_teaching_guidance(store,submission)
        return result
    preset=store.get_method_preset(choice['preset_id'])
    if preset is None:
        raise ValueError('所选方法组合不存在，请重新选择。')
    if preset['question_type']!=question_type:
        raise ValueError('所选方法组合题型与本题不匹配，请重新选择。')
    result['preset']=copy.deepcopy(preset)
    cards={c['id']:c for c in store.list_cards()}
    for identity in preset['article_ids']:
        card=cards.get(identity)
        if card is None or not card.get('enabled') or question_type not in card.get('applicable_types',[]):
            raise ValueError(f'组合中的文章方法 {identity} 不存在、未启用或不适用本题。')
        try:
            validator._validate_card(card)
        except ValueError as exc:
            raise ValueError(f'组合中的文章方法 {identity} 来源不可用：{exc}') from exc
        result['article_methods'].append(copy.deepcopy(card))
    teaching=[]
    for identity in preset['teaching_ids']:
        method=store.get_teaching_method(identity)
        if method is None:
            raise ValueError(f'组合中的教学方法 {identity} 不存在或已删除。')
        method=validate_teaching_record(method)
        if not method['enabled'] or question_type not in method['applicable_types']:
            raise ValueError(f'组合中的教学方法 {identity} 未启用或不适用本题。')
        teaching.append(method)
    if teaching:
        snapshot={'schema':'teaching-guidance-1','mode':'combined','owner_ids':[],'methods':teaching}
        attach_owner_names(snapshot,store)
        snapshot['content_hash']=teaching_content_hash(snapshot)
        result['teaching_snapshot']=validate_teaching_snapshot(snapshot,submission)
    return result
