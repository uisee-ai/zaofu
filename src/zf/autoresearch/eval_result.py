"""Autoresearch eval-result.v1 contract and deterministic comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "eval-result.v1"

DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "correctness": 0.30,
    "regression": 0.15,
    "stability": 0.15,
    "harness_recovery": 0.15,
    "context_safety": 0.10,
    "coordination": 0.05,
    "cost_efficiency": 0.05,
    "learning_value": 0.05,
}

PASSING_GATE_STATES = {"passed", "skipped", "not_applicable"}
BLOCKING_GATE_STATES = {"failed", "blocked", "rejected"}


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_score(value: Any) -> float:
    score = _as_float(value)
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return score


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        text = _clean_str(value)
        if text:
            cleaned.append(text)
    return cleaned


@dataclass(frozen=True)
class GateResult:
    """One gate verdict in an eval result."""

    name: str
    status: str
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_GATE_STATES

    @property
    def is_passing(self) -> bool:
        return self.status in PASSING_GATE_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateResult":
        return cls(
            name=_clean_str(data.get("name")) or "gate",
            status=_clean_str(data.get("status")).lower() or "unknown",
            reason=_clean_str(data.get("reason")),
            evidence_refs=_clean_str_list(data.get("evidence_refs")),
        )


@dataclass(frozen=True)
class EvalResult:
    """Serializable eval-result.v1 artifact.

    Gate verdicts decide whether a run is acceptable. Scores compare acceptable
    runs against baselines or alternative candidates.
    """

    result_id: str
    scenario_id: str
    mode: str
    experiment_id: str = ""
    gates: list[GateResult] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    evidence_refs: dict[str, list[str]] = field(default_factory=dict)
    baseline_ref: str = ""
    candidate_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def gate_status(self) -> str:
        if any(gate.is_blocking for gate in self.gates):
            return "failed"
        if any(gate.status == "passed" for gate in self.gates):
            return "passed"
        if self.gates and all(gate.is_passing for gate in self.gates):
            return "passed"
        return "unknown"

    @property
    def gate_passed(self) -> bool:
        return self.gate_status == "passed"

    @property
    def blocking_gates(self) -> list[GateResult]:
        return [gate for gate in self.gates if gate.is_blocking]

    @property
    def total_score(self) -> float:
        weight_sum = sum(
            weight
            for key, weight in self.weights.items()
            if key in self.scores and weight > 0
        )
        if weight_sum <= 0:
            return 0.0
        weighted = sum(
            _clamp_score(self.scores[key]) * weight
            for key, weight in self.weights.items()
            if key in self.scores and weight > 0
        )
        return round(weighted / weight_sum, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "result_id": self.result_id,
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "experiment_id": self.experiment_id,
            "gate": {
                "final": self.gate_status,
                "results": [gate.to_dict() for gate in self.gates],
            },
            "score": {
                "dimensions": dict(self.scores),
                "weights": dict(self.weights),
                "total": self.total_score,
            },
            "evidence_refs": {
                key: list(values)
                for key, values in self.evidence_refs.items()
            },
            "baseline_ref": self.baseline_ref,
            "candidate_ref": self.candidate_ref,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalResult":
        gate_data = data.get("gate") if isinstance(data.get("gate"), dict) else {}
        raw_gates = gate_data.get("results") if isinstance(gate_data, dict) else []
        score_data = data.get("score") if isinstance(data.get("score"), dict) else {}
        raw_scores = score_data.get("dimensions") if isinstance(score_data, dict) else {}
        raw_weights = score_data.get("weights") if isinstance(score_data, dict) else {}
        evidence = data.get("evidence_refs") if isinstance(data.get("evidence_refs"), dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

        weights = dict(DEFAULT_SCORE_WEIGHTS)
        if isinstance(raw_weights, dict):
            for key, value in raw_weights.items():
                text = _clean_str(key)
                if text:
                    weights[text] = max(0.0, _as_float(value))

        scores: dict[str, float] = {}
        if isinstance(raw_scores, dict):
            for key, value in raw_scores.items():
                text = _clean_str(key)
                if text:
                    scores[text] = _clamp_score(value)

        return cls(
            result_id=_clean_str(data.get("result_id")) or "eval-result",
            scenario_id=_clean_str(data.get("scenario_id")) or "unknown",
            mode=_clean_str(data.get("mode")) or "candidate",
            experiment_id=_clean_str(data.get("experiment_id")),
            gates=[
                GateResult.from_dict(gate)
                for gate in raw_gates
                if isinstance(gate, dict)
            ],
            scores=scores,
            weights=weights,
            evidence_refs={
                _clean_str(key): _clean_str_list(values)
                for key, values in evidence.items()
                if _clean_str(key)
            },
            baseline_ref=_clean_str(data.get("baseline_ref")),
            candidate_ref=_clean_str(data.get("candidate_ref")),
            metadata=dict(metadata),
        )

    @classmethod
    def load(cls, path: Path) -> "EvalResult":
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"eval result must be a JSON object: {path}")
        schema = data.get("schema_version")
        if schema and schema != SCHEMA_VERSION:
            raise ValueError(f"unsupported eval result schema {schema!r}: {path}")
        return cls.from_dict(data)

    def write(self, path: Path) -> None:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class EvalComparison:
    baseline: EvalResult
    candidate: EvalResult
    winner: str
    score_delta: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "eval-comparison.v1",
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "winner": self.winner,
            "score_delta": self.score_delta,
            "reasons": list(self.reasons),
        }


def compare_eval_results(
    baseline: EvalResult,
    candidate: EvalResult,
    *,
    min_delta: float = 0.0,
) -> EvalComparison:
    """Compare baseline and candidate without mutating runtime state."""

    delta = round(candidate.total_score - baseline.total_score, 2)
    reasons: list[str] = []

    if baseline.gate_passed and not candidate.gate_passed:
        winner = "baseline"
        reasons.append("candidate gate failed while baseline gate passed")
    elif candidate.gate_passed and not baseline.gate_passed:
        winner = "candidate"
        reasons.append("candidate gate passed while baseline gate did not pass")
    elif not baseline.gate_passed and not candidate.gate_passed:
        winner = "none"
        reasons.append("both baseline and candidate gates failed or are unknown")
    elif delta > min_delta:
        winner = "candidate"
        reasons.append(f"candidate score improved by {delta:.2f}")
    elif delta < -min_delta:
        winner = "baseline"
        reasons.append(f"candidate score regressed by {abs(delta):.2f}")
    else:
        winner = "tie"
        reasons.append("candidate and baseline scores are within min_delta")

    for gate in candidate.blocking_gates:
        reasons.append(f"candidate blocking gate: {gate.name}={gate.status}")
    for key, value in sorted(candidate.scores.items()):
        base_value = baseline.scores.get(key)
        if base_value is None:
            continue
        diff = round(value - base_value, 2)
        if abs(diff) >= 5:
            direction = "improved" if diff > 0 else "regressed"
            reasons.append(f"{key} {direction} by {abs(diff):.2f}")

    return EvalComparison(
        baseline=baseline,
        candidate=candidate,
        winner=winner,
        score_delta=delta,
        reasons=reasons,
    )


def comparison_to_markdown(comparison: EvalComparison) -> str:
    rows = [
        "# Autoresearch A/B Eval Comparison",
        "",
        f"- baseline: `{comparison.baseline.result_id}` "
        f"gate={comparison.baseline.gate_status} "
        f"score={comparison.baseline.total_score:.2f}",
        f"- candidate: `{comparison.candidate.result_id}` "
        f"gate={comparison.candidate.gate_status} "
        f"score={comparison.candidate.total_score:.2f}",
        f"- winner: `{comparison.winner}`",
        f"- score_delta: `{comparison.score_delta:.2f}`",
        "",
        "## Reasons",
    ]
    rows.extend(f"- {reason}" for reason in comparison.reasons)
    rows.append("")
    return "\n".join(rows)


__all__ = [
    "DEFAULT_SCORE_WEIGHTS",
    "EvalComparison",
    "EvalResult",
    "GateResult",
    "SCHEMA_VERSION",
    "compare_eval_results",
    "comparison_to_markdown",
]
