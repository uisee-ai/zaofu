"""doc 80 rev1 review N4 — forcing function for "唯一驱动" invariant.

The SM-stuck-on-bypass story (rev1 § 3 / § 6 invariant 1) only physically
holds if **decision functions** are not invokable outside the pipeline
interpreter. This test makes that mechanical: any new caller of
`decide_cascade` / `decide_repair` / `classify_bucket` / `derive_taxonomy_bucket`
/ `route` from outside `remediation_pipeline.py` fails CI.

Pre-existing callers that P2 sweep retirement will remove sit in
`P2_RETIRE_ALLOWLIST` with a TODO so the violation count goes 0 by the time
P2 closes.

Tool functions (e.g. `recovery_sufficiency.verify_artifact_ref`) are NOT
locked — they have legitimate non-remediation uses (see doc 80 § 6 inv 1,
the deliberate "锁决策、不锁工具" narrowing).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zf.runtime.remediation_pipeline import DECISION_FUNCTIONS


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "zf"
PIPELINE = SRC / "runtime" / "remediation_pipeline.py"

# Existing import sites that pre-date the SM pipeline. Each entry is
#   (file_relative_to_src, imported_name).
# P2 sweep retirement will remove every one — when P2 closes the allowlist
# should be empty. New violations must add fresh entries with explicit
# justification (or, better, route through the pipeline).
P2_RETIRE_ALLOWLIST: set[tuple[str, str]] = {
    # decide_cascade / classify_bucket — pre-P2 decision wiring
    ("runtime/repair_authorization.py", "classify_bucket"),
    ("runtime/orchestrator_lifecycle.py", "decide_cascade"),
    ("runtime/orchestrator_lifecycle.py", "build_safe_halt_payload"),
    ("runtime/orchestrator_lifecycle.py", "CASCADE_SAFE_HALT"),
    # derive_taxonomy_bucket — pre-P2 classify scattering (rework_triage owners)
    ("runtime/remediation_cascade.py", "derive_taxonomy_bucket"),
    # decide_repair — pre-P2 reactor lazy import
    ("runtime/orchestrator_reactor.py", "decide_repair"),
    # K3 相 3(2026-06-11):shadow-only 路径 —— route 在 _route_rework_trigger
    # 入口以影子事件并行发射(remediation.decision.shadow, mode=shadow_only,
    # 零执行)。TODO(P2 切换):影子对比零分歧后,本入口转正为唯一权威,
    # 上面 decide_cascade/decide_repair 等条目同 PR 退场。
    ("runtime/orchestrator_dispatch.py", "route"),
}


def _gather_decision_function_imports() -> list[tuple[str, int, str, str]]:
    """Walk src/zf, parse, return (relpath, line, module, name) per import
    of any decision function from outside the pipeline."""
    findings: list[tuple[str, int, str, str]] = []
    for py in SRC.rglob("*.py"):
        if py == PIPELINE:
            continue  # pipeline is the legitimate single caller
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        relpath = str(py.relative_to(SRC))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            short = module.removeprefix("zf.")
            if short not in DECISION_FUNCTIONS:
                continue
            locked = DECISION_FUNCTIONS[short]
            for alias in node.names:
                if alias.name in locked:
                    findings.append((relpath, node.lineno, short, alias.name))
    return findings


def test_decision_functions_registry_non_empty():
    """DECISION_FUNCTIONS must enumerate every decision-owning function so
    that adding a new `decide_foo` without registering it is impossible to
    miss — the grep test below relies on this registry being complete."""
    assert "runtime.remediation_pipeline" in DECISION_FUNCTIONS
    assert "route" in DECISION_FUNCTIONS["runtime.remediation_pipeline"]
    assert "decide_cascade" in DECISION_FUNCTIONS["runtime.remediation_cascade"]
    assert "decide_repair" in DECISION_FUNCTIONS["runtime.repair_authorization"]
    assert "derive_taxonomy_bucket" in DECISION_FUNCTIONS["runtime.rework_triage"]


def test_decision_functions_locked_to_pipeline_or_allowlist():
    """Any import of a decision function from anywhere other than the
    pipeline must be on the P2_RETIRE_ALLOWLIST (with a TODO to remove it
    during P2 sweep retirement)."""
    new_violations: list[tuple[str, int, str, str]] = []
    for relpath, lineno, mod, name in _gather_decision_function_imports():
        if (relpath, name) in P2_RETIRE_ALLOWLIST:
            continue
        new_violations.append((relpath, lineno, mod, name))

    assert not new_violations, (
        "Decision function imported outside remediation_pipeline.py and "
        "without P2_RETIRE_ALLOWLIST entry — this breaks the SM forcing "
        "function (doc 80 § 6 inv 1). Either route through the pipeline or "
        "add an explicit allowlist entry with a P2 retirement TODO.\n"
        + "\n".join(f"  {r}:{l}  imports {n} from {m}" for r, l, m, n in new_violations)
    )


def test_p2_allowlist_entries_actually_exist():
    """The allowlist is a debt ledger, not a wishlist — every entry must
    correspond to a real import in src/. Stale entries get removed."""
    observed = {
        (rel, name) for rel, _, _, name in _gather_decision_function_imports()
    }
    stale = P2_RETIRE_ALLOWLIST - observed
    assert not stale, (
        f"P2_RETIRE_ALLOWLIST has stale entries that no longer match any "
        f"real import (likely already removed): {sorted(stale)}"
    )


def test_pipeline_self_import_route_internally_excluded():
    """remediation_pipeline.py defines `route` and uses it locally — that
    must never count as a violation."""
    findings = _gather_decision_function_imports()
    assert not any(
        rel == "runtime/remediation_pipeline.py" for rel, *_ in findings
    ), "pipeline.py imports of its own decision symbols must be ignored"
