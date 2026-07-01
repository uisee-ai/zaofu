"""Tests for `_render_required_payload_shape` → terminal_done_hardening contract.

The renderer in orchestrator_dispatch.py must emit a schema example that the
hardening gate in core/verification/evidence.py actually accepts. r5 surfaced
that the previous renderer produced `score` (singular) and `verification_tiers`
as a top-level dict, while the gate reads `evidence.scores` (plural) and
expects a `checks` LIST with per-item `tier` field — silently dropping
anything else.

These tests pin the renderer ↔ gate contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from zf.runtime.orchestrator_dispatch import _render_required_payload_shape
from zf.core.verification.evidence import _validate_judge_payload


@dataclass
class _FakeEvent:
    payload: dict[str, Any]


def _shape_from_render(missing_list: list[str]) -> dict[str, Any]:
    """Render and extract the JSON shape block."""
    out = _render_required_payload_shape(
        _FakeEvent(payload={"missing": missing_list}),
        "judge.passed",
    )
    assert out, "renderer should produce text when missing list is non-empty"
    # The shape JSON is between ```json fences
    start = out.find("```json\n") + len("```json\n")
    end = out.find("\n```", start)
    return json.loads(out[start:end])


def test_renderer_uses_plural_scores_not_singular_score():
    """Gate reads `scores` plural; `score` singular is silently dropped."""
    shape = _shape_from_render([
        "judge.passed payload missing score dimensions: "
        "completeness, correctness, evidence_quality, regression_risk"
    ])
    # Must have evidence.scores (nested + plural), NOT top-level singular score
    assert "evidence" in shape, f"missing evidence block: {shape}"
    assert isinstance(shape["evidence"], dict)
    assert "scores" in shape["evidence"], f"missing scores: {shape['evidence']}"
    assert "score" not in shape, "singular `score` must not appear at top level"
    # All 4 dimensions
    scores = shape["evidence"]["scores"]
    assert set(scores) == {
        "completeness", "correctness", "evidence_quality", "regression_risk"
    }


def test_renderer_emits_checks_list_with_tier_field():
    """Gate reads tier coverage from `checks` LIST items, not nested dict."""
    shape = _shape_from_render([
        "judge.passed payload missing verification tier evidence: runtime, static"
    ])
    assert "evidence" in shape
    assert "checks" in shape["evidence"]
    checks = shape["evidence"]["checks"]
    assert isinstance(checks, list), "checks MUST be a list (isinstance(list))"
    tiers_in_checks = {c["tier"] for c in checks if isinstance(c, dict)}
    assert tiers_in_checks == {"runtime", "static"}
    for c in checks:
        assert "command" in c
        assert c["passed"] is True
        assert c["exit_code"] == 0


def test_renderer_handles_passing_command_evidence_missing():
    """When gate says 'must include passing command/check evidence', emit one
    default check so the worker has the right shape."""
    shape = _shape_from_render([
        "judge.passed payload must include passing command/check evidence"
    ])
    assert "evidence" in shape
    assert "checks" in shape["evidence"]
    assert isinstance(shape["evidence"]["checks"], list)
    assert len(shape["evidence"]["checks"]) >= 1


def test_renderer_combines_scores_and_tiers_in_same_evidence_block():
    """All evidence sub-keys must land under `evidence` dict, not at top level."""
    shape = _shape_from_render([
        "judge.passed payload missing score dimensions: completeness, correctness, evidence_quality, regression_risk",
        "judge.passed payload missing verification tier evidence: runtime, static",
    ])
    assert "scores" in shape["evidence"]
    assert "checks" in shape["evidence"]
    # Top-level singular keys must NOT exist
    assert "score" not in shape
    assert "verification_tiers" not in shape


def test_renderer_artifact_refs_and_evidence_refs():
    shape = _shape_from_render([
        "judge.passed payload must include replayable artifact refs",
        "judge.passed payload must include evidence refs",
    ])
    assert "artifact_refs" in shape
    assert "evidence_refs" in shape
    assert isinstance(shape["artifact_refs"], list)
    assert isinstance(shape["evidence_refs"], list)


# ---------------------------------------------------------------------------
# Renderer ↔ Hardening Gate round trip
# ---------------------------------------------------------------------------


def _payload_from_shape(shape: dict[str, Any]) -> dict[str, Any]:
    """Convert the renderer's shape (with placeholder values) into a
    concrete judge.passed payload as if the worker followed it verbatim.
    """
    payload = {}
    if "summary" in shape:
        payload["summary"] = "test summary"
    if "evidence" in shape:
        ev = dict(shape["evidence"])
        # Replace placeholder scores with numeric values
        if "scores" in ev:
            ev["scores"] = {k: 0.9 for k in ev["scores"]}
        if "checks" in ev:
            # checks already have correct shape (command, exit_code=0, passed=True, tier)
            ev["checks"] = [
                {**c, "command": f"echo {c['tier']}"} for c in ev["checks"]
            ]
        payload["evidence"] = ev
    if "artifact_refs" in shape:
        payload["artifact_refs"] = ["pkg/foo.ts"]
    if "evidence_refs" in shape:
        payload["evidence_refs"] = ["pkg/foo.ts"]
    return payload


def _done_evidence_from_payload(
    payload: dict[str, Any],
    *,
    required_tiers: list[str] | None = None,
) -> dict[str, Any]:
    """Mirror build_done_evidence_payload's projection of judge.payload."""
    return {
        "task_id": "T1",
        "trigger_event": "judge.passed",
        "trigger_event_id": "evt-test",
        "actor": "judge",
        "payload_evidence": payload.get("evidence") or {},
        "payload_checks": payload.get("checks") or payload.get("commands") or [],
        "payload_summary": str(payload.get("summary") or ""),
        "payload_scores": payload.get("scores") or payload.get("scorecard") or {},
        "artifact_refs": payload.get("artifact_refs") or [],
        "evidence_refs": payload.get("evidence_refs") or [],
        "review_event_id": "evt-r",
        "test_event_id": "evt-t",
        "judge_event_id": "evt-j",
        "discriminator_event_id": "evt-d",
        "contract": {
            "behavior": "b",
            "verification": "v",
            "verification_tiers": list(required_tiers or []),
            "scope": [],
            "exclusions": [],
            "acceptance": "exit_code=0",
        },
        "task_evidence": {
            "commit": "abc",
            "commits": ["abc"],
            "files_touched": [],
            "output_summary": "",
            "verified_at": "",
        },
        "discriminators": [],
    }


def test_renderer_output_passes_hardening_gate():
    """End-to-end: render → emulate worker filling in placeholders → hardening
    gate must accept the result. This is the bug that bit r5 5 times in a row.
    """
    missing = [
        "judge.passed payload must include summary",
        "judge.passed payload must include passing command/check evidence",
        "judge.passed payload missing score dimensions: "
        "completeness, correctness, evidence_quality, regression_risk",
        "judge.passed payload must include replayable artifact refs",
        "judge.passed payload must include evidence refs",
        "judge.passed payload missing verification tier evidence: runtime, static",
    ]
    shape = _shape_from_render(missing)
    payload = _payload_from_shape(shape)
    done_evidence = _done_evidence_from_payload(
        payload, required_tiers=["runtime", "static"],
    )
    remaining = _validate_judge_payload(done_evidence)
    assert remaining == [], (
        f"Renderer's schema example does not pass hardening gate: {remaining}\n"
        f"Payload built from shape: {json.dumps(payload, indent=2)}"
    )


def test_renderer_minimal_score_only_passes_gate_for_scores():
    """Per-axis: just scores fix."""
    shape = _shape_from_render([
        "judge.passed payload missing score dimensions: completeness, correctness, evidence_quality, regression_risk"
    ])
    payload = _payload_from_shape(shape)
    payload["summary"] = "x"
    # Need to also provide checks / refs for full gate, but score dimension
    # specifically should now be satisfied
    done_evidence = _done_evidence_from_payload(payload)
    remaining = _validate_judge_payload(done_evidence)
    # The renderer focused only on scores; we expect remaining to NOT contain
    # "score dimensions"
    assert not any(
        "score dimension" in m for m in remaining
    ), f"score dimensions still missing: {remaining}"


# B-NEW-2 tests (backlogs/2026-05-16-0052-zaofu-kernel-edge-bugs-from-cangjie-p5-validation.md):
# the renderer must merge previously-provided tiers (from trigger_event payload)
# with the currently-missing tiers, so judge LLM doesn't drop already-good
# tiers when fixing the missing one (whack-a-mole).


def _render_with_previous(missing: list[str], previous_tiers: list[str]) -> str:
    """Render with both `missing` field AND a nested previous payload_evidence
    showing tiers the worker already supplied last attempt."""
    trigger_payload = {
        "missing": missing,
        "evidence": {
            "payload_evidence": {
                "checks": [
                    {
                        "command": f"<{t} cmd>",
                        "exit_code": 0,
                        "passed": True,
                        "tier": t,
                    }
                    for t in previous_tiers
                ],
            },
        },
    }
    return _render_required_payload_shape(
        _FakeEvent(payload=trigger_payload),
        "judge.passed",
    )


def test_renderer_includes_previously_provided_tiers_in_shape():
    """B-NEW-2: when judge previously emitted tier=static checks and the gate
    now complains 'missing tier: runtime', the renderer must show BOTH tiers
    in the shape — so the LLM keeps static and adds runtime, instead of
    dropping static."""
    out = _render_with_previous(
        missing=["judge.passed payload missing verification tier evidence: runtime"],
        previous_tiers=["static"],
    )
    # Shape contains BOTH tiers in checks list
    start = out.find("```json\n") + len("```json\n")
    end = out.find("\n```", start)
    shape = json.loads(out[start:end])
    tiers = {c["tier"] for c in shape["evidence"]["checks"]}
    assert tiers == {"static", "runtime"}, (
        f"renderer must include both previously-provided AND newly-missing "
        f"tiers; got {tiers}"
    )


def test_renderer_briefing_explains_keep_and_add_pattern():
    """B-NEW-2: the bullet text must explicitly tell LLM "keep previous, add
    missing" so it doesn't reset the payload."""
    out = _render_with_previous(
        missing=["judge.passed payload missing verification tier evidence: runtime"],
        previous_tiers=["static"],
    )
    assert "static" in out and "runtime" in out
    # The bullet must mention KEEP + ADD semantics:
    assert "KEEP" in out and "ADD" in out, (
        "briefing must spell out the keep-and-add pattern, not just list "
        "tiers neutrally"
    )


def test_renderer_no_previous_tiers_works_as_before():
    """Regression: when trigger payload has no previous payload_evidence
    (first failure, not a reissue-of-reissue), behave as before — only the
    missing tier shows up."""
    out = _render_with_previous(
        missing=["judge.passed payload missing verification tier evidence: runtime, static"],
        previous_tiers=[],
    )
    start = out.find("```json\n") + len("```json\n")
    end = out.find("\n```", start)
    shape = json.loads(out[start:end])
    tiers = {c["tier"] for c in shape["evidence"]["checks"]}
    assert tiers == {"static", "runtime"}, (
        "with no previous tiers, full_tier_set should equal missing tiers"
    )
    assert "KEEP" not in out, (
        "no previous tiers → no need for keep-and-add bullet text"
    )


def test_renderer_dedupes_previous_and_missing_tier_overlap():
    """Edge case: previous tier list overlaps with missing tier (gate
    inconsistently reports). Renderer should de-dupe."""
    out = _render_with_previous(
        missing=["judge.passed payload missing verification tier evidence: static"],
        previous_tiers=["static", "runtime"],
    )
    start = out.find("```json\n") + len("```json\n")
    end = out.find("\n```", start)
    shape = json.loads(out[start:end])
    tiers_list = [c["tier"] for c in shape["evidence"]["checks"]]
    assert len(tiers_list) == len(set(tiers_list)), (
        f"renderer must de-dupe tiers; got duplicates in {tiers_list}"
    )
    assert set(tiers_list) == {"static", "runtime"}
