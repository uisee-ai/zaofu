"""Fail-closed parsing for the optional mutmut quality audit.

The mutation tool is deliberately an opt-in release dependency.  This module
keeps its textual result format at the TestKit boundary and never treats a
timeout, missing tests, or an infrastructure status as a killed mutant.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class MutationAuditError(ValueError):
    """The mutation run did not produce a trustworthy result set."""


_STATUSES = (
    "killed",
    "survived",
    "timeout",
    "no_tests",
    "skipped",
    "suspicious",
    "not_checked",
    "check_was_interrupted_by_user",
    "segfault",
    "caught_by_type_check",
)
_STATUS_PATTERN = re.compile(
    r"^\s+.+?:\s+(?P<status>killed|survived|timeout|no tests|skipped|"
    r"suspicious|not checked|check was interrupted by user|segfault|"
    r"caught by type check)\s*$"
)


@dataclass(frozen=True)
class MutationSummary:
    """Counts emitted by ``mutmut results --all``."""

    total: int
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    no_tests: int = 0
    skipped: int = 0
    suspicious: int = 0
    not_checked: int = 0
    check_was_interrupted_by_user: int = 0
    segfault: int = 0
    caught_by_type_check: int = 0

    def __post_init__(self) -> None:
        values = [getattr(self, name) for name in ("total", *_STATUSES)]
        if any(value < 0 for value in values):
            raise MutationAuditError("mutation counts must be non-negative")
        counted = sum(getattr(self, name) for name in _STATUSES)
        if counted != self.total:
            raise MutationAuditError(
                f"mutation counts sum to {counted}, expected total {self.total}"
            )


def parse_mutmut_results(text: str) -> MutationSummary:
    """Parse status lines from mutmut's machine-readable-enough CLI output."""

    counts = {name: 0 for name in _STATUSES}
    for line in text.splitlines():
        match = _STATUS_PATTERN.match(line)
        if match is None:
            continue
        status = match.group("status").replace(" ", "_")
        counts[status] += 1
    total = sum(counts.values())
    if total == 0:
        raise MutationAuditError("mutmut output contains no mutant status lines")
    return MutationSummary(total=total, **counts)


def verdict_for_mutation(summary: MutationSummary) -> str:
    """Return pass only when every checked mutant was killed by tests."""

    if summary.total == 0:
        raise MutationAuditError("mutation audit has no mutants")
    non_killed = summary.total - summary.killed
    return "pass" if non_killed == 0 else "fail"


__all__ = [
    "MutationAuditError",
    "MutationSummary",
    "parse_mutmut_results",
    "verdict_for_mutation",
]
