from __future__ import annotations

from types import SimpleNamespace

import pytest

from zf.runtime.orchestrator_module_parity import (
    ModuleParityBridgeMixin,
    _payload_has_any_ref,
    _quality_floor_required_ref_groups,
)
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


class _BridgeContractHarness(ModuleParityBridgeMixin):
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            workflow=SimpleNamespace(stages=[]),
        )
        self.stale_ids: list[str] = []
        self.bridge_lookup_ids: list[str | None] = []
        self.emitted: list[ZfEvent] = []
        self.event_log = SimpleNamespace(read_all=lambda: [])

    def _reject_stale_candidate_success(self, event):  # noqa: ANN001
        self.stale_ids.append(event.id)
        return WorkflowRuntimeDecision(action="reject", reason="stale")

    def _has_bridge_output(self, source_event_id, event_types):  # noqa: ANN001
        self.bridge_lookup_ids.append(source_event_id)
        return True


class _EmittingBridgeHarness(_BridgeContractHarness):
    def __init__(self) -> None:
        super().__init__()
        self._has_bridge_output = lambda source_event_id, event_types: False
        self.event_writer = SimpleNamespace(append=self._append)
        self.closure_calls: list[dict] = []

    def _append(self, event: ZfEvent) -> ZfEvent:
        self.emitted.append(event)
        return event

    def _bridge_gap_plan_ready_to_task_map(self, event):  # noqa: ANN001
        return None

    def _emit_current_goal_closure(self, *, event_type, source_event, flow_kind, payload):  # noqa: ANN001
        self.closure_calls.append(
            {
                "event_type": event_type,
                "source_event": source_event,
                "flow_kind": flow_kind,
                "payload": payload,
            }
        )
        event = ZfEvent(
            id=f"emitted-{event_type}",
            type=event_type,
            causation_id=source_event.id,
            payload={**payload, "flow_kind": flow_kind},
        )
        self.emitted.append(event)
        return event

    def _maybe_start_reader_fanout(self, event):  # noqa: ANN001
        return None


def test_quality_floor_ref_groups_use_declared_groups_and_builtin_fallbacks() -> None:
    assert _quality_floor_required_ref_groups(
        "issue-regression", {}
    ) == (("repro_ref", "regression_refs", "test_refs"),)
    assert _quality_floor_required_ref_groups(
        "unknown", {}
    ) == ()
    assert _quality_floor_required_ref_groups(
        "issue-regression",
        {"quality_floor_ref_groups": [
            ["custom_a", "custom_b"],
            "custom_c",
            ["", "custom_d", 0],
            [],
            42,
        ]},
    ) == (("custom_a", "custom_b"), ("custom_c",), ("custom_d", "0"))


def test_payload_has_any_ref_requires_non_empty_supported_values() -> None:
    keys = ("ref", "refs", "mapping")
    assert _payload_has_any_ref({"ref": "  value  "}, keys) is True
    assert _payload_has_any_ref({"refs": ["", "r-1"]}, keys) is True
    assert _payload_has_any_ref({"mapping": {"a": "digest"}}, keys) is True
    assert _payload_has_any_ref({"ref": "", "refs": [], "mapping": {}}, keys) is False
    assert _payload_has_any_ref({"refs": [""]}, keys) is False
    assert _payload_has_any_ref({"mapping": {"a": ""}}, keys) is False
    assert _payload_has_any_ref({"ref": 123, "refs": [0, False]}, keys) is True
    assert _payload_has_any_ref({"ref": 123, "refs": False}, keys) is False


def test_module_parity_payload_helpers_have_exact_precedence_and_shapes() -> None:
    helper = ModuleParityBridgeMixin
    assert helper._first_payload_text(
        {"id": "  primary "}, {"id": "fallback"}, "id"
    ) == "primary"
    assert helper._first_payload_text(
        {"id": ""}, {"id": " fallback "}, "id"
    ) == "fallback"
    assert helper._first_payload_text({}, {}, "id") == ""

    assert helper._payload_int({"count": "3"}, "count") == 3
    assert helper._payload_int({"count": "bad", "fallback": 4}, "count", "fallback") == 4
    assert helper._payload_int({"count": None}, "count") is None
    assert helper._payload_text_list({"items": ["a", "", 2]}, "items") == ["a", "2"]
    assert helper._payload_text_list({"items": "a"}, "items") == []


def test_first_payload_text_ignores_non_mapping_fallback_sources() -> None:
    assert ModuleParityBridgeMixin._first_payload_text(
        {},
        ["not", "a", "mapping"],  # type: ignore[arg-type]
        "trace_id",
    ) == ""


def test_module_parity_gap_and_closure_helpers_merge_nested_payloads() -> None:
    helper = ModuleParityBridgeMixin
    payload = {
        "gap_tasks": [{"id": "top"}, "ignored"],
        "report": {"tasks": [{"id": "report"}]},
        "summary": {"gap_tasks": [{"id": "summary"}]},
        "gap_plan": {"tasks": [{"id": "plan"}]},
    }
    assert helper._parity_gap_tasks(payload) == [
        {"id": "top"},
        {"id": "report"},
        {"id": "summary"},
        {"id": "plan"},
    ]
    assert helper._parity_gap_tasks({"report": {"tasks": "bad"}}) == []

    # Nested sources are normalized to mappings before `_parity_gap_tasks`
    # iterates them; a non-mapping branch is therefore unreachable by contract.
    assert helper._parity_gap_tasks({"report": ["not-a-mapping"]}) == []

    assert helper._payload_declares_parity_closed({"status": "completed"}) is True
    assert helper._payload_declares_parity_closed(
        {"report": {"open_gap_count": 0}}
    ) is True
    assert helper._payload_declares_parity_closed(
        {"status": "open", "gap_task_count": 1}
    ) is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"goal_kind": "  issue  ", "feature_id": "F-1"},
            {"flow_kind": "issue"},
        ),
        (
            {"flow_kind": "  refactor  ", "goal_kind": "issue"},
            {"flow_kind": "  refactor  "},
        ),
        (
            {"flow_kind": "", "goal_kind": "research"},
            {"flow_kind": "research"},
        ),
        ({"flow_kind": "issue"}, {"flow_kind": "issue"}),
        ({"goal_kind": "issue"}, {"flow_kind": "issue"}),
        ({"flow_kind": ""}, {}),
        ({}, {}),
    ],
)
def test_gap_workflow_identity_normalizes_flow_kind_aliases(
    payload: dict,
    expected: dict,
) -> None:
    identity = ModuleParityBridgeMixin._gap_workflow_identity(payload)
    assert identity == expected


def test_gap_workflow_identity_projection_is_already_owned_by_base_helper() -> None:
    """The bridge's extra flow-kind projection is an exact output equivalent.

    `_candidate_rework_identity_payload` already projects `flow_kind`; the
    bridge-level fallback only repeats that projection and must not alter the
    canonical identity when the field is present.
    """
    payload = {"flow_kind": "issue", "candidate_ref": "candidate/1"}

    identity = ModuleParityBridgeMixin._gap_workflow_identity(payload)

    assert identity["flow_kind"] == "issue"
    assert identity["candidate_ref"] == "candidate/1"


def test_gap_amended_workflow_identity_replaces_generation_bound_fields() -> None:
    payload = {
        "flow_kind": "issue",
        "goal_kind": "research",
        "task_map_generation": "old-generation",
        "plan_revision": "old-revision",
        "candidate_ref": "candidate/ref",
    }
    identity = ModuleParityBridgeMixin._gap_amended_workflow_identity(
        payload,
        task_map_generation="new-generation",
    )
    assert identity["flow_kind"] == "issue"
    assert identity["candidate_ref"] == "candidate/ref"
    assert identity["task_map_generation"] == "new-generation"
    assert "plan_revision" not in identity


@pytest.mark.parametrize(
    ("payload", "expected_flow_kind"),
    [
        ({"flow_kind": "issue"}, "issue"),
        ({"goal_kind": "research"}, "research"),
        ({"flow_kind": ""}, None),
        ({}, None),
    ],
)
def test_gap_amended_workflow_identity_preserves_flow_kind_aliases(
    payload: dict,
    expected_flow_kind: str | None,
) -> None:
    identity = ModuleParityBridgeMixin._gap_amended_workflow_identity(
        payload,
        task_map_generation="generation-1",
    )
    assert identity.get("flow_kind") == expected_flow_kind


def test_gap_amended_identity_projection_is_already_owned_by_base_helper() -> None:
    payload = {"flow_kind": "issue", "candidate_ref": "candidate/2"}

    identity = ModuleParityBridgeMixin._gap_amended_workflow_identity(
        payload,
        task_map_generation="generation-2",
    )

    assert identity["flow_kind"] == "issue"
    assert identity["candidate_ref"] == "candidate/2"
    assert identity["task_map_generation"] == "generation-2"


@pytest.mark.parametrize(
    "closed_value",
    [
        "closed",
        "passed",
        "approved",
        "pass",
        "approve",
        "complete",
        "completed",
        "no_gap",
        "no_gaps",
        "no-open-gaps",
        "no_open_p0_p1_gaps",
        "no-open-p0-p1-gaps",
        "no_blocking_gaps",
        "no-blocking-gaps",
    ],
)
def test_module_parity_closure_accepts_every_declared_closed_value(
    closed_value: str,
) -> None:
    helper = ModuleParityBridgeMixin

    assert helper._payload_declares_parity_closed({"status": closed_value}) is True


@pytest.mark.parametrize(
    "status_key",
    [
        "parity_status",
        "closure_status",
        "goal_status",
        "status",
        "recommendation",
        "result",
        "verdict",
    ],
)
def test_module_parity_closure_reads_all_status_aliases(status_key: str) -> None:
    helper = ModuleParityBridgeMixin

    assert helper._payload_declares_parity_closed({status_key: "closed"}) is True
    assert helper._payload_declares_parity_closed(
        {"report": {status_key: "closed"}},
    ) is True


@pytest.mark.parametrize("nested_key", ["report", "closure", "verdict", "summary"])
def test_module_parity_closure_reads_all_nested_sources(nested_key: str) -> None:
    helper = ModuleParityBridgeMixin

    assert helper._payload_declares_parity_closed(
        {nested_key: {"status": "closed"}},
    ) is True


@pytest.mark.parametrize(
    "count_key",
    [
        "open_p0_p1_gap_count",
        "open_gap_count",
        "gap_task_count",
        "blocking_gap_count",
    ],
)
def test_module_parity_closure_reads_all_zero_gap_count_aliases(count_key: str) -> None:
    helper = ModuleParityBridgeMixin

    assert helper._payload_declares_parity_closed({count_key: 0}) is True
    assert helper._payload_declares_parity_closed(
        {"summary": {count_key: 0}},
    ) is True


def test_module_parity_closure_rejects_non_mapping_nested_values() -> None:
    helper = ModuleParityBridgeMixin

    assert helper._payload_declares_parity_closed({"report": ["closed"]}) is False


def test_latest_refactor_context_projects_all_supported_fields_from_latest_scope() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="wrong-pdd",
                type="candidate.ready",
                payload={"pdd_id": "OTHER", "trace_id": "wrong"},
            ),
            ZfEvent(
                id="ignored-type",
                type="flow.discovery.completed",
                payload={"pdd_id": "PDD-1", "trace_id": "ignored"},
            ),
            ZfEvent(
                id="matching-context",
                type="task_map.ready",
                payload={
                    "pdd_id": "PDD-1",
                    "feature_id": "F-1",
                    "trace_id": "trace-1",
                    "task_map_ref": "map/ref",
                    "task_map_generation": "generation-1",
                    "task_map_digest": "digest-1",
                    "source_index_ref": "index/ref",
                    "source_commit": "source-commit",
                    "candidate_base_commit": "base-commit",
                    "candidate_ref": "candidate/ref",
                    "candidate_head_commit": "head-commit",
                    "commit": "commit-1",
                    "target_ref": "target/ref",
                    "branch": "branch-1",
                    "goal_id": "goal-1",
                    "workflow_run_id": "run-1",
                    "plan_artifact_package_id": "package-1",
                    "plan_artifact_package_ref": "package/ref",
                    "plan_artifact_package_digest": "package-digest",
                    "goal_claim_set_ref": "claims/ref",
                    "goal_claim_set_digest": "claims-digest",
                },
            ),
        ],
    )

    context = harness._latest_refactor_context({"pdd_id": "PDD-1"})

    assert context == {
        "pdd_id": "PDD-1",
        "feature_id": "F-1",
        "trace_id": "trace-1",
        "task_map_ref": "map/ref",
        "task_map_generation": "generation-1",
        "task_map_digest": "digest-1",
        "source_index_ref": "index/ref",
        "source_commit": "source-commit",
        "candidate_base_commit": "base-commit",
        "candidate_ref": "candidate/ref",
        "candidate_head_commit": "head-commit",
        "commit": "commit-1",
        "target_ref": "target/ref",
        "branch": "branch-1",
        "goal_id": "goal-1",
        "workflow_run_id": "run-1",
        "plan_artifact_package_id": "package-1",
        "plan_artifact_package_ref": "package/ref",
        "plan_artifact_package_digest": "package-digest",
        "goal_claim_set_ref": "claims/ref",
        "goal_claim_set_digest": "claims-digest",
    }
    assert "ignored" not in context.values()
    assert "wrong" not in context.values()


def _gap_task(
    task_id: str,
    *,
    status: str = "in_progress",
    feature_id: str = "",
    evidence: object = None,
) -> SimpleNamespace:
    contract = SimpleNamespace(
        feature_id=feature_id,
        evidence_contract=(
            evidence if evidence is not None else {"gap_kind": "module_parity_gap"}
        ),
    )
    return SimpleNamespace(id=task_id, status=status, contract=contract)


def test_active_gap_task_ids_filters_status_shape_gap_and_identity() -> None:
    harness = _BridgeContractHarness()
    harness.task_store = SimpleNamespace(list_all=lambda: [
        _gap_task("done", status="done", feature_id="FEATURE"),
        _gap_task("cancelled", status="cancelled", feature_id="FEATURE"),
        SimpleNamespace(
            id="missing-status",
            contract=SimpleNamespace(
                feature_id="FEATURE",
                evidence_contract={"gap_kind": "module_parity_gap"},
            ),
        ),
        SimpleNamespace(
            id="missing-contract",
            status="in_progress",
            contract=None,
        ),
        SimpleNamespace(
            id="missing-evidence-field",
            status="in_progress",
            contract=SimpleNamespace(feature_id="FEATURE"),
        ),
        SimpleNamespace(
            id="non-mapping-evidence",
            status="in_progress",
            contract=SimpleNamespace(feature_id="FEATURE", evidence_contract=[]),
        ),
        _gap_task("wrong", feature_id="OTHER"),
        _gap_task("no-gap", feature_id="FEATURE", evidence={}),
        _gap_task("feature-match", feature_id="FEATURE"),
        _gap_task(
            "goal-match",
            evidence={"gap_category": "acceptance", "goal_id": "FEATURE"},
        ),
        _gap_task(
            "candidate-match",
            evidence={"gap_kind": "parity", "candidate_ref": "cand/1"},
        ),
        _gap_task(
            "target-match",
            evidence={"gap_kind": "parity", "target_ref": "target/1"},
        ),
    ])

    assert harness._active_gap_task_ids(
        pdd_id="PDD",
        feature_id="FEATURE",
        candidate_ref="cand/1",
    ) == ["candidate-match", "feature-match", "goal-match", "missing-status"]

    assert harness._active_gap_task_ids(
        pdd_id="PDD",
        feature_id="OTHER",
        candidate_ref="target/1",
    ) == ["target-match", "wrong"]


def test_active_gap_task_ids_accepts_empty_expected_scope_and_sorts_ids() -> None:
    harness = _BridgeContractHarness()
    harness.task_store = SimpleNamespace(list_all=lambda: [
        _gap_task("z-task"),
        _gap_task("a-task"),
        _gap_task(""),
    ])

    assert harness._active_gap_task_ids(
        pdd_id="",
        feature_id="",
        candidate_ref="",
    ) == ["a-task", "z-task"]


def test_active_gap_task_ids_ignores_matching_task_without_id() -> None:
    harness = _BridgeContractHarness()
    harness.task_store = SimpleNamespace(list_all=lambda: [
        SimpleNamespace(
            status="in_progress",
            contract=SimpleNamespace(
                feature_id="FEATURE",
                evidence_contract={"gap_kind": "parity"},
            ),
        ),
    ])

    assert harness._active_gap_task_ids(
        pdd_id="PDD",
        feature_id="FEATURE",
        candidate_ref="candidate/ref",
    ) == []


def test_active_gap_task_ids_handles_missing_contract_and_feature_fields() -> None:
    harness = _BridgeContractHarness()
    harness.task_store = SimpleNamespace(list_all=lambda: [
        SimpleNamespace(
            id="missing-contract-attribute",
            status="in_progress",
        ),
        SimpleNamespace(
            id="missing-feature-attribute",
            status="in_progress",
            contract=SimpleNamespace(
                evidence_contract={"gap_kind": "parity", "goal_id": "FEATURE"},
            ),
        ),
    ])

    assert harness._active_gap_task_ids(
        pdd_id="PDD",
        feature_id="FEATURE",
        candidate_ref="",
    ) == ["missing-feature-attribute"]

    assert harness._active_gap_task_ids(
        pdd_id="",
        feature_id="None",
        candidate_ref="",
    ) == []

    harness.task_store = SimpleNamespace(list_all=lambda: [
        SimpleNamespace(
            id="missing-feature-literal",
            status="in_progress",
            contract=SimpleNamespace(
                evidence_contract={"gap_kind": "parity"},
            ),
        ),
    ])
    assert harness._active_gap_task_ids(
        pdd_id="",
        feature_id="XXXX",
        candidate_ref="",
    ) == []


def test_active_gap_task_ids_fails_closed_when_store_listing_raises() -> None:
    harness = _BridgeContractHarness()
    harness.task_store = SimpleNamespace(
        list_all=lambda: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )

    assert harness._active_gap_task_ids(
        pdd_id="PDD",
        feature_id="FEATURE",
        candidate_ref="cand/1",
    ) == []


def test_latest_refactor_context_uses_latest_allowed_event_per_field_and_scope() -> None:
    harness = _BridgeContractHarness()
    older = ZfEvent(
        id="task-map-old",
        type="task_map.ready",
        payload={
            "pdd_id": "PDD-A",
            "feature_id": "FEATURE-A",
            "trace_id": "trace-old",
            "task_map_ref": "maps/old.json",
            "task_map_generation": "gen-old",
            "task_map_digest": "digest-old",
            "source_index_ref": "index-old",
            "source_commit": "source-old",
            "candidate_base_commit": "base-old",
            "candidate_ref": "candidate-old",
            "candidate_head_commit": "head-old",
            "commit": "commit-old",
            "target_ref": "target-old",
            "branch": "branch-old",
            "goal_id": "goal-old",
            "workflow_run_id": "run-old",
            "plan_artifact_package_id": "package-old",
            "plan_artifact_package_ref": "packages/old.json",
            "plan_artifact_package_digest": "package-digest-old",
            "goal_claim_set_ref": "claims/old.json",
            "goal_claim_set_digest": "claims-digest-old",
        },
    )
    newer = ZfEvent(
        id="candidate-new",
        type="candidate.ready",
        payload={
            "pdd_id": "PDD-A",
            "trace_id": "trace-new",
            "candidate_ref": "candidate-new",
            "candidate_head_commit": "head-new",
        },
    )
    ignored_type = ZfEvent(
        id="ignored-type",
        type="task.updated",
        payload={"pdd_id": "PDD-A", "trace_id": "trace-poison"},
    )
    other_scope = ZfEvent(
        id="other-scope",
        type="candidate.ready",
        payload={"pdd_id": "PDD-B", "trace_id": "trace-other"},
    )
    harness.event_log = SimpleNamespace(
        read_all=lambda: [older, newer, ignored_type, other_scope],
    )

    context = harness._latest_refactor_context({"pdd_id": "PDD-A"})

    assert context["pdd_id"] == "PDD-A"
    assert context["feature_id"] == "FEATURE-A"
    assert context["trace_id"] == "trace-new"
    assert context["candidate_ref"] == "candidate-new"
    assert context["candidate_head_commit"] == "head-new"
    assert context["task_map_ref"] == "maps/old.json"
    assert context["goal_claim_set_digest"] == "claims-digest-old"


def test_latest_refactor_context_accepts_feature_scope_and_read_failure() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [ZfEvent(
        id="feature-context",
        type="verify.parity_scan.requested",
        payload={"feature_id": "FEATURE-A", "trace_id": "trace-feature"},
    )])

    feature_context = harness._latest_refactor_context({"feature_id": "FEATURE-A"})
    assert feature_context["trace_id"] == "trace-feature"
    assert feature_context["feature_id"] == "FEATURE-A"

    harness.event_log = SimpleNamespace(
        read_all=lambda: (_ for _ in ()).throw(RuntimeError("ledger unavailable")),
    )
    assert harness._latest_refactor_context({"pdd_id": "PDD-A"}) == {}


def test_latest_refactor_context_accepts_amended_and_non_mapping_events() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [
        ZfEvent(
            id="non-mapping",
            type="candidate.ready",
            payload=["ignored"],  # type: ignore[arg-type]
        ),
        ZfEvent(
            id="amended-context",
            type="task_map.amended",
            payload={
                "pdd_id": "PDD-A",
                "trace_id": "trace-amended",
                "task_map_ref": "maps/amended.json",
            },
        ),
    ])

    context = harness._latest_refactor_context({"pdd_id": "PDD-A"})

    assert context["trace_id"] == "trace-amended"
    assert context["task_map_ref"] == "maps/amended.json"


def test_latest_refactor_context_feature_scope_uses_feature_id_fallback() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [
        ZfEvent(
            id="wrong-feature",
            type="candidate.ready",
            payload={"feature_id": "FEATURE-B", "trace_id": "wrong"},
        ),
        ZfEvent(
            id="right-feature",
            type="candidate.ready",
            payload={"feature_id": "FEATURE-A", "trace_id": "right"},
        ),
    ])

    context = harness._latest_refactor_context({"feature_id": "FEATURE-A"})

    assert context["trace_id"] == "right"


def test_latest_refactor_context_rejects_only_wrong_feature_scope() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [
        ZfEvent(
            id="wrong-feature-only",
            type="candidate.ready",
            payload={"feature_id": "FEATURE-B", "trace_id": "wrong"},
        ),
    ])

    assert harness._latest_refactor_context({"feature_id": "FEATURE-A"}) == {}


def test_latest_refactor_context_preserves_identityless_events_and_literal_values() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [
        ZfEvent(
            id="identityless",
            type="candidate.ready",
            payload={"trace_id": "trace-identityless", "branch": "XXXX"},
        ),
    ])

    context = harness._latest_refactor_context({"pdd_id": "PDD-A"})

    assert context["trace_id"] == "trace-identityless"
    assert context["branch"] == "XXXX"


def test_latest_refactor_context_uses_feature_scope_to_reject_other_pdd() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [ZfEvent(
        id="other-pdd",
        type="candidate.ready",
        payload={"pdd_id": "PDD-OTHER", "trace_id": "trace-other"},
    )])

    assert harness._latest_refactor_context(
        {"feature_id": "FEATURE-A"},
    ) == {}


def test_fanout_reader_trigger_requires_both_topology_and_exact_trigger() -> None:
    harness = _BridgeContractHarness()
    harness.config.workflow.stages = [
        SimpleNamespace(topology="fanout_writer", trigger="verify.parity_scan.requested"),
        SimpleNamespace(topology="fanout_reader", trigger="verify.other"),
        SimpleNamespace(topology="fanout_reader", trigger="verify.parity_scan.requested"),
    ]

    assert harness._has_fanout_reader_trigger("verify.parity_scan.requested") is True
    assert harness._has_fanout_reader_trigger("verify.missing") is False


def test_fanout_reader_trigger_handles_missing_stage_attributes_and_empty_config() -> None:
    harness = _BridgeContractHarness()
    harness.config.workflow.stages = [SimpleNamespace(), object()]

    assert harness._has_fanout_reader_trigger("verify.parity_scan.requested") is False

    harness.config.workflow.stages = None
    assert harness._has_fanout_reader_trigger("verify.parity_scan.requested") is False


def test_bridge_output_requires_exact_type_and_source_event_id() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(read_all=lambda: [
        ZfEvent(
            id="older",
            type="flow.goal.closed",
            payload={"source_event_id": "source-old"},
        ),
        ZfEvent(
            id="newer-wrong-source",
            type="flow.goal.closed",
            payload={"source_event_id": "source-other"},
        ),
        ZfEvent(
            id="newer-right",
            type="flow.goal.closed",
            payload={"source_event_id": "source-right"},
        ),
        ZfEvent(
            id="non-mapping",
            type="flow.goal.closed",
            payload=["source-right"],  # type: ignore[arg-type]
        ),
    ])

    has_output = ModuleParityBridgeMixin._has_bridge_output
    assert has_output(harness, "source-right", {"flow.goal.closed"}) is True
    assert has_output(harness, "source-old", {"flow.goal.closed"}) is True
    assert has_output(harness, "source-missing", {"flow.goal.closed"}) is False
    assert has_output(harness, "source-right", {"module.parity.closed"}) is False


def test_bridge_output_fails_closed_when_event_log_read_fails() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(
        read_all=lambda: (_ for _ in ()).throw(RuntimeError("ledger unavailable")),
    )

    assert ModuleParityBridgeMixin._has_bridge_output(
        harness,
        "source-right",
        {"flow.goal.closed"},
    ) is False


def test_bridge_output_fails_closed_for_non_mapping_event_payload() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="non-mapping-only",
                type="flow.goal.closed",
                payload=["source-right"],  # type: ignore[arg-type]
            ),
        ],
    )

    assert ModuleParityBridgeMixin._has_bridge_output(
        harness,
        "source-right",
        {"flow.goal.closed"},
    ) is False


def test_bridge_output_does_not_treat_empty_source_as_literal_fallback() -> None:
    harness = _BridgeContractHarness()
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="missing-source",
                type="flow.goal.closed",
                payload={},
            ),
        ],
    )

    assert ModuleParityBridgeMixin._has_bridge_output(
        harness,
        "XXXX",
        {"flow.goal.closed"},
    ) is False


def test_parity_scan_bridge_honors_stale_candidate_rejection_before_emission() -> None:
    harness = _BridgeContractHarness()
    event = ZfEvent(id="verify-stale-1", type="verify.passed")

    decision = harness._bridge_verify_passed_to_parity_scan(event)

    assert decision is not None
    assert decision.action == "reject"
    assert harness.stale_ids == ["verify-stale-1"]


def test_verify_bridge_ignores_non_verify_events_and_requires_reader_trigger() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None

    assert harness._bridge_verify_passed_to_parity_scan(
        ZfEvent(id="other-event", type="candidate.ready"),
    ) is None

    verify = ZfEvent(id="verify-no-reader", type="verify.passed")
    assert harness._bridge_verify_passed_to_parity_scan(verify) is None
    assert harness.emitted == []


def test_verify_bridge_deduplicates_requested_and_suppressed_outputs() -> None:
    for output_type in ("verify.parity_scan.requested", "verify.parity_scan.suppressed"):
        harness = _EmittingBridgeHarness()
        harness._reject_stale_candidate_success = lambda event: None
        harness.config.workflow.stages = [
            SimpleNamespace(
                topology="fanout_reader",
                trigger="verify.parity_scan.requested",
            ),
        ]
        source = ZfEvent(id=f"verify-dedup-{output_type}", type="verify.passed")
        harness.event_log = SimpleNamespace(
            read_all=lambda: [
                ZfEvent(
                    id="existing-verify-output",
                    type=output_type,
                    payload={"source_event_id": source.id},
                ),
            ],
        )
        harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
            harness,
            type(harness),
        )

        assert harness._bridge_verify_passed_to_parity_scan(source) is None
        assert harness.emitted == []


def test_verify_bridge_suppresses_active_gap_scope_before_emission() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    harness.task_store = SimpleNamespace(
        list_all=lambda: [
            SimpleNamespace(
                id="GAP-TASK-1",
                status="in_progress",
                contract=SimpleNamespace(
                    feature_id="FEATURE-1",
                    evidence_contract={"gap_kind": "parity"},
                ),
            ),
        ],
    )
    source = ZfEvent(
        id="verify-active-gap",
        type="verify.passed",
        payload={"feature_id": "FEATURE-1", "candidate_ref": "candidate/ref"},
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "suppress"
    assert harness.emitted[0].type == "verify.parity_scan.suppressed"
    assert harness.emitted[0].payload["active_gap_task_ids"] == ["GAP-TASK-1"]


def test_verify_bridge_emits_request_with_identity_and_commit_aliases() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-request",
        type="verify.passed",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-1",
            "feature_id": "FEATURE-1",
            "trace_id": "trace-payload",
            "candidate_ref": "candidate/ref",
            "source_commit": "source-commit",
            "task_map_ref": "map/ref",
            "candidate_head_commit": "head-commit",
            "task_map_generation": "gen-1",
            "plan_artifact_package_id": "pkg-1",
        },
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.type == "verify.parity_scan.requested"
    assert requested.correlation_id == "trace-payload"
    assert requested.payload["pdd_id"] == "PDD-1"
    assert requested.payload["feature_id"] == "FEATURE-1"
    assert requested.payload["candidate_ref"] == "candidate/ref"
    assert requested.payload["target_ref"] == "candidate/ref"
    assert requested.payload["candidate_base_commit"] == "source-commit"
    assert requested.payload["candidate_head_commit"] == "head-commit"
    assert requested.payload["target_commit"] == "head-commit"
    assert requested.payload["task_map_generation"] == "gen-1"
    assert requested.payload["plan_artifact_package_id"] == "pkg-1"


def test_verify_bridge_request_projects_canonical_schema_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-request-canonical",
        type="verify.passed",
        correlation_id="trace-canonical",
        payload={
            "pdd_id": "PDD-CANONICAL",
            "feature_id": "FEATURE-CANONICAL",
            "candidate_ref": "candidate/canonical",
            "task_map_ref": "maps/canonical",
            "source_index_ref": "indexes/canonical",
            "source_commit": "source-canonical",
            "candidate_head_commit": "head-canonical",
        },
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.type == "verify.parity_scan.requested"
    assert requested.actor == "zf-cli"
    assert requested.causation_id == source.id
    assert requested.correlation_id == "trace-canonical"
    assert requested.payload == {
        "schema_version": "module-parity-scan-request.v1",
        "flow_kind": "refactor",
        "pdd_id": "PDD-CANONICAL",
        "feature_id": "FEATURE-CANONICAL",
        "trace_id": "trace-canonical",
        "task_map_ref": "maps/canonical",
        "source_index_ref": "indexes/canonical",
        "source_commit": "source-canonical",
        "candidate_base_commit": "source-canonical",
        "candidate_ref": "candidate/canonical",
        "target_ref": "candidate/canonical",
        "task_map_generation": "",
        "candidate_head_commit": "head-canonical",
        "target_commit": "head-canonical",
        "plan_artifact_package_id": "",
        "plan_artifact_package_ref": "",
        "plan_artifact_package_digest": "",
        "source_event_id": source.id,
        "source": "verify_passed_bridge",
    }


def test_verify_bridge_request_projects_complete_identity_defaults() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-request-defaults",
        type="verify.passed",
        payload={"pdd_id": "PDD-DEFAULTS", "candidate_ref": "candidate/defaults"},
    )

    harness._bridge_verify_passed_to_parity_scan(source)

    requested = harness.emitted[0]
    assert requested.payload == {
        "schema_version": "module-parity-scan-request.v1",
        "flow_kind": "refactor",
        "pdd_id": "PDD-DEFAULTS",
        "feature_id": "PDD-DEFAULTS",
        "trace_id": source.id,
        "task_map_ref": "",
        "source_index_ref": "",
        "source_commit": "",
        "candidate_base_commit": "",
        "candidate_ref": "candidate/defaults",
        "target_ref": "candidate/defaults",
        "task_map_generation": "",
        "candidate_head_commit": "",
        "target_commit": "",
        "plan_artifact_package_id": "",
        "plan_artifact_package_ref": "",
        "plan_artifact_package_digest": "",
        "source_event_id": source.id,
        "source": "verify_passed_bridge",
    }


def test_verify_bridge_request_falls_back_identity_and_trace_fields() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-fallback-fields",
        type="verify.passed",
        task_id="TASK-FALLBACK",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-FALLBACK",
            "candidate_ref": "candidate/fallback",
        },
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    payload = harness.emitted[0].payload
    assert payload["pdd_id"] == "PDD-FALLBACK"
    assert payload["feature_id"] == "PDD-FALLBACK"
    assert payload["trace_id"] == "trace-correlation"
    assert payload["candidate_ref"] == "candidate/fallback"
    assert payload["target_ref"] == "candidate/fallback"


def test_verify_bridge_request_uses_feature_id_when_pdd_id_is_missing() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-feature-only",
        type="verify.passed",
        payload={
            "feature_id": "FEATURE-ONLY",
            "candidate_ref": "candidate/feature-only",
        },
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    payload = harness.emitted[0].payload
    assert payload["pdd_id"] == "FEATURE-ONLY"
    assert payload["feature_id"] == "FEATURE-ONLY"


def test_verify_bridge_request_uses_task_id_when_identity_fields_are_missing() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-task-only",
        type="verify.passed",
        task_id="TASK-ONLY",
        payload={"candidate_ref": "candidate/task-only"},
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    payload = harness.emitted[0].payload
    assert payload["pdd_id"] == "TASK-ONLY"
    assert payload["feature_id"] == "TASK-ONLY"


@pytest.mark.parametrize("ref_key", ["target_ref", "branch"])
def test_verify_bridge_request_uses_candidate_ref_aliases(
    ref_key: str,
) -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id=f"verify-ref-alias-{ref_key}",
        type="verify.passed",
        payload={ref_key: f"candidate/{ref_key}"},
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    payload = harness.emitted[0].payload
    assert payload["candidate_ref"] == f"candidate/{ref_key}"
    assert payload["target_ref"] == f"candidate/{ref_key}"


def test_verify_bridge_suppression_projects_canonical_schema_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    harness.task_store = SimpleNamespace(
        list_all=lambda: [
            SimpleNamespace(
                id="GAP-CANONICAL",
                status="in_progress",
                contract=SimpleNamespace(
                    feature_id="FEATURE-CANONICAL",
                    evidence_contract={"gap_kind": "parity"},
                ),
            ),
        ],
    )
    source = ZfEvent(
        id="verify-suppressed-canonical",
        type="verify.passed",
        correlation_id="trace-suppressed",
        payload={
            "pdd_id": "PDD-SUPPRESSED",
            "feature_id": "FEATURE-CANONICAL",
            "candidate_ref": "candidate/canonical",
        },
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "suppress"
    suppressed = harness.emitted[0]
    assert suppressed.type == "verify.parity_scan.suppressed"
    assert suppressed.actor == "zf-cli"
    assert suppressed.causation_id == source.id
    assert suppressed.correlation_id == "trace-suppressed"
    assert suppressed.payload == {
        "schema_version": "module-parity-scan-suppressed.v1",
        "pdd_id": "PDD-SUPPRESSED",
        "feature_id": "FEATURE-CANONICAL",
        "candidate_ref": "candidate/canonical",
        "source_event_id": source.id,
        "active_gap_task_ids": ["GAP-CANONICAL"],
        "reason": "active gap work already owns this candidate scope",
    }


def test_verify_bridge_uses_scoped_context_for_missing_primary_identity() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.stages = [
        SimpleNamespace(
            topology="fanout_reader",
            trigger="verify.parity_scan.requested",
        ),
    ]
    source = ZfEvent(
        id="verify-context-fallback",
        type="verify.passed",
        task_id="TASK-1",
        correlation_id="trace-event",
        payload={},
    )
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="context-task-map",
                type="task_map.ready",
                payload={
                    "pdd_id": "PDD-CONTEXT",
                    "feature_id": "FEATURE-CONTEXT",
                    "trace_id": "trace-context",
                    "candidate_ref": "candidate/context",
                    "source_index_ref": "index/context",
                    "source_commit": "source-context",
                    "candidate_base_commit": "base/context",
                    "task_map_ref": "map/context",
                    "task_map_generation": "generation-context",
                    "candidate_head_commit": "head/context",
                    "plan_artifact_package_id": "pkg/context",
                    "plan_artifact_package_ref": "pkg/ref/context",
                    "plan_artifact_package_digest": "sha256:context",
                },
            ),
        ],
    )

    decision = harness._bridge_verify_passed_to_parity_scan(source)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.payload["pdd_id"] == "PDD-CONTEXT"
    assert requested.payload["feature_id"] == "FEATURE-CONTEXT"
    assert requested.payload["trace_id"] == "trace-context"
    assert requested.payload["candidate_ref"] == "candidate/context"
    assert requested.payload["candidate_base_commit"] == "base/context"
    assert requested.payload["task_map_ref"] == "map/context"
    assert requested.payload["source_index_ref"] == "index/context"
    assert requested.payload["source_commit"] == "source-context"
    assert requested.payload["candidate_head_commit"] == "head/context"
    assert requested.payload["target_commit"] == "head/context"
    assert requested.payload["task_map_generation"] == "generation-context"
    assert requested.payload["plan_artifact_package_id"] == "pkg/context"
    assert requested.payload["plan_artifact_package_ref"] == "pkg/ref/context"
    assert requested.payload["plan_artifact_package_digest"] == "sha256:context"


def test_verify_to_flow_discovery_ignores_stale_or_unconfigured_flows() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None

    assert harness._bridge_verify_passed_to_flow_discovery(
        ZfEvent(id="no-flow-kind", type="verify.passed"),
    ) is None
    assert harness.emitted == []

    harness.config.workflow.flow_metadata = {
        "flow_kind": "refactor",
        "post_verify_discovery": "module_parity",
    }
    assert harness._bridge_verify_passed_to_flow_discovery(
        ZfEvent(id="refactor-flow", type="verify.passed"),
    ) is None
    assert harness.emitted == []


def test_verify_to_flow_discovery_honors_stale_candidate_rejection_before_emission() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    source = ZfEvent(
        id="stale-flow-discovery",
        type="verify.passed",
        payload={"candidate_ref": "candidate/stale"},
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "reject"
    assert decision.reason == "stale"
    assert harness.stale_ids == [source.id]
    assert harness.emitted == []


def test_verify_to_flow_discovery_normalizes_non_mapping_source_payload() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {"flow_kind": "issue"}

    decision = harness._bridge_verify_passed_to_flow_discovery(
        ZfEvent(id="non-mapping-verify", type="verify.passed", payload=["bad"]),  # type: ignore[arg-type]
    )

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "flow.goal.closed"


def test_verify_to_flow_discovery_normalizes_non_mapping_candidate_verification_payload() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    verification = ZfEvent(
        id="verify-with-bad-payload",
        type="verify.passed",
        payload=["bad"],  # type: ignore[arg-type]
    )
    candidate = ZfEvent(
        id="candidate-with-bad-verification",
        type="candidate.ready",
        payload={"feature_id": "FEATURE-1", "candidate_head_commit": "HEAD"},
    )
    harness.event_log = SimpleNamespace(read_all=lambda: [verification])
    harness._latest_flow_verification_before_candidate = lambda event: verification  # type: ignore[method-assign]

    decision = harness._bridge_verify_passed_to_flow_discovery(candidate)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "flow.discovery.requested"
    assert harness.emitted[0].payload["feature_id"] == "FEATURE-1"


def test_verify_to_flow_discovery_suppresses_refactor_or_module_parity_independently() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "refactor",
        "post_verify_discovery": "issue_scan",
    }
    assert harness._bridge_verify_passed_to_flow_discovery(
        ZfEvent(id="refactor-with-issue-scan", type="verify.passed"),
    ) is None
    assert harness.emitted == []

    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "module_parity",
    }
    assert harness._bridge_verify_passed_to_flow_discovery(
        ZfEvent(id="issue-with-module-parity", type="verify.passed"),
    ) is None
    assert harness.emitted == []


def test_verify_to_flow_discovery_closes_flow_without_discovery_profile() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {"flow_kind": "issue"}
    source = ZfEvent(
        id="issue-no-discovery",
        type="verify.passed",
        payload={
            "pdd_id": "PDD-ISSUE",
            "feature_id": "ISSUE-1",
            "trace_id": "trace-issue",
        },
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "flow.goal.closed"
    assert harness.emitted[0].payload["schema_version"] == "flow-goal-closed.v1"
    assert harness.emitted[0].payload["pdd_id"] == "PDD-ISSUE"
    assert harness.emitted[0].payload["feature_id"] == "ISSUE-1"
    assert harness.emitted[0].payload["goal_id"] == "ISSUE-1"
    assert harness.emitted[0].payload["trace_id"] == "trace-issue"
    assert harness.emitted[0].payload["flow_kind"] == "issue"
    assert harness.emitted[0].payload["open_p0_p1_gap_count"] == 0
    assert harness.emitted[0].payload["source"] == "verified_flow_closure_bridge"


def test_verify_to_flow_discovery_uses_correlation_or_event_id_trace_fallback() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {"flow_kind": "issue"}

    correlated = ZfEvent(
        id="issue-correlation-trace",
        type="verify.passed",
        correlation_id="correlation-1",
    )
    assert harness._bridge_verify_passed_to_flow_discovery(correlated) is not None
    assert harness.emitted[-1].payload["trace_id"] == "correlation-1"

    event_only = ZfEvent(id="issue-event-trace", type="verify.passed")
    assert harness._bridge_verify_passed_to_flow_discovery(event_only) is not None
    assert harness.emitted[-1].payload["trace_id"] == "issue-event-trace"


def test_verify_to_flow_discovery_deduplicates_requested_output() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    source = ZfEvent(id="issue-discovery", type="verify.passed")
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="existing-discovery",
                type="flow.discovery.requested",
                payload={"source_event_id": source.id},
            ),
        ],
    )
    harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
        harness,
        type(harness),
    )

    assert harness._bridge_verify_passed_to_flow_discovery(source) is None
    assert harness.emitted == []


def test_verify_to_flow_discovery_waits_for_candidate_head_commit() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    source = ZfEvent(
        id="issue-awaits-candidate",
        type="verify.passed",
        payload={"candidate_ref": "candidate/ref"},
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "wait"
    assert "materialized candidate" in decision.reason
    assert harness.emitted == []


def test_verify_to_flow_discovery_emits_scoped_request_payload() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
        "quality_floor": "issue-regression",
        "evidence_policy": "strict",
        "task_pipeline": {"candidate": {"rolling_smoke": "smoke.py"}},
    }
    source = ZfEvent(
        id="issue-discovery-request",
        type="verify.passed",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-ISSUE",
            "feature_id": "ISSUE-1",
            "trace_id": "trace-payload",
            "candidate_ref": "candidate/ref",
            "candidate_head_commit": "head-commit",
            "task_map_ref": "maps/current",
            "artifact_refs": ["artifact/a", "", 0],
            "task_map_generation": "gen-1",
        },
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.type == "flow.discovery.requested"
    assert requested.correlation_id == "trace-payload"
    assert requested.payload["flow_kind"] == "issue"
    assert requested.payload["discovery_profile"] == "issue_scan"
    assert requested.payload["candidate_ref"] == "candidate/ref"
    assert requested.payload["candidate_head_commit"] == "head-commit"
    assert requested.payload["artifact_refs"] == ["artifact/a", "0"]
    assert requested.payload["candidate_policy"] == {"rolling_smoke": "smoke.py"}
    assert requested.payload["task_map_generation"] == "gen-1"


def test_verify_to_flow_discovery_projects_canonical_request_payload() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
        "quality_floor": "issue-regression",
        "evidence_policy": "strict",
        "environment_policy": "isolated",
        "projection_policy": "kanban",
        "task_pipeline": {"candidate": {"rolling_smoke": "smoke.py"}},
    }
    source = ZfEvent(
        id="issue-discovery-canonical",
        type="verify.passed",
        correlation_id="trace-canonical",
        payload={
            "pdd_id": "PDD-CANONICAL",
            "feature_id": "ISSUE-CANONICAL",
            "candidate_ref": "candidate/canonical",
            "candidate_head_commit": "head-canonical",
            "task_map_ref": "maps/canonical",
            "task_map_generation": "generation-canonical",
            "artifact_refs": ["artifact/a", "", 0],
        },
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.type == "flow.discovery.requested"
    assert requested.actor == "zf-cli"
    assert requested.causation_id == source.id
    assert requested.correlation_id == "trace-canonical"
    assert requested.payload == {
        "schema_version": "flow-discovery-request.v1",
        "workflow_run_id": "trace-canonical",
        "pdd_id": "PDD-CANONICAL",
        "feature_id": "ISSUE-CANONICAL",
        "goal_id": "ISSUE-CANONICAL",
        "trace_id": "trace-canonical",
        "flow_kind": "issue",
        "discovery_profile": "issue_scan",
        "quality_floor": "issue-regression",
        "evidence_policy": "strict",
        "environment_policy": "isolated",
        "projection_policy": "kanban",
        "task_map_ref": "maps/canonical",
        "candidate_ref": "candidate/canonical",
        "target_ref": "candidate/canonical",
        "candidate_head_commit": "head-canonical",
        "artifact_refs": ["artifact/a", "0"],
        "source_event_id": source.id,
        "source": "post_verify_flow_discovery_bridge",
        "candidate_policy": {"rolling_smoke": "smoke.py"},
        "task_map_generation": "generation-canonical",
    }


def test_verify_to_flow_discovery_falls_back_feature_identity_and_trace() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    source = ZfEvent(
        id="issue-discovery-feature-only",
        type="verify.passed",
        correlation_id="trace-feature-only",
        payload={
            "feature_id": "FEATURE-ONLY",
            "candidate_ref": "candidate/feature-only",
            "candidate_head_commit": "head-feature-only",
        },
    )

    decision = harness._bridge_verify_passed_to_flow_discovery(source)

    assert decision is not None and decision.action == "bridge"
    payload = harness.emitted[0].payload
    assert payload["pdd_id"] == "FEATURE-ONLY"
    assert payload["feature_id"] == "FEATURE-ONLY"
    assert payload["goal_id"] == "FEATURE-ONLY"
    assert payload["trace_id"] == "trace-feature-only"


def test_candidate_ready_flow_discovery_merges_current_verification_context() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    verification = ZfEvent(
        id="verify-candidate-current",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-current",
            "pdd_id": "PDD-CURRENT",
            "feature_id": "FEATURE-CURRENT",
            "trace_id": "trace-current",
            "candidate_ref": "candidate/current",
            "candidate_head_commit": "head-current",
            "task_map_generation": "generation-current",
            "status": "passed",
        },
    )
    candidate = ZfEvent(
        id="candidate-current",
        type="candidate.ready",
        payload={
            "workflow_run_id": "run-current",
            "pdd_id": "PDD-CURRENT",
            "feature_id": "FEATURE-CURRENT",
            "candidate_ref": "candidate/current",
            "candidate_head_commit": "head-current",
            "task_map_generation": "generation-current",
        },
    )
    harness.event_log = SimpleNamespace(read_all=lambda: [verification])

    decision = harness._bridge_verify_passed_to_flow_discovery(candidate)

    assert decision is not None and decision.action == "bridge"
    requested = harness.emitted[0]
    assert requested.type == "flow.discovery.requested"
    assert requested.payload["verification_event_id"] == verification.id
    assert requested.payload["pdd_id"] == "PDD-CURRENT"
    assert requested.payload["feature_id"] == "FEATURE-CURRENT"
    assert requested.payload["trace_id"] == "trace-current"
    assert requested.payload["candidate_ref"] == "candidate/current"
    assert requested.payload["candidate_head_commit"] == "head-current"


@pytest.mark.parametrize(
    ("verification_patch", "candidate_patch"),
    [
        ({"status": "failed"}, {}),
        ({"candidate_ref": "candidate/stale"}, {}),
        ({"candidate_head_commit": "head-stale"}, {}),
    ],
)
def test_candidate_ready_flow_discovery_rejects_non_current_verification(
    verification_patch: dict,
    candidate_patch: dict,
) -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    verification = ZfEvent(
        id="verify-candidate-non-current",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-current",
            "pdd_id": "PDD-CURRENT",
            "feature_id": "FEATURE-CURRENT",
            "trace_id": "trace-current",
            "candidate_ref": "candidate/current",
            "candidate_head_commit": "head-current",
            "task_map_generation": "generation-current",
            "status": "passed",
            **verification_patch,
        },
    )
    candidate = ZfEvent(
        id="candidate-current",
        type="candidate.ready",
        payload={
            "workflow_run_id": "run-current",
            "pdd_id": "PDD-CURRENT",
            "feature_id": "FEATURE-CURRENT",
            "candidate_ref": "candidate/current",
            "candidate_head_commit": "head-current",
            "task_map_generation": "generation-current",
            **candidate_patch,
        },
    )
    harness.event_log = SimpleNamespace(read_all=lambda: [verification])

    assert harness._bridge_verify_passed_to_flow_discovery(candidate) is None
    assert harness.emitted == []


@pytest.mark.parametrize("consumed_type", ["flow.discovery.requested", "flow.goal.closed"])
@pytest.mark.parametrize("ref_key", ["verification_event_id", "source_event_id"])
@pytest.mark.parametrize("same_verification", [True, False])
def test_candidate_ready_flow_discovery_does_not_replay_consumed_verification(
    consumed_type: str, ref_key: str, same_verification: bool,
) -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan" if consumed_type == "flow.discovery.requested" else "",
    }
    verification = ZfEvent(
        id="verify-candidate-consumed",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-consumed",
            "pdd_id": "PDD-CONSUMED",
            "feature_id": "FEATURE-CONSUMED",
            "candidate_ref": "candidate/consumed",
            "candidate_head_commit": "head-consumed",
            "task_map_generation": "generation-consumed",
            "status": "passed",
        },
    )
    candidate = ZfEvent(
        id="candidate-consumed",
        type="candidate.ready",
        payload={**verification.payload},
    )
    consumed = ZfEvent(
        id="flow-discovery-consumed",
        type=consumed_type,
        payload={ref_key: verification.id if same_verification else "older-verification"},
    )
    harness.event_log = SimpleNamespace(read_all=lambda: [verification, consumed])

    decision = harness._bridge_verify_passed_to_flow_discovery(candidate)
    if same_verification:
        assert decision is None
        assert harness.emitted == []
    else:
        assert decision.action == "bridge"
        assert [event.type for event in harness.emitted] == [consumed_type]


def test_candidate_ready_flow_discovery_deduplicates_existing_candidate_output() -> None:
    harness = _EmittingBridgeHarness()
    harness._reject_stale_candidate_success = lambda event: None
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "post_verify_discovery": "issue_scan",
    }
    verification = ZfEvent(
        id="verify-candidate-dedup",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-dedup",
            "pdd_id": "PDD-DEDUP",
            "candidate_ref": "candidate/dedup",
            "candidate_head_commit": "head-dedup",
            "task_map_generation": "generation-dedup",
            "status": "passed",
        },
    )
    candidate = ZfEvent(
        id="candidate-dedup",
        type="candidate.ready",
        payload={**verification.payload},
    )
    existing = ZfEvent(
        id="flow-discovery-dedup-candidate",
        type="flow.discovery.requested",
        payload={"source_event_id": candidate.id},
    )
    harness.event_log = SimpleNamespace(
        read_all=lambda: [verification, existing],
    )
    harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
        harness,
        type(harness),
    )

    assert harness._bridge_verify_passed_to_flow_discovery(candidate) is None
    assert harness.emitted == []


def test_flow_discovery_completion_deduplication_is_bound_to_source_event() -> None:
    harness = _BridgeContractHarness()
    event = ZfEvent(id="flow-discovery-1", type="flow.discovery.completed")

    assert harness._bridge_flow_discovery_completed(event) is None
    assert harness.bridge_lookup_ids == ["flow-discovery-1"]


@pytest.mark.parametrize(
    "output_type",
    ["flow.gap_plan.ready", "flow.goal.closed", "flow.goal.blocked"],
)
def test_flow_discovery_completion_deduplicates_each_terminal_output_type(
    output_type: str,
) -> None:
    harness = _EmittingBridgeHarness()
    harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
        harness,
        type(harness),
    )
    source = ZfEvent(id="flow-discovery-dedup", type="flow.discovery.completed")
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="existing-output",
                type=output_type,
                payload={"source_event_id": source.id},
            ),
        ],
    )

    assert harness._bridge_flow_discovery_completed(source) is None
    assert harness.emitted == []


def test_flow_discovery_completion_fails_closed_for_non_mapping_payload() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="flow-discovery-non-mapping",
        type="flow.discovery.completed",
        payload=["not", "a", "mapping"],  # type: ignore[arg-type]
    )

    decision = harness._bridge_flow_discovery_completed(source)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "flow.goal.blocked"


@pytest.mark.parametrize(
    ("payload", "correlation_id", "expected"),
    [
        ({"trace_id": "trace-payload"}, "trace-correlation", "trace-payload"),
        ({}, "trace-correlation", "trace-correlation"),
        ({}, None, "flow-discovery-trace"),
    ],
)
def test_flow_discovery_completion_prefers_payload_trace_then_correlation_then_event_id(
    payload: dict,
    correlation_id: str | None,
    expected: str,
) -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="flow-discovery-trace",
        type="flow.discovery.completed",
        correlation_id=correlation_id,
        payload={
            **payload,
            "status": "completed",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_flow_discovery_completed(source)

    assert harness.emitted[0].payload["trace_id"] == expected


def test_flow_discovery_completion_preserves_flow_kind_alias_and_empty_default() -> None:
    harness = _EmittingBridgeHarness()
    alias_event = ZfEvent(
        id="flow-discovery-goal-kind",
        type="flow.discovery.completed",
        payload={"goal_kind": "research", "open_p0_p1_gap_count": 0},
    )
    harness._bridge_flow_discovery_completed(alias_event)
    assert harness.emitted[0].payload["flow_kind"] == "research"

    harness.emitted.clear()
    empty_event = ZfEvent(
        id="flow-discovery-empty-kind",
        type="flow.discovery.completed",
        payload={"open_p0_p1_gap_count": 0},
    )
    harness._bridge_flow_discovery_completed(empty_event)
    assert harness.emitted[0].payload["flow_kind"] == "flow"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"base_task_map_ref": "maps/base"}, "maps/base"),
        ({"supersedes_task_map_ref": "maps/superseded"}, "maps/superseded"),
        ({}, ""),
    ],
)
def test_flow_discovery_completion_task_map_ref_fallbacks(
    payload: dict,
    expected: str,
) -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id=f"flow-discovery-task-map-{expected or 'empty'}",
        type="flow.discovery.completed",
        payload={**payload, "open_p0_p1_gap_count": 0},
    )

    harness._bridge_flow_discovery_completed(source)

    assert harness.emitted[0].payload["task_map_ref"] == expected


def test_module_parity_completion_deduplication_is_bound_to_source_event() -> None:
    harness = _BridgeContractHarness()
    event = ZfEvent(id="parity-complete-1", type="module.parity.scan.completed")

    assert harness._bridge_module_parity_scan_completed(event) is None
    assert harness.bridge_lookup_ids == ["parity-complete-1"]


@pytest.mark.parametrize(
    "output_type",
    ["gap_plan.ready", "module.parity.closed", "module.parity.blocked"],
)
def test_module_parity_completion_deduplicates_each_terminal_output_type(
    output_type: str,
) -> None:
    harness = _EmittingBridgeHarness()
    harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
        harness,
        type(harness),
    )
    source = ZfEvent(id="parity-dedup", type="module.parity.scan.completed")
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="existing-parity-output",
                type=output_type,
                payload={"source_event_id": source.id},
            ),
        ],
    )

    assert harness._bridge_module_parity_scan_completed(source) is None
    assert harness.emitted == []


def test_module_parity_completion_fails_closed_for_non_mapping_payload() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-non-mapping",
        type="module.parity.scan.completed",
        payload=["not", "a", "mapping"],  # type: ignore[arg-type]
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "module.parity.blocked"


@pytest.mark.parametrize(
    ("payload", "correlation_id", "expected"),
    [
        ({"trace_id": "trace-payload"}, "trace-correlation", "trace-payload"),
        ({}, "trace-correlation", "trace-correlation"),
        ({}, None, "parity-trace"),
    ],
)
def test_module_parity_completion_trace_precedence(
    payload: dict,
    correlation_id: str | None,
    expected: str,
) -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-trace",
        type="module.parity.scan.completed",
        correlation_id=correlation_id,
        payload={**payload, "status": "completed", "open_p0_p1_gap_count": 0},
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["trace_id"] == expected


def test_module_parity_completion_preserves_identity_and_target_fallbacks() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-identity",
        type="module.parity.scan.completed",
        payload={
            "candidate_ref": "candidate/ref",
            "candidate_head_commit": "head-commit",
            "task_map_generation": "gen-1",
            "plan_artifact_package_id": "pkg-1",
            "plan_artifact_package_ref": "pkg/ref",
            "plan_artifact_package_digest": "sha256:pkg",
            "status": "completed",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    emitted = harness.emitted[0].payload
    assert emitted["target_ref"] == "candidate/ref"
    assert emitted["candidate_ref"] == "candidate/ref"
    assert emitted["task_map_generation"] == "gen-1"
    assert emitted["candidate_head_commit"] == "head-commit"
    assert emitted["target_commit"] == "head-commit"
    assert emitted["plan_artifact_package_id"] == "pkg-1"
    assert emitted["plan_artifact_package_ref"] == "pkg/ref"
    assert emitted["plan_artifact_package_digest"] == "sha256:pkg"


def test_flow_discovery_gap_bridge_falls_back_from_feature_id_to_pdd_id() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-feature-only",
        type="flow.discovery.completed",
        payload={
            "feature_id": "FEATURE-ONLY",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    decision = harness._bridge_flow_discovery_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert len(harness.emitted) == 1
    assert harness.emitted[0].payload["pdd_id"] == "FEATURE-ONLY"
    assert harness.emitted[0].payload["feature_id"] == "FEATURE-ONLY"


def test_flow_discovery_gap_bridge_prefers_pdd_id_over_feature_id() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-pdd-precedence",
        type="flow.discovery.completed",
        payload={
            "pdd_id": "PDD-FIRST",
            "feature_id": "FEATURE-SECOND",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_flow_discovery_completed(event)

    assert harness.emitted[0].payload["pdd_id"] == "PDD-FIRST"
    assert harness.emitted[0].payload["feature_id"] == "FEATURE-SECOND"


def test_flow_discovery_gap_bridge_keeps_missing_identity_empty() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-no-identity",
        type="flow.discovery.completed",
        payload={"gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_flow_discovery_completed(event)

    assert harness.emitted[0].payload["pdd_id"] == ""
    assert harness.emitted[0].payload["feature_id"] == ""


def test_module_parity_gap_bridge_falls_back_from_feature_id_to_pdd_id() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-gap-feature-only",
        type="module.parity.scan.completed",
        payload={
            "feature_id": "FEATURE-ONLY",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].payload["pdd_id"] == "FEATURE-ONLY"
    assert harness.emitted[0].payload["feature_id"] == "FEATURE-ONLY"


def test_module_parity_gap_bridge_prefers_pdd_id_over_feature_id() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-gap-pdd-precedence",
        type="module.parity.scan.completed",
        payload={
            "pdd_id": "PDD-FIRST",
            "feature_id": "FEATURE-SECOND",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_module_parity_scan_completed(event)

    assert harness.emitted[0].payload["pdd_id"] == "PDD-FIRST"
    assert harness.emitted[0].payload["feature_id"] == "FEATURE-SECOND"


def test_module_parity_gap_bridge_keeps_missing_identity_empty() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-gap-no-identity",
        type="module.parity.scan.completed",
        payload={"gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_module_parity_scan_completed(event)

    assert harness.emitted[0].payload["pdd_id"] == ""
    assert harness.emitted[0].payload["feature_id"] == ""


def test_module_parity_gap_bridge_projects_complete_plan_identity_and_propagates_decision() -> None:
    harness = _EmittingBridgeHarness()
    captured: list[ZfEvent] = []
    expected = WorkflowRuntimeDecision(action="dispatch", reason="gap-plan-dispatched")

    def bridge_gap_plan(event: ZfEvent) -> WorkflowRuntimeDecision:
        captured.append(event)
        return expected

    harness._bridge_gap_plan_ready_to_task_map = bridge_gap_plan
    source = ZfEvent(
        id="parity-gap-complete",
        type="module.parity.scan.completed",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-1",
            "feature_id": "FEATURE-1",
            "trace_id": "trace-payload",
            "task_map_ref": "maps/current",
            "gap_plan_ref": "plans/gap-1",
            "source_index_ref": "indexes/source-1",
            "source_commit": "source-commit",
            "candidate_base_commit": "candidate-base",
            "target_ref": "refs/candidate",
            "supersedes_task_ids": ["OLD-1", "", 2],
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is expected
    assert len(captured) == 1
    emitted = captured[0]
    assert emitted.type == "gap_plan.ready"
    assert emitted.actor == "zf-cli"
    assert emitted.causation_id == source.id
    assert emitted.correlation_id == "trace-payload"
    assert emitted.payload == {
        "schema_version": "module-gap-plan.v1",
        "pdd_id": "PDD-1",
        "feature_id": "FEATURE-1",
        "trace_id": "trace-payload",
        "task_map_ref": "maps/current",
        "gap_plan_ref": "plans/gap-1",
        "gap_tasks": [{"task_id": "GAP-1"}],
        "gap_task_count": 1,
        "supersedes_task_ids": ["OLD-1", "2"],
        "source_index_ref": "indexes/source-1",
        "source_commit": "source-commit",
        "candidate_base_commit": "candidate-base",
        "target_ref": "refs/candidate",
        "source_event_id": source.id,
        "source": "module_parity_scan_bridge",
    }


def test_module_parity_gap_bridge_reports_gap_count_in_bridge_reason() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-gap-reason",
        type="module.parity.scan.completed",
        payload={
            "gap_tasks": [{"task_id": "GAP-1"}, {"task_id": "GAP-2"}],
        },
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None
    assert decision.action == "bridge"
    assert decision.reason == "module parity scan produced 2 gap task(s)"


def test_module_parity_gap_bridge_uses_source_commit_for_missing_candidate_base() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-gap-source-fallback",
        type="module.parity.scan.completed",
        payload={
            "source_commit": "source-commit",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    emitted = harness.emitted[0].payload
    assert emitted["candidate_base_commit"] == "source-commit"
    assert emitted["source_commit"] == "source-commit"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"task_map_ref": "maps/direct"}, "maps/direct"),
        ({"base_task_map_ref": "maps/base"}, "maps/base"),
        ({"supersedes_task_map_ref": "maps/superseded"}, "maps/superseded"),
        ({}, ""),
    ],
)
def test_module_parity_gap_bridge_task_map_ref_fallbacks(
    payload: dict,
    expected: str,
) -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id=f"parity-task-map-{expected or 'empty'}",
        type="module.parity.scan.completed",
        payload={**payload, "gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["task_map_ref"] == expected


def test_module_parity_gap_bridge_task_map_ref_precedence_when_multiple_refs_exist() -> None:
    """Direct ref wins, then base, then superseded map; precedence is observable."""
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-task-map-multiple",
        type="module.parity.scan.completed",
        payload={
            "task_map_ref": "",
            "base_task_map_ref": "maps/base",
            "supersedes_task_map_ref": "maps/superseded",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["task_map_ref"] == "maps/base"


def test_module_parity_gap_bridge_uses_superseded_ref_when_other_refs_empty() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-task-map-superseded-only",
        type="module.parity.scan.completed",
        payload={
            "task_map_ref": "",
            "base_task_map_ref": "",
            "supersedes_task_map_ref": "maps/superseded",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["task_map_ref"] == "maps/superseded"


def test_module_parity_gap_bridge_defaults_optional_refs_to_empty_strings() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-gap-empty-refs",
        type="module.parity.scan.completed",
        payload={"gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_module_parity_scan_completed(source)

    emitted = harness.emitted[0].payload
    assert emitted["gap_plan_ref"] == ""
    assert emitted["source_index_ref"] == ""
    assert emitted["source_commit"] == ""
    assert emitted["candidate_base_commit"] == ""
    assert emitted["target_ref"] == ""


def test_module_parity_blocked_projection_has_canonical_identity_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-blocked-projection",
        type="module.parity.scan.completed",
        correlation_id="trace-blocked",
        payload={
            "pdd_id": "PDD-BLOCKED",
            "feature_id": "FEATURE-BLOCKED",
            "task_map_ref": "maps/blocked",
            "open_p0_p1_gap_count": 2,
        },
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None and decision.action == "block"
    blocked = harness.emitted[0]
    assert blocked.type == "module.parity.blocked"
    assert blocked.actor == "zf-cli"
    assert blocked.causation_id == source.id
    assert blocked.correlation_id == "trace-blocked"
    assert blocked.payload == {
        "pdd_id": "PDD-BLOCKED",
        "feature_id": "FEATURE-BLOCKED",
        "trace_id": "trace-blocked",
        "task_map_ref": "maps/blocked",
        "open_p0_p1_gap_count": 2,
        "reason": "module parity scan reported open gaps without gap_tasks",
        "source_event_id": source.id,
        "source": "module_parity_scan_bridge",
    }


def test_module_parity_closure_projects_goal_and_target_identity() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closure-identity",
        type="module.parity.scan.completed",
        payload={
            "pdd_id": "PDD-1",
            "feature_id": "FEATURE-1",
            "goal_id": "GOAL-1",
            "task_map_ref": "maps/current",
            "candidate_ref": "refs/candidate",
            "target_ref": "refs/target",
            "candidate_head_commit": "candidate-commit",
            "status": "completed",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    emitted = harness.emitted[0]
    assert emitted.type == "module.parity.closed"
    assert emitted.payload["goal_id"] == "GOAL-1"
    assert emitted.payload["candidate_ref"] == "refs/candidate"
    assert emitted.payload["target_ref"] == "refs/target"
    assert emitted.payload["candidate_head_commit"] == "candidate-commit"
    assert emitted.payload["target_commit"] == "candidate-commit"


def test_module_parity_closure_falls_back_to_candidate_ref_when_target_ref_missing() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closure-target-fallback",
        type="module.parity.scan.completed",
        payload={
            "candidate_ref": "refs/candidate-only",
            "status": "completed",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["target_ref"] == "refs/candidate-only"


def test_module_parity_closure_projection_has_canonical_schema_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closure-schema",
        type="module.parity.scan.completed",
        payload={"pdd_id": "PDD-CLOSED", "open_p0_p1_gap_count": 0},
    )

    harness._bridge_module_parity_scan_completed(source)

    closed = harness.emitted[0]
    assert closed.type == "module.parity.closed"
    assert closed.causation_id == source.id
    assert closed.payload["schema_version"] == "module-parity-closed.v1"
    assert closed.payload["pdd_id"] == "PDD-CLOSED"
    assert closed.payload["feature_id"] == "PDD-CLOSED"
    assert closed.payload["goal_id"] == "PDD-CLOSED"
    assert closed.payload["flow_kind"] == "refactor"
    assert closed.payload["open_p0_p1_gap_count"] == 0
    assert closed.payload["source"] == "module_parity_scan_bridge"


def test_module_parity_closure_defaults_optional_identity_fields_without_invention() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closure-empty-identity",
        type="module.parity.scan.completed",
        payload={
            "pdd_id": "PDD-ONLY",
            "feature_id": "FEATURE-ONLY",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    payload = harness.emitted[0].payload
    assert payload["goal_id"] == "FEATURE-ONLY"
    assert payload["candidate_ref"] == ""
    assert payload["target_ref"] == ""


def test_module_parity_closure_uses_feature_identity_when_goal_id_is_missing() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closure-feature-goal",
        type="module.parity.scan.completed",
        payload={
            "pdd_id": "PDD-1",
            "feature_id": "FEATURE-1",
            "open_p0_p1_gap_count": 0,
        },
    )

    harness._bridge_module_parity_scan_completed(source)

    assert harness.emitted[0].payload["goal_id"] == "FEATURE-1"


def test_flow_discovery_gap_bridge_preserves_candidate_and_commit_fallbacks() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-candidate-fallbacks",
        type="flow.discovery.completed",
        payload={
            "candidate_ref": "candidate/ref",
            "target_commit": "target-commit",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_flow_discovery_completed(event)

    emitted = harness.emitted[0].payload
    assert emitted["candidate_ref"] == "candidate/ref"
    assert emitted["target_ref"] == "candidate/ref"
    assert emitted["candidate_head_commit"] == "target-commit"
    assert emitted["source_commit"] == "target-commit"
    assert emitted["candidate_base_commit"] == "target-commit"
    assert emitted["dispatch_base_commit"] == "target-commit"


def test_flow_discovery_gap_bridge_preserves_explicit_target_and_source_values() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-explicit-values",
        type="flow.discovery.completed",
        payload={
            "candidate_ref": "candidate/ref",
            "target_ref": "target/ref",
            "candidate_head_commit": "head-commit",
            "source_commit": "source-commit",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_flow_discovery_completed(event)

    emitted = harness.emitted[0].payload
    assert emitted["target_ref"] == "target/ref"
    assert emitted["candidate_head_commit"] == "head-commit"
    assert emitted["source_commit"] == "source-commit"


def test_flow_discovery_gap_bridge_propagates_each_reference_group() -> None:
    harness = _EmittingBridgeHarness()
    refs = {
        key: [f"{key}-1"]
        for key in (
            "artifact_refs",
            "evidence_refs",
            "test_refs",
            "e2e_refs",
            "demo_refs",
            "regression_refs",
            "parity_refs",
            "provider_refs",
        )
    }
    event = ZfEvent(
        id="flow-gap-all-ref-groups",
        type="flow.discovery.completed",
        payload={**refs, "gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_flow_discovery_completed(event)

    emitted = harness.emitted[0].payload
    for key, value in refs.items():
        assert emitted[key] == value


def test_flow_discovery_gap_bridge_emits_canonical_payload_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="flow-gap-canonical",
        type="flow.discovery.completed",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-CANONICAL",
            "feature_id": "FEATURE-CANONICAL",
            "goal_id": "GOAL-CANONICAL",
            "goal_kind": "issue",
            "flow_kind": "issue",
            "workflow_run_id": "run-canonical",
            "task_map_ref": "maps/canonical",
            "gap_plan_ref": "plans/canonical",
            "candidate_ref": "candidate/canonical",
            "target_ref": "target/canonical",
            "candidate_head_commit": "head-canonical",
            "source_commit": "source-canonical",
            "candidate_base_commit": "base-canonical",
            "dispatch_base_commit": "dispatch-canonical",
            "gap_category": "acceptance",
            "discovery_profile": "issue_scan",
            "artifact_refs": ["artifact/canonical"],
            "gap_tasks": [{"task_id": "GAP-CANONICAL"}],
            "supersedes_task_ids": ["OLD-CANONICAL"],
        },
    )

    decision = harness._bridge_flow_discovery_completed(source)

    assert decision is not None and decision.action == "bridge"
    emitted = harness.emitted[0]
    assert emitted.type == "flow.gap_plan.ready"
    assert emitted.actor == "zf-cli"
    assert emitted.causation_id == source.id
    assert emitted.correlation_id == "trace-correlation"
    assert emitted.payload == {
        "schema_version": "goal-gap-plan.v1",
        "pdd_id": "PDD-CANONICAL",
        "feature_id": "FEATURE-CANONICAL",
        "goal_id": "GOAL-CANONICAL",
        "goal_kind": "issue",
        "flow_kind": "issue",
        "workflow_run_id": "run-canonical",
        "gap_category": "acceptance",
        "trace_id": "trace-correlation",
        "task_map_ref": "maps/canonical",
        "gap_plan_ref": "plans/canonical",
        "candidate_ref": "candidate/canonical",
        "target_ref": "target/canonical",
        "candidate_head_commit": "head-canonical",
        "source_commit": "source-canonical",
        "candidate_base_commit": "base-canonical",
        "dispatch_base_commit": "dispatch-canonical",
        "gap_tasks": [{"task_id": "GAP-CANONICAL"}],
        "gap_task_count": 1,
        "supersedes_task_ids": ["OLD-CANONICAL"],
        "artifact_refs": ["artifact/canonical"],
        "source_event_id": source.id,
        "source": "flow_discovery_bridge",
    }


def test_flow_discovery_gap_bridge_rejects_non_list_reference_groups() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-non-list-ref",
        type="flow.discovery.completed",
        payload={
            "artifact_refs": "not-a-list",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_flow_discovery_completed(event)

    assert "artifact_refs" not in harness.emitted[0].payload


def test_flow_discovery_gap_bridge_preserves_candidate_base_and_dispatch_commits() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-commit-propagation",
        type="flow.discovery.completed",
        payload={
            "candidate_ref": "candidate/ref",
            "candidate_head_commit": "head",
            "source_commit": "source",
            "candidate_base_commit": "base",
            "dispatch_base_commit": "dispatch",
            "gap_tasks": [{"task_id": "GAP-1"}],
        },
    )

    harness._bridge_flow_discovery_completed(event)

    emitted = harness.emitted[0].payload
    assert emitted["candidate_base_commit"] == "base"
    assert emitted["dispatch_base_commit"] == "dispatch"


def test_flow_discovery_gap_bridge_does_not_invent_missing_candidate_or_commit() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-gap-empty-values",
        type="flow.discovery.completed",
        payload={"gap_tasks": [{"task_id": "GAP-1"}]},
    )

    harness._bridge_flow_discovery_completed(event)

    emitted = harness.emitted[0].payload
    assert emitted["candidate_ref"] == ""
    assert emitted["target_ref"] == ""
    assert emitted["candidate_head_commit"] == ""
    assert emitted["source_commit"] == ""


def test_flow_discovery_completion_blocks_open_gaps_without_gap_tasks() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-open-gaps-no-tasks",
        type="flow.discovery.completed",
        payload={"open_p0_p1_gap_count": 2},
    )

    decision = harness._bridge_flow_discovery_completed(event)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "flow.goal.blocked"
    assert decision.reason == "flow discovery missing gap_tasks for open gaps"


@pytest.mark.parametrize("count_key", ["open_p0_p1_gap_count", "open_gap_count", "gap_task_count"])
def test_flow_discovery_completion_honors_all_open_gap_count_aliases(count_key: str) -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id=f"flow-open-gap-{count_key}",
        type="flow.discovery.completed",
        payload={count_key: 2},
    )

    decision = harness._bridge_flow_discovery_completed(event)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "flow.goal.blocked"
    assert decision.reason == "flow discovery missing gap_tasks for open gaps"
    if count_key == "open_p0_p1_gap_count":
        assert harness.emitted[0].payload["open_p0_p1_gap_count"] == 2


def test_flow_discovery_blocked_projection_is_canonical() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="flow-blocked-canonical",
        type="flow.discovery.completed",
        correlation_id="trace-blocked-canonical",
        payload={
            "pdd_id": "PDD-BLOCKED-CANONICAL",
            "feature_id": "FEATURE-BLOCKED-CANONICAL",
            "flow_kind": "issue",
            "task_map_ref": "maps/blocked-canonical",
            "open_p0_p1_gap_count": 2,
        },
    )

    decision = harness._bridge_flow_discovery_completed(source)

    assert decision is not None and decision.action == "block"
    blocked = harness.emitted[0]
    assert blocked.type == "flow.goal.blocked"
    assert blocked.actor == "zf-cli"
    assert blocked.causation_id == source.id
    assert blocked.correlation_id == "trace-blocked-canonical"
    assert blocked.payload == {
        "schema_version": "flow-goal-blocked.v1",
        "pdd_id": "PDD-BLOCKED-CANONICAL",
        "feature_id": "FEATURE-BLOCKED-CANONICAL",
        "trace_id": "trace-blocked-canonical",
        "flow_kind": "issue",
        "task_map_ref": "maps/blocked-canonical",
        "open_p0_p1_gap_count": 2,
        "reason": "flow discovery reported open gaps without gap_tasks",
        "source_event_id": source.id,
        "source": "flow_discovery_bridge",
    }


def test_flow_discovery_completion_closes_only_explicitly_closed_payload() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-closed-explicit",
        type="flow.discovery.completed",
        payload={"status": "completed", "open_p0_p1_gap_count": 0},
    )

    decision = harness._bridge_flow_discovery_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert decision.reason == "flow discovery closed without open P0/P1 gaps"
    assert harness.emitted[0].type == "flow.goal.closed"


def test_flow_discovery_completion_blocks_ambiguous_payload_without_closure() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="flow-ambiguous",
        type="flow.discovery.completed",
        payload={},
    )

    decision = harness._bridge_flow_discovery_completed(event)

    assert decision is not None and decision.action == "block"
    assert decision.reason == "flow discovery lacks closure/gap evidence"
    blocked = harness.emitted[0]
    assert blocked is not None
    assert blocked.type == "flow.goal.blocked"
    assert blocked.actor == "zf-cli"
    assert blocked.causation_id == event.id
    assert blocked.correlation_id == event.id
    assert blocked.payload == {
        "schema_version": "flow-goal-blocked.v1",
        "pdd_id": "",
        "feature_id": "",
        "trace_id": event.id,
        "flow_kind": "",
        "task_map_ref": "",
        "reason": "flow discovery completed without explicit closure or dispatchable gap_tasks",
        "source_event_id": event.id,
        "source": "flow_discovery_bridge",
    }


def test_module_parity_completion_blocks_open_gaps_without_gap_tasks() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-open-gaps-no-tasks",
        type="module.parity.scan.completed",
        payload={"open_p0_p1_gap_count": 2},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "module.parity.blocked"


@pytest.mark.parametrize("count", [1, 2])
def test_module_parity_completion_blocks_any_positive_open_gap_count_without_tasks(
    count: int,
) -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id=f"parity-blocked-positive-{count}",
        type="module.parity.scan.completed",
        payload={"open_p0_p1_gap_count": count},
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "module.parity.blocked"
    assert harness.emitted[0].payload["open_p0_p1_gap_count"] == count


@pytest.mark.parametrize("count_key", ["open_p0_p1_gap_count", "gap_task_count"])
def test_module_parity_completion_honors_all_open_gap_count_aliases(count_key: str) -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id=f"parity-open-gap-{count_key}",
        type="module.parity.scan.completed",
        payload={count_key: 2},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].type == "module.parity.blocked"
    assert decision.reason == "module parity scan missing gap_tasks for open gaps"
    if count_key == "open_p0_p1_gap_count":
        assert harness.emitted[0].payload["open_p0_p1_gap_count"] == 2


def test_module_parity_blocked_output_preserves_event_envelope_and_provenance() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-blocked-envelope",
        type="module.parity.scan.completed",
        correlation_id="source-correlation",
        payload={
            "pdd_id": "PDD-BLOCKED",
            "feature_id": "FEATURE-BLOCKED",
            "trace_id": "trace-blocked",
            "task_map_ref": "maps/blocked",
            "open_p0_p1_gap_count": 1,
        },
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None and decision.action == "block"
    blocked = harness.emitted[0]
    assert blocked.type == "module.parity.blocked"
    assert blocked.actor == "zf-cli"
    assert blocked.causation_id == source.id
    assert blocked.correlation_id == "trace-blocked"
    assert blocked.payload == {
        "pdd_id": "PDD-BLOCKED",
        "feature_id": "FEATURE-BLOCKED",
        "trace_id": "trace-blocked",
        "task_map_ref": "maps/blocked",
        "open_p0_p1_gap_count": 1,
        "reason": "module parity scan reported open gaps without gap_tasks",
        "source_event_id": source.id,
        "source": "module_parity_scan_bridge",
    }


def test_module_parity_completion_closes_explicitly_closed_payload() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-closed-explicit",
        type="module.parity.scan.completed",
        payload={"status": "completed", "open_p0_p1_gap_count": 0},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "module.parity.closed"
    assert harness.closure_calls[0]["flow_kind"] == "refactor"


def test_module_parity_completion_closed_envelope_preserves_identity_and_parity_fields() -> None:
    harness = _EmittingBridgeHarness()
    source = ZfEvent(
        id="parity-closed-envelope",
        type="module.parity.scan.completed",
        correlation_id="parity-correlation",
        payload={
            "pdd_id": "PDD-CLOSED",
            "feature_id": "FEATURE-CLOSED",
            "goal_id": "GOAL-CLOSED",
            "trace_id": "trace-closed",
            "task_map_ref": "maps/closed",
            "candidate_ref": "refs/candidate",
            "target_ref": "refs/target",
            "candidate_head_commit": "head-closed",
            "task_map_generation": "generation-closed",
            "plan_artifact_package_id": "package-closed",
            "plan_artifact_package_ref": "packages/closed",
            "plan_artifact_package_digest": "sha256:closed",
            "status": "completed",
            "open_p0_p1_gap_count": 0,
        },
    )

    decision = harness._bridge_module_parity_scan_completed(source)

    assert decision is not None and decision.action == "bridge"
    assert decision.reason == "module parity scan closed without open P0/P1 gaps"
    closed = harness.emitted[0]
    assert closed.type == "module.parity.closed"
    assert closed.causation_id == source.id
    assert closed.payload == {
        "schema_version": "module-parity-closed.v1",
        "pdd_id": "PDD-CLOSED",
        "feature_id": "FEATURE-CLOSED",
        "goal_id": "GOAL-CLOSED",
        "trace_id": "trace-closed",
        "flow_kind": "refactor",
        "task_map_ref": "maps/closed",
        "candidate_ref": "refs/candidate",
        "target_ref": "refs/target",
        "task_map_generation": "generation-closed",
        "candidate_head_commit": "head-closed",
        "target_commit": "head-closed",
        "plan_artifact_package_id": "package-closed",
        "plan_artifact_package_ref": "packages/closed",
        "plan_artifact_package_digest": "sha256:closed",
        "open_p0_p1_gap_count": 0,
        "source": "module_parity_scan_bridge",
    }


def test_module_parity_completion_closes_explicit_status_without_count() -> None:
    """An explicit terminal status is sufficient when no count is supplied."""
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-closed-no-count",
        type="module.parity.scan.completed",
        payload={"status": "completed"},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "module.parity.closed"


def test_module_parity_completion_closes_zero_count_without_status() -> None:
    """A canonical zero count closes even when no status alias is present."""
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-closed-zero-count",
        type="module.parity.scan.completed",
        payload={"open_p0_p1_gap_count": 0},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "bridge"
    assert harness.emitted[0].type == "module.parity.closed"


def test_module_parity_completion_blocks_ambiguous_payload_without_closure() -> None:
    harness = _EmittingBridgeHarness()
    event = ZfEvent(
        id="parity-ambiguous",
        type="module.parity.scan.completed",
        payload={},
    )

    decision = harness._bridge_module_parity_scan_completed(event)

    assert decision is not None and decision.action == "block"
    assert decision.reason == "module parity scan lacks closure/gap evidence"
    assert harness.emitted[0].type == "module.parity.blocked"


def test_judge_evidence_gate_blocks_missing_quality_floor_refs() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    event = ZfEvent(
        id="judge-missing-repro",
        type="judge.passed",
        task_id="TASK-ISSUE-1",
        payload={"feature_id": "ISSUE-1"},
    )

    decision = harness._reject_flow_judge_evidence_gap(event)

    assert decision is not None and decision.action == "block"
    blocked = harness.emitted[0]
    assert blocked.type == "flow.goal.blocked"
    assert blocked.payload["pdd_id"] == "ISSUE-1"
    assert blocked.payload["feature_id"] == "ISSUE-1"
    assert blocked.payload["missing_ref_groups"] == [[
        "repro_ref",
        "regression_refs",
        "test_refs",
    ]]


def test_judge_evidence_gate_uses_flow_kind_scoped_metadata_from_payload() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata_by_kind = {
        "issue": {"quality_floor": "issue-regression"},
    }
    event = ZfEvent(
        id="judge-scoped-metadata",
        type="judge.passed",
        payload={"flow_kind": "issue", "feature_id": "ISSUE-SCOPED"},
    )

    decision = harness._reject_flow_judge_evidence_gap(event)

    assert decision is not None and decision.action == "block"


def test_judge_evidence_gate_noops_without_quality_floor() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {}
    event = ZfEvent(
        id="judge-no-quality-floor",
        type="judge.passed",
        payload={"feature_id": "ISSUE-NO-FLOOR"},
    )

    assert harness._reject_flow_judge_evidence_gap(event) is None
    assert harness.emitted == []


def test_judge_evidence_gate_uses_custom_ref_groups_and_reports_all_missing_groups() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {
        "quality_floor": "issue-regression",
        "quality_floor_ref_groups": [
            ["custom_repro", "custom_test"],
            ["custom_e2e"],
        ],
    }
    event = ZfEvent(
        id="judge-custom-groups",
        type="judge.passed",
        payload={"feature_id": "ISSUE-CUSTOM"},
    )

    decision = harness._reject_flow_judge_evidence_gap(event)

    assert decision is not None and decision.action == "block"
    assert harness.emitted[0].payload["missing_ref_groups"] == [
        ["custom_repro", "custom_test"],
        ["custom_e2e"],
    ]


def test_judge_evidence_gate_deduplicates_existing_blocked_output() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    source = ZfEvent(
        id="judge-dedup",
        type="judge.passed",
        payload={"feature_id": "ISSUE-DEDUP"},
    )
    harness.event_log = SimpleNamespace(
        read_all=lambda: [
            ZfEvent(
                id="existing-blocked",
                type="flow.goal.blocked",
                payload={"source_event_id": source.id},
            ),
        ],
    )
    harness._has_bridge_output = ModuleParityBridgeMixin._has_bridge_output.__get__(
        harness,
        type(harness),
    )

    decision = harness._reject_flow_judge_evidence_gap(source)

    assert decision is not None and decision.action == "block"
    assert harness.emitted == []


def test_judge_evidence_gate_preserves_identity_and_trace_precedence() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    source = ZfEvent(
        id="judge-identity",
        type="judge.passed",
        task_id="TASK-FALLBACK",
        correlation_id="trace-correlation",
        payload={
            "pdd_id": "PDD-JUDGE",
            "feature_id": "ISSUE-JUDGE",
            "trace_id": "trace-payload",
        },
    )

    harness._reject_flow_judge_evidence_gap(source)

    blocked = harness.emitted[0]
    assert blocked.correlation_id == "trace-payload"
    assert blocked.payload["pdd_id"] == "PDD-JUDGE"
    assert blocked.payload["feature_id"] == "ISSUE-JUDGE"
    assert blocked.payload["trace_id"] == "trace-payload"


def test_judge_evidence_gate_projects_canonical_blocked_event() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "quality_floor": "issue-regression",
        "evidence_policy": "repro-and-regression",
    }
    source = ZfEvent(
        id="judge-canonical-blocked",
        type="judge.passed",
        correlation_id="trace-canonical",
        payload={
            "pdd_id": "PDD-CANONICAL",
            "feature_id": "ISSUE-CANONICAL",
            "trace_id": "trace-payload",
            "task_map_ref": "maps/judge",
        },
    )

    decision = harness._reject_flow_judge_evidence_gap(source)

    assert decision is not None and decision.action == "block"
    assert decision.reason == (
        "judge.passed missing issue-regression evidence refs: "
        "[['repro_ref', 'regression_refs', 'test_refs']]"
    )
    blocked = harness.emitted[0]
    assert blocked.type == "flow.goal.blocked"
    assert blocked.actor == "zf-cli"
    assert blocked.causation_id == source.id
    assert blocked.correlation_id == "trace-payload"
    assert blocked.payload == {
        "schema_version": "flow-goal-blocked.v1",
        "pdd_id": "PDD-CANONICAL",
        "feature_id": "ISSUE-CANONICAL",
        "trace_id": "trace-payload",
        "flow_kind": "issue",
        "quality_floor": "issue-regression",
        "evidence_policy": "repro-and-regression",
        "reason": "judge.passed missing required flow evidence refs",
        "missing_ref_groups": [["repro_ref", "regression_refs", "test_refs"]],
        "expected_downstream_events": [
            "flow.gap_plan.ready",
            "goal.gap_plan.ready",
            "flow.goal.closed",
        ],
        "task_map_ref": "maps/judge",
        "source_event_id": source.id,
        "source": "flow_judge_evidence_gate",
    }


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason="ZF-TEST-059: flow_metadata_for assumes judge payload is a mapping",
)
def test_judge_evidence_gate_normalizes_non_mapping_payload_fail_closed() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    event = ZfEvent(
        id="judge-non-mapping-payload",
        type="judge.passed",
        payload=["not", "a", "mapping"],  # type: ignore[arg-type]
    )

    decision = harness._reject_flow_judge_evidence_gap(event)

    assert decision is not None and decision.action == "block"


def test_judge_evidence_gate_uses_latest_context_for_identity_fallback() -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    harness.event_log = SimpleNamespace(read_all=lambda: [ZfEvent(
        id="context-candidate",
        type="candidate.ready",
        payload={"feature_id": "ISSUE-CONTEXT"},
    )])
    event = ZfEvent(
        id="judge-context-fallback",
        type="judge.passed",
        payload={},
    )

    harness._reject_flow_judge_evidence_gap(event)

    assert harness.emitted[0].payload["pdd_id"] == "ISSUE-CONTEXT"


@pytest.mark.parametrize("ref_key", ["repro_ref", "regression_refs", "test_refs"])
def test_judge_evidence_gate_accepts_any_issue_regression_ref(ref_key: str) -> None:
    harness = _EmittingBridgeHarness()
    harness.config.workflow.flow_metadata = {"quality_floor": "issue-regression"}
    event = ZfEvent(
        id=f"judge-ref-{ref_key}",
        type="judge.passed",
        payload={ref_key: ["evidence/ref"] if ref_key.endswith("_refs") else "evidence/ref"},
    )

    assert harness._reject_flow_judge_evidence_gap(event) is None
    assert harness.emitted == []
