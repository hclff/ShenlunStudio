"""Machine-local backup coordination, independent of restorable SQLite settings.

Only verified, registered daily files in the currently selected directory can
be rotated. Manual and restore-safety snapshots are never automatically erased.
"""
from contextlib import closing, contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import uuid

from .task_lock import AssessmentBusyError, assessment_lock


def _plain_path(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('备份路径不能包含符号链接或重解析点。')
    if path != path.resolve():
        raise ValueError('备份路径不安全。')
    return path


def _identity(info):
    return [info.st_dev, info.st_ino]


def _fingerprint(handle):
    handle.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024*1024), b''):
        digest.update(block)
    return digest.hexdigest()


def _snapshot(path):
    path = _plain_path(path)
    with _pinned_directory(path.parent), _snapshot_reader(path) as handle:
        before = os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, 'st_file_attributes', 0) & 0x400):
            raise ValueError('备份必须是独立的普通文件。')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
            conn.execute('PRAGMA trusted_schema=OFF')
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('备份数据库完整性检查失败。')
        digest = _fingerprint(handle)
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns, _identity(before)) != (after.st_size, after.st_mtime_ns, _identity(after)):
            raise ValueError('备份文件已变更，请重试。')
        if _identity(path.lstat()) != _identity(after):
            raise ValueError('备份文件已替换，请重试。')
    return {'size': after.st_size, 'identity': _identity(after), 'sha256': digest}


@contextmanager
def _snapshot_reader(path):
    if os.name != 'nt':
        with path.open('rb') as handle: yield handle
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    native = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if native == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), '备份文件无法安全访问。')
    try:
        descriptor = msvcrt.open_osfhandle(native, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(native); raise
    with os.fdopen(descriptor, 'rb') as handle: yield handle


@contextmanager
def _pinned_directory(directory):
    """Deny directory replacement during copy/rotation on Windows."""
    directory = _plain_path(directory)
    handles = []
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            for part in reversed((directory, *directory.parents)):
                handle = kernel.CreateFileW(str(part), 0x80, 3, None, 3, 0x02200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise OSError(ctypes.get_last_error(), '备份目录无法安全访问。')
                handles.append(handle)
            _plain_path(directory)
        yield directory
    finally:
        if handles:
            for handle in reversed(handles): kernel.CloseHandle(handle)


def _remove_daily(record, directory):
    """Validate ownership and delete by the opened file handle on Windows.

    The handle disallows concurrent writes/renames and FILE_DISPOSITION_INFO
    deletes that same object, so a path swap cannot redirect the deletion.
    """
    try:
        path = _plain_path(record['path'])
        if (Path(record['path']) != path or path.parent != directory
                or record['directory'] != str(directory)): return False
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            import msvcrt
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
            kernel.SetFileInformationByHandle.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            native = kernel.CreateFileW(str(path), 0x80010000, 1, None, 3, 0x00200000, None)
            if native == wintypes.HANDLE(-1).value: return False
            try:
                descriptor = msvcrt.open_osfhandle(native, os.O_RDONLY | os.O_BINARY)
            except Exception:
                kernel.CloseHandle(native); raise
            with os.fdopen(descriptor, 'rb') as handle:
                if not _matches(handle, record): return False
                disposition = ctypes.c_ubyte(1)
                if not kernel.SetFileInformationByHandle(native, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                    return False
            return True
        # A conservative fallback: retention is optional, so unsupported
        # platforms retain files rather than offering weaker deletion safety.
        return False
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _matches(handle, record):
    info = os.fstat(handle.fileno())
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & 0x400
            and _identity(info) == record['identity'] and info.st_size == record['size']
            and _fingerprint(handle) == record['sha256'])


class AutoBackupManager:
    def __init__(self, store):
        self.store = store
        self.state_path = Path(str(store.path.resolve())+'.backup-state.json')
        self.lock_database = Path(str(store.path.resolve())+'.backups')

    def _read(self):
        if not self.state_path.exists():
            return {'version': 1, 'app_id': uuid.uuid4().hex, 'enabled': True,
                    'retention': 7, 'directory': str((self.store.path.parent/'backups').resolve()),
                    'last_success': '', 'records': []}
        _plain_path(self.state_path)
        if self.state_path.stat().st_size > 8*1024*1024:
            raise ValueError('备份登记文件过大，请保留文件并重新设置。')
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
            if (set(state) != {'version','app_id','enabled','retention','directory','last_success','records'}
                    or state['version'] != 1 or type(state['enabled']) is not bool
                    or type(state['retention']) is not int or not 1 <= state['retention'] <= 30
                    or not isinstance(state['directory'], str) or not Path(state['directory']).is_absolute()
                    or not isinstance(state['app_id'], str) or len(state['app_id']) != 32
                    or not isinstance(state['last_success'], str) or not isinstance(state['records'], list)):
                raise ValueError
            for record in state['records']:
                if (set(record) != {'id','app_id','kind','date','path','directory','size','identity','sha256'}
                        or record['app_id'] != state['app_id'] or record['kind'] not in {'daily','manual','safety'}
                        or any(not isinstance(record[key], str) for key in ('id','date','path','directory','sha256'))
                        or not Path(record['path']).is_absolute() or not Path(record['directory']).is_absolute()
                        or type(record['size']) is not int or record['size'] <= 0
                        or not isinstance(record['identity'], list) or len(record['identity']) != 2
                        or any(type(number) is not int for number in record['identity'])
                        or len(record['sha256']) != 64): raise ValueError
            return state
        except (ValueError, KeyError, TypeError):
            raise ValueError('备份登记文件无法读取；现有备份已保留。') from None

    def _write(self, state):
        _plain_path(self.state_path)
        descriptor, temporary = tempfile.mkstemp(prefix='.backup-state-', dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def settings(self):
        state = self._read()
        return {key: state[key] for key in ('enabled','retention','directory','last_success')}

    def configure(self, *, enabled=None, retention=None, directory=None):
        if enabled is not None and type(enabled) is not bool: raise ValueError('自动备份开关无效。')
        if retention is not None and (type(retention) is not int or not 1 <= retention <= 30):
            raise ValueError('保留份数应为 1—30。')
        if directory is not None:
            directory = _plain_path(directory)
            if directory.exists() and not directory.is_dir(): raise ValueError('请选择备份目录。')
        with assessment_lock(self.lock_database):
            state = self._read()
            if enabled is not None: state['enabled'] = enabled
            if retention is not None: state['retention'] = retention
            if directory is not None: state['directory'] = str(directory)
            self._write(state)

    def records(self):
        return sorted(self._read()['records'], key=lambda record: (record['date'], record['id']), reverse=True)

    def _record(self, state, path, kind, day):
        path = _plain_path(path)
        snapshot = _snapshot(path)
        record = {'id': uuid.uuid4().hex, 'app_id': state['app_id'], 'kind': kind,
                  'date': day, 'path': str(path), 'directory': str(path.parent), **snapshot}
        state['records'] = [old for old in state['records'] if old['path'] != str(path)] + [record]
        return record

    def register(self, path, kind='manual'):
        if kind not in {'manual','safety'}: raise ValueError('备份类型无效。')
        with assessment_lock(self.lock_database):
            state = self._read()
            record = self._record(state, path, kind, datetime.now().isoformat(timespec='seconds'))
            self._write(state)
            return record

    def forget(self, record_id):
        with assessment_lock(self.lock_database):
            state = self._read()
            state['records'] = [record for record in state['records'] if record['id'] != record_id]
            self._write(state)

    def run_daily(self, today=None):
        day = (today or date.today()).isoformat()
        try:
            with assessment_lock(self.lock_database):
                state = self._read()
                if not state['enabled']: return 'disabled'
                if state['last_success'] == day: return 'current'
                with self.store.assessment_restore_guard():
                    directory = _plain_path(state['directory'])
                    directory.mkdir(parents=True, exist_ok=True)
                    with _pinned_directory(directory):
                        path = directory/('申论研习-每日-'+day+'-'+uuid.uuid4().hex+'.db')
                        self.store.backup_to(path, register=False)
                        self._record(state, path, 'daily', day)
                        state['last_success'] = day
                        # Durably register the successful snapshot BEFORE any deletion.
                        self._write(state)
                        daily = sorted((record for record in state['records']
                            if record['kind'] == 'daily' and record['directory'] == str(directory)),
                            key=lambda record: (record['date'], record['id']), reverse=True)
                        for record in daily[state['retention']:]:
                            if _remove_daily(record, directory): state['records'].remove(record)
                        try:
                            self._write(state)
                        except (OSError, ValueError):
                            # The snapshot and success date were already durably
                            # committed. A stale old record is safe to forget
                            # manually; do not turn that success into a retry.
                            pass
                        return 'created'
        except AssessmentBusyError:
            return 'busy'
