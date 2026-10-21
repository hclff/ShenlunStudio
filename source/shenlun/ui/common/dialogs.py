"""Confirmation prompts with one safety rule: the default button is always No.

Every caller used to spell out ``Yes|No`` plus ``defaultButton=No`` and compare the
result itself; one call site had forgotten the default, making Enter accept a
billable upload. These helpers keep the wording at the call site and centralise
the button semantics.
"""
from PySide6.QtWidgets import QMessageBox


def confirm_question(parent, title, text):
    """Ask a question and return True only for an explicit Yes."""
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    answer = QMessageBox.question(parent, title, text, buttons, QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def confirm_destructive(parent, title, text):
    """Same semantics as ``confirm_question``; named for review-time intent."""
    return confirm_question(parent, title, text)


def confirm_billable(parent, title, text):
    """Confirm an action that may cost money (model upload or repeated attempt)."""
    return confirm_question(parent, title, text)


def confirm_discard(parent, title, text):
    """Confirm throwing away unsaved edits in the calling dialog."""
    return confirm_question(parent, title, text)
