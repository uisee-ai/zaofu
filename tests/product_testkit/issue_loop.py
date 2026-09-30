"""Product-agnostic Issue -> test -> delivery -> merge regression loop.

This module models the boundary only.  ZaoFu or an integration adapter owns
the actual Issue ingress, approval intent, candidate action and remote API;
the TestKit supplies ordering, identity and fail-closed assertions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from tests.testkit.evidence import write_json_atomic


class IssueLoopError(ValueError):
    """An Issue input or callback result is not safe to admit."""


@dataclass(frozen=True)
class IssueLoopResult:
    issue_key: str
    revision: str
    status: str
    states: tuple[str, ...]
    test_verdict: str = ""
    merge_called: bool = False
    merge_status: str = ""
    post_merge_verdict: str = ""
    duplicate: bool = False
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "merged" and self.post_merge_verdict == "pass"


def _issue_identity(issue: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(issue, Mapping):
        raise IssueLoopError("issue must be an object")
    issue_key = issue.get("issue_id", issue.get("id"))
    revision = issue.get("revision")
    if not isinstance(issue_key, str) or not issue_key.strip():
        raise IssueLoopError("issue_id must be a non-empty string")
    if not isinstance(revision, str) or not revision.strip():
        raise IssueLoopError("issue revision must be a non-empty string")
    return issue_key, revision


def _status(result: Mapping[str, Any], field: str, allowed: set[str]) -> str:
    value = result.get(field)
    if not isinstance(value, str) or value not in allowed:
        raise IssueLoopError(f"callback result {field} is invalid")
    return value


class IssueMergeLoop:
    """Run each Issue revision at most once and never merge before gates."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path
        self._completed: dict[tuple[str, str], IssueLoopResult] = {}
        if state_path is not None:
            self._load_state(state_path)

    def run(
        self,
        issue: Mapping[str, Any],
        *,
        triage: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        approve: Callable[[Mapping[str, Any]], bool],
        run_tests: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        merge: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        post_merge: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> IssueLoopResult:
        issue_key, revision = _issue_identity(issue)
        key = (issue_key, revision)
        prior = self._completed.get(key)
        if prior is not None:
            return IssueLoopResult(**{**prior.__dict__, "duplicate": True})

        states = ["ingressed"]
        try:
            triage_result = triage(issue)
            if not isinstance(triage_result, Mapping):
                raise IssueLoopError("triage callback must return an object")
            triage_status = _status(triage_result, "status", {"ready", "needs_info", "rejected"})
            states.append(f"triage:{triage_status}")
            if triage_status != "ready":
                return self._remember(key, IssueLoopResult(
                    issue_key, revision, "blocked", tuple(states), reason=f"triage_{triage_status}",
                ))

            if approve(triage_result) is not True:
                states.append("approval:rejected")
                return self._remember(key, IssueLoopResult(
                    issue_key, revision, "blocked", tuple(states), reason="approval_required",
                ))
            states.append("approval:accepted")

            test_result = run_tests(triage_result)
            if not isinstance(test_result, Mapping):
                raise IssueLoopError("run_tests callback must return an object")
            test_verdict = _status(test_result, "verdict", {"pass", "fail", "flaky", "not_run", "environment_failure", "harness_failure"})
            states.append(f"tests:{test_verdict}")
            readiness = test_result.get("delivery_readiness")
            if test_verdict != "pass":
                return self._remember(key, IssueLoopResult(
                    issue_key, revision, "blocked", tuple(states), test_verdict=test_verdict,
                    reason="product_tests_not_passed",
                ))
            if not isinstance(readiness, Mapping) or readiness.get("allowed") is not True:
                return self._remember(key, IssueLoopResult(
                    issue_key, revision, "blocked", tuple(states), test_verdict=test_verdict,
                    reason="delivery_readiness_not_allowed",
                ))
            states.append("delivery:allowed")

            merge_result = merge(test_result)
            if not isinstance(merge_result, Mapping):
                raise IssueLoopError("merge callback must return an object")
            merge_status = _status(merge_result, "status", {"merged", "rejected", "failed"})
            states.append(f"merge:{merge_status}")
            if merge_status != "merged":
                return self._remember(key, IssueLoopResult(
                    issue_key, revision, "blocked", tuple(states), test_verdict=test_verdict,
                    merge_called=True, merge_status=merge_status, reason="merge_not_completed",
                ))

            post_result = post_merge(merge_result)
            if not isinstance(post_result, Mapping):
                raise IssueLoopError("post_merge callback must return an object")
            post_verdict = _status(post_result, "verdict", {"pass", "fail", "flaky", "not_run", "environment_failure", "harness_failure"})
            states.append(f"post_merge:{post_verdict}")
            final = "merged" if post_verdict == "pass" else "post_merge_failed"
            return self._remember(key, IssueLoopResult(
                issue_key, revision, final, tuple(states), test_verdict=test_verdict,
                merge_called=True, merge_status=merge_status, post_merge_verdict=post_verdict,
                reason="" if final == "merged" else "post_merge_regression_failed",
            ))
        except IssueLoopError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter failures are harness errors
            return self._remember(key, IssueLoopResult(
                issue_key, revision, "harness_failure", tuple(states), reason=f"{type(exc).__name__}: {exc}",
            ))

    def _remember(self, key: tuple[str, str], result: IssueLoopResult) -> IssueLoopResult:
        previous = self._completed.get(key)
        self._completed[key] = result
        if self._state_path is not None:
            payload = [
                {
                    **item.__dict__,
                    "states": list(item.states),
                }
                for item in self._completed.values()
            ]
            try:
                write_json_atomic(self._state_path, {
                    "schema_version": "product-issue-loop-state.v1",
                    "results": payload,
                })
            except (OSError, TypeError, ValueError) as exc:
                if previous is None:
                    self._completed.pop(key, None)
                else:
                    self._completed[key] = previous
                raise IssueLoopError("issue loop state could not be persisted") from exc
        return result

    def _load_state(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IssueLoopError(f"issue loop state is unreadable: {path}") from exc
        if isinstance(payload, Mapping):
            if payload.get("schema_version") != "product-issue-loop-state.v1":
                raise IssueLoopError("issue loop state schema is invalid")
            payload = payload.get("results")
        if not isinstance(payload, list):
            raise IssueLoopError("issue loop state must contain a results list")
        for row in payload:
            if not isinstance(row, Mapping):
                raise IssueLoopError("issue loop state rows must be objects")
            try:
                result = IssueLoopResult(
                    issue_key=str(row["issue_key"]),
                    revision=str(row["revision"]),
                    status=str(row["status"]),
                    states=tuple(str(item) for item in row["states"]),
                    test_verdict=str(row.get("test_verdict", "")),
                    merge_called=bool(row.get("merge_called", False)),
                    merge_status=str(row.get("merge_status", "")),
                    post_merge_verdict=str(row.get("post_merge_verdict", "")),
                    duplicate=False,
                    reason=str(row.get("reason", "")),
                )
            except (KeyError, TypeError) as exc:
                raise IssueLoopError("issue loop state row is malformed") from exc
            self._completed[(result.issue_key, result.revision)] = result


__all__ = ["IssueLoopError", "IssueLoopResult", "IssueMergeLoop"]

