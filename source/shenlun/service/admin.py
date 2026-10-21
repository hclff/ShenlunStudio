from __future__ import annotations





class AdminMixin:
    def enable_card(self,card_id,enabled,*,expected_card=None):
        card=next((c for c in self.store.list_cards() if c['id']==card_id),None)
        if not card:raise ValueError('方法卡不存在')
        if expected_card is not None and card!=expected_card:
            from ..method_library import MethodConflictError
            raise MethodConflictError('方法已在其他窗口更新，请重新勾选并核对。')
        if enabled:self._validate_card(card)
        self.store.set_card_enabled(card_id,enabled,expected_card=card)

    def test_connection(self,cancel=None):
        response=self._complete('仅输出JSON对象 {"ok":true}，用于确认接口连接。',{'action':'connection_test'},cancel)
        if response['data'].get('ok') is not True:raise ValueError('接口可访问，但未返回要求的JSON结果；请检查模型与JSON模式。')
        return {'ok':True,'model':response.get('model'),'usage':response['usage']}

    def check_compatibility(self, progress=None, cancel=None):
        from ..model_diagnostics import check_compatibility
        return check_compatibility(self, progress, cancel)
