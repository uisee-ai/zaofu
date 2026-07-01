"""LH-2.T1 (降级版): SemanticDiscriminator — scope/exclusion rule check.

Original design called for LLM-as-judge. We deliver a deterministic
rule-based version now; LLM-as-judge is deferred to LH-2.5 where it
can ship with its own calibration set + cost tracking.

Rules (all must hold for pass):
  1. evidence is present and has files_touched.
  2. When task.contract.scope is non-empty, every touched file must be
     under some scope prefix. Empty scope = unconstrained (pass).
  3. No touched file may be under any task.contract.exclusions prefix.

"Scope prefix" means `normpath(file).startswith(normpath(scope))`,
with trailing-slash normalisation so ``src/zf/auth/`` matches
``src/zf/auth/login.py`` but not ``src/zf/authority.py``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Iterable

from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.verification.discriminator import (
    BaseDiscriminator, DiscriminatorResult,
)


def _normalise(path: str) -> str:
    return str(PurePosixPath(path))


def _prefix_matches(file: str, prefix: str) -> bool:
    f = _normalise(file)
    p = _normalise(prefix)
    if p in ("", "."):
        return False
    return f == p or f.startswith(p + "/")


def _matches_any(file: str, prefixes: Iterable[str]) -> bool:
    return any(_prefix_matches(file, p) for p in prefixes if p)


class SemanticDiscriminator(BaseDiscriminator):
    """Rule-based alignment check — no LLM, deterministic."""

    name = "SemanticD"

    def evaluate(
        self,
        task: Task,
        workspace: Path,
        event_log: EventLog,
    ) -> DiscriminatorResult:
        contract = task.contract
        evidence = task.evidence
        # Rule 1: evidence must be present.
        if evidence is None or not evidence.files_touched:
            return DiscriminatorResult(
                d_name=self.name,
                passed=False,
                evidence={"files_touched": 0},
                reason=(
                    "missing evidence (no files_touched recorded); "
                    "semantic alignment cannot be verified"
                ),
            )

        touched = list(evidence.files_touched)

        # Rule 3 first: explicit exclusions always fail (even under scope).
        exclusions = list(getattr(contract, "exclusions", []) or [])
        if exclusions:
            hits = [f for f in touched if _matches_any(f, exclusions)]
            if hits:
                return DiscriminatorResult(
                    d_name=self.name,
                    passed=False,
                    evidence={"violating_files": hits,
                              "exclusions": exclusions},
                    reason=(
                        f"exclusion violation: {hits!r} intersects "
                        f"contract.exclusions {exclusions!r}"
                    ),
                )

        # Rule 2: scope fidelity (only when scope is declared).
        scope = list(contract.scope or [])
        if scope:
            out_of_scope = [f for f in touched if not _matches_any(f, scope)]
            if out_of_scope:
                return DiscriminatorResult(
                    d_name=self.name,
                    passed=False,
                    evidence={"out_of_scope": out_of_scope, "scope": scope},
                    reason=(
                        f"scope fidelity violation: {out_of_scope!r} "
                        f"is not within contract.scope {scope!r}"
                    ),
                )

        return DiscriminatorResult(
            d_name=self.name,
            passed=True,
            evidence={"files_touched": len(touched),
                      "scope": scope, "exclusions": exclusions},
            reason="scope + exclusion rules satisfied",
        )
