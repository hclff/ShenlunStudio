"""Backup controls and a worker owned separately from paid/model tasks."""
from datetime import date
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QSpinBox, QVBoxLayout)

from ..auto_backup import AutoBackupManager
from .widgets import button, row
from .workers import Job


class _BackupWait(QDialog):
    def __init__(self, job, parent):
        super().__init__(parent)
        self.job = job
        self.setWindowTitle('恢复数据库')
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('正在验证、创建安全备份并恢复，请稍候…'))
        self.setMinimumWidth(410)

    def reject(self):
        if not self.job.isRunning(): super().reject()


class BackupMixin:
    def build_backup_controls(self):
        self.backup_manager = AutoBackupManager(self.store)
        self._backup_job = None
        self._backup_today = date.today
        self._backup_attempt_date = None
        group = QGroupBox('每日自动备份')
        form = QFormLayout(group)
        try:
            settings = self.backup_manager.settings()
            status = '空闲时检查今日备份。'
        except (OSError, ValueError):
            settings = {'enabled':False, 'retention':7, 'directory':str(self.store.path.parent/'backups')}
            status = '备份登记无法读取；现有文件已保留。'
        self.auto_backup_enabled = QCheckBox('每天自动备份一次')
        self.auto_backup_enabled.setChecked(settings['enabled'])
        self.backup_retention = QSpinBox()
        self.backup_retention.setRange(1,30); self.backup_retention.setValue(settings['retention'])
        self.backup_retention.setMaximumWidth(120)
        self.backup_directory = QLabel(settings['directory'])
        self.backup_directory.setTextFormat(Qt.TextFormat.PlainText)
        self.backup_directory.setWordWrap(True)
        self.backup_directory.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.auto_backup_enabled)
        form.addRow('保留自动备份', self.backup_retention)
        form.addRow('备份目录', self.backup_directory)
        form.addRow(row(button('选择目录…', self.choose_backup_directory),
                        button('备份管理…', self.show_backup_manager),
                        button('重试今日备份', lambda: self.check_auto_backup(force=True))))
        self.backup_status = QLabel(status)
        self.backup_status.setObjectName('backup_status'); self.backup_status.setWordWrap(True)
        form.addRow(self.backup_status)
        note = QLabel('仅轮换超额的自动备份；手动和安全备份保留。备份含私密训练资料，未加密。')
        note.setWordWrap(True); note.setObjectName('muted'); form.addRow(note)
        return group

    def initialize_backup_schedule(self):
        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(60000)
        self.backup_timer.timeout.connect(self.check_auto_backup)
        self.backup_timer.start()
        QTimer.singleShot(0, self.check_auto_backup)
        QApplication.instance().aboutToQuit.connect(self.wait_for_backup)

    def save_backup_preferences(self):
        previous = self.backup_manager.settings()
        self.backup_manager.configure(enabled=self.auto_backup_enabled.isChecked(),
                                      retention=self.backup_retention.value())
        if not previous['enabled'] and self.auto_backup_enabled.isChecked():
            self._backup_attempt_date = None

    def choose_backup_directory(self):
        directory = QFileDialog.getExistingDirectory(self, '选择自动备份目录', self.backup_directory.text())
        if not directory: return
        try:
            self.backup_manager.configure(directory=directory)
            self.backup_directory.setText(self.backup_manager.settings()['directory'])
            self.backup_status.setText('目录已更新；原目录文件保留。')
        except (OSError, ValueError):
            self.backup_status.setText('目录未更新，请选择可安全访问的普通目录。')

    def check_auto_backup(self, force=False):
        if self._closing or self.workers or self._backup_job is not None: return
        today = self._backup_today()
        if not force and self._backup_attempt_date == today: return
        self._backup_attempt_date = today
        self.backup_status.setText('正在后台检查并备份…')
        self._start_backup_operation('daily', lambda: self.backup_manager.run_daily(today), self._daily_backup_result)

    def _daily_backup_result(self, result):
        self.backup_status.setText({'created':'今日自动备份已完成。', 'current':'今日已完成自动备份。',
            'disabled':'自动备份已关闭。', 'busy':'其他窗口或任务正在使用数据库，将在空闲时再检查。'}[result])
        if result == 'busy': self._backup_attempt_date = None

    def _start_backup_operation(self, kind, operation, completed, *, modal=False):
        if self._closing or self._backup_job is not None: return False
        job = Job(lambda progress, cancel: operation(), self)
        job.backup_kind = kind
        job.backup_completed = completed
        job.succeeded = False
        self._backup_job = job
        job.result.connect(self._backup_result)
        job.failed.connect(self._backup_failed)
        job.finished.connect(self._backup_finished)
        waiting = _BackupWait(job, self) if modal else None
        if waiting is not None: job.finished.connect(waiting.accept)
        job.start()
        if waiting is None: return True
        waiting.exec()
        succeeded = job.succeeded
        waiting.deleteLater()
        return succeeded

    def _backup_result(self, value):
        job = self.sender()
        if self._closing: return
        try:
            job.backup_completed(value)
            job.succeeded = True
        except Exception:
            self._backup_failed('')

    def _backup_failed(self, message):
        if self._closing: return
        job = self.sender() or self._backup_job
        if job is not None and job.backup_kind == 'restore':
            self._autosave_paused = False
            self.notify('恢复失败，当前数据库已保留或回滚；请检查备份并重试。')
        self.backup_status.setText('备份或恢复失败；已有备份保留，可手动重试。')

    def _backup_finished(self):
        job = self.sender()
        if self._backup_job is job: self._backup_job = None
        job.deleteLater()

    def wait_for_backup(self):
        if hasattr(self, 'backup_timer'): self.backup_timer.stop()
        job = self._backup_job
        if job is not None:
            job.cancel()
            # SQLite backup is not instantly interruptible. Keep its owner alive
            # until the native worker has actually exited, including app.quit().
            job.wait()

    def build_backup_manager_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName('backup_manager_dialog')
        dialog.setWindowTitle('备份管理'); dialog.resize(780,450)
        layout = QVBoxLayout(dialog)
        notice = QLabel('仅显示本机登记；手动和安全备份不自动删除。备份含私密训练资料，未加密。')
        notice.setWordWrap(True); layout.addWidget(notice)
        listing = QListWidget(); listing.setObjectName('backup_list')
        dialog.backup_list = listing; layout.addWidget(listing,1)
        registered = {}
        def refresh():
            listing.clear()
            registered.clear()
            try:
                for record in self.backup_manager.records():
                    kind = {'daily':'自动','manual':'手动','safety':'恢复前安全'}[record['kind']]
                    item = QListWidgetItem(f'{record["date"]}　{kind}　{record["size"]:,} 字节\n{record["path"]}')
                    registered[record['id']] = record
                    item.setData(Qt.ItemDataRole.UserRole, record['id']); listing.addItem(item)
            except (OSError, ValueError): self.backup_status.setText('备份登记无法读取；现有文件已保留。')
        def selected():
            item = listing.currentItem()
            return registered.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        def open_folder():
            record = selected()
            if record: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(record['path']).parent)))
        def restore():
            record = selected()
            if record:
                self.restore_backup_path(record['path'])
                refresh()
        def forget():
            record = selected()
            if not record: return
            answer = QMessageBox.question(dialog, '移除备份登记', '只移除这条登记，磁盘文件保留。是否继续？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes: return
            try: self.backup_manager.forget(record['id']); refresh()
            except (OSError, ValueError): self.backup_status.setText('登记未移除，请稍后重试。')
        dialog.forget_button = button('移除登记', forget)
        layout.addLayout(row(button('打开所在目录', open_folder), button('恢复所选…', restore),
                             dialog.forget_button, button('刷新', refresh)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        refresh()
        return dialog

    def show_backup_manager(self):
        dialog = self.build_backup_manager_dialog()
        dialog.exec(); dialog.deleteLater()
