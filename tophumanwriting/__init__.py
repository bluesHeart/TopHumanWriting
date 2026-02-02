# -*- coding: utf-8 -*-
"""
TopHumanWriting: exemplar-alignment writing audit toolkit.

Public API (stable):
  - Workspace
  - LibraryBuilder
  - AuditRunner
"""

from __future__ import annotations

from ._version import VERSION as __version__
from .workspace import Workspace
from .library import LibraryBuilder
from .api import AuditExport, TopHumanWriting
from .runner import AuditRunConfig, AuditRunner

__all__ = [
    "Workspace",
    "LibraryBuilder",
    "TopHumanWriting",
    "AuditExport",
    "AuditRunner",
    "AuditRunConfig",
    "__version__",
]
