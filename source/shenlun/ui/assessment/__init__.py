"""Grading workspace: question editor, rubric panel, submission and export.

``AssessmentMixin`` keeps the previous base classes and puts the split mixins
first, so methods that used to live in the class body still take precedence.
"""
from ..intake import IntakeMixin
from ..method_presets import MethodPresetUiMixin
from ..recovery import AssessmentRecoveryMixin
from ..review_goals import ReviewGoalsUiMixin
from ..review_learning import ReviewLearningMixin
from ..rubric_library import RubricLibraryUiMixin
from ..teaching import TeachingUiMixin
from .editor import EditorMixin
from .export import ExportMixin
from .rubric import RubricMixin
from .workflow import WorkflowMixin


class AssessmentMixin(EditorMixin, RubricMixin, WorkflowMixin, ExportMixin,
                      ReviewGoalsUiMixin, RubricLibraryUiMixin, AssessmentRecoveryMixin,
                      IntakeMixin, TeachingUiMixin, MethodPresetUiMixin,
                      ReviewLearningMixin):
    """Compatibility facade for the grading workspace."""
