"""Corpus reading and method-library workbench, split by surface.

``LibraryMixin`` keeps every method it had; the mixins are ordered so that its
own definitions still win over ``ArticleBatchMixin``, as before.
"""
from ...importers import import_people_range
from ..method_batch import ArticleBatchMixin
from .corpus import CorpusMixin
from .imports import ImportMixin
from .methods import MethodsMixin


class LibraryMixin(CorpusMixin, ImportMixin, MethodsMixin, ArticleBatchMixin):
    """Compatibility facade for the library workspace."""
