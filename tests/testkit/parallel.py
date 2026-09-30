"""Fail-closed merge primitives for future xdist TestKit runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def merge_worker_reports(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge worker-owned reports without allowing identity overwrite.

    This deliberately does not infer missing worker identity.  A caller may
    only publish a merged report after every worker has finished and every
    result has a unique ``(testrun_uid, worker_id, nodeid)`` identity.
    """
    merged: dict[str, Any] = {
        "schema_version": "zf-testkit-pytest.v1",
        "items": [], "results": {}, "workers": [],
        "collection_errors": [], "deselected": [],
        "session_finished": False,
    }
    seen: set[tuple[str, str, str]] = set()
    for report in reports:
        if not isinstance(report, Mapping) or report.get("session_finished") is not True:
            raise ValueError("worker report is not a finished report")
        uid, worker = report.get("testrun_uid"), report.get("worker_id")
        if not isinstance(uid, str) or not uid or not isinstance(worker, str) or not worker:
            raise ValueError("worker report lacks testrun_uid/worker_id")
        merged["workers"].append(worker)
        for nodeid, case in (report.get("results") or {}).items():
            if not isinstance(nodeid, str) or not isinstance(case, Mapping):
                raise ValueError("malformed worker result")
            # xdist workers collect the whole file but execute only their
            # assigned items; collection placeholders are not worker facts.
            if case.get("status") == "not_run":
                continue
            identity = (uid, worker, nodeid)
            if identity in seen:
                raise ValueError(f"duplicate worker result identity: {identity}")
            seen.add(identity)
            if nodeid in merged["results"]:
                raise ValueError(f"duplicate executed nodeid across workers: {nodeid}")
            merged["results"][nodeid] = dict(case) | {
                "worker_id": worker, "testrun_uid": uid,
            }
            merged["items"].append(nodeid)
        merged["collection_errors"].extend(report.get("collection_errors") or [])
        merged["deselected"].extend(report.get("deselected") or [])
    if not merged["workers"] or len(set(merged["workers"])) != len(merged["workers"]):
        raise ValueError("missing or duplicate worker report")
    merged["session_finished"] = True
    merged["session_exitstatus"] = 0
    return merged


__all__ = ["merge_worker_reports"]
