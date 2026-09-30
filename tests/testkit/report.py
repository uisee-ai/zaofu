"""Structured verdict and evidence index for TestKit runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .evidence import write_json_atomic
from .oracles import Verdict


@dataclass
class TestResult:
    __test__ = False

    case_id: str
    verdict: Verdict
    commit: str
    command_id: str = ""
    acceptance_ids: tuple[str, ...] = ()
    evidence: list[dict[str, str]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        data["acceptance_ids"] = list(self.acceptance_ids)
        return data


def write_test_result(path: Path, result: TestResult) -> str:
    return write_json_atomic(path, result.to_dict())


def summarize_results(results: list[TestResult]) -> Verdict:
    if any(item.verdict is Verdict.HARNESS_FAILURE for item in results):
        return Verdict.HARNESS_FAILURE
    if any(item.verdict is Verdict.FLAKY for item in results):
        return Verdict.FLAKY
    if any(item.verdict is Verdict.FAIL for item in results):
        return Verdict.FAIL
    if not results or any(
        item.verdict is Verdict.NOT_APPLICABLE for item in results
    ):
        return Verdict.NOT_APPLICABLE if not results else Verdict.PASS
    return Verdict.PASS


def write_evidence_index(
    path: Path,
    *,
    run_id: str,
    commit: str,
    results: list[TestResult],
) -> str:
    mismatched = [
        item.case_id for item in results if item.commit != commit
    ]
    if mismatched:
        raise ValueError(
            "evidence result commit does not match target commit: "
            + ", ".join(mismatched)
        )
    payload: Mapping[str, Any] = {
        "schema_version": "zf-testkit-evidence-index.v1",
        "run_id": run_id,
        "target_commit": commit,
        "verdict": summarize_results(results).value,
        "results": [item.to_dict() for item in results],
    }
    return write_json_atomic(path, payload)
