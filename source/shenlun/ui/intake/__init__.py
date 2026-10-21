"""Question intake: editor controls, options, preview dialog and workspace hook.

``IntakePreviewDialog`` stays one class (its 550 lines are one responsibility);
the surrounding widgets and the workspace mixin are separate modules.
"""
from .controls import TARGETS, IntakeEditor, columns_combo, local_mode_combo, target_combo
from .mixin import IntakeMixin
from .options import IntakeOptionsDialog
from .preview import IntakePreviewDialog
from .source_preview import SourcePreview

__all__ = ['TARGETS', 'IntakeEditor', 'columns_combo', 'local_mode_combo', 'target_combo',
           'IntakeMixin', 'IntakeOptionsDialog', 'IntakePreviewDialog', 'SourcePreview']
