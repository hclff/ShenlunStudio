"""Nonblocking database ownership; the kernel releases it on process death.

Never remove the lock file: replacing its inode would split ownership. The same
lock guards assessment execution and database replacement, across Store objects.
"""
from contextlib import contextmanager
from pathlib import Path


class AssessmentBusyError(ValueError):
    pass


@contextmanager
def assessment_lock(database):
    path = Path(str(Path(database).resolve()) + '.assessment.lock')
    handle = path.open('a+b')
    locked = False
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AssessmentBusyError('其他窗口有批改任务正在运行，请等待完成后再试。') from None
        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def assessment_active(database):
    try:
        with assessment_lock(database):
            return False
    except AssessmentBusyError:
        return True
