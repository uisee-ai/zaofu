"""Deterministic regression eval-case store (design 101 §8 C/D/E).

Opik's "failures-first" eval: a failure becomes a persistent regression
case with *deterministic* assertions (no LLM judge). This module is the
write/eval/replay core:

- C ``capture_regression_case`` — assemble a case from a failed task and
  persist it as a lightweight, rebuildable artifact under
  ``state_dir/artifacts/regression/<case_id>.json`` (NOT a second control
  plane — it is a derived artifact; truth stays in events.jsonl).
- D ``evaluate_assertions`` — evaluate deterministic predicate strings
  (e.g. ``scope_violation==0``, ``rework<=1``) against a facts dict,
  reusing the campaign ``hard_assertions`` predicate style.
- E ``replay_regression_case`` — re-evaluate assertions against current
  facts and optionally re-run the captured CommandGate, returning a
  verdict. No agent re-run required.

The semantic / NL-assertion layer (LLM judge) is gated on LH-2.5 and is
NOT part of this deterministic core.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

REGRESSION_CASE_CAPTURED = "regression.case.captured"

_OPS: dict[str, Callable[[float, float], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}
# longest operators first so "<=" is matched before "<".
_OP_RE = re.compile(r"\s*(==|!=|<=|>=|<|>)\s*")


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    source_task_id: str
    feature_id: str
    source_event_ids: tuple[str, ...] = ()
    command: str = ""  # optional gate command for replay
    assertions: tuple[str, ...] = ()  # deterministic predicates
    captured_at: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


def _cases_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "artifacts" / "regression"


# ---------------------------------------------------------------------------
# C — capture
# ---------------------------------------------------------------------------


def capture_regression_case(
    state_dir: Path,
    *,
    case_id: str,
    source_task_id: str,
    feature_id: str = "",
    source_event_ids: tuple[str, ...] = (),
    command: str = "",
    assertions: tuple[str, ...] = (),
    captured_at: str = "",
    provenance: dict[str, Any] | None = None,
) -> RegressionCase:
    """Persist a regression case. Idempotent on case_id (overwrite)."""
    case = RegressionCase(
        case_id=str(case_id),
        source_task_id=str(source_task_id),
        feature_id=str(feature_id or ""),
        source_event_ids=tuple(str(e) for e in source_event_ids),
        command=str(command or ""),
        assertions=tuple(str(a) for a in assertions),
        captured_at=str(captured_at or ""),
        provenance=dict(provenance or {}),
    )
    d = _cases_dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{case.case_id}.json").write_text(
        json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return case


def list_regression_cases(state_dir: Path) -> list[RegressionCase]:
    d = _cases_dir(state_dir)
    out: list[RegressionCase] = []
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                RegressionCase(
                    case_id=str(data.get("case_id") or path.stem),
                    source_task_id=str(data.get("source_task_id") or ""),
                    feature_id=str(data.get("feature_id") or ""),
                    source_event_ids=tuple(data.get("source_event_ids") or ()),
                    command=str(data.get("command") or ""),
                    assertions=tuple(data.get("assertions") or ()),
                    captured_at=str(data.get("captured_at") or ""),
                    provenance=dict(data.get("provenance") or {}),
                )
            )
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# D — deterministic assertion evaluation
# ---------------------------------------------------------------------------


def _coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_assertion(assertion: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one deterministic predicate against facts.

    Supported forms:
      - ``<fact_key> <op> <number>``  (op in == != <= >= < >)
      - ``gate:<name> not failed``    (facts["gate:<name>"] != "failed")
    Unknown fact or unparseable predicate → passed=False with a reason
    (fail-closed; an assertion we cannot evaluate must not silently pass).
    """
    text = str(assertion or "").strip()
    low = text.lower()
    if low.startswith("gate:") and low.endswith("not failed"):
        key = text[: text.lower().rfind("not failed")].strip()
        status = str(facts.get(key, "")).strip().lower()
        if key not in facts:
            return {"assertion": text, "passed": False, "reason": f"missing fact {key!r}"}
        return {
            "assertion": text,
            "passed": status != "failed",
            "actual": status,
        }
    m = _OP_RE.search(text)
    if not m:
        return {"assertion": text, "passed": False, "reason": "unparseable predicate"}
    lhs = text[: m.start()].strip()
    op = m.group(1)
    rhs = text[m.end():].strip()
    if lhs not in facts:
        return {"assertion": text, "passed": False, "reason": f"missing fact {lhs!r}"}
    a = _coerce_number(facts.get(lhs))
    b = _coerce_number(rhs)
    if a is None or b is None:
        return {"assertion": text, "passed": False, "reason": "non-numeric comparison"}
    return {"assertion": text, "passed": _OPS[op](a, b), "actual": a, "expected": b}


def evaluate_assertions(
    assertions: tuple[str, ...], facts: dict[str, Any]
) -> list[dict[str, Any]]:
    return [evaluate_assertion(a, facts) for a in assertions]


# ---------------------------------------------------------------------------
# E — replay
# ---------------------------------------------------------------------------


def replay_regression_case(
    case: RegressionCase,
    *,
    facts: dict[str, Any],
    run_command: bool = False,
) -> dict[str, Any]:
    """Replay a case deterministically: evaluate assertions against current
    facts, and (optionally) re-run the captured CommandGate. Returns a
    verdict dict. No agent re-run; CommandGate has no workspace dependency
    so it is replay-safe (design 101 §8 E)."""
    assertion_results = evaluate_assertions(case.assertions, facts)
    passed = all(r["passed"] for r in assertion_results)
    command_result: dict[str, Any] | None = None
    if case.command and run_command:
        try:
            from zf.core.verification.gates import CommandGate

            gate = CommandGate(f"regression:{case.case_id}", case.command)
            result = gate.run()
            cmd_passed = bool(getattr(result, "passed", False))
            command_result = {"command": case.command, "passed": cmd_passed}
            passed = passed and cmd_passed
        except Exception as exc:  # pragma: no cover - defensive
            command_result = {"command": case.command, "passed": False, "error": str(exc)}
            passed = False
    return {
        "case_id": case.case_id,
        "source_task_id": case.source_task_id,
        "passed": passed,
        "assertions": assertion_results,
        "command": command_result,
    }
