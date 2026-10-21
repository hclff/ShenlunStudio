"""Offline per-item batch state changes using the existing eligibility gates."""
from copy import deepcopy
import sqlite3

from .method_library import MethodConflictError
from .service import AppService


def set_methods_enabled(store,kind,records,enabled,*,reviewer='',note=''):
    if kind not in ('article','teaching') or type(enabled) is not bool:
        raise ValueError('请选择方法类别并明确启用或停用。')
    if (not isinstance(records,list) or not 1<=len(records)<=10000 or
        any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in records) or
        len({r['id'] for r in records})!=len(records)):
        raise ValueError('每次请选择 1—10000 个不同的方法。')
    service=AppService(store,{},'')
    results=[]
    for original in deepcopy(records):
        result={'id':original['id'],'name':original.get('name',original['id'])}
        try:
            if kind=='teaching':
                current=store.get_teaching_method(original['id'])
                if current is None:raise ValueError('方法不存在或已删除。')
                if current!=original:raise MethodConflictError('方法已在其他窗口更新，请重新勾选并核对。')
            else:
                current=next((r for r in store.list_cards() if r['id']==original['id']),None)
                if current is None:raise ValueError('方法不存在或已删除。')
                if current!=original:raise MethodConflictError('方法已在其他窗口更新，请重新勾选并核对。')
            if current.get('enabled',False)==enabled:
                result.update(status='skipped',reason='已经启用。' if enabled else '已经停用。')
            else:
                if kind=='article':service.enable_card(original['id'],enabled,expected_card=original)
                else:
                    store.set_teaching_method_enabled(original['id'],enabled,reviewer=reviewer,
                        note=note,expected_version=original['version'])
                result.update(status='success',reason='已启用。' if enabled else '已停用。')
        except (ValueError,TypeError,OSError,sqlite3.Error) as exc:
            result.update(status='failed',reason=str(exc) or type(exc).__name__)
        results.append(result)
    return results
