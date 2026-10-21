"""Suspend modal autosave and preserve input when its database context changes."""
import json

from ..state_write import state_write

_UNREAD_WORKSPACE = object()


class ModalWorkspaceGuard:
    def __init__(self, host, generation, workspace=_UNREAD_WORKSPACE, report=None):
        self.host = host
        self.generation = generation
        self.workspace = workspace
        self.report = report if report is not None else host.notify
        self.released = False
        timer = host.autosave_timer
        self.was_active = timer.isActive()
        self.was_blocked = timer.blockSignals(True)
        timer.stop()

    def protect(self):
        try:
            with state_write(self.host.store, self.generation) as conn:
                row = conn.execute("SELECT data FROM practice_workspace WHERE id='current'").fetchone()
                current = json.loads(row[0]) if row else None
                # Opening may fail before its first row is captured. The epoch
                # and readability checks still apply; None means a known empty row.
                stale = self.workspace is not _UNREAD_WORKSPACE and current != self.workspace
        except Exception:
            stale = True
        if stale:
            self.host._workspace_context_stale = True
            self.host._workspace_dirty = True
            self.host.set_workspace_paused(True)
            self.host.autosave_label.setText('草稿已变化 · 请载入草稿')
            self.report('草稿已在其他窗口更新或数据库已恢复，输入已保留且暂停保存。关闭对话框后，使用“载入草稿”重新核对。')

    def release(self, *, committed=False):
        if self.released:
            return
        if not committed:
            self.protect()
        self.released = True
        timer = self.host.autosave_timer
        active = timer.isActive()
        timer.stop()
        timer.blockSignals(self.was_blocked)
        if (not committed and (active or self.was_active) and self.host._workspace_dirty
                and not self.host._autosave_paused and not self.host._closing):
            timer.start()
