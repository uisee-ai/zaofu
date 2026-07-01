"""P1/K2 (docs/impl/22-zaofu-canonical-dag.md): yaml workflow.rework_routing
must take priority over heuristic classifiers in rework_triage.

Before P1, ``gate.failed`` emitted by critic with risks/fix_items text
containing words like "missing" or "artifact_refs" was caught by the
_evidence_gap heuristic and classified as "evidence_payload_gap → critic
reissue". That ignored the yaml's explicit ``rework_routing: gate.failed:
arch`` config and caused the 2-hop critic-self-recovery loop observed in
cangjie F-952f2065 round.

P1/K2 inserts a yaml-priority check at the top of classify_rework_trigger:
if config.workflow.rework_routing[event.type] resolves, return immediately
with classification="yaml_routing" and suspected_owner=<configured target>.
"""

from __future__ import annotations

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.rework_triage import (
    REWORK_RETRY_CLASSIFICATIONS,
    classify_rework_trigger,
)


def _config_with_routing(routing: dict[str, str]) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="p1-yaml-priority"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="arch", backend="mock")],
        workflow=WorkflowConfig(rework_routing=routing),
    )


def test_yaml_routing_overrides_evidence_payload_gap_for_gate_failed():
    """The exact bug from cangjie F-952f2065: critic emits gate.failed with
    fix_items text that mentions 'artifact_refs' (an evidence-gap marker).
    Without K2, this gets classified as evidence_payload_gap → critic
    reissue. With K2, yaml routing → arch wins."""
    cfg = _config_with_routing({"gate.failed": "arch"})
    event = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T1",
        payload={
            "verdict": "reject",
            "summary": "Plan has 2 BLOCKERs",
            "risks": [
                "BLOCKER-1: dependencies must include artifact_refs"
            ],
            "fix_items": [
                "add artifact_refs to handoff section",
                "remove missing references",
            ],
        },
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "yaml_routing"
    assert result.suspected_owner == "arch"
    assert result.recommended_action == "dispatch_rework"
    assert "gate.failed" in result.notes and "arch" in result.notes


def test_yaml_routing_overrides_heuristic_for_review_rejected():
    cfg = _config_with_routing({"review.rejected": "arch"})  # design-flavored project
    event = ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="T2",
        payload={"summary": "implementation does not match spec"},
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "yaml_routing"
    assert result.suspected_owner == "arch"


def test_review_child_failed_defaults_to_product_rework():
    cfg = _config_with_routing({})
    event = ZfEvent(
        type="review.child.failed",
        actor="review-lane-1",
        task_id="T2",
        payload={"findings": [{"summary": "provider parity missing"}]},
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "product_issue"
    assert result.suspected_owner == "dev"


def test_dev_failed_artifact_mismatch_defaults_to_arch_rework():
    cfg = _config_with_routing({})
    event = ZfEvent(
        type="dev.failed",
        actor="dev-lane-1",
        task_id="T3",
        payload={
            "reason": "artifact_integrity_mismatch",
            "action": "reproject_or_replan",
        },
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "design_issue"
    assert result.suspected_owner == "arch"


def test_integration_failed_authoritative_verification_scope_gap_replans():
    cfg = _config_with_routing({})
    event = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "status": "failed",
            "findings": [
                {
                    "child_id": "dev-lane-3-CJMIN-PROVIDERS-FUNCTION-CALLING-001",
                    "reason": "authoritative_verification_unrunnable_inside_allowed_scope",
                    "summary": (
                        "focused checks pass, but authoritative verification "
                        "requires workspace/package files outside this slice's "
                        "allowed_paths"
                    ),
                }
            ],
        },
    )

    result = classify_rework_trigger(event, cfg)

    assert result.classification == "design_issue"
    assert result.suspected_owner == "arch"


def test_yaml_routing_overrides_test_failed_when_configured():
    cfg = _config_with_routing({"test.failed": "test"})  # weird but valid
    event = ZfEvent(
        type="test.failed",
        actor="test",
        task_id="T3",
        payload={"summary": "vitest failed"},
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "yaml_routing"
    assert result.suspected_owner == "test"


def test_no_yaml_routing_falls_through_to_heuristic():
    """When yaml does NOT have an entry, heuristic classifier runs as before."""
    cfg = _config_with_routing({})  # empty
    event = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T4",
        payload={
            "summary": "missing artifact_refs",
            # No risks/fix_items, just markers that trip evidence_payload_gap.
            "missing": ["artifact_refs"],
        },
    )
    result = classify_rework_trigger(event, cfg)
    # Without yaml routing, the evidence_payload_gap heuristic catches it.
    assert result.classification == "evidence_payload_gap"


def test_classify_without_config_keeps_legacy_behavior():
    """Calling classify_rework_trigger(event) without config (legacy call
    site) must still work — yaml priority just doesn't kick in."""
    event = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T5",
        payload={"summary": "design has a blocker", "fix_items": ["x"]},
    )
    result = classify_rework_trigger(event)  # no config arg
    # Falls through to existing gate.failed handler (line 102) → design_issue
    assert result.classification == "design_issue"
    assert result.suspected_owner == "arch"


def test_yaml_routing_increments_retry_count():
    """yaml_routing must be in REWORK_RETRY_CLASSIFICATIONS so each cycle
    counts toward the retry-cap. Without this, yaml-routed reworks would
    loop forever."""
    assert "yaml_routing" in REWORK_RETRY_CLASSIFICATIONS


def test_yaml_routing_priority_over_harness_marker():
    """A 'harness bug' marker would normally route to harness suspension.
    yaml routing wins even over harness/environment classification, because
    the operator explicitly opted into the route."""
    cfg = _config_with_routing({"gate.failed": "arch"})
    event = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T6",
        payload={
            "summary": "internal error in discriminator: traceback",
            # These markers would otherwise trigger _HARNESS_MARKERS.
        },
    )
    result = classify_rework_trigger(event, cfg)
    assert result.classification == "yaml_routing"
    assert result.suspected_owner == "arch"


def test_grep_yaml_routing_called_first():
    """Wire-up self-check: yaml routing logic must appear before the
    task.done.blocked / evidence_payload_gap classifier blocks."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "src/zf/runtime/rework_triage.py"
    text = src.read_text(encoding="utf-8")

    # Locate yaml_routing first-use line
    yaml_idx = text.find('"yaml_routing"')
    # First evidence_payload_gap classification block
    evgap_idx = text.find('"evidence_payload_gap"')

    assert yaml_idx >= 0, "yaml_routing classification must exist"
    assert evgap_idx >= 0, "evidence_payload_gap classification must exist"
    assert yaml_idx < evgap_idx, (
        "P1/K2: yaml_routing must appear textually BEFORE evidence_payload_gap "
        f"(yaml_idx={yaml_idx}, evgap_idx={evgap_idx})"
    )
