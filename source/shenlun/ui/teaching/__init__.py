"""Teaching-method management: constants, dialogs, panel and workspace hook.

The re-exports keep every previous import path working, including the dialogs
patched by tests through ``shenlun.ui.teaching``.
"""
from .constants import MODES, SCOPES, STATUS, dialog_buttons, label
from .dialogs import (TeachingJsonPreviewDialog, TeachingReviewDialog,
                      TeachingSelectionDialog, TeachingSourcePreviewDialog)
from .mixin import TeachingUiMixin
from .panel import TeachingPanel

__all__ = ['MODES', 'SCOPES', 'STATUS', 'dialog_buttons', 'label',
           'TeachingJsonPreviewDialog', 'TeachingReviewDialog',
           'TeachingSelectionDialog', 'TeachingSourcePreviewDialog',
           'TeachingPanel', 'TeachingUiMixin']
