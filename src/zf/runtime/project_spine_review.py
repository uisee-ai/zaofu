"""Public Project Spine Review API.

The implementation is split by responsibility so the deterministic read model,
artifact/proposal IO, and the import-facing facade stay small.
"""

from __future__ import annotations

from zf.runtime.project_spine_review_analysis import (
    build_project_spine_review,
    resolve_spine_review_context,
)
from zf.runtime.project_spine_review_artifacts import (
    create_spine_review_proposal,
    load_spine_review_artifact,
    project_spine_review_insight,
    proposal_events,
    render_spine_review_markdown,
    write_spine_review_artifact,
)
from zf.runtime.project_spine_review_common import (
    ARTIFACT_EVENT,
    INSIGHT_SCHEMA_VERSION,
    PROPOSAL_EVENT,
    PROPOSAL_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SpineReviewError,
)


__all__ = [
    "ARTIFACT_EVENT",
    "INSIGHT_SCHEMA_VERSION",
    "PROPOSAL_EVENT",
    "PROPOSAL_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SpineReviewError",
    "build_project_spine_review",
    "create_spine_review_proposal",
    "load_spine_review_artifact",
    "project_spine_review_insight",
    "proposal_events",
    "render_spine_review_markdown",
    "resolve_spine_review_context",
    "write_spine_review_artifact",
]
