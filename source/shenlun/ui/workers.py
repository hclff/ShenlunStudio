"""One cancellable job per QThread; the window owns its lifetime."""
import threading
from PySide6.QtCore import QThread, Signal


class Job(QThread):
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.error = None

    def run(self):
        try:
            value = self.operation(self.progress.emit, self.cancel_event)
            if not self.cancel_event.is_set():
                self.result.emit(value)
        except Exception as exc:
            self.error = exc
            self.failed.emit(str(exc) or type(exc).__name__)

    def cancel(self):
        self.cancel_event.set()
