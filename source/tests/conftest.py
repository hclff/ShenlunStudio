"""Shared Qt setup: every run is headless, DPI-independent and process-local.

Test modules keep their own fixtures where a different timeout, database name or
seeded row is needed; these defaults only remove the copy-paste.
"""
import os

# Must be set before any Qt import. Offscreen keeps devicePixelRatio at 1, so
# screenshots and layout assertions do not depend on the developer's scaling.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time

import pytest


@pytest.fixture(scope='session')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def drain(app):
    """Wait until ``predicate`` holds while pumping the Qt event loop."""
    from PySide6.QtTest import QTest

    def wait(predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(10)
        assert predicate()

    return wait


@pytest.fixture
def window(app, tmp_path):
    from shenlun.storage import Store
    from shenlun.ui import MainWindow

    win = MainWindow(Store(tmp_path / 'conftest.db'))
    yield win
    win.close()
    app.processEvents()
