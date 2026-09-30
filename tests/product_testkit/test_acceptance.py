from __future__ import annotations

import pytest

from .acceptance import AcceptanceReportError, build_acceptance_report


def _spec() -> dict:
    return {
        "schema_version": "product_acceptance_spec.v1",
        "workflow_run_id": "run-1", "flow_kind": "prd", "plan_revision": "p1",
        "task_map_generation": "g1", "assembly_owner": "testkit",
        "entrypoints": [{"entrypoint_id": "main", "start_command": "true",
                          "health_check": "true", "owner": "testkit"}],
        "user_journeys": [{"journey_id": "j1", "title": "smoke",
                            "verification_tier": "runtime", "mandatory": True,
                            "assertions": [{"assertion_id": "a1", "statement": "works",
                                             "observation_kind": "cli", "mandatory": True}]}],
        "provider_qualification": {},
    }


def _evidence() -> list[dict[str, str]]:
    return [{"ref": "evidence.json", "sha256": "a" * 64}]


def _journey(status: str = "passed") -> dict:
    return {"journey_id": "j1", "status": status, "evidence_refs": _evidence(),
            "assertion_results": [{"assertion_id": "a1", "status": status,
                                    "evidence_refs": _evidence()}]}


def test_build_acceptance_report_validates_complete_results():
    report = build_acceptance_report(spec=_spec(), spec_ref="spec.json", spec_digest="b" * 64,
                                     candidate_ref="candidate", target_commit="c" * 40,
                                     journey_results=[_journey()])
    assert report["verdict"] == "passed"


def test_build_acceptance_report_rejects_missing_evidence():
    row = _journey()
    row["evidence_refs"] = []
    with pytest.raises(AcceptanceReportError):
        build_acceptance_report(spec=_spec(), spec_ref="spec.json", spec_digest="b" * 64,
                                candidate_ref="candidate", target_commit="c" * 40,
                                journey_results=[row])


def test_build_acceptance_report_rejects_empty_results():
    with pytest.raises(AcceptanceReportError):
        build_acceptance_report(spec=_spec(), spec_ref="spec.json", spec_digest="b" * 64,
                                candidate_ref="candidate", target_commit="c" * 40,
                                journey_results=[])
