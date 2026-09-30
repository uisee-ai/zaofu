"""Shared initial-identity metadata for explicitly adopted runtime cases."""

from pathlib import Path
from typing import Any

from .case_evidence import CaseEvidence, attach_case_evidence


def attach_runtime_case_evidence(
    request: Any, root: Path, *, given: str, when: str, then: str,
    forbidden: list[str], task_id: str | None = None, inputs: dict[str, Any] | None = None,
) -> CaseEvidence | None:
    """Caller declares a private synthetic root; do not infer an admitted Run.

These task-level cases register before their action. Generated dispatch,
attempt and operation identities remain in the captured canonical files.
"""
    # Registration is intentionally opt-in only when the caller has already
    # materialized the private state root.  This avoids changing the behavior
    # of tests whose action is specifically to create that root.
    if not root.exists():
        return None
    return attach_case_evidence(request, root, metadata={
        "given": given, "when": when, "then": then, "forbidden": forbidden,
        "inputs": inputs or {}, "identity_observation": "before_test_action",
        "identity": {
            "run_id": None, "task_id": task_id, "attempt": None,
            "generation": None, "operation_id": None,
            "reason": "No admitted Run or generated attempt/operation identity is asserted at registration; later identities are in raw state",
        },
    })
