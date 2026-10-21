"""Model-facing application service, split by responsibility.

The public surface is unchanged: ``AppService`` still owns every method callers
used before, and the prompt constants stay importable from this package.
"""
from __future__ import annotations

from .prompts import (ASSESSMENT_SCHEMA, ASSESSMENT_SYSTEM, EXTRACTION_SYSTEM,
                      EXTRACTION_VERSION, PROMPT_VERSION, REVISION_SYSTEM,
                      _USE_ACTIVE_POINTS)
from .text import _answer_segments, _chunks, _keyword_list, _notify, _now
from .core import CoreService
from .grading import GradingMixin
from .tasks import TaskMixin
from .extraction import ExtractionMixin
from .admin import AdminMixin


class AppService(CoreService, GradingMixin, TaskMixin, ExtractionMixin, AdminMixin):
    """Compatibility facade; the mixins keep the previous class-body grouping."""
