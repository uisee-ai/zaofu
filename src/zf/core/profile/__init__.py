"""Project profile — deterministic stack detection + zf.yaml recommendation.

See ``docs/design/102-project-profile-bootstrap-design.md``.
"""

from __future__ import annotations

from zf.core.profile.schema import ProjectProfile, Recommendation, StackUnit
from zf.core.profile.project_types import PROJECT_TYPES, ProjectType

__all__ = [
    "ProjectProfile",
    "Recommendation",
    "StackUnit",
    "PROJECT_TYPES",
    "ProjectType",
]
