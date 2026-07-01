"""TR-EVENT-SCHEMA-LOCK-001 step 1/3 (doc 42 §11.3 A from cangjie-mono eval) —
unit tests for EventSchemaRegistry."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    EventSchemaRule,
    SchemaViolation,
)


# Fake event class for tests (avoids hard dependency on ZfEvent + uuid id
# generation noise in payload-only tests).
@dataclass
class FakeEvent:
    type: str
    payload: dict


# Canonical 7-event schema fixture matching the sprint plan + backlog.
SEVEN_RULE_FIXTURE = {
    "arch.proposal.done": {
        "required": ["feature_id", "proposal_ref", "contract_draft"],
        "nested": {
            "contract_draft": {
                "required": ["behavior", "verification", "scope"],
                "optional": ["exclusions", "handoff_artifacts", "wave"],
            },
        },
    },
    "design.critique.done": {
        "required": ["verdict"],
        "enum": {"verdict": ["approve", "reject"]},
        "when": {
            "if": {"verdict": "reject"},
            "then": {
                "required": ["gate_ref", "findings"],
                "list_item": {
                    "findings": {
                        "required": ["axis", "severity", "issue"],
                        "enum": {
                            "severity": ["low", "medium", "high", "critical"],
                            "axis": ["coverage", "compatibility", "tdd",
                                     "security", "spec"],
                        },
                    },
                },
            },
        },
    },
    "dev.build.done": {
        "required": ["files_changed", "diff_ref", "build_summary"],
        "nested": {
            "build_summary": {
                "required": ["tsc_ok", "vitest_local_ok"],
            },
        },
    },
    "review.approved": {
        "required": ["verdict", "diff_ref"],
        "enum": {"verdict": ["approved", "rejected"]},
    },
    "review.rejected": {
        "required": ["verdict", "diff_ref", "blocking_findings"],
    },
    "test.passed": {
        "required": ["test_runs", "evidence_ref"],
    },
    "judge.passed": {
        "required": ["verdict", "checked_criteria"],
        "enum": {"verdict": ["passed"]},
    },
}


# ---------------------------------------------------------------------------
# Acceptance #1 + #6 + #8 — registry construction
# ---------------------------------------------------------------------------


class TestLoadFromDict:
    def test_load_all_7_rules(self):
        """Acceptance #1: registry parses all 7 event_schemas entries."""
        registry = EventSchemaRegistry.from_dict(SEVEN_RULE_FIXTURE)
        assert registry.rule_count() == 7
        for event_type in SEVEN_RULE_FIXTURE:
            assert registry.has_rule(event_type), f"missing rule for {event_type}"

    def test_load_empty_dict_returns_empty_registry(self):
        registry = EventSchemaRegistry.from_dict({})
        assert registry.rule_count() == 0

    def test_load_none_returns_empty_registry(self):
        registry = EventSchemaRegistry.from_dict(None)
        assert registry.rule_count() == 0

    def test_skips_non_dict_bodies(self):
        """If a yaml entry is malformed (e.g. a string), skip it gracefully."""
        registry = EventSchemaRegistry.from_dict({
            "good.event": {"required": ["x"]},
            "bad.event": "not a dict",
        })
        assert registry.has_rule("good.event")
        assert not registry.has_rule("bad.event")


class TestRegistry:
    def test_loose_for_unknown(self):
        """Acceptance #6: is_loose returns True for unregistered events."""
        registry = EventSchemaRegistry.from_dict(SEVEN_RULE_FIXTURE)
        assert registry.is_loose("worker.heartbeat") is True
        assert registry.is_loose("arch.proposal.done") is False

    def test_get_rule_returns_rule_for_known(self):
        registry = EventSchemaRegistry.from_dict(SEVEN_RULE_FIXTURE)
        rule = registry.get_rule("arch.proposal.done")
        assert rule is not None
        assert rule.event_type == "arch.proposal.done"
        assert "feature_id" in rule.required
        assert "proposal_ref" in rule.required

    def test_get_rule_returns_none_for_unknown(self):
        registry = EventSchemaRegistry.from_dict(SEVEN_RULE_FIXTURE)
        assert registry.get_rule("xyz.unknown") is None


# ---------------------------------------------------------------------------
# Acceptance #2 #3 #4 #5 #7 — validation
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    return EventSchemaRegistry.from_dict(SEVEN_RULE_FIXTURE)


class TestValidate:
    def test_valid_payload_passes(self, registry):
        """Acceptance #2: fully-formed payload returns empty violations."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload={
                "feature_id": "F-1",
                "proposal_ref": ".zf/artifacts/.../proposal.md",
                "contract_draft": {
                    "behavior": "Add X",
                    "verification": "pnpm tsc",
                    "scope": ["src/x.ts"],
                },
            },
        )
        assert registry.validate(event) == []

    def test_missing_required_field(self, registry):
        """Acceptance #3: missing top-level required → missing_required."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload={"feature_id": "F-1"},  # missing proposal_ref + contract_draft
        )
        violations = registry.validate(event)
        assert len(violations) >= 2
        codes = {v.code for v in violations}
        assert "missing_required" in codes
        field_paths = {v.field_path for v in violations}
        assert "payload.proposal_ref" in field_paths
        assert "payload.contract_draft" in field_paths

    def test_enum_mismatch(self, registry):
        """Acceptance #4: bad enum value → enum_mismatch violation."""
        event = FakeEvent(
            type="design.critique.done",
            payload={"verdict": "maybe"},
        )
        violations = registry.validate(event)
        # verdict is enum-constrained, so 'maybe' is invalid
        enum_violations = [v for v in violations if v.code == "enum_mismatch"]
        assert len(enum_violations) == 1
        assert enum_violations[0].field_path == "payload.verdict"
        assert "maybe" in enum_violations[0].actual

    def test_nested_field_path(self, registry):
        """Acceptance #5: nested missing field reports correct path."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload={
                "feature_id": "F-1",
                "proposal_ref": "ref",
                "contract_draft": {"behavior": "X"},  # missing verification + scope
            },
        )
        violations = registry.validate(event)
        paths = {v.field_path for v in violations}
        assert "payload.contract_draft.verification" in paths
        assert "payload.contract_draft.scope" in paths

    def test_nested_type_mismatch(self, registry):
        """Nested field present but not a dict → type_mismatch."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload={
                "feature_id": "F-1",
                "proposal_ref": "ref",
                "contract_draft": "should be a dict",
            },
        )
        violations = registry.validate(event)
        type_violations = [v for v in violations if v.code == "type_mismatch"]
        assert len(type_violations) == 1
        assert type_violations[0].field_path == "payload.contract_draft"

    def test_conditional_when_reject(self, registry):
        """Acceptance #7: design.critique.done verdict=reject requires findings."""
        # verdict=approve → conditional not triggered, no extra violations
        event_approve = FakeEvent(
            type="design.critique.done",
            payload={"verdict": "approve"},
        )
        assert registry.validate(event_approve) == []

        # verdict=reject → must also have gate_ref + findings
        event_reject = FakeEvent(
            type="design.critique.done",
            payload={"verdict": "reject"},  # missing gate_ref + findings
        )
        violations = registry.validate(event_reject)
        paths = {v.field_path for v in violations}
        assert "payload.gate_ref" in paths
        assert "payload.findings" in paths

    def test_conditional_with_list_item_validation(self, registry):
        """verdict=reject + findings[] each item validated."""
        event = FakeEvent(
            type="design.critique.done",
            payload={
                "verdict": "reject",
                "gate_ref": "ref",
                "findings": [
                    {"axis": "coverage", "severity": "high", "issue": "x"},
                    {"axis": "BAD_AXIS", "severity": "high", "issue": "y"},
                ],
            },
        )
        violations = registry.validate(event)
        enum_violations = [v for v in violations if v.code == "enum_mismatch"]
        # The second item has bad axis
        assert any(
            "findings[1].axis" in v.field_path for v in enum_violations
        )

    def test_loose_event_passes(self, registry):
        """Acceptance #6 (negative): unknown event_type → empty violations."""
        event = FakeEvent(
            type="worker.heartbeat",
            payload={},  # heartbeats have no schema
        )
        assert registry.validate(event) == []

    def test_missing_payload_treated_as_empty(self, registry):
        """Acceptance #3 corner: event with no payload at all → required missing."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload=None,
        )
        violations = registry.validate(event)
        assert len(violations) >= 3  # feature_id + proposal_ref + contract_draft

    def test_optional_field_missing_is_fine(self, registry):
        """`optional` fields don't trigger missing_required."""
        event = FakeEvent(
            type="arch.proposal.done",
            payload={
                "feature_id": "F-1",
                "proposal_ref": "ref",
                "contract_draft": {
                    "behavior": "X",
                    "verification": "Y",
                    "scope": ["a"],
                    # optional: exclusions, handoff_artifacts, wave (all missing — fine)
                },
            },
        )
        assert registry.validate(event) == []


# ---------------------------------------------------------------------------
# Acceptance #8 — backward compatibility
# ---------------------------------------------------------------------------


class TestBackcompat:
    def test_legacy_yaml_passes(self):
        """Old zf.yaml without event_schemas → empty registry → no validation."""
        # Simulate: parse a yaml config that has no workflow.dag.event_schemas
        from zf.core.config.schema import WorkflowDagConfig
        dag = WorkflowDagConfig()  # default: event_schemas={}
        assert dag.event_schemas == {}

        registry = EventSchemaRegistry.from_dict(dag.event_schemas)
        assert registry.rule_count() == 0
        # Any event passes
        for et in (
            "arch.proposal.done", "dev.build.done", "judge.passed",
            "completely.made.up", "worker.heartbeat",
        ):
            event = FakeEvent(type=et, payload={})
            assert registry.validate(event) == []

    def test_loader_default_yields_empty_registry(self, tmp_path):
        """End-to-end: minimal yaml without event_schemas loads + registry empty."""
        import textwrap
        yaml_path = tmp_path / "zf.yaml"
        yaml_path.write_text(textwrap.dedent("""
            version: '1.0'
            project:
              name: test
            session:
              tmux_session: t
            roles:
              - name: dev
                backend: mock
        """).strip(), encoding="utf-8")

        from zf.core.config.loader import load_config
        cfg = load_config(yaml_path)
        assert cfg.workflow.dag.event_schemas == {}
        registry = EventSchemaRegistry.from_config(cfg)
        assert registry.rule_count() == 0

    def test_loader_parses_event_schemas_when_present(self, tmp_path):
        """zf.yaml with event_schemas loads correctly through loader."""
        import textwrap
        yaml_path = tmp_path / "zf.yaml"
        yaml_path.write_text(textwrap.dedent("""
            version: '1.0'
            project:
              name: test
            session:
              tmux_session: t
            workflow:
              dag:
                enabled: true
                event_schemas:
                  arch.proposal.done:
                    required: [feature_id, proposal_ref]
                  dev.build.done:
                    required: [files_changed]
            roles:
              - name: dev
                backend: mock
        """).strip(), encoding="utf-8")

        from zf.core.config.loader import load_config
        cfg = load_config(yaml_path)
        assert "arch.proposal.done" in cfg.workflow.dag.event_schemas
        assert "dev.build.done" in cfg.workflow.dag.event_schemas

        registry = EventSchemaRegistry.from_config(cfg)
        assert registry.rule_count() == 2
        assert registry.has_rule("arch.proposal.done")


# ---------------------------------------------------------------------------
# Acceptance #9 — wire-up grep proof (sanity import test)
# ---------------------------------------------------------------------------


class TestWireUpGrepProof:
    def test_module_exports(self):
        from zf.core.verification import event_schema as mod
        for name in (
            "EventSchemaRule",
            "EventSchemaRegistry",
            "SchemaViolation",
        ):
            assert hasattr(mod, name), f"event_schema missing {name}"

    def test_workflow_dag_config_has_event_schemas_field(self):
        from zf.core.config.schema import WorkflowDagConfig
        dag = WorkflowDagConfig()
        assert hasattr(dag, "event_schemas")
        assert isinstance(dag.event_schemas, dict)


# ---------------------------------------------------------------------------
# Extra: SchemaViolation is hashable / comparable (useful for test asserts)
# ---------------------------------------------------------------------------


class TestSchemaViolation:
    def test_violation_is_dataclass_with_expected_fields(self):
        v = SchemaViolation(
            event_type="x", field_path="payload.foo",
            code="missing_required", expected="present", actual="missing",
        )
        assert v.event_type == "x"
        assert v.field_path == "payload.foo"
        assert v.code == "missing_required"

    def test_violation_frozen(self):
        v = SchemaViolation(
            event_type="x", field_path="payload.foo",
            code="missing_required", expected="present", actual="missing",
        )
        with pytest.raises(Exception):
            v.event_type = "y"  # type: ignore[misc]
