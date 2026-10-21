"""Real SQLite snapshots and bounded machine-local retention."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from shenlun.storage import Store


def manager(tmp_path):
    from shenlun.auto_backup import AutoBackupManager
    return AutoBackupManager(Store(tmp_path/'local.db'))


def test_daily_snapshot_dedupes_across_managers_and_midnight(tmp_path):
    m = manager(tmp_path)
    m.store.set_setting('fixture', 'private training')
    assert m.run_daily(date(2026, 10, 19)) == 'created'
    assert manager(tmp_path).run_daily(date(2026, 10, 19)) == 'current'
    assert m.run_daily(date(2026, 10, 20)) == 'created'
    records = m.records()
    assert len(records) == 2
    assert Store(Path(records[0]['path'])).get_setting('fixture') == 'private training'
    assert m.settings()['retention'] == 7
    assert m.settings()['directory'] == str((tmp_path/'backups').resolve())


def test_retention_only_registered_daily_and_preserves_manual_safety_foreign(tmp_path):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.store.backup_to(tmp_path/'manual.db')
    restore = m.store.backup_to(tmp_path/'restore.db')
    safety = m.store.restore_from(restore)
    foreign = tmp_path/'backups'/'foreign.db'
    foreign.parent.mkdir(exist_ok=True)
    foreign.write_bytes(b'belongs to another application')
    m.run_daily(date(2026, 10, 19))
    first = Path(next(r['path'] for r in m.records() if r['kind'] == 'daily'))
    m.run_daily(date(2026, 10, 20))
    assert not first.exists()
    assert foreign.read_bytes() == b'belongs to another application'
    assert safety.exists() and (tmp_path/'manual.db').exists()
    assert {r['kind'] for r in m.records()} == {'daily', 'manual', 'safety'}


def test_failure_has_no_marker_or_prune_and_retry_works(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    old = m.records()
    original = m.store.backup_to
    def broken(*args, **kwargs):
        raise OSError('SECRET credential raw provider error')
    monkeypatch.setattr(m.store, 'backup_to', broken)
    with pytest.raises(OSError):
        m.run_daily(date(2026, 10, 20))
    assert m.records() == old and Path(old[0]['path']).exists()
    assert 'SECRET' not in m.state_path.read_text(encoding='utf-8')
    monkeypatch.setattr(m.store, 'backup_to', original)
    assert m.run_daily(date(2026, 10, 20)) == 'created'


def test_integrity_failure_does_not_register_or_prune(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    old = m.records()
    def corrupt(path, **kwargs):
        Path(path).write_bytes(b'not sqlite')
        return Path(path)
    monkeypatch.setattr(m.store, 'backup_to', corrupt)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        m.run_daily(date(2026, 10, 20))
    assert m.records() == old and Path(old[0]['path']).exists()


@pytest.mark.parametrize('substitution', ['content', 'replacement', 'hardlink', 'outside', 'noncanonical'])
def test_daily_substitution_is_never_deleted(tmp_path, substitution):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    first = Path(m.records()[0]['path'])
    if substitution == 'content':
        with first.open('ab') as handle: handle.write(b'foreign content')
    elif substitution == 'replacement':
        first.unlink(); first.write_bytes(b'a substituted file')
    elif substitution == 'hardlink':
        os.link(first, tmp_path/'other-hardlink.db')
    elif substitution == 'outside':
        state = json.loads(m.state_path.read_text(encoding='utf-8'))
        outside = tmp_path/'outside.db'
        outside.write_bytes(first.read_bytes())
        state['records'][0]['path'] = str(outside)
        m.state_path.write_text(json.dumps(state), encoding='utf-8')
        first = outside
    else:
        state = json.loads(m.state_path.read_text(encoding='utf-8'))
        state['records'][0]['path'] = str(first.parent/'..'/'backups'/first.name)
        m.state_path.write_text(json.dumps(state), encoding='utf-8')
    expected = first.read_bytes()
    m.run_daily(date(2026, 10, 20))
    assert first.read_bytes() == expected


def test_location_change_never_prunes_old_location_and_restore_keeps_local_settings(tmp_path):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    first = Path(m.records()[0]['path'])
    backup = m.store.backup_to(tmp_path/'snapshot.db')
    external = tmp_path/'chosen-external'
    m.configure(directory=external, retention=2, enabled=False)
    m.store.restore_from(backup)
    assert manager(tmp_path).settings()['directory'] == str(external.resolve())
    assert m.run_daily(date(2026, 10, 20)) == 'disabled'
    m.configure(enabled=True)
    for day in (20, 21, 22): m.run_daily(date(2026, 10, day))
    assert first.exists()
    assert len(list(external.glob('*.db'))) == 2


def test_coordinator_and_restore_ownership_prevent_duplicate_work(tmp_path):
    from shenlun.task_lock import assessment_lock
    m = manager(tmp_path)
    with assessment_lock(m.lock_database):
        assert manager(tmp_path).run_daily(date(2026, 10, 19)) == 'busy'
    with m.store.assessment_restore_guard():
        assert m.run_daily(date(2026, 10, 19)) == 'busy'
    assert m.records() == []
    assert m.run_daily(date(2026, 10, 19)) == 'created'


def test_forget_only_removes_registry_record_and_validation_is_bounded(tmp_path):
    m = manager(tmp_path)
    for invalid in (0, 31, True):
        with pytest.raises(ValueError): m.configure(retention=invalid)
    m.run_daily(date(2026, 10, 19))
    record = m.records()[0]
    m.forget(record['id'])
    assert m.records() == [] and Path(record['path']).exists()


def test_symlink_directory_or_daily_file_is_not_followed(tmp_path):
    m = manager(tmp_path)
    real = tmp_path/'real'; real.mkdir()
    link = tmp_path/'linked'
    try: link.symlink_to(real, target_is_directory=True)
    except OSError: pytest.skip('symlink permission unavailable')
    with pytest.raises(ValueError): m.configure(directory=link)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    first = Path(m.records()[0]['path'])
    victim = tmp_path/'victim.db'; victim.write_bytes(first.read_bytes())
    first.unlink(); first.symlink_to(victim)
    m.run_daily(date(2026, 10, 20))
    assert first.is_symlink() and victim.exists()


def test_process_lock_blocks_duplicate_daily_then_releases_on_exit(tmp_path):
    m = manager(tmp_path)
    script = ('import sys; from shenlun.task_lock import assessment_lock; '
              'scope=assessment_lock(sys.argv[1]); scope.__enter__(); '
              'print("LOCKED", flush=True); sys.stdin.readline()')
    process = subprocess.Popen([sys.executable, '-c', script, str(m.lock_database)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'LOCKED'
        assert m.run_daily(date(2026, 10, 19)) == 'busy'
    finally:
        process.communicate('\n', timeout=10)
    assert process.returncode == 0
    assert m.run_daily(date(2026, 10, 19)) == 'created'
    assert manager(tmp_path).run_daily(date(2026, 10, 19)) == 'current'


def test_registry_write_failure_preserves_prior_snapshots_and_success_marker(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    before = m.state_path.read_bytes()
    old = Path(m.records()[0]['path'])
    def unwritable(*args): raise OSError('disk full')
    monkeypatch.setattr(m, '_write', unwritable)
    with pytest.raises(OSError): m.run_daily(date(2026, 10, 20))
    assert m.state_path.read_bytes() == before and old.exists()


def test_cleanup_registry_write_failure_keeps_successful_daily_snapshot(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.configure(retention=1)
    m.run_daily(date(2026, 10, 19))
    original_write = m._write
    calls = []
    def fail_cleanup(state):
        calls.append(True)
        if len(calls) == 2: raise OSError('post-prune bookkeeping failure')
        return original_write(state)
    monkeypatch.setattr(m, '_write', fail_cleanup)
    assert m.run_daily(date(2026, 10, 20)) == 'created'
    assert m.settings()['last_success'] == '2026-10-20'
    latest = next(record for record in m.records() if record['date'] == '2026-10-20')
    assert Path(latest['path']).exists()


def test_restore_rolls_back_after_replacement_and_registers_safety(tmp_path, monkeypatch):
    m = manager(tmp_path)
    m.store.set_setting('fixture', 'original data')
    source = Store(tmp_path/'source.db')
    source.set_setting('fixture', 'incoming data')
    backup = source.backup_to(tmp_path/'incoming.db')
    original_init = Store.__init__
    attempted = []
    def fail_once(store, path, **kwargs):
        if Path(path) == m.store.path and not attempted:
            attempted.append(True)
            raise RuntimeError('migration failure')
        return original_init(store, path, **kwargs)
    monkeypatch.setattr(Store, '__init__', fail_once)
    with pytest.raises(RuntimeError): m.store.restore_from(backup)
    assert m.store.get_setting('fixture') == 'original data'
    safety = next(record for record in m.records() if record['kind'] == 'safety')
    assert Store(Path(safety['path'])).get_setting('fixture') == 'original data'


def test_old_backup_without_tasks_restores_without_inheriting_auto_settings(tmp_path):
    m = manager(tmp_path)
    source = Store(tmp_path/'legacy-source.db')
    source.set_setting('auto_backup_directory', str(tmp_path/'untrusted-inherited'))
    backup = source.backup_to(tmp_path/'legacy.db')
    with sqlite3.connect(backup) as conn: conn.execute('DROP TABLE assessment_tasks')
    m.store.restore_from(backup)
    assert m.store.list_assessment_tasks() == []
    assert m.settings()['directory'] == str((tmp_path/'backups').resolve())
    assert not (tmp_path/'untrusted-inherited').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing contract')
def test_registration_denies_writers_during_integrity_and_fingerprint(tmp_path, monkeypatch):
    import shenlun.auto_backup as module
    m = manager(tmp_path)
    backup = m.store.backup_to(tmp_path/'snapshot.db', register=False)
    fingerprint = module._fingerprint
    blocked = []
    def concurrent_writer(handle):
        try:
            with backup.open('r+b') as writer: writer.write(b'SQLite format 3\x00')
        except PermissionError:
            blocked.append(True)
        return fingerprint(handle)
    monkeypatch.setattr(module, '_fingerprint', concurrent_writer)
    m.register(backup)
    assert blocked == [True]
