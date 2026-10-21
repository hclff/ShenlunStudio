"""Baseline benchmark: cold start, page switching, refresh cost and memory.

Runs the real workbench against a throwaway data directory copied from the
bundled corpus seed. Never touches the regular user data directory and never
calls a model. Output is a JSON report used to compare 0.9.5 with later builds.

    python scripts/benchmark_ui.py --data-dir <empty dir> --report docs/baseline/0.9.5.json
"""
import argparse
import ctypes
import json
import os
import platform
import shutil
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REFRESHES = ('refresh_corpus', 'refresh_methods', 'refresh_history',
             'refresh_usage', 'refresh_questions', 'refresh_calibration')


def mb(value):
    return None if value is None else round(value / 1048576.0, 1)


def seconds(value):
    return round(value, 4)


class _MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


def memory():
    """Return (working_set, peak_working_set) in bytes; (None, None) outside Windows."""
    if os.name != 'nt':
        return None, None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters),
                                           wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters),
                                      counters.cb):
        return None, None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def timed(operation):
    started = time.perf_counter()
    value = operation()
    return value, time.perf_counter() - started


def main(argv=None):
    parser = argparse.ArgumentParser(description='申论研习基线性能基准')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--seed', type=Path, default=ROOT / 'assets' / 'seed.db')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--keep-data', action='store_true')
    args = parser.parse_args(argv)

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    data_dir = args.data_dir.resolve()
    created = not data_dir.exists() or not (data_dir / 'studio.db').exists()
    if created and data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    from PySide6 import __version__ as pyside_version
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from shenlun import __version__ as app_version
    from shenlun.__main__ import create_store
    from shenlun.auto_backup import AutoBackupManager
    from shenlun.ui import MainWindow

    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'app_version': app_version,
        'python': sys.version.split()[0],
        'pyside6': pyside_version,
        'platform': platform.platform(),
        'processor': platform.processor(),
        'data_dir': str(data_dir),
        'first_run': created,
        'seed_db_mb': mb(args.seed.stat().st_size) if args.seed.is_file() else None,
    }

    store, report['store_open_seconds'] = timed(lambda: create_store(data_dir, args.seed))
    # Isolate the measurement from the 262 MB daily auto backup.
    AutoBackupManager(store).configure(enabled=False)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setFont(QFont('Microsoft YaHei UI', 10))

    window, report['window_construct_seconds'] = timed(lambda: MainWindow(store))
    _, report['first_paint_seconds'] = timed(lambda: (window.show(), app.processEvents()))
    working, _ = memory()
    report['memory_after_construct_mb'] = mb(working)

    switches = []
    for index in range(window.pages.count()):
        _, elapsed = timed(lambda i=index: (window.navigation.setCurrentRow(i), app.processEvents()))
        switches.append({'page': index, 'name': window.PAGE_NAMES[index], 'seconds': seconds(elapsed)})
    report['page_switch_seconds'] = switches
    window.navigation.setCurrentRow(0)
    app.processEvents()

    refreshes = {}
    for name in REFRESHES:
        samples = []
        for _ in range(2):
            _, elapsed = timed(lambda n=name: (getattr(window, n)(), app.processEvents()))
            samples.append(seconds(elapsed))
        refreshes[name] = samples
    report['refresh_seconds'] = refreshes

    queries = {}
    for label, operation in (
            ('stats', store.stats),
            ('list_articles_100000', lambda: store.list_articles(limit=100000)),
            ('list_articles_100000_again', lambda: store.list_articles(limit=100000)),
            ('list_articles_query', lambda: store.list_articles(query='发展', limit=100000)),
            ('list_article_summaries_100000', lambda: store.list_article_summaries(limit=100000)),
            ('list_article_summaries_query', lambda: store.list_article_summaries(query='发展', limit=100000)),
            ('count_articles', store.count_articles),
            ('list_reviews_1000', lambda: store.list_reviews(limit=1000)),
            ('list_questions_1000', lambda: store.list_questions(limit=1000)),
            ('list_cards', store.list_cards)):
        _, elapsed = timed(operation)
        queries[label] = seconds(elapsed)
    report['query_seconds'] = queries

    working, peak = memory()
    report['memory_end_mb'] = mb(working)
    report['memory_peak_mb'] = mb(peak)
    report['db_size_mb'] = mb((data_dir / 'studio.db').stat().st_size)

    window.close()
    app.processEvents()

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {
        'first_run': report['first_run'],
        'store_open_s': report['store_open_seconds'],
        'construct_s': report['window_construct_seconds'],
        'first_paint_s': report['first_paint_seconds'],
        'refresh_total_s': round(sum(s[0] for s in refreshes.values()), 3),
        'list_articles_100000_s': queries['list_articles_100000'],
        'peak_mb': report['memory_peak_mb'],
    }
    print(json.dumps(summary, ensure_ascii=True))
    if not args.keep_data:
        shutil.rmtree(data_dir, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
