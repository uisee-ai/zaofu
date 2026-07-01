"""Validity triage for autoresearch claims and candidate evidence."""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_PROTECTED_GLOBS = (
    "tests/fixtures/holdout/**",
    "scripts/autoresearch/evaluators/**",
    "docs/design/KERNEL_INVARIANTS.md",
    "tools/evaluators/**",
)
_REAL_CLAIM_TOKENS = (
    "真实 codex",
    "真实 provider",
    "real codex",
    "real provider",
    "真实场景",
)
_PROD_CLAIM_TOKENS = (
    "可以上生产",
    "生产可用",
    "release-ready",
    "production ready",
)
_HOLDOUT_CLAIM_TOKENS = (
    "holdout passed",
    "holdout 通过",
)


@dataclass(frozen=True)
class ValidityTriage:
    status: str = "pass"  # pass | needs_probe | blocked
    risk_labels: list[str] = field(default_factory=list)
    evidence_debt: list[dict[str, list[str] | str]] = field(default_factory=list)
    recommended_probe: dict[str, str] = field(default_factory=dict)
    allowed_claims: list[str] = field(default_factory=list)
    blocked_claims: list[str] = field(default_factory=list)
    protected_paths_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ValidityTriage":
        if not isinstance(data, dict):
            return cls()
        return cls(
            status=str(data.get("status") or "pass"),
            risk_labels=[str(v) for v in data.get("risk_labels") or []],
            evidence_debt=[
                item for item in data.get("evidence_debt") or []
                if isinstance(item, dict)
            ],
            recommended_probe={
                str(k): str(v)
                for k, v in (data.get("recommended_probe") or {}).items()
            },
            allowed_claims=[str(v) for v in data.get("allowed_claims") or []],
            blocked_claims=[str(v) for v in data.get("blocked_claims") or []],
            protected_paths_touched=[
                str(v) for v in data.get("protected_paths_touched") or []
            ],
        )


def _norm_paths(paths: Iterable[str | Path]) -> list[str]:
    return [str(path).replace("\\", "/").strip() for path in paths if str(path).strip()]


def protected_paths_touched(
    changed_files: Iterable[str | Path],
    *,
    protected_globs: Iterable[str] = DEFAULT_PROTECTED_GLOBS,
) -> list[str]:
    changed = _norm_paths(changed_files)
    patterns = [str(pattern).replace("\\", "/") for pattern in protected_globs]
    touched: list[str] = []
    for path in changed:
        if any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
            touched.append(path)
    return touched


def _has_evidence(evidence_paths: list[str], tokens: Iterable[str]) -> bool:
    lowered = [path.lower() for path in evidence_paths]
    return any(any(token in path for token in tokens) for path in lowered)


def assess_validity(
    *,
    changed_files: Iterable[str | Path] = (),
    claims: Iterable[str] = (),
    evidence_paths: Iterable[str | Path] = (),
    protected_globs: Iterable[str] = DEFAULT_PROTECTED_GLOBS,
) -> ValidityTriage:
    changed = _norm_paths(changed_files)
    evidence = _norm_paths(evidence_paths)
    claim_list = [str(claim).strip() for claim in claims if str(claim).strip()]
    risks: list[str] = []
    debt: list[dict[str, list[str] | str]] = []
    blocked_claims: list[str] = []
    allowed_claims: list[str] = []
    touched = protected_paths_touched(changed, protected_globs=protected_globs)

    if touched:
        risks.append("PROTECTED_PATH_TOUCHED")
        if any(path.startswith("tests/fixtures/holdout/") for path in touched):
            risks.append("HOLDOUT_TOUCHED")
        else:
            risks.append("EVALUATOR_TOUCHED")

    for claim in claim_list:
        lower = claim.lower()
        missing: list[str] = []
        if any(token in lower for token in _REAL_CLAIM_TOKENS):
            if not _has_evidence(evidence, ("run", "log", "session", "screenshot", "events")):
                missing.append("real provider run log/session/screenshot evidence")
        if any(token in lower for token in _PROD_CLAIM_TOKENS):
            if not _has_evidence(evidence, ("full", "pytest", "zf-validate", "validate", "holdout")):
                missing.append("full regression and holdout evidence")
        if any(token in lower for token in _HOLDOUT_CLAIM_TOKENS):
            if not _has_evidence(evidence, ("holdout",)):
                missing.append("holdout evidence")
        if missing:
            blocked_claims.append(claim)
            debt.append({"claim": claim, "missing_evidence": missing})
        else:
            allowed_claims.append(claim)

    recommended_probe: dict[str, str] = {}
    if debt and not touched:
        recommended_probe = {
            "type": "evidence_probe",
            "command": "pytest && zf validate --cold-start",
        }
    if evidence and not _has_evidence(evidence, ("screenshot", "playwright")):
        risks.append("SCREENSHOT_NOT_COLLECTED")
        if not recommended_probe:
            recommended_probe = {
                "type": "playwright_probe",
                "command": "docker run --rm --network host mcp/playwright:latest",
            }

    if touched or blocked_claims:
        status = "blocked"
    elif risks or debt:
        status = "needs_probe"
    else:
        status = "pass"
    return ValidityTriage(
        status=status,
        risk_labels=sorted(set(risks)),
        evidence_debt=debt,
        recommended_probe=recommended_probe,
        allowed_claims=allowed_claims,
        blocked_claims=blocked_claims,
        protected_paths_touched=touched,
    )


__all__ = [
    "DEFAULT_PROTECTED_GLOBS",
    "ValidityTriage",
    "protected_paths_touched",
    "assess_validity",
]
