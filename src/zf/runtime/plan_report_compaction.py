"""Lossless report projection over already relocated, complete Plan ports."""
from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.call_result_envelope import canonical_json_bytes
from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.task_map import resolve_artifact_file

REFERENCE_PORTS = ('source_inventory', 'capability_matrix', 'acceptance_matrix',
                   'test_matrix', 'real_e2e_matrix', 'source_index')


def compact_report(report: dict, *, state_dir: Path, project_root: Path) -> dict:
    """Remove only exact inline duplicates; retain disagreements for review.

    The admitted producer result is untouched. This projection is persisted by
    the existing handoff writer, and consumers hydrate the original full port.
    """
    result = dict(report)
    for name in REFERENCE_PORTS:
        body, ref = report.get(name), report.get(name + '_ref')
        if not isinstance(body, dict) or not body or not isinstance(ref, str) or not ref:
            continue
        try:
            path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
            # Only compact copies already owned by runtime, never author paths.
            path.resolve().relative_to(state_dir.resolve())
            value = json.loads(read_plan_port_bytes(path, name))
            if canonical_json_bytes(value) == canonical_json_bytes(body):
                result.pop(name)
        except (OSError, ValueError, TypeError):
            continue
    return result
