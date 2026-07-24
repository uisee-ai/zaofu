from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    GitIsolationConfig,
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowAdmissionReplanConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.contract_validation import validate_task_contract
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_fanout import _writer_task_dependencies_satisfied
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.candidates import CandidateRebuilder, CandidateTask
from zf.runtime.light_flow import synthesize_light_task_map
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot,
    task_map_generation,
    write_task_contract_snapshot,
)
from zf.runtime.writer_fanout_data import WriterFanoutDataMixin


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


class _FanoutPayloadProbe(WriterFanoutDataMixin):
    def _fanout_child_payloads(self, manifest: dict) -> list[dict]:
        return [
            dict(child.get("payload") or {})
            for child in manifest.get("children", [])
            if isinstance(child, dict)
        ]


def test_writer_dependency_waits_for_canonical_task_terminal() -> None:
    class Store:
        status = "review"

        def get(self, task_id):  # noqa: ANN001
            assert task_id == "TASK-UPSTREAM"
            return SimpleNamespace(status=self.status)

    store = Store()
    task_item = {"depends_on": ["TASK-UPSTREAM"]}

    assert not _writer_task_dependencies_satisfied(store, task_item)
    assert _writer_task_dependencies_satisfied(
        store,
        task_item,
        completed_task_ids={"TASK-UPSTREAM"},
    )
    store.status = "done"
    assert _writer_task_dependencies_satisfied(store, task_item)


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config(
    state_dir: Path,
    *,
    quality_gates: dict[str, QualityGateConfig] | None = None,
    dev_skills: bool = False,
    trigger: str = "task_map.ready",
    task_map: str = ".zf/artifacts/${pdd_id}/task_map.json",
    affinity_stage_slots: bool = False,
    synthesize_canonical: bool = False,
    include_orchestrator: bool = False,
    harness_profile: str = "baseline",
    affinity_lane_count: int = 2,
    resynth_trigger: str = "",
) -> ZfConfig:
    writer_skills = ["zf-harness-state-sync"] if dev_skills else []
    writer_count = affinity_lane_count if affinity_stage_slots else 2
    assignment = (
        FanoutAssignmentConfig(
            strategy="affinity_stage_slots",
            lane_profile="refactor-2",
            stage_slot="impl",
        )
        if affinity_stage_slots
        else FanoutAssignmentConfig()
    )
    affinity_lanes = (
        {
            "refactor-2": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id=f"lane{index}",
                        impl=f"dev-{index + 1}",
                        review=f"review-{index + 1}",
                        verify=f"test-{index + 1}",
                    )
                    for index in range(affinity_lane_count)
                ],
            ),
        }
        if affinity_stage_slots
        else {}
    )
    roles = [
        RoleConfig(
            name="supervisor",
            instance_id="supervisor",
            backend="mock",
            role_kind="reader",
            publishes=["task_map.ready"],
        ),
        *[
            RoleConfig(
                name="dev",
                instance_id=f"dev-{index + 1}",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done", "dev.blocked"],
                skills=writer_skills,
            )
            for index in range(writer_count)
        ],
    ]
    if include_orchestrator:
        roles.insert(0, RoleConfig(
            name="orchestrator",
            instance_id="orchestrator",
            backend="mock",
            triggers=["dev.build.done"],
        ))

    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=roles,
        workflow=WorkflowConfig(
            harness_profile=harness_profile,
            stages=[
                WorkflowStageConfig(
                    id="dev-fanout",
                    trigger=trigger,
                    topology="fanout_writer_scoped",
                    roles=[f"dev-{index + 1}" for index in range(writer_count)],
                    task_map=task_map,
                    assignment=assignment,
                    synthesize_canonical_tasks=synthesize_canonical,
                    aggregate=FanoutAggregateConfig(
                        mode="candidate_integration",
                        success_event="candidate.ready",
                        failure_event="integration.failed",
                    ),
                ),
            ],
            affinity_lanes=affinity_lanes,
            admission_replan=WorkflowAdmissionReplanConfig(
                enabled=bool(resynth_trigger),
                resynth_trigger=resynth_trigger,
            ),
        ),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
        quality_gates=quality_gates or {},
    )


def _state(
    tmp_path: Path,
    *,
    quality_gates: dict[str, QualityGateConfig] | None = None,
    dev_skills: bool = False,
    trigger: str = "task_map.ready",
    task_map_template: str = ".zf/artifacts/${pdd_id}/task_map.json",
    affinity_stage_slots: bool = False,
    synthesize_canonical: bool = False,
    include_orchestrator: bool = False,
    harness_profile: str = "baseline",
    affinity_lane_count: int = 2,
    resynth_trigger: str = "",
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "core",
                "affinity_tag": "pi-core",
                "allowed_paths": ["a.txt"],
                "summary": "SCOPE GUARD: pure extraction only, do not add behavior.",
                "acceptance": ["a.txt exists", "no behavior change"],
                "verification": "test -f a.txt",
                "payload": {
                    "instruction": "Create a.txt with TASK-1 smoke content.",
                },
            },
            {
                "task_id": "TASK-2",
                "scope": "runtime",
                "affinity_tag": "gateway",
                "allowed_paths": ["b.txt"],
            },
        ],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _config(
            state_dir,
            quality_gates=quality_gates,
            dev_skills=dev_skills,
            trigger=trigger,
            task_map=task_map_template,
            affinity_stage_slots=affinity_stage_slots,
            synthesize_canonical=synthesize_canonical,
            include_orchestrator=include_orchestrator,
            harness_profile=harness_profile,
            affinity_lane_count=affinity_lane_count,
            resynth_trigger=resynth_trigger,
        ),
        transport,
    )  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def _start(orch: Orchestrator) -> None:
    orch.run_once(events=[ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )])


def _seed_tasks(
    state_dir: Path,
    *,
    task_map_ref: str = ".zf/artifacts/F-11111111/task_map.json",
    task_ids: tuple[str, ...] = ("TASK-1", "TASK-2"),
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    for task_id in task_ids:
        store.add(Task(
            id=task_id,
            title=task_id,
            status="backlog",
            contract=TaskContract(
                feature_id="F-11111111",
                evidence_contract={"source_refs": {"task_map_ref": task_map_ref}},
            ),
        ))


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


def _fanout_id(log: EventLog) -> str:
    return next(event.payload["fanout_id"] for event in log.read_all()
                if event.type == "fanout.started")


def _archive_day(days_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d")


def _child(manifest: dict, task_id: str) -> dict:
    return next(child for child in manifest["children"]
                if child["task_id"] == task_id)


def _commit(workdir: Path, file_name: str, content: str, message: str) -> str:
    (workdir / file_name).write_text(content, encoding="utf-8")
    _git(workdir, "add", file_name)
    _git(workdir, "commit", "-q", "-m", message)
    return _git(workdir, "rev-parse", "HEAD")


def test_generic_fanout_success_payload_preserves_inventory_refs() -> None:
    payload = _FanoutPayloadProbe()._generic_fanout_success_payload(
        manifest={
            "fanout_id": "fanout-scan",
            "children": [{
                "child_id": "scan-contract",
                "report_path": "docs/plans/scan-contract.json",
                "payload": {
                    "inventory_refs": [
                        "docs/plans/hermes-tool-inventory.json",
                    ],
                    "hermes_source_inventory_ref": (
                        "docs/plans/hermes-source-inventory.json"
                    ),
                    "inventory_coverage_matrix_ref": (
                        "docs/plans/hermes-inventory-coverage-matrix.json"
                    ),
                    "evidence_refs": ["docs/plans/scan-contract.md"],
                },
            }],
        },
        success_event="zaofu.refactor.review.ready",
    )

    assert payload["inventory_refs"] == [
        "docs/plans/hermes-tool-inventory.json",
    ]
    assert payload["source_inventory_ref"] == (
        "docs/plans/hermes-source-inventory.json"
    )
    assert "hermes_source_inventory_ref" not in payload
    assert payload["inventory_coverage_matrix_ref"] == (
        "docs/plans/hermes-inventory-coverage-matrix.json"
    )
    assert "docs/plans/hermes-tool-inventory.json" in payload["artifact_refs"]
    assert "docs/plans/hermes-source-inventory.json" in payload["artifact_refs"]
    assert (
        "docs/plans/hermes-inventory-coverage-matrix.json"
        in payload["evidence_refs"]
    )


def test_writer_fanout_requires_goal_claim_set_admission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    monkeypatch.setattr(orch, "_pin_goal_claim_set", lambda _event: False)

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.started"]
    assert not [
        sent for sent in transport.sent if sent[0].startswith("dev-")
    ]
    assert all(
        TaskStore(state_dir / "kanban.json").get(task_id).status == "blocked"
        for task_id in ("TASK-1", "TASK-2")
    )
    cancelled = [event for event in events if event.type == "fanout.cancelled"]
    assert cancelled
    assert cancelled[-1].payload["reason"] == "goal_claim_set_pin_failed"


def test_goal_claim_set_is_pinned_before_writer_fanout_starts(tmp_path: Path) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    event_types = [event.type for event in log.read_all()]
    assert "goal.claim_set.pinned" in event_types
    assert event_types.index("goal.claim_set.pinned") < event_types.index(
        "fanout.started"
    )


def test_fanout_success_payload_preserves_workflow_request_identity() -> None:
    payload = _FanoutPayloadProbe()._fanout_flow_identity_payload(
        {
            "fanout_id": "fanout-issue",
            "trigger_payload": {
                "request_id": "req-001",
                "workflow_run_id": "run-001",
                "flow_kind": "issue",
                "workflow_request_ref": "requests/req-001.json",
                "requirement_spec_ref": "requests/req-001-rev-2.json",
                "requirement_spec_digest": "sha256:abc123",
                "request_revision": 2,
            },
            "children": [{
                "child_id": "issue-plan",
                "payload": {"evidence_refs": ["docs/plans/issue.md"]},
            }],
        }
    )

    assert payload["request_id"] == "req-001"
    assert payload["run_id"] == "run-001"
    assert payload["workflow_run_id"] == "run-001"
    assert payload["flow_kind"] == "issue"
    assert payload["workflow_request_ref"] == "requests/req-001.json"
    assert payload["requirement_spec_ref"] == "requests/req-001-rev-2.json"
    assert payload["requirement_spec_digest"] == "sha256:abc123"
    assert payload["request_revision"] == 2


def test_fanout_success_payload_does_not_promote_child_run_to_workflow() -> None:
    payload = _FanoutPayloadProbe()._fanout_flow_identity_payload(
        {
            "fanout_id": "fanout-issue",
            "trace_id": "workflow-parent-001",
            "trigger_payload": {
                "trace_id": "workflow-parent-001",
                "flow_kind": "issue",
            },
            "children": [{
                "child_id": "issue-verify",
                "run_id": "run-fanout-issue-verify",
                "payload": {"run_id": "run-fanout-issue-verify"},
            }],
        }
    )

    assert payload["run_id"] == "workflow-parent-001"
    assert payload["workflow_run_id"] == "workflow-parent-001"


def test_writer_fanout_missing_kanban_tasks_fail_closed(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)

    _start(orch)

    events = log.read_all()
    cancelled = [event for event in events if event.type == "fanout.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "missing_kanban_tasks"
    assert cancelled[0].payload["missing_task_ids"] == ["TASK-1", "TASK-2"]
    assert transport.sent == []
    assert not [event for event in events if event.type == "fanout.child.dispatched"]


def test_writer_fanout_required_contract_rejects_before_dispatch(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    orch.config.workflow.candidate_quality_source = "task_contract_required"

    _start(orch)

    events = log.read_all()
    cancelled = [event for event in events if event.type == "fanout.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["failure_scope"] == "plan_admission"
    assert "task_contract_required" in cancelled[0].payload["reason"]
    assert "TASK-2" in cancelled[0].payload["reason"]
    assert not [sent for sent in transport.sent if sent[0].startswith("dev-")]
    assert not [event for event in events if event.type == "fanout.started"]
    assert not [event for event in events if event.type == "fanout.child.dispatched"]
    assert all(
        TaskStore(state_dir / "kanban.json").get(task_id).status == "blocked"
        for task_id in ("TASK-1", "TASK-2")
    )


def test_writer_fanout_records_task_map_admission_before_start(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    events = log.read_all()
    admitted = next(event for event in events if event.type == "task_map.admitted")
    started = next(event for event in events if event.type == "fanout.started")
    assert admitted.payload["trigger_event_id"] == started.payload["trigger_event_id"]
    assert started.payload["task_map_admitted_event_id"] == admitted.id
    assert (
        started.payload["plan_admission_incident_id"]
        == admitted.payload["plan_admission_incident_id"]
    )


def test_writer_fanout_synthesize_canonical_tasks_admits_unseeded(tmp_path: Path):
    # Opt-in: a refactor-style writer stage (synthesize_canonical_tasks) makes
    # its task_map tasks canonical in the kanban so the admission gate passes
    # without a product-delivery ingest — replacing the manual kanban-seed bridge.
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    task_map_path = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map = json.loads(task_map_path.read_text(encoding="utf-8"))
    task_map["goal_claims"] = [
        {"goal_claim_id": "CLAIM-A", "text": "A exists", "mandatory": True},
        {"goal_claim_id": "CLAIM-B", "text": "B exists", "mandatory": True},
    ]
    task_map["tasks"][0]["goal_claim_ids"] = ["CLAIM-A"]
    task_map["tasks"][1]["goal_claim_ids"] = ["CLAIM-B"]
    task_map["tasks"][1]["verification"] = "test -f b.txt"
    task_map_path.write_text(json.dumps(task_map), encoding="utf-8")

    _start(orch)

    events = log.read_all()
    task_map_manifests = [
        event for event in events
        if event.type == "artifact.manifest.published"
        and event.payload.get("handoff_contract", {}).get("source") == "refactor_task_map"
    ]
    assert task_map_manifests
    orch.run_once(events=task_map_manifests)
    events = log.read_all()
    assert not [e for e in events if e.type == "fanout.cancelled"]
    assert not [
        e for e in events
        if e.type == "event.malformed"
        and e.payload.get("origin_event") == "artifact.manifest.published"
    ]
    assert not [
        e for e in events
        if e.type == "artifact.manifest.rejected"
        and e.payload.get("source") == "artifact.manifest.published"
    ]
    assert [sent[0] for sent in transport.sent if sent[0].startswith("dev-")] == [
        "dev-1",
        "dev-2",
    ]
    store = TaskStore(state_dir / "kanban.json")
    for tid in ("TASK-1", "TASK-2"):
        task = store.get(tid)
        assert task is not None and task.status == "in_progress"
        refs = (task.contract.evidence_contract or {}).get("source_refs", {})
        assert refs.get("task_map_ref") == ".zf/artifacts/F-11111111/task_map.json"
        assert task.contract.behavior
        assert task.contract.verification_tiers == ["runtime"]
        expected_command = "test -f a.txt" if tid == "TASK-1" else "test -f b.txt"
        assert task.contract.verification == expected_command
        assert task.contract.product_contract_ref == ".zf/artifacts/F-11111111/task_map.json"
        assert validate_task_contract(task, config=orch.config, project_root=tmp_path) == []
    # behavior comes from the task_map payload instruction when present.
    assert store.get("TASK-1").contract.behavior == "Create a.txt with TASK-1 smoke content."
    assert store.get("TASK-1").contract.acceptance_criteria == [
        "a.txt exists",
        "no behavior change",
    ]
    # contract.scope must carry the allowed_paths globs, not the prose scope
    # label — task_refs fnmatches changed files against contract.scope, and a
    # prose entry rejects every writer handoff (HIC-E8311AE35F).
    assert store.get("TASK-1").contract.scope == ["a.txt"]
    assert store.get("TASK-2").contract.scope == ["b.txt"]
    assert store.get("TASK-1").contract.goal_claim_ids == ["CLAIM-A"]
    assert store.get("TASK-2").contract.goal_claim_ids == ["CLAIM-B"]


def test_writer_fanout_synthesize_canonical_refreshes_workflow_bootstrap_placeholder(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    store = TaskStore(state_dir / "kanban.json")
    for task_id in ("TASK-1", "TASK-2"):
        store.add(Task(
            id=task_id,
            title=f"bootstrap {task_id}",
            status="backlog",
            contract=TaskContract(
                acceptance="exit_code=0",
                evidence_contract={
                    "source": "workflow_invoke_bootstrap",
                    "workflow_fanout_anchor": True,
                    "pattern_id": "issue-triage",
                },
            ),
        ))

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent if sent[0].startswith("dev-")] == [
        "dev-1",
        "dev-2",
    ]
    updates = [
        event for event in events
        if event.type == "task.contract.update"
        and event.payload.get("source") == "workflow_task_map_adoption"
    ]
    assert [event.task_id for event in updates] == ["TASK-1", "TASK-2"]
    task = store.get("TASK-1")
    assert task is not None
    assert task.contract.evidence_contract["source"] == "refactor_task_map"
    assert (
        task.contract.evidence_contract["source_refs"]["task_map_ref"]
        == ".zf/artifacts/F-11111111/task_map.json"
    )
    assert task.contract.behavior == "Create a.txt with TASK-1 smoke content."
    assert validate_task_contract(task, config=orch.config, project_root=tmp_path) == []


def test_plan_contract_requires_assembly_task_for_multi_bundle():
    # A multi-bundle plan lacking a root_owner_class=assembly task is rejected by
    # writer fanout admission → wasted replan. The plan contract must warn the
    # planner up front for ALL flows (was refactor-gated; PRD/Issue planners
    # missed it and only learned via the post-hoc rejection — 2026-07-09).
    contract = "\n".join(WriterFanoutDataMixin._plan_artifact_contract_lines())
    assert 'root_owner_class: "assembly"' in contract
    assert "more than one parallel bundle" in contract
    assert "workspace_root_owner_required=true" in contract


def test_writer_briefing_forbids_out_of_scope_layout(tmp_path: Path):
    # allowed_paths must be framed as a hard boundary, not just a permission —
    # otherwise the agent invents its own layout (e.g. src/) and is only caught
    # post-hoc at verify/quality (2026-07-09 fanout src/ divergence root).
    state_dir, _log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    _start(orch)
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "ONLY those exact" in briefing
    assert "do NOT invent an alternative layout" in briefing
    assert "`src/`" in briefing
    assert "git add -- <path>" in briefing
    assert "never use `git add -A`" in briefing
    assert "Materialized runtime files" in briefing


def test_writer_briefing_carries_allowed_paths_reason(tmp_path: Path):
    # P0 structure-spec propagation: the dev's scope_contract must carry the
    # positive structure narrative (allowed_paths_reason), not only the exact
    # paths + a "no src/" prohibition — this is how the planner's structure
    # intent reaches the implementer, aligning with agent-skills' Project
    # Structure reaching the builder (2026-07-09 alignment root fix).
    state_dir, _log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    data["tasks"][0]["allowed_paths_reason"] = (
        "owns the server entrypoint app/server.js and store app/lib/task-store.js"
    )
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "allowed_paths_reason" in briefing
    assert "server entrypoint app/server.js and store app/lib/task-store.js" in briefing


def test_writer_briefing_preserves_workflow_matrix_refs(tmp_path: Path):
    state_dir, _log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    data["tasks"][0].update({
        "workflow_input_manifest_ref": "artifacts/workflow/wf/workflow-input-manifest.json",
        "acceptance_matrix_ref": "artifacts/workflow/wf/acceptance-matrix.json",
        "test_matrix_ref": "artifacts/workflow/wf/test-matrix.json",
        "real_e2e_matrix_ref": "artifacts/workflow/wf/real-e2e-matrix.json",
        "source_refs": {"prd_ref": "docs/prd/tiny.md"},
        "artifact_refs": [
            "artifacts/workflow/wf/acceptance-matrix.json",
            "artifacts/workflow/wf/test-matrix.json",
        ],
    })
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Workflow Input Manifest" in briefing
    assert '"workflow_input_manifest_ref": "artifacts/workflow/wf/workflow-input-manifest.json"' in briefing
    assert '"acceptance_matrix_ref": "artifacts/workflow/wf/acceptance-matrix.json"' in briefing
    assert '"test_matrix_ref": "artifacts/workflow/wf/test-matrix.json"' in briefing
    assert '"real_e2e_matrix_ref": "artifacts/workflow/wf/real-e2e-matrix.json"' in briefing
    assert '"workflow_source_refs"' in briefing
    assert '"prd_ref": "docs/prd/tiny.md"' in briefing


def test_writer_fanout_canonicalizes_semantic_task_map_owner_role(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    data["tasks"][0]["owner_role"] = "dev-core"
    data["tasks"][0]["acceptance"] = ["core module parity is implemented"]
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)
    task_map_manifests = [
        event for event in log.read_all()
        if event.type == "artifact.manifest.published"
        and event.payload.get("handoff_contract", {}).get("source") == "refactor_task_map"
    ]
    orch.run_once(events=task_map_manifests)

    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-1")
    assert task is not None
    assert task.contract.owner_role == "dev"
    assert task.contract.evidence_contract["semantic_owner_role"] == "dev-core"
    assert task.contract.acceptance_criteria == ["core module parity is implemented"]
    assert validate_task_contract(task, config=orch.config, project_root=tmp_path) == []
    assert [sent[0] for sent in transport.sent if sent[0].startswith("dev-")]


def test_refactor_replan_refreshes_existing_canonical_task_map_refs(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    _start(orch)
    assert [sent[0] for sent in transport.sent if sent[0].startswith("dev-")] == [
        "dev-1",
        "dev-2",
    ]
    first_dispatch = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
    )
    inflight_marker = Path(first_dispatch.payload["workdir"]) / "inflight.txt"
    inflight_marker.write_text("provider still working\n", encoding="utf-8")

    next_ref = ".zf/artifacts/F-11111111/task_map-v2.json"
    next_path = state_dir / "artifacts" / "F-11111111" / "task_map-v2.json"
    next_path.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "core v2",
                "allowed_paths": ["a2.txt"],
                "verification": "npm run test:v2",
                "payload": {"instruction": "Rewrite TASK-1 against v2 fixtures."},
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway v2",
                "allowed_paths": ["b2.txt"],
                "verification": "npm run test:v2",
                "payload": {"instruction": "Rewrite TASK-2 against v2 fixtures."},
            },
        ],
    }), encoding="utf-8")
    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": next_ref,
            "rework_of": "verify-failed-r35-contract-gap",
            "rework_attempt": 2,
            "rework_source": "verify.failed",
            "replan_classification": "contract_freeze_gap",
        },
    )

    orch.run_once(events=[trigger])

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("trigger_event_id") == trigger.id
    ]
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == trigger.id
    ]
    assert started, "replan adoption should start writer fanout, not stale-cancel"
    updates = [
        event for event in events
        if event.type == "task.contract.update"
        and event.payload.get("source") == "refactor_replan_adoption"
    ]
    assert {event.task_id for event in updates} == {"TASK-1", "TASK-2"}
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-1").contract.evidence_contract["source_refs"]["task_map_ref"] == next_ref
    assert store.get("TASK-1").contract.scope == ["a2.txt"]
    assert store.get("TASK-1").contract.behavior == "Rewrite TASK-1 against v2 fixtures."
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert inflight_marker.read_text(encoding="utf-8") == "provider still working\n"
    deferred = [
        event for event in events
        if event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("reason") in {
            "task_attempt_fence_active",
            "task_writer_busy_fence_active",
        }
    ]
    assert {event.payload.get("task_id") for event in deferred} == {"TASK-1", "TASK-2"}


def test_replan_reopens_terminal_refactor_tasks_for_writer_fanout(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    orch.config.workflow.flow_metadata = {
        "result_protocol": {"mode": "blocking"},
    }
    _start(orch)
    store = TaskStore(state_dir / "kanban.json")
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]

    store.update("TASK-1", status="done")
    store.update("TASK-2", status="done")
    orch._last_worker_state["dev-1"] = "blocked"  # type: ignore[attr-defined]
    orch._last_worker_state["dev-2"] = "blocked"  # type: ignore[attr-defined]
    assert store.list_all() == []
    terminal_index_path = state_dir / "kanban-terminal-index.json"
    terminal_index = json.loads(terminal_index_path.read_text(encoding="utf-8"))
    assert set(terminal_index) == {"TASK-1", "TASK-2"}

    next_ref = ".zf/artifacts/F-11111111/task_map-v3.json"
    next_path = state_dir / "artifacts" / "F-11111111" / "task_map-v3.json"
    next_path.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "core v3",
                "allowed_paths": ["a3.txt"],
                "verification": "npm run test:v3",
                "payload": {"instruction": "Repair TASK-1 from verify feedback."},
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway v3",
                "allowed_paths": ["b3.txt"],
                "verification": "npm run test:v3",
                "payload": {"instruction": "Repair TASK-2 from verify feedback."},
            },
        ],
    }), encoding="utf-8")
    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": next_ref,
            "rework_of": "verify-failed-r37-contract-gap",
            "rework_attempt": 2,
            "rework_source": "verify.failed",
            "replan_classification": "contract_freeze_gap",
        },
    )

    orch.run_once(events=[trigger])

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("trigger_event_id") == trigger.id
        and event.payload.get("reason") == "terminal_tasks"
    ]
    assert [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == trigger.id
    ]
    updates = [
        event for event in events
        if event.type == "task.contract.update"
        and event.payload.get("reopened_from_terminal") is True
    ]
    assert {event.task_id for event in updates} == {"TASK-1", "TASK-2"}

    reopened_store = TaskStore(state_dir / "kanban.json")
    task1 = reopened_store.get("TASK-1")
    assert task1 is not None
    assert task1.status == "in_progress"
    assert task1.contract.evidence_contract["source_refs"]["task_map_ref"] == next_ref
    assert task1.contract.scope == ["a3.txt"]
    assert task1.contract.behavior == "Repair TASK-1 from verify feedback."
    assert json.loads(terminal_index_path.read_text(encoding="utf-8")) == {}
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "dev-1", "dev-2"]
    assert orch._last_worker_state["dev-1"] == "busy"  # type: ignore[attr-defined]
    assert orch._last_worker_state["dev-2"] == "busy"  # type: ignore[attr-defined]
    released = [
        event for event in events
        if event.type == "worker.state.changed"
        and event.payload.get("from") == "blocked"
        and event.payload.get("to") == "idle"
    ]
    assert {event.actor for event in released} == {"dev-1", "dev-2"}
    replan_dispatch = next(
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
        and event.payload.get("task_map_ref") == next_ref
    )
    assert replan_dispatch.payload["rework_of"] == "verify-failed-r37-contract-gap"
    task1_operation_ids = {
        event.payload.get("operation_id")
        for event in events
        if event.type == "workflow.operation.requested"
        and event.payload.get("task_id") == "TASK-1"
    }
    assert len(task1_operation_ids) == 2


def test_operator_replan_may_release_blocked_human_writer(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    _start(orch)
    store = TaskStore(state_dir / "kanban.json")
    store.update("TASK-1", status="done")
    store.update("TASK-2", status="done")
    orch._last_worker_state["dev-1"] = "blocked_human"  # type: ignore[attr-defined]
    orch._last_worker_state["dev-2"] = "blocked_human"  # type: ignore[attr-defined]
    next_ref = ".zf/artifacts/F-11111111/task-map-operator.json"
    (state_dir / "artifacts" / "F-11111111" / "task-map-operator.json").write_text(
        (state_dir / "artifacts" / "F-11111111" / "task_map.json").read_text(
            encoding="utf-8",
        ),
        encoding="utf-8",
    )

    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": next_ref,
            "rework_of": "operator-replan-source",
            "rework_attempt": 2,
            "operator_authorized": True,
        },
    )

    orch.run_once(events=[trigger])

    assert [sent[0] for sent in transport.sent] == [
        "dev-1",
        "dev-2",
        "dev-1",
        "dev-2",
    ]
    released = [
        event for event in log.read_all()
        if event.type == "worker.state.changed"
        and event.payload.get("from") == "blocked_human"
        and event.payload.get("to") == "idle"
    ]
    assert {event.actor for event in released} == {"dev-1", "dev-2"}


def test_replan_contract_refresh_resets_blocked_task_generation(tmp_path: Path):
    state_dir, _log, _transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    _start(orch)
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        status="blocked",
        blocked_reason="upstream contract gap",
        retry_count=2,
    )
    next_ref = ".zf/artifacts/F-11111111/task-map-blocked-replan.json"
    (state_dir / "artifacts" / "F-11111111" / "task-map-blocked-replan.json").write_text(
        (state_dir / "artifacts" / "F-11111111" / "task_map.json").read_text(
            encoding="utf-8",
        ),
        encoding="utf-8",
    )
    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": next_ref,
            "rework_of": "task-contract-blocked",
            "rework_attempt": 2,
        },
    )
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    loaded = load_writer_task_map(
        stage=orch.config.workflow.stages[0],
        event=trigger,
        pdd_id="F-11111111",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    orch._ensure_writer_tasks_canonical(loaded)  # type: ignore[attr-defined]

    refreshed = store.get("TASK-1")
    assert refreshed is not None
    assert refreshed.status == "backlog"
    assert refreshed.assigned_to is None
    assert refreshed.active_dispatch_id == ""
    assert refreshed.retry_count == 0
    assert refreshed.blocked_reason == ""
    assert refreshed.contract.evidence_contract["source_refs"]["task_map_ref"] == next_ref


def test_light_task_contract_uses_config_quality_gate_fallback(tmp_path: Path):
    check = "python app/verify.py"
    state_dir, log, _transport, orch = _state(
        tmp_path,
        quality_gates={
            "static": QualityGateConfig(required_checks=[check]),
        },
        synthesize_canonical=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    payload = synthesize_light_task_map(
        pdd_id="F-11111111",
        objective="Deliver the minimal product",
        prd_ref="docs/prd/minimal.md",
        target_root="app",
    )
    task_map.write_text(json.dumps(payload), encoding="utf-8")
    task_id = payload["tasks"][0]["task_id"]
    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-light",
        payload={"pdd_id": "F-11111111", "feature_id": "F-11111111"},
    )
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    loaded = load_writer_task_map(
        stage=orch.config.workflow.stages[0],
        event=trigger,
        pdd_id="F-11111111",
        state_dir=state_dir,
        project_root=tmp_path,
    )
    orch._ensure_writer_tasks_canonical(loaded)  # type: ignore[attr-defined]

    task = TaskStore(state_dir / "kanban.json").get(task_id)
    assert task is not None
    assert task.contract.verification == ""

    checks, source = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=orch.config,
        event_log=log,
    )._quality_checks([
        CandidateTask(
            task_id=task_id,
            task_ref=f"refs/heads/task/{task_id}",
            source_commit="source-light",
            approval_event_id="approved-light",
            approval_event_type="review.approved",
        ),
    ])

    assert source == "zf_config_fallback"
    assert checks == [("static", check)]


def test_same_task_map_replan_reopens_blocked_task(tmp_path: Path):
    state_dir, _log, _transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    _start(orch)
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        status="blocked",
        blocked_reason="transient harness failure",
        retry_count=2,
    )
    trigger = ZfEvent(
        type="task_map.ready",
        actor="operator",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "rework_of": "harness-failure",
            "rework_attempt": 2,
            "operator_authorized": True,
            "task_ids": ["TASK-1"],
            "resume_scope": "failed_children_only",
        },
    )
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    loaded = load_writer_task_map(
        stage=orch.config.workflow.stages[0],
        event=trigger,
        pdd_id="F-11111111",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    orch._ensure_writer_tasks_canonical(loaded)  # type: ignore[attr-defined]

    refreshed = store.get("TASK-1")
    assert refreshed is not None
    assert refreshed.status == "backlog"
    assert refreshed.assigned_to is None
    assert refreshed.active_dispatch_id == ""
    assert refreshed.retry_count == 0
    assert refreshed.blocked_reason == ""


def test_refactor_plan_bridge_preserves_replan_metadata(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path, synthesize_canonical=True)

    orch._bridge_refactor_plan_ready_to_task_map(
        manifest={"pdd_id": "F-11111111", "target_ref": "main"},
        projection_payload={
            "pdd_id": "F-11111111",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "rework_of": "verify-failed-r35-contract-gap",
            "rework_attempt": 2,
            "rework_source": "verify.failed",
            "replan_classification": "contract_freeze_gap",
        },
        trace_id="trace-1",
    )

    ready = [event for event in log.read_all() if event.type == "task_map.ready"]
    assert ready
    payload = ready[-1].payload
    assert payload["source"] == "refactor_plan_bridge"
    assert payload["rework_of"] == "verify-failed-r35-contract-gap"
    assert payload["rework_attempt"] == 2
    assert payload["rework_source"] == "verify.failed"
    assert payload["replan_classification"] == "contract_freeze_gap"


def test_strict_plan_approval_does_not_leak_canonical_tasks_before_approval(
    tmp_path: Path,
):
    # B14 leak guard: with plan approval enabled (strict profile), a
    # synthesize-canonical writer fanout must NOT seed canonical backlog tasks
    # while parked pending plan.approved. Before the fix, _ensure_writer_tasks_
    # canonical seeded them as status=backlog, and feature_backlog_scheduler
    # dispatched them OUTSIDE the parked fanout — the worker then ran the task
    # and emitted a bare completion the (never-started) aggregator could not turn
    # into candidate.ready.
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    orch.config.workflow.plan_approval_enabled = True

    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    log.append(trigger)
    orch.run_once(events=[trigger])

    events = log.read_all()
    assert [e for e in events if e.type == "plan.approval.requested"]
    assert not [e for e in events if e.type == "fanout.started"]
    # LEAK GUARD: nothing seeded while parked → scheduler has nothing to grab.
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-1") is None
    assert store.get("TASK-2") is None

    # operator approves → re-enter → tasks seeded + fanout dispatches.
    plan_id = next(
        e.payload["plan_id"] for e in events if e.type == "plan.approval.requested"
    )
    # `zf plan approve` appends plan.approved to the log, then the reactor routes
    # it through _maybe_start_writer_fanout → _resume_writer_fanout_on_plan_approved.
    # Mirror that ordering (the approval gate reads the log for the approved event).
    approved = ZfEvent(
        type="plan.approved",
        actor="operator",
        correlation_id="trace-1",
        payload={"plan_id": plan_id},
    )
    log.append(approved)
    orch._maybe_start_writer_fanout(approved)
    store2 = TaskStore(state_dir / "kanban.json")
    assert store2.get("TASK-1") is not None
    assert [e for e in log.read_all() if e.type == "fanout.started"]


def test_writer_fanout_source_index_gate_blocks_canonical_synthesis(
    tmp_path: Path,
):
    # R30 regression: strict source-index failure must not leave synthetic
    # kanban tasks behind for the regular backlog scheduler to dispatch outside
    # writer fanout.
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        harness_profile="strict",
    )

    _start(orch)

    events = log.read_all()
    cancelled = [event for event in events if event.type == "fanout.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "source_index_gap"
    assert cancelled[0].payload["missing_anchor_task_ids"] == ["TASK-1", "TASK-2"]
    assert not [event for event in events if event.type == "fanout.started"]
    assert transport.sent == []
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_writer_fanout_source_index_gate_uses_task_source_refs(
    tmp_path: Path,
):
    # R30 second root cause: writer_task_items must preserve per-task
    # source_refs from task_map.json; otherwise the B4 gate falsely reports
    # source_index_gap even when synth produced anchors.
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        harness_profile="strict",
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        task["source_refs"] = [f"scan/findings.json#{task['task_id']}"]
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert [event.type for event in events if event.type == "fanout.started"] == [
        "fanout.started"
    ]


def test_writer_fanout_source_index_gate_uses_task_source_refs_dict(
    tmp_path: Path,
):
    # Hermes plan synth emits source_refs as a map of named anchors. The
    # admission reader must preserve those anchors before strict B4 evaluation.
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        harness_profile="strict",
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        task["source_refs"] = {
            "prompt_ref": "docs/plans/cj-min-refactor-prompt.md",
            "scan": f"scan/findings.json#{task['task_id']}",
        }
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert [event.type for event in events if event.type == "fanout.started"] == [
        "fanout.started"
    ]


def test_writer_fanout_source_index_gate_uses_task_source_keys(
    tmp_path: Path,
):
    # Refactor synth can emit source_keys rather than source_refs. Admission
    # must preserve the field before strict B4 evaluation.
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        harness_profile="strict",
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        task["source_keys"] = [f"scan/findings.json#{task['task_id']}"]
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert [event.type for event in events if event.type == "fanout.started"] == [
        "fanout.started"
    ]


def test_writer_fanout_source_index_gate_uses_task_sources_index(
    tmp_path: Path,
):
    # Real refactor E2E produced a source_index with task_sources[] instead of
    # source_index.tasks[]. Strict writer admission should treat that as
    # per-task provenance, not source_index_gap.
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        harness_profile="strict",
    )
    source_index = state_dir / "artifacts" / "F-11111111" / "source_index.json"
    source_index.write_text(json.dumps({
        "schema_version": "source-index.v1",
        "task_sources": [
            {
                "task_id": "TASK-1",
                "source_keys": ["scan/findings.json#TASK-1"],
            },
            {
                "task_id": "TASK-2",
                "source_keys": ["scan/findings.json#TASK-2"],
            },
        ],
    }), encoding="utf-8")
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    data = json.loads(task_map.read_text(encoding="utf-8"))
    data["source_refs"] = {
        "source_index_ref": ".zf/artifacts/F-11111111/source_index.json",
    }
    task_map.write_text(json.dumps(data), encoding="utf-8")

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert [event.type for event in events if event.type == "fanout.started"] == [
        "fanout.started"
    ]


def test_writer_fanout_quarantined_after_candidate_rework_escalate(tmp_path: Path):
    # R22 no-livelock: once candidate rework for the pdd has escalated, a fresh
    # (non-rework) task_map.ready must NOT re-arm the writer fanout — otherwise a
    # spurious re-emit restarts the capped loop from zero (cj-min R22). The happy
    # path (no escalate) is covered by the synthesize test above, which dispatches.
    state_dir, log, transport, orch = _state(tmp_path, synthesize_canonical=True)
    orch.event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        payload={"pdd_id": "F-11111111", "rework_source": "integration.failed"},
    ))

    _start(orch)  # fresh task_map.ready for F-11111111, no rework_of

    events = log.read_all()
    assert [e for e in events if e.type == "candidate.rework.quarantined"]
    assert not [e for e in events if e.type == "fanout.started"]
    assert not [e for e in events if e.type == "fanout.child.dispatched"]
    assert transport.sent == []


def test_writer_fanout_dispatches_seeded_children_to_distinct_workdirs_and_branches(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    events = log.read_all()
    dispatched = [event for event in events if event.type == "fanout.child.dispatched"]
    assert not [
        event for event in events
        if event.type == "event.malformed"
        and event.payload.get("origin_event") == "task_map.ready"
    ]
    assert len(dispatched) == 2
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert {event.payload["workdir"] for event in dispatched} == {
        str(state_dir / "workdirs" / "dev-1" / "project"),
        str(state_dir / "workdirs" / "dev-2" / "project"),
    }
    assert {event.payload["source_branch"] for event in dispatched} == {
        "worker/dev-1",
        "worker/dev-2",
    }
    for event in dispatched:
        assert _git(Path(event.payload["workdir"]), "branch", "--show-current") == event.payload["source_branch"]


def test_pending_writer_fanout_dispatches_after_worker_recovers(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    orch.config.workflow.flow_metadata = {
        "result_protocol": {"mode": "blocking"},
    }
    alive = {"dev-1": True, "dev-2": False}
    respawns: list[str] = []

    transport.is_alive = lambda role_name: alive.get(role_name, True)  # type: ignore[method-assign]

    def _respawn(role):  # noqa: ANN001
        respawns.append(role.instance_id)
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="test respawn",
        )

    orch._respawn_instance = _respawn  # type: ignore[method-assign]

    _start(orch)

    fanout_id = _fanout_id(log)
    events = log.read_all()
    assert respawns == ["dev-2"]
    dispatches = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert [event.payload["task_id"] for event in dispatches] == ["TASK-1"]
    deferred = [
        event for event in events
        if event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(deferred) == 1
    assert deferred[0].payload["role_instance"] == "dev-2"
    assert not [
        event for event in events
        if event.type == "workflow.operation.requested"
        and event.payload.get("task_id") == "TASK-2"
    ]
    assert _child(_manifest(state_dir, fanout_id), "TASK-2")["status"] == "pending"

    orch.run_once(events=[])
    deferred = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("role_instance") == "dev-2"
    ]
    assert len(deferred) == 1

    alive["dev-2"] = True
    orch.run_once(events=[])

    events = log.read_all()
    dispatches = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert [event.payload["task_id"] for event in dispatches] == ["TASK-1", "TASK-2"]
    assert [sent[0] for sent in transport.sent if sent[0].startswith("dev-")] == [
        "dev-1",
        "dev-2",
    ]
    task = TaskStore(state_dir / "kanban.json").get("TASK-2")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev-2"
    assert task.active_dispatch_id == dispatches[-1].payload["run_id"]
    task2_operations = [
        event for event in events
        if event.type == "workflow.operation.requested"
        and event.payload.get("task_id") == "TASK-2"
    ]
    assert len(task2_operations) == 1
    assert not [
        event for event in events
        if event.type == "workflow.operation.blocked"
        and event.payload.get("task_id") == "TASK-2"
        and event.payload.get("reason") == "request_hash_divergence"
    ]


def test_writer_fanout_accepts_task_map_ref_in_manual_evidence(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    for task_id in ("TASK-1", "TASK-2"):
        store.add(Task(
            id=task_id,
            title=task_id,
            status="backlog",
            contract=TaskContract(
                feature_id="F-11111111",
                evidence_contract={
                    "manual_evidence": {
                        "source_refs": {
                            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                        },
                    },
                },
            ),
        ))

    _start(orch)

    events = log.read_all()
    assert not [event for event in events if event.type == "fanout.cancelled"]
    dispatched = [
        event for event in events if event.type == "fanout.child.dispatched"
    ]
    assert len(dispatched) == 2
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_writer_briefing_includes_task_scope_and_payload_instruction(tmp_path: Path):
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "- task_id: `TASK-1`" in briefing
    assert "- scope: `core`" in briefing
    assert "Task instruction:" in briefing
    assert "Create a.txt with TASK-1 smoke content." in briefing
    assert '"instruction": "Create a.txt with TASK-1 smoke content."' in briefing
    assert "Candidate-ready preflight" in briefing
    assert "frozen lockfile install plus root typecheck/test" in briefing
    assert "exact golden fixtures" in briefing
    assert "Shape-only fixture checks are not enough" in briefing


def test_writer_fanout_starts_from_task_map_target_commit(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    main_commit = _git(tmp_path, "rev-parse", "HEAD")
    target_commit = _commit(
        tmp_path,
        "baseline.txt",
        "planned baseline\n",
        "planned baseline",
    )
    _git(tmp_path, "branch", "planned-baseline", target_commit)
    _git(tmp_path, "reset", "--hard", main_commit)
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map_payload = json.loads(task_map.read_text(encoding="utf-8"))
    task_map_payload["target_commit"] = target_commit
    task_map.write_text(json.dumps(task_map_payload), encoding="utf-8")

    _start(orch)

    manifest = _manifest(state_dir, _fanout_id(log))
    first = _child(manifest, "TASK-1")
    assert manifest["target_ref"] == target_commit
    assert first["target_ref"] == target_commit
    assert first["payload"]["dispatch_base_commit"] == target_commit
    assert _git(Path(first["workdir"]), "rev-parse", "HEAD") == target_commit
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert f'"base_ref": "{target_commit}"' in briefing
    assert f'"base_git_head": "{target_commit}"' in briefing


def test_writer_briefing_uses_configured_zf_cli_cmd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "uv --project /repo run zf emit dev.build.done" in briefing
    assert "uv --project /repo run zf emit dev.blocked" in briefing
    assert f"--state-dir {state_dir}" in briefing
    assert '"failure_class": "task_contract_unsatisfiable"' in briefing
    assert '"recommended_action": "replan"' in briefing
    assert '"blocker_task_ids": []' in briefing
    assert '"required_paths": []' in briefing


def test_writer_briefing_inlines_acceptance_verification_and_scope_guard(tmp_path: Path):
    # 2026-06-19 e2e context-continuity audit: the impl agent must not have to
    # dereference task_map_ref to learn its acceptance/verification — the
    # briefing's scope contract inlines them, including the SCOPE GUARD that
    # lives in the task summary.
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert '"acceptance"' in briefing
    assert "no behavior change" in briefing
    assert '"verification"' in briefing
    assert "test -f a.txt" in briefing
    assert "SCOPE GUARD: pure extraction only" in briefing


def test_writer_briefing_surfaces_candidate_rework_feedback(tmp_path: Path):
    # A re-triggered task_map.ready (candidate-rework sweep) carries the
    # reviewers' findings; the writer briefing MUST surface them so the
    # re-dispatched writer fixes the rejected defect instead of re-running blind.
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    orch.run_once(events=[ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "rework_of": "evt-rej-1",
            "rework_attempt": 2,
            "rework_feedback": [
                "review-architecture: @cj-min/state declared but not provided",
            ],
            "rework_summary": {
                "source_event_type": "verify.failed",
                "action": "retrigger",
                "categories": ["contract_fixture_gap"],
            },
        },
    )])

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "REWORK (attempt 2)" in briefing
    assert "@cj-min/state declared but not provided" in briefing
    assert "Reviewer findings to resolve:" in briefing
    assert "Rework summary:" in briefing
    assert '"contract_fixture_gap"' in briefing


def test_candidate_rework_sweep_drives_writer_fanout_immediately(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
    ))
    log.append(ZfEvent(
        id="failed-stale-task-map",
        type="fanout.child.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-dev-fanout-old",
            "stage_id": "dev-fanout",
            "child_id": "dev-1-TASK-1",
            "task_id": "TASK-1",
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "reason": "stale_task_map",
        },
    ))

    orch._run_candidate_rework_sweep()

    events = log.read_all()
    rework = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("rework_of") == "failed-stale-task-map"
    ]
    assert rework, "expected stale task-map recovery to re-emit task_map.ready"
    assert rework[-1].payload["rework_summary"]["action"] == "retrigger"
    assert rework[-1].payload["rework_categories"] == []
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework[-1].id
    ]
    assert started, "expected candidate rework sweep to start writer fanout"
    assert [sent[0] for sent in transport.sent] == ["dev-1"]
    assert rework[-1].payload["task_ids"] == ["TASK-1"]
    assert rework[-1].payload["resume_scope"] == "failed_children_only"


def test_candidate_rework_sweep_does_not_tick_run_manager_without_pending_action(
    tmp_path: Path,
):
    """Idle orchestration must not turn a healthy sweep into an RM loop."""
    state_dir, log, _transport, orch = _state(tmp_path)
    log.append(ZfEvent(
        type="orchestrator.decision.recorded",
        actor="zf-cli",
        payload={"outcome_reason": "idle"},
    ))

    with patch("zf.runtime.run_manager.run_manager_tick") as run_manager_tick:
        orch._run_candidate_rework_sweep()

    run_manager_tick.assert_not_called()


def test_idle_orchestrator_cycles_do_not_tick_run_manager_without_rework(
    tmp_path: Path,
):
    """The five-second watcher idle loop must preserve RM coalescing."""
    _state_dir, _log, _transport, orch = _state(tmp_path)

    with patch("zf.runtime.run_manager.run_manager_tick") as run_manager_tick:
        orch.run_once()
        orch.run_once()

    run_manager_tick.assert_not_called()


def test_run_once_reacts_to_candidate_verify_failed_with_rework(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
    ))
    failed = ZfEvent(
        id="verify-failed-r35-regression",
        type="verify.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "target_ref": "cand/F-11111111",
            "findings": [
                {
                    "message": "gateway fixture parity failed",
                    "task_id": "TASK-1",
                },
            ],
        },
    )
    log.append(failed)

    orch.run_once(events=[failed])

    events = log.read_all()
    rework = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("rework_of") == "verify-failed-r35-regression"
    ]
    assert rework, "verify.failed wake should immediately re-emit task_map.ready"
    assert rework[-1].payload["rework_feedback"] == [
        "TASK-1: gateway fixture parity failed",
    ]
    assert rework[-1].payload["task_ids"] == ["TASK-1"]
    assert rework[-1].payload["resume_scope"] == "failed_children_only"
    assert rework[-1].payload["rework_summary"]["failed_task_ids"] == ["TASK-1"]
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework[-1].id
    ]
    assert started, "verify.failed wake should start writer rework fanout"
    assert [sent[0] for sent in transport.sent] == ["dev-1"]


def test_run_once_reacts_to_candidate_verify_child_failed_with_rework(tmp_path: Path):
    _state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
    ))
    child_failed = ZfEvent(
        id="verify-child-r37-regression",
        type="verify.child.failed",
        actor="verify-lane-0",
        correlation_id="trace-1",
        payload={
            "trace_id": "trace-1",
            "child_id": "verify-lane-0-task-1",
            "reason": "package typecheck failed",
        },
    )
    log.append(child_failed)
    log.append(ZfEvent(
        id="verify-failed-r37-regression",
        type="verify.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "target_ref": "cand/F-11111111",
            "findings": [
                {
                    "message": "package typecheck failed",
                    "task_id": "TASK-1",
                },
            ],
        },
    ))

    orch.run_once(events=[child_failed])

    events = log.read_all()
    rework = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("rework_of") == "verify-failed-r37-regression"
    ]
    assert rework, "verify.child.failed wake should run candidate rework sweep"
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework[-1].id
    ]
    assert started, "child failure wake should start writer rework fanout"
    assert rework[-1].payload["task_ids"] == ["TASK-1"]
    assert rework[-1].payload["resume_scope"] == "failed_children_only"
    assert [sent[0] for sent in transport.sent] == ["dev-1"]


def test_repeated_verify_contract_gap_resynthes_plan_trigger(tmp_path: Path):
    _state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        resynth_trigger="zaofu.refactor.review.ready",
    )
    log.append(ZfEvent(
        id="scan-review-ready",
        type="zaofu.refactor.review.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "target_ref": "main",
            "source_commit": "abc123",
            "review_artifact_ref": "artifacts/scan/review.md",
        },
    ))
    log.append(ZfEvent(
        id="verify-failed-first",
        type="verify.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "target_ref": "cand/F-11111111",
        },
    ))
    log.append(ZfEvent(
        id="verify-rework-first",
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "rework_of": "verify-failed-first",
            "rework_source": "verify.failed",
            "rework_attempt": 1,
        },
    ))
    log.append(ZfEvent(
        id="verify-rework-first-started",
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-dev-fanout-verify-rework-first",
            "trigger_event_id": "verify-rework-first",
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
        },
    ))
    log.append(ZfEvent(
        id="verify-child-contract-gap",
        type="verify.child.failed",
        actor="verify-1",
        correlation_id="trace-1",
        payload={
            "child_id": "verify-lane-4-web-tui",
            "trace_id": "trace-1",
            "reason": (
                "path completion fixture is shape-only; Python reference "
                "returns extra @staged/@url:/@git: items"
            ),
        },
    ))
    failed = ZfEvent(
        id="verify-failed-r35-contract-gap",
        type="verify.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "target_ref": "cand/F-11111111",
        },
    )
    log.append(failed)

    orch.run_once(events=[failed])

    events = log.read_all()
    requested = [
        event for event in events
        if event.type == "orchestrator.replan_requested"
        and event.payload.get("rework_of") == "verify-failed-r35-contract-gap"
    ]
    assert requested, "expected repeated contract verify failure to request replan"
    assert requested[-1].payload["classification"] == "contract_freeze_gap"
    resynth = [
        event for event in events
        if event.type == "zaofu.refactor.review.ready"
        and event.payload.get("rework_of") == "verify-failed-r35-contract-gap"
    ]
    assert resynth, "expected plan-level rework to re-emit synth trigger"
    assert resynth[-1].payload["review_artifact_ref"] == "artifacts/scan/review.md"
    assert resynth[-1].payload["replan_classification"] == "contract_freeze_gap"
    assert resynth[-1].payload["rework_source"] == "verify.failed"
    assert any("shape-only" in item for item in resynth[-1].payload["rework_feedback"])
    assert transport.sent == []


def test_evidenced_task_contract_blocker_resynthes_plan_without_human(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
        resynth_trigger="prd.scan.completed",
    )
    log.append(ZfEvent(
        id="scan-ready",
        type="prd.scan.completed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_index_ref": "artifacts/scan/source-index.json",
        },
    ))
    _start(orch)
    (state_dir / "artifacts" / "F-11111111" / "task_map.json").write_text(
        json.dumps({
            "tasks": [
                {
                    "task_id": "TASK-0",
                    "allowed_paths": ["src/core.py"],
                },
                {
                    "task_id": "TASK-1",
                    "allowed_paths": ["src/scheduler.py"],
                    "blocked_by": ["TASK-0"],
                },
                {
                    "task_id": "TASK-2",
                    "allowed_paths": ["src/ui.py"],
                    "blocked_by": ["TASK-1"],
                },
            ],
        }),
        encoding="utf-8",
    )
    blocked = ZfEvent(
        id="task-contract-blocked",
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "blocker_kind": "upstream_contract_gap",
            "blocked_on_task": "TASK-0",
            "blocked_on_paths": ["src/core.py"],
            "reason": "required core behavior is outside allowed_paths",
            "evidence_refs": ["git:abc123", "cmd:pytest blocker"],
        },
    )
    log.append(blocked)

    orch.run_once(events=[blocked])
    orch._run_candidate_rework_sweep()  # type: ignore[attr-defined]

    events = log.read_all()
    assert not any(event.type == "human.escalate" for event in events)
    requested = [
        event for event in events
        if event.type == "orchestrator.replan_requested"
        and event.payload.get("rework_of") == "task-contract-blocked"
    ]
    assert requested
    assert requested[-1].payload["failed_task_ids"] == ["TASK-0", "TASK-1"]
    assert requested[-1].payload["task_ids"] == ["TASK-0", "TASK-1", "TASK-2"]
    assert requested[-1].payload["downstream_task_ids"] == ["TASK-2"]
    assert requested[-1].payload["resume_scope"] == (
        "failed_children_and_downstream"
    )
    resynth = [
        event for event in events
        if event.type == "prd.scan.completed"
        and event.payload.get("rework_of") == "task-contract-blocked"
    ]
    assert resynth
    assert resynth[-1].payload["replan_classification"] == "design_issue"
    assert resynth[-1].payload["source_index_ref"] == "artifacts/scan/source-index.json"
    assert resynth[-1].payload["failed_task_ids"] == ["TASK-0", "TASK-1"]
    assert resynth[-1].payload["task_ids"] == ["TASK-0", "TASK-1", "TASK-2"]
    assert resynth[-1].payload["downstream_task_ids"] == ["TASK-2"]
    assert resynth[-1].payload["resume_scope"] == "failed_children_and_downstream"
    assert any("outside allowed_paths" in item for item in resynth[-1].payload["rework_feedback"])


def test_candidate_rework_sweep_uses_archived_runtime_events_after_rotation(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    archived = [
        ZfEvent(
            id="ready-before-rotation",
            type="task_map.ready",
            actor="zf-cli",
            correlation_id="trace-1",
            payload={
                "pdd_id": "F-11111111",
                "trace_id": "trace-1",
                "source_commit": "abc123",
                "candidate_base_commit": "abc123",
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            },
        ),
        ZfEvent(
            id="verify-child-before-rotation",
            type="verify.child.failed",
            actor="verify-1",
            correlation_id="trace-1",
            payload={
                "child_id": "verify-1-core",
                "trace_id": "trace-1",
                "reason": "fixture contract mismatch",
            },
        ),
        ZfEvent(
            id="verify-failed-before-rotation",
            type="verify.failed",
            actor="zf-cli",
            correlation_id="trace-1",
            payload={
                "pdd_id": "F-11111111",
                "trace_id": "trace-1",
                "target_ref": "cand/F-11111111",
            },
        ),
    ]
    (archive_dir / f"{_archive_day(1)}.jsonl").write_text(
        "".join(log._encode(event) + "\n" for event in archived),
        encoding="utf-8",
    )
    log.append(ZfEvent(type="orchestrator.decision.recorded", actor="zf-cli"))

    orch._run_candidate_rework_sweep()

    events = log.read_all()
    rework = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("rework_of") == "verify-failed-before-rotation"
    ]
    assert rework, "expected archived verify.failed to trigger candidate rework"
    assert rework[-1].payload["task_map_ref"] == ".zf/artifacts/F-11111111/task_map.json"
    assert rework[-1].payload["rework_feedback"] == [
        "verify-1-core: fixture contract mismatch"
    ]
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework[-1].id
    ]
    assert started, "expected rework task_map.ready to start writer fanout"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_candidate_rework_sweep_resumes_unstarted_rework_task_map(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    rework_ready_id = "rework-ready-without-fanout"
    log.append(ZfEvent(
        id=rework_ready_id,
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "rework_of": "failed-stale-task-map",
            "rework_attempt": 1,
            "rework_source": "fanout.child.failed",
        },
    ))

    orch._run_candidate_rework_sweep()

    events = log.read_all()
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework_ready_id
    ]
    assert started, "expected sweep to resume unstarted rework task_map.ready"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_candidate_rework_sweep_retries_stale_cancel_after_admission_recovers(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    for task_id in ("TASK-1", "TASK-2"):
        store.add(Task(
            id=task_id,
            title=task_id,
            status="backlog",
            contract=TaskContract(
                feature_id="F-11111111",
                evidence_contract={
                    "manual_evidence": {
                        "source_refs": {
                            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                        },
                    },
                },
            ),
        ))
    rework_ready_id = "rework-ready-stale-cancel"
    log.append(ZfEvent(
        id=rework_ready_id,
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "rework_of": "failed-stale-task-map",
            "rework_attempt": 1,
            "rework_source": "fanout.child.failed",
        },
    ))
    log.append(ZfEvent(
        type="fanout.cancelled",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "stage_id": "dev-fanout",
            "trigger_event_id": rework_ready_id,
            "pdd_id": "F-11111111",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "reason": "stale_task_map",
            "stale_task_ids": ["TASK-1", "TASK-2"],
        },
    ))

    orch._run_candidate_rework_sweep()

    events = log.read_all()
    started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("trigger_event_id") == rework_ready_id
    ]
    assert started, "expected stale cancellation to retry after admission recovers"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_candidate_rework_sweep_dedupes_equivalent_rework_task_maps(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(
        tmp_path,
        synthesize_canonical=True,
    )
    for event_id in ("rework-ready-a", "rework-ready-b"):
        log.append(ZfEvent(
            id=event_id,
            type="task_map.ready",
            actor="zf-cli",
            correlation_id="trace-1",
            payload={
                "pdd_id": "F-11111111",
                "trace_id": "trace-1",
                "source_commit": "abc123",
                "candidate_base_commit": "abc123",
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                "rework_of": "failed-stale-task-map",
                "rework_attempt": 1,
                "rework_source": "fanout.child.failed",
            },
        ))

    orch._run_candidate_rework_sweep()

    started = [
        event for event in log.read_all()
        if event.type == "fanout.started"
    ]
    assert len(started) == 1
    assert started[0].payload["trigger_event_id"] == "rework-ready-a"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_writer_briefing_omits_rework_section_on_fresh_dispatch(tmp_path: Path):
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "REWORK" not in briefing


def test_writer_fanout_briefing_includes_enabled_role_skills(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path, dev_skills=True)
    _seed_tasks(state_dir)

    _start(orch)

    dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    ]
    assert dispatched[0].payload["skills"] == ["zf-harness-state-sync"]
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Enabled Skills" in briefing
    assert "`/zf-harness-state-sync`" in briefing


def test_writer_fanout_claims_seeded_tasks_before_regular_dispatch(tmp_path: Path):
    state_dir, _log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    store = TaskStore(state_dir / "kanban.json")
    task1 = store.get("TASK-1")
    task2 = store.get("TASK-2")
    assert task1 is not None and task1.status == "in_progress"
    assert task1.assigned_to == "dev-1"
    # The fanout-writer dispatch MUST bind active_dispatch_id (= the child run
    # id), mirroring the normal dispatch path. Leaving it "" used to strand the
    # worker at the Stop-guard (provider.stop.check: active_dispatch_id gate is
    # unsatisfiable) so it could never cleanly stop after dev.build.done and the
    # lane never released for candidate-rework re-dispatch.
    assert task1.active_dispatch_id == f"run-{_fanout_id(_log)}-dev-1-TASK-1"
    assert task2 is not None and task2.status == "in_progress"
    assert task2.assigned_to == "dev-2"
    assert task2.active_dispatch_id == f"run-{_fanout_id(_log)}-dev-2-TASK-2"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_writer_fanout_retry_rebinds_canonical_task_dispatch_id(tmp_path: Path):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    child = _child(manifest, "TASK-1")
    previous_dispatch = next(
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
    )
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        assigned_to="orchestrator",
        active_dispatch_id="old-run",
    )

    orch._retry_fanout_child(  # type: ignore[attr-defined]
        manifest=manifest,
        child=child,
        previous_dispatch=previous_dispatch,
        attempt=1,
    )

    retry_run_id = f"run-{fanout_id}-{child['child_id']}-retry-1"
    task = store.get("TASK-1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "orchestrator"
    assert task.active_dispatch_id == "old-run"
    dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("run_id") == retry_run_id
    ]
    assert dispatched == []
    deferred = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
        and event.payload.get("run_id") == retry_run_id
    ]
    assert deferred


def test_writer_fanout_completion_keeps_canonical_task_active_for_verify(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    orch.run_once(events=[progress])

    task = TaskStore(state_dir / "kanban.json").get("TASK-1")
    assert task is not None
    assert task.status not in {"done", "cancelled"}
    assert any(
        task.id == "TASK-1"
        for task in TaskStore(state_dir / "kanban.json").list_all()
    )
    assert any(
        event.type == "fanout.child.completed"
        and event.payload.get("task_id") == "TASK-1"
        for event in log.read_all()
    )
    assert not any(
        event.type == "task.status_changed"
        and event.task_id == "TASK-1"
        and event.payload.get("trigger_event") == "fanout.child.completed"
        for event in log.read_all()
    )


def test_reader_operation_inherits_plan_package_identity_from_contract_snapshot(
    tmp_path: Path,
) -> None:
    state_dir, _log, _transport, orch = _state(tmp_path)
    task = Task(
        id="TASK-PACKAGE",
        title="package-bound task",
        contract=TaskContract(
            behavior="verify package-bound result",
            verification="true",
            acceptance_criteria=["result is current"],
            evidence_contract={
                "source_refs": {
                    "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                    "plan_artifact_package_id": "planpkg-abc",
                    "plan_artifact_package_ref": "artifacts/plan-packages/abc.json",
                    "plan_artifact_package_digest": "abc",
                },
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    target_commit = _git(tmp_path, "rev-parse", "HEAD")
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id="trace-package",
        task_map_generation_id=task_map_generation(task),
        base_commit=target_commit,
        task_ref="task/TASK-PACKAGE",
    )
    descriptor = write_task_contract_snapshot(state_dir, snapshot)
    child = SimpleNamespace(
        target_ref=target_commit,
        payload={
            "task_id": task.id,
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "target_commit": target_commit,
            "contract_snapshot_ref": descriptor["ref"],
            "contract_snapshot_digest": descriptor["sha256"],
        },
    )

    orch._prepare_reader_contract_target(child)  # type: ignore[attr-defined]

    assert child.payload["plan_artifact_package_id"] == "planpkg-abc"
    assert (
        child.payload["plan_artifact_package_ref"]
        == "artifacts/plan-packages/abc.json"
    )
    assert child.payload["plan_artifact_package_digest"] == "abc"


def test_selected_writer_call_settles_with_admitted_implementation_result(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    orch.config.workflow.flow_metadata = {
        "result_protocol": {"mode": "blocking"},
    }
    _start(orch)
    dispatch_events = log.read_all()
    operation_requests = [
        event for event in dispatch_events
        if event.type == "workflow.operation.requested"
        and event.payload.get("operation_type") == "fanout_writer_child"
    ]
    operation_started = [
        event for event in dispatch_events
        if event.type == "workflow.operation.started"
        and any(
            request.payload["operation_id"] == event.payload["operation_id"]
            for request in operation_requests
        )
    ]
    positions = {
        event.id: index for index, event in enumerate(dispatch_events)
    }
    assert len(operation_requests) == 2
    assert max(positions[event.id] for event in operation_requests) < min(
        positions[event.id] for event in operation_started
    )
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "role_instance": task1["role_instance"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
            "summary": "implemented task one",
            **{
                key: value
                for key, value in dict(task1.get("payload") or {}).items()
                if key in {
                    "workflow_run_id",
                    "operation_id",
                    "request_hash",
                        "attempt_id",
                        "attempt_domain",
                        "result_protocol_mode",
                        "plan_artifact_package_id",
                        "plan_artifact_package_ref",
                        "plan_artifact_package_digest",
                        "run_contract_ref",
                        "run_contract_digest",
                    "attempt_source_manifest_ref",
                    "attempt_source_manifest_digest",
                    "attempt_source_manifest",
                }
            },
        },
    )

    orch.run_once(events=[progress])

    events = log.read_all()
    admitted = next(
        event for event in events
        if event.type == "workflow.call.result.admitted"
        and event.task_id == "TASK-1"
    )
    settled = next(
        event for event in events
        if event.type == "workflow.operation.settled"
        and event.task_id == "TASK-1"
    )
    completed = next(
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("task_id") == "TASK-1"
    )
    assert admitted.payload["control_result_schema"] == "implementation-result.v1"
    assert settled.payload["admitted_call_result_ref"]["ref"] == (
        admitted.payload["envelope_ref"]["ref"]
    )
    assert completed.payload["admitted_call_result_ref"]["ref"] == (
        admitted.payload["envelope_ref"]["ref"]
    )
    assert not any("rework" in event.type for event in events)


def test_run_once_recovers_overwritten_writer_fanout_dispatch(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_ids = ("TASK-1", "TASK-2", "TASK-3", "TASK-4")
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": task_id,
                "scope": f"slice-{index}",
                "affinity_tag": f"slice-{index}",
                "allowed_paths": [f"slice-{index}.txt"],
            }
            for index, task_id in enumerate(task_ids, start=1)
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=task_ids)
    _start(orch)

    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit = _commit(
        Path(task1["workdir"]),
        "slice-1.txt",
        "TASK-1\n",
        "task-1",
    )
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )])

    manifest = _manifest(state_dir, fanout_id)
    task3 = _child(manifest, "TASK-3")
    task4 = _child(manifest, "TASK-4")
    assert task3["status"] == "dispatched"
    assert task4["status"] == "queued"
    assert task3["role_instance"] == task1["role_instance"]
    task4_run_id = f"run-{fanout_id}-{task4['child_id']}"
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": task4["child_id"],
            "run_id": task4_run_id,
            "role_instance": task3["role_instance"],
            "task_id": "TASK-4",
            "target_ref": task4["target_ref"],
            "lane_id": task3["lane_id"],
            "assignment_strategy": "affinity_stage_slots",
        },
        correlation_id="trace-1",
    ))
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": task4["child_id"],
            "run_id": task4_run_id,
            "role_instance": task3["role_instance"],
            "task_id": "TASK-4",
            "status": "completed",
            "result_event_id": "synthetic-task-4-done",
            "lane_id": task3["lane_id"],
            "assignment_strategy": "affinity_stage_slots",
        },
        correlation_id="trace-1",
    ))
    assert _child(_manifest(state_dir, fanout_id), "TASK-3")["status"] == "dispatched"
    assert _child(_manifest(state_dir, fanout_id), "TASK-4")["status"] == "completed"

    orch.run_once(events=[])

    retry_run_id = f"run-{fanout_id}-{task3['child_id']}-retry-1"
    events = log.read_all()
    assert any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("child_id") == task3["child_id"]
        and event.payload.get("overwritten_by_child_id") == task4["child_id"]
        for event in events
    )
    retry_dispatches = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("child_id") == task3["child_id"]
        and event.payload.get("run_id") == retry_run_id
    ]
    assert retry_dispatches
    final_task3 = _child(_manifest(state_dir, fanout_id), "TASK-3")
    assert final_task3["status"] == "dispatched"
    assert final_task3["run_id"] == retry_run_id
    task = TaskStore(state_dir / "kanban.json").get("TASK-3")
    assert task is not None
    assert task.assigned_to == task3["role_instance"]
    assert task.active_dispatch_id == retry_run_id
    assert [sent[0] for sent in transport.sent][-1] == task3["role_instance"]


def test_run_once_recovers_stale_writer_fanout_task_binding(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        assigned_to="orchestrator",
        active_dispatch_id="old-run",
    )
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"assignee": "orchestrator"},
    ))

    orch.run_once(events=[])

    task = store.get("TASK-1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == child["role_instance"]
    assert task.active_dispatch_id == child["run_id"]
    bound = [
        event for event in log.read_all()
        if event.type == "task.dispatch_context.bound"
        and event.task_id == "TASK-1"
        and event.payload.get("source") == "writer_fanout_task_binding_recovery"
    ]
    assert len(bound) == 1
    assert bound[0].payload["dispatch_id"] == child["run_id"]
    worker_states = [
        event for event in log.read_all()
        if event.type == "worker.state.changed"
        and event.actor == child["role_instance"]
    ]
    assert worker_states[-1].payload["to"] == "busy"
    assert worker_states[-1].task_id == "TASK-1"
    assert not [
        event for event in log.read_all()
        if event.type == "task.dispatched"
        and event.task_id == "TASK-1"
    ]

    orch.run_once(events=[])

    assert len([
        event for event in log.read_all()
        if event.type == "task.dispatch_context.bound"
        and event.task_id == "TASK-1"
        and event.payload.get("source") == "writer_fanout_task_binding_recovery"
    ]) == 1
    assert not [
        event for event in log.read_all()
        if event.type == "task.dispatched"
        and event.task_id == "TASK-1"
    ]


def test_affinity_stage_slots_queue_and_dispatch_next_on_lane_release(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "pi-core",
                "affinity_tag": "pi-core",
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway",
                "affinity_tag": "gateway",
                "allowed_paths": ["b.txt"],
            },
            {
                "task_id": "TASK-3",
                "scope": "web-tui",
                "affinity_tag": "web-tui",
                "allowed_paths": ["c.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1", "TASK-2", "TASK-3"))

    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    ]
    queued = [
        event for event in log.read_all()
        if event.type == "fanout.child.queued"
    ]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]
    assert len(dispatched) == 2
    assert len(queued) == 1
    assert queued[0].payload["task_id"] == "TASK-3"
    assert _child(manifest, "TASK-1")["lane_id"] == "lane0"
    assert _child(manifest, "TASK-2")["lane_id"] == "lane1"
    assert _child(manifest, "TASK-3")["status"] == "queued"

    task2 = _child(manifest, "TASK-2")
    commit2 = _commit(Path(task2["workdir"]), "b.txt", "TASK-2\n", "TASK-2")
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "dispatch_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit2,
            "source_branch": task2["source_branch"],
            "workdir": task2["workdir"],
        },
    )])

    final_manifest = _manifest(state_dir, fanout_id)
    task3 = _child(final_manifest, "TASK-3")
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "dev-2"]
    assert task3["status"] == "dispatched"
    assert task3["role_instance"] == "dev-2"
    assert task3["lane_id"] == "lane1"
    assert any(event.type == "fanout.slot.released" for event in log.read_all())
    assert [
        event.payload["task_id"] for event in log.read_all()
        if event.type == "fanout.slot.assigned"
    ] == ["TASK-1", "TASK-2", "TASK-3"]
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-3").assigned_to == "dev-2"


def test_affinity_stage_slots_honors_blocked_by_before_dispatch(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=3,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-SCAFFOLD",
                "scope": "scaffold",
                "affinity_tag": "scaffold",
                "allowed_paths": ["app/package.json"],
            },
            {
                "task_id": "TASK-CORE",
                "scope": "core",
                "affinity_tag": "core",
                "blocked_by": ["TASK-SCAFFOLD"],
                "allowed_paths": ["app/src/index.js"],
            },
            {
                "task_id": "TASK-TESTS",
                "scope": "tests",
                "affinity_tag": "tests",
                "blocked_by": ["TASK-CORE"],
                "allowed_paths": ["app/test/cli.test.js"],
            },
        ],
    }), encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    contract = TaskContract(
        feature_id="F-11111111",
        evidence_contract={
            "source_refs": {
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            },
        },
    )
    store.add(Task(
        id="TASK-SCAFFOLD",
        title="TASK-SCAFFOLD",
        status="backlog",
        contract=contract,
    ))
    store.add(Task(
        id="TASK-CORE",
        title="TASK-CORE",
        status="backlog",
        blocked_by=["TASK-SCAFFOLD"],
        contract=contract,
    ))
    store.add(Task(
        id="TASK-TESTS",
        title="TASK-TESTS",
        status="backlog",
        blocked_by=["TASK-CORE"],
        contract=contract,
    ))

    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    assert [sent[0] for sent in transport.sent] == ["dev-1"]
    assert _child(manifest, "TASK-SCAFFOLD")["status"] == "dispatched"
    assert _child(manifest, "TASK-CORE")["status"] == "queued"
    assert _child(manifest, "TASK-TESTS")["status"] == "queued"
    assert [
        event.payload["task_id"] for event in log.read_all()
        if event.type == "fanout.slot.assigned"
    ] == ["TASK-SCAFFOLD"]

    scaffold = _child(manifest, "TASK-SCAFFOLD")
    Path(scaffold["workdir"], "app").mkdir()
    commit = _commit(
        Path(scaffold["workdir"]),
        "app/package.json",
        "{}\n",
        "scaffold",
    )
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=scaffold["role_instance"],
        task_id="TASK-SCAFFOLD",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": scaffold["child_id"],
            "run_id": scaffold["run_id"],
            "dispatch_id": scaffold["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": scaffold["source_branch"],
            "workdir": scaffold["workdir"],
        },
    )])

    manifest_after = _manifest(state_dir, fanout_id)
    core = _child(manifest_after, "TASK-CORE")
    tests = _child(manifest_after, "TASK-TESTS")
    assert core["status"] == "dispatched"
    assert tests["status"] == "queued"
    assert core["lane_id"] == scaffold["lane_id"]
    assert [
        event.payload["task_id"] for event in log.read_all()
        if event.type == "fanout.slot.assigned"
    ] == ["TASK-SCAFFOLD", "TASK-CORE"]


def test_affinity_reconcile_fills_all_free_lanes_after_dependency_completion(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=2,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-ROOT",
                "affinity_tag": "root",
                "allowed_paths": ["root.txt"],
            },
            {
                "task_id": "TASK-A",
                "affinity_tag": "a",
                "blocked_by": ["TASK-ROOT"],
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-B",
                "affinity_tag": "b",
                "blocked_by": ["TASK-ROOT"],
                "allowed_paths": ["b.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(
        state_dir,
        task_ids=("TASK-ROOT", "TASK-A", "TASK-B"),
    )

    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    root = _child(manifest, "TASK-ROOT")
    assert [sent[0] for sent in transport.sent] == ["dev-1"]
    assert _child(manifest, "TASK-A")["status"] == "queued"
    assert _child(manifest, "TASK-B")["status"] == "queued"

    commit = _commit(
        Path(root["workdir"]),
        "root.txt",
        "done\n",
        "complete root",
    )
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=root["role_instance"],
        task_id="TASK-ROOT",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": root["child_id"],
            "run_id": root["run_id"],
            "dispatch_id": root["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": root["source_branch"],
            "workdir": root["workdir"],
        },
    )])

    reconciled = _manifest(state_dir, fanout_id)
    dispatched = [_child(reconciled, task_id) for task_id in ("TASK-A", "TASK-B")]
    assert {child["status"] for child in dispatched} == {"dispatched"}
    assert {child["lane_id"] for child in dispatched} == {"lane0", "lane1"}
    assert [sent[0] for sent in transport.sent[1:]] == ["dev-1", "dev-2"]


def test_affinity_pipeline_dependency_waits_for_final_task_terminal(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=2,
    )
    stage = orch.config.workflow.stages[0]
    stage.id = "dev-lanes-impl"
    orch.config.workflow.pipelines = [SimpleNamespace(
        pipeline_id="dev-lanes",
        stage_transition="per_lane",
        stages=(
            SimpleNamespace(stage_id="impl"),
            SimpleNamespace(stage_id="verify"),
        ),
    )]
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "TASK-ROOT", "allowed_paths": ["root.txt"]},
            {
                "task_id": "TASK-NEXT",
                "blocked_by": ["TASK-ROOT"],
                "allowed_paths": ["next.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-ROOT", "TASK-NEXT"))
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    _child(manifest, "TASK-ROOT")["status"] = "completed"
    manifest_path = state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert orch._reconcile_active_affinity_writer_fanouts() == 0  # type: ignore[attr-defined]
    assert _child(_manifest(state_dir, fanout_id), "TASK-NEXT")["status"] == "queued"

    orch.task_store.update("TASK-ROOT", status="done")
    orch._reconcile_active_affinity_writer_fanouts()  # type: ignore[attr-defined]
    assert _child(_manifest(state_dir, fanout_id), "TASK-NEXT")["status"] != "queued"
    assert any(
        event.type == "fanout.slot.assigned"
        and event.payload.get("task_id") == "TASK-NEXT"
        for event in log.read_all()
    )


def test_affinity_reconcile_recovers_ready_queue_after_missed_release(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=2,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "TASK-ROOT", "allowed_paths": ["root.txt"]},
            {
                "task_id": "TASK-A",
                "blocked_by": ["TASK-ROOT"],
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-B",
                "blocked_by": ["TASK-ROOT"],
                "allowed_paths": ["b.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(
        state_dir,
        task_ids=("TASK-ROOT", "TASK-A", "TASK-B"),
    )
    _start(orch)
    fanout_id = _fanout_id(log)
    root = _child(_manifest(state_dir, fanout_id), "TASK-ROOT")
    root_commit = _commit(
        Path(root["workdir"]),
        "root.txt",
        "root\n",
        "root dependency",
    )
    refs_dir = state_dir / "refs"
    refs_dir.mkdir(exist_ok=True)
    _git(tmp_path, "update-ref", "refs/heads/task/TASK-ROOT", root_commit)
    (refs_dir / "task-index.json").write_text(json.dumps({
        "TASK-ROOT": {
            "task_id": "TASK-ROOT",
            "task_ref": "task/TASK-ROOT",
            "source_commit": root_commit,
        },
    }), encoding="utf-8")
    TaskStore(state_dir / "kanban.json").update("TASK-ROOT", status="done")
    EventWriter(log).append(ZfEvent(
        type="fanout.child.completed",
        actor=root["role_instance"],
        task_id="TASK-ROOT",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "dev-fanout",
            "child_id": root["child_id"],
            "run_id": root["run_id"],
            "task_id": "TASK-ROOT",
            "lane_id": root["lane_id"],
            "stage_slot": "impl",
        },
    ))

    orch.run_once(events=[])

    recovered = _manifest(state_dir, fanout_id)
    children = [_child(recovered, task_id) for task_id in ("TASK-A", "TASK-B")]
    assert {child["status"] for child in children} == {"dispatched"}
    assert {child["lane_id"] for child in children} == {"lane0", "lane1"}
    assert len(transport.sent) == 3


def test_affinity_stage_slots_honors_task_affinity_tag_for_initial_dispatch(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=3,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "runtime",
                "owner_role": "dev-3",
                "affinity_tag": "lane2",
                "allowed_paths": ["runtime.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1",))

    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    child = _child(manifest, "TASK-1")
    assert [sent[0] for sent in transport.sent] == ["dev-3"]
    assert child["lane_id"] == "lane2"
    assert child["role_instance"] == "dev-3"
    assert TaskStore(state_dir / "kanban.json").get("TASK-1").assigned_to == "dev-3"


def test_affinity_stage_slots_missing_affinity_tag_falls_back_to_task_id(tmp_path: Path):
    # 语义变更(2026-07-04 prod-e2e):affinity_tag 缺失回退 task_id
    # (每任务独占 lane),不再整盘取消——合同从未要求该字段,fail-closed
    # 曾把合法 task_map 打成死端。task_id 也缺才 fail-closed。
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "pi-core",
                "allowed_paths": ["a.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1",))

    _start(orch)

    events = log.read_all()
    started = [event for event in events if event.type == "fanout.started"]
    assert started, "affinity_tag 缺失应回退 task_id 并照常开扇"
    dispatched = [
        event for event in events if event.type == "fanout.child.dispatched"
    ]
    assert dispatched and dispatched[0].payload.get("affinity_tag") == "TASK-1" or (
        dispatched and "TASK-1" in json.dumps(dispatched[0].payload)
    )


def test_product_delivery_wave_ready_triggers_writer_fanout_for_current_wave(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        trigger="product_delivery.wave.ready",
        task_map_template="",
    )
    task_map_ref = ".zf/artifacts/F-11111111/task-map.json"
    task_map_path = state_dir / "artifacts" / "F-11111111" / "task-map.json"
    task_map_path.parent.mkdir(parents=True, exist_ok=True)
    task_map_payload = {
        "schema_version": "task-map.v1",
        "feature_id": "F-11111111",
        "tasks": [
            {
                "task_id": "TASK-1",
                "title": "TASK-1",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["a.txt"],
                "verification": "test -f a.txt",
                "verification_tiers": ["runtime"],
            },
            {
                "task_id": "TASK-2",
                "title": "TASK-2",
                "owner_role": "dev",
                "wave": 2,
                "scope": ["b.txt"],
                "verification": "test -f b.txt",
                "verification_tiers": ["runtime"],
            },
        ],
    }
    task_map_path.write_text(json.dumps(task_map_payload), encoding="utf-8")
    result = ingest_task_map_to_kanban(
        state_dir,
        task_map_payload,
        task_map_ref=task_map_ref,
        writer=EventWriter(log),
    )
    assert result.passed is True
    wave_event = [
        event for event in log.read_all()
        if event.type == "product_delivery.wave.ready"
    ][-1]

    orch.run_once(events=[wave_event])

    dispatched = [event for event in log.read_all() if event.type == "fanout.child.dispatched"]
    assert [event.payload["task_id"] for event in dispatched] == ["TASK-1"]
    assert dispatched[0].payload["task_map_ref"] == task_map_ref
    assert dispatched[0].payload["feature_id"] == "F-11111111"
    assert [sent[0] for sent in transport.sent] == ["dev-1"]


def test_stale_writer_completion_is_rejected_before_candidate_integration(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        status="cancelled",
        blocked_reason="superseded by newer task map",
    )

    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )])

    events = log.read_all()
    failed = [event for event in events if event.type == "fanout.child.failed"]
    assert failed[-1].payload["reason"] == "superseded_task_map"
    assert failed[-1].payload["superseded_task_ids"] == ["TASK-1"]
    assert "candidate.ready" not in [event.type for event in events]


def test_writer_completion_after_run_terminal_is_audit_only(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task = _child(manifest, "TASK-1")
    commit = _commit(Path(task["workdir"]), "late.txt", "late\n", "late result")
    log.append(ZfEvent(
        id="goal-terminal",
        type="run.goal.completed",
        correlation_id="trace-1",
        payload={"workflow_run_id": "trace-1"},
    ))
    late = ZfEvent(
        id="late-writer-result",
        type="dev.build.done",
        actor=task["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "workflow_run_id": "trace-1",
            "fanout_id": fanout_id,
            "child_id": task["child_id"],
            "run_id": task["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": task["source_branch"],
            "workdir": task["workdir"],
        },
    )
    log.append(late)

    orch.run_once(events=[late])

    stale = [
        event for event in log.read_all()
        if event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == late.id
    ]
    assert stale
    assert stale[-1].payload["reason"] == "run_terminal"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] != "completed"


def test_writer_completion_creates_task_ref_and_candidate_excludes_failed_child(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task1 = _child(manifest, "TASK-1")
    task2 = _child(manifest, "TASK-2")
    workdir1 = Path(task1["workdir"])
    commit1 = _commit(workdir1, "a.txt", "TASK-1\n", "TASK-1")

    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )])
    orch.run_once(events=[ZfEvent(
        type="dev.blocked",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "reason": "blocked",
            "report": {
                "status": "failed",
                "recommendation": "reject",
                "summary": "gateway parity failed",
                "findings": [
                    {
                        "finding_id": "gateway-offsets",
                        "task_id": "TASK-2",
                        "severity": "high",
                        "category": "parity_gap",
                        "message": "delivery chunk offsets must be UTF-16 based",
                        "verification_command": "npm test -- gateway",
                    },
                ],
            },
        },
    )])

    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    assert index["TASK-1"]["source_commit"] == commit1
    assert index["TASK-1"]["task_ref"] == "task/TASK-1"
    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["aggregate"]["status"] == "failed"
    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "TASK-1"
    missing = subprocess.run(
        ["git", "show", "candidate/F-11111111:b.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert any(event.type == "integration.failed" for event in log.read_all())
    failed = [
        event for event in log.read_all()
        if event.type == "integration.failed"
    ][-1]
    assert failed.payload["findings"][0]["finding_id"] == "gateway-offsets"
    assert failed.payload["findings"][0]["task_id"] == "TASK-2"


def test_pending_handoff_reconcile_completes_writer_fanout_child_after_task_ref(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    log.append(progress)
    orch._processed_event_ids.add(progress.id)  # type: ignore[attr-defined]

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    events = log.read_all()
    completed = [
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert completed
    assert completed[-1].payload["result_event_id"] == progress.id
    assert completed[-1].payload["task_ref"] == "task/TASK-1"
    assert completed[-1].payload["source_commit"] == commit1
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_run_once_replays_missed_writer_completion(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task1 = _child(manifest, "TASK-1")
    task2 = _child(manifest, "TASK-2")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    commit2 = _commit(Path(task2["workdir"]), "b.txt", "TASK-2\n", "TASK-2")

    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )])

    missed = ZfEvent(
        type="dev.build.done",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit2,
            "source_branch": task2["source_branch"],
            "workdir": task2["workdir"],
        },
    )
    log.append(missed)
    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=orch.config,
    ).process_dev_build_done(missed)
    assert result is not None and result.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-2",
        payload=result.payload,
        causation_id=missed.id,
        correlation_id=missed.correlation_id,
    ))
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == missed.id
    ]

    orch.run_once(events=[])

    events = log.read_all()
    completed = [
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == missed.id
    ]
    assert completed
    assert completed[-1].payload["child_id"] == task2["child_id"]
    assert _child(_manifest(state_dir, fanout_id), "TASK-2")["status"] == "completed"
    assert any(
        event.type == "candidate.ready"
        and event.payload.get("fanout_id") == fanout_id
        for event in events
    )


def test_run_once_recovers_incomplete_writer_aggregate(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    writer = EventWriter(log)
    for task_id, file_name in (("TASK-1", "a.txt"), ("TASK-2", "b.txt")):
        task = _child(_manifest(state_dir, fanout_id), task_id)
        commit = _commit(
            Path(task["workdir"]),
            file_name,
            f"{task_id}\n",
            task_id,
        )
        progress = ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commit,
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )
        log.append(progress)
        result = TaskRefManager(
            state_dir=state_dir,
            project_root=tmp_path,
            config=orch.config,
        ).process_dev_build_done(progress)
        assert result is not None and result.status == "updated"
        log.append(ZfEvent(
            type="task.ref.updated",
            actor="zf-cli",
            task_id=task_id,
            payload=result.payload,
            causation_id=progress.id,
            correlation_id=progress.correlation_id,
        ))
        writer.append(ZfEvent(
            type="fanout.child.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": "trace-1",
                "stage_id": "dev-fanout",
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "role_instance": task["role_instance"],
                "task_id": task_id,
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                "status": "completed",
                "result_event_id": progress.id,
                "task_ref": f"task/{task_id}",
                "source_commit": commit,
            },
            causation_id=progress.id,
            correlation_id="trace-1",
        ))

    writer.append(ZfEvent(
        type="fanout.aggregate.started",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "mode": "candidate_integration",
        },
        correlation_id="trace-1",
    ))
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]

    orch.run_once(events=[])

    events = log.read_all()
    assert any(
        event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("status") == "completed"
        for event in events
    )
    assert any(
        event.type == "candidate.ready"
        and event.payload.get("fanout_id") == fanout_id
        for event in events
    )


def test_writer_completion_without_fanout_payload_uses_task_id_lookup(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    orch.run_once(events=[progress])

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert completed
    assert completed[-1].payload["fanout_id"] == fanout_id
    assert completed[-1].payload["child_id"] == task1["child_id"]
    assert completed[-1].payload["task_ref"] == "task/TASK-1"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_duplicate_writer_completion_does_not_reprocess_task_ref(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    payload = {
        "fanout_id": fanout_id,
        "child_id": task1["child_id"],
        "run_id": task1["run_id"],
        "pdd_id": "F-11111111",
        "source_commit": commit1,
        "source_branch": task1["source_branch"],
        "workdir": task1["workdir"],
    }
    first = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload=payload,
    )
    orch.run_once(events=[first])
    duplicate = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload=payload,
    )

    orch.run_once(events=[duplicate])

    events = log.read_all()
    assert len([
        event for event in events
        if event.type == "task.ref.updated" and event.task_id == "TASK-1"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == task1["child_id"]
    ]) == 1
    assert not [
        event for event in events
        if event.type in {"task.ref.rejected", "task.ref.repair.requested"}
        and event.task_id == "TASK-1"
    ]


def test_old_dispatch_completion_does_not_bind_current_dispatched_child(
    tmp_path: Path,
):
    state_dir, _log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(EventLog(state_dir / "events.jsonl"))
    current = _child(_manifest(state_dir, fanout_id), "TASK-1")
    stale = ZfEvent(
        type="dev.build.done",
        actor=current["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": "disp-obsolete-generation",
            "source_commit": "old-commit",
            "source_branch": current["source_branch"],
            "workdir": current["workdir"],
        },
    )

    bound = orch._fanout_result_payload(stale)  # type: ignore[attr-defined]

    assert not bound.get("fanout_id")
    assert not bound.get("child_id")


def test_regular_dispatch_completion_adopts_current_fenced_pending_child(
    tmp_path: Path,
):
    """A normal scheduler dispatch may win the race before writer fanout claim.

    Its exact completion must satisfy the one pending current-generation child
    instead of being ignored and causing a duplicate writer dispatch.
    """
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    regular_dispatch_id = "disp-regular-task-1"
    regular_dispatch = ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "role": "dev-1",
            "assignee": "dev-1",
            "dispatch_id": regular_dispatch_id,
        },
    )
    log.append(regular_dispatch)
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id=regular_dispatch_id,
    )

    _start(orch)

    fanout_id = _fanout_id(log)
    pending = _child(_manifest(state_dir, fanout_id), "TASK-1")
    assert pending["status"] == "pending"
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("task_id") == "TASK-1"
    ]
    progress = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": regular_dispatch_id,
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
    )
    log.append(progress)

    bound = orch._fanout_result_payload(progress)  # type: ignore[attr-defined]
    assert bound["fanout_id"] == fanout_id
    assert bound["child_id"] == pending["child_id"]
    assert bound["_writer_fanout_adopted_dispatch_id"] == regular_dispatch_id

    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]
    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]

    adopted = [
        event for event in log.read_all()
        if event.type == "fanout.child.completion_adopted"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert len(adopted) == 1
    assert adopted[0].payload["fanout_id"] == fanout_id
    assert adopted[0].payload["child_id"] == pending["child_id"]
    assert adopted[0].payload["adopted_from"] == (
        f"task_dispatch:{regular_dispatch_id}"
    )
    assert adopted[0].payload["reason"] == "active_task_attempt_fence"


def test_regular_completion_does_not_adopt_pending_child_without_dispatch_proof(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    alive = {"dev-1": False, "dev-2": True}
    transport.is_alive = lambda role_name: alive.get(role_name, True)  # type: ignore[method-assign]
    orch._respawn_instance = lambda role: OrchestratorDecision(  # type: ignore[method-assign]
        action="defer",
        role=role.instance_id,
        reason="test unavailable",
    )
    _start(orch)
    progress = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        payload={"dispatch_id": "disp-not-in-ledger"},
    )

    bound = orch._fanout_result_payload(progress)  # type: ignore[attr-defined]

    assert not bound.get("fanout_id")
    assert not bound.get("child_id")


def test_adopted_regular_completion_mints_typed_verify_target(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        harness_profile="baseline",
    )
    orch._typed_task_contract_handoff_enabled = lambda _payload: True  # type: ignore[method-assign]
    orch.config.workflow.impl_self_check_required = True
    _seed_tasks(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        contract=TaskContract(
            feature_id="F-11111111",
            scope=["a.txt"],
            acceptance_criteria=["a.txt contains the delivered result"],
            verification="test -f a.txt",
            verification_tiers=["task_non_smoke"],
            evidence_contract={
                "source_refs": {
                    "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                },
            },
        ),
    )
    base_commit = _git(tmp_path, "rev-parse", "HEAD")
    dispatch_id = "disp-typed-regular-task-1"
    dispatch = ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "role": "dev-1",
            "assignee": "dev-1",
            "dispatch_id": dispatch_id,
            "base_git_head": base_commit,
        },
    )
    log.append(dispatch)
    store.update(
        "TASK-1",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id=dispatch_id,
    )
    _start(orch)
    fanout_id = _fanout_id(log)
    pending = _child(_manifest(state_dir, fanout_id), "TASK-1")
    assert pending["status"] == "pending"
    canonical_task = store.get("TASK-1")
    assert canonical_task is not None
    contract_snapshot = build_task_contract_snapshot(
        canonical_task,
        workflow_run_id="trace-1",
        task_map_generation_id=task_map_generation(canonical_task),
        base_commit=base_commit,
        task_ref="task/TASK-1",
    )
    contract_descriptor = write_task_contract_snapshot(state_dir, contract_snapshot)
    command = contract_snapshot["verification_commands"][0]
    criterion = contract_snapshot["acceptance_criteria"][0]
    source_commit = _commit(tmp_path, "a.txt", "delivered\n", "deliver TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": dispatch_id,
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "source_branch": "main",
            "source_commit": source_commit,
            "attempt_id": dispatch_id,
            "impl_self_check": {
                "schema_version": "impl-self-check.v1",
                "workflow_run_id": "trace-1",
                "task_id": "TASK-1",
                "attempt_id": dispatch_id,
                "contract_revision": contract_snapshot["contract_revision"],
                "task_map_generation": contract_snapshot["task_map_generation"],
                "source_commit": source_commit,
                "target_commit": source_commit,
                "contract_snapshot_ref": contract_descriptor["ref"],
                "contract_snapshot_digest": contract_descriptor["sha256"],
                "command_receipts": [{
                    "receipt_id": "receipt-contract",
                    "command_id": command["command_id"],
                    "command_digest": command["command_digest"],
                    "target_commit": source_commit,
                    "status": "passed",
                    "exit_code": 0,
                    "evidence_refs": ["evt-command-pass"],
                }],
                "acceptance_results": [{
                    "acceptance_id": criterion["acceptance_id"],
                    "status": "passed",
                    "command_receipt_ids": ["receipt-contract"],
                    "evidence_refs": ["evt-ac-pass"],
                    "residual_risks": [],
                }],
                "evidence_refs": ["evt-impl-summary"],
                "residual_risks": [],
            },
        },
    )
    log.append(progress)
    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]
    ref_updated = ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        causation_id=progress.id,
        payload={
            "task_id": "TASK-1",
            "task_ref": "task/TASK-1",
            "trigger_event_id": progress.id,
            "source_branch": "main",
            "source_commit": source_commit,
        },
    )
    log.append(ref_updated)

    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]

    completed = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.child.completed"
        and event.payload.get("task_id") == "TASK-1"
    )
    assert completed.payload["base_commit"] == base_commit
    assert completed.payload["target_commit"] == source_commit
    assert completed.payload["contract_snapshot_ref"]
    assert completed.payload["contract_snapshot_digest"]
    assert completed.payload["target_snapshot_ref"]
    assert completed.payload["target_snapshot_digest"]
    assert completed.payload["impl_self_check_ref"]
    assert completed.payload["impl_self_check_digest"]
    self_check_event = next(
        event for event in log.read_all()
        if event.type == "impl.self_check.completed"
    )
    assert self_check_event.payload["target_commit"] == source_commit
    assert "impl_self_check" not in self_check_event.payload
    result, error = orch._normalize_lane_verification_result(  # type: ignore[attr-defined]
        completed.payload,
        manifest=_manifest(state_dir, fanout_id),
    )
    assert result is None
    assert error == ""
    target = json.loads(
        (state_dir / completed.payload["target_snapshot_ref"]).read_text(
            encoding="utf-8",
        )
    )
    assert target["target_commit"] == source_commit
    assert target["base_commit"] == base_commit


def test_writer_fanout_does_not_repeat_failed_result_for_same_event(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        contract=TaskContract(
            feature_id="F-11111111",
            evidence_contract={
                "source_refs": {
                    "task_map_ref": ".zf/artifacts/OTHER/task_map.json",
                },
            },
        ),
    )
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    orch.run_once(events=[progress])
    orch.run_once(events=[progress])

    failed = [
        event for event in log.read_all()
        if event.type == "fanout.child.failed"
        and event.payload.get("child_id") == task1["child_id"]
        and event.causation_id == progress.id
    ]
    assert len(failed) == 1
    assert failed[0].payload["reason"] == "stale_task_map"


def test_static_gate_reconcile_completes_writer_fanout_child_from_trigger_event(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    log.append(progress)
    task_ref = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        progress
    )
    assert task_ref is not None
    assert task_ref.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref.payload,
        causation_id=progress.id,
    ))
    gate = ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "passed": True,
            "trigger_event_id": progress.id,
            "trigger_event_type": "dev.build.done",
            "dispatch_id": task1["run_id"],
        },
        causation_id=progress.id,
    )
    log.append(gate)

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert completed
    assert completed[-1].payload["fanout_id"] == fanout_id
    assert completed[-1].payload["child_id"] == task1["child_id"]
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_dev_build_done_waits_for_late_task_ref_updated(tmp_path: Path):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    log.append(progress)
    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]
    terminal = [
        event for event in log.read_all()
        if event.type in {"fanout.child.failed", "fanout.child.completed"}
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert terminal == []

    task_ref = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        progress
    )
    assert task_ref is not None
    assert task_ref.status == "updated"
    ref_updated = ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref.payload,
        causation_id=progress.id,
    )
    log.append(ref_updated)

    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert completed[-1].payload["result_event_id"] == progress.id
    assert "recovered_from_status" not in completed[-1].payload
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_writer_completion_rejects_stale_task_ref_from_prior_attempt(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    workdir = Path(task1["workdir"])
    old_commit = _commit(workdir, "a.txt", "old\n", "old attempt")
    old_progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "source_commit": old_commit,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    old_ref = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=orch.config,
    ).process_dev_build_done(old_progress)
    assert old_ref is not None and old_ref.status == "updated"

    new_commit = _commit(workdir, "a.txt", "new\n", "new attempt")
    new_progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "source_commit": new_commit,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    orch._maybe_update_writer_fanout(new_progress)  # type: ignore[attr-defined]

    terminal = [
        event for event in log.read_all()
        if event.type in {"fanout.child.failed", "fanout.child.completed"}
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert terminal == []

    new_ref = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=orch.config,
    ).process_dev_build_done(new_progress)
    assert new_ref is not None and new_ref.status == "updated"
    ref_updated = ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=new_ref.payload,
        causation_id=new_progress.id,
    )
    log.append(ref_updated)
    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert completed[-1].payload["source_commit"] == new_commit


def test_late_task_ref_repair_replaces_completed_writer_child_without_identity(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    workdir = Path(task1["workdir"])
    commit1 = _commit(workdir, "a.txt", "TASK-1\n", "TASK-1")
    progress1 = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )
    log.append(progress1)
    task_ref1 = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        progress1
    )
    assert task_ref1 is not None
    assert task_ref1.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref1.payload,
        causation_id=progress1.id,
    ))
    orch._maybe_update_writer_fanout(progress1)  # type: ignore[attr-defined]
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"

    commit2 = _commit(workdir, "a.txt", "TASK-1 repaired\n", "TASK-1 repair")
    progress2 = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": "repair-dispatch",
            "source_commit": commit2,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
            "files_touched": ["a.txt"],
        },
    )
    log.append(progress2)
    task_ref2 = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        progress2
    )
    assert task_ref2 is not None
    assert task_ref2.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref2.payload,
        causation_id=progress2.id,
    ))

    orch._maybe_update_writer_fanout(progress2)  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert completed[-1].payload["result_event_id"] == progress2.id
    assert completed[-1].payload["recovered_from_status"] == "completed"
    assert (
        completed[-1].payload["recovery_reason"]
        == "late_dev_build_done_replaces_completed_child"
    )
    updated_child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    assert updated_child["result_event_id"] == progress2.id
    assert updated_child["source_commit"] == commit2


def test_late_completed_child_repair_refreshes_completed_writer_candidate(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task1 = _child(manifest, "TASK-1")
    task2 = _child(manifest, "TASK-2")

    def complete_child(task: dict, file_name: str, content: str) -> ZfEvent:
        commit = _commit(Path(task["workdir"]), file_name, content, task["task_id"])
        progress = ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task["task_id"],
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commit,
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )
        log.append(progress)
        task_ref = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
            progress
        )
        assert task_ref is not None and task_ref.status == "updated"
        log.append(ZfEvent(
            type="task.ref.updated",
            actor="zf-cli",
            task_id=task["task_id"],
            payload=task_ref.payload,
            causation_id=progress.id,
        ))
        orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]
        return progress

    complete_child(task1, "a.txt", "TASK-1\n")
    complete_child(task2, "b.txt", "TASK-2\n")
    first_ready = [
        event for event in log.read_all()
        if event.type == "candidate.ready"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(first_ready) == 1

    repair_commit = _commit(
        Path(task1["workdir"]),
        "a.txt",
        "TASK-1 repaired\n",
        "TASK-1 repair",
    )
    repair = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": "repair-dispatch",
            "source_commit": repair_commit,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
            "files_touched": ["a.txt"],
        },
    )
    log.append(repair)
    task_ref = orch._process_task_ref_for_progress_event(  # type: ignore[attr-defined]
        repair
    )
    assert task_ref is not None and task_ref.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=task_ref.payload,
        causation_id=repair.id,
    ))

    orch._maybe_update_writer_fanout(repair)  # type: ignore[attr-defined]

    ready = [
        event for event in log.read_all()
        if event.type == "candidate.ready"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(ready) == 2
    aggregates = [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert aggregates[-1].payload["recovered_from_aggregate_status"] == "completed"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["source_commit"] == repair_commit


def test_stale_writer_completion_from_superseded_fanout_is_adopted(
    tmp_path: Path,
):
    """BF-1(r6.1 16:42 实弹):fanout 换代期间到达的真交付跨代收编,
    不再作为 stale_completion 丢弃——当前代同 task 的 child 仍在等待。"""
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    new_fanout_id = "fanout-dev-fanout-new"
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = new_fanout_id
    new_payload["trigger_event_id"] = "task-map-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    log.append(progress)
    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]

    events = log.read_all()
    adopted = [
        event for event in events
        if event.type == "fanout.child.completion_adopted"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert len(adopted) == 1
    assert adopted[0].payload["fanout_id"] == new_fanout_id
    assert adopted[0].payload["adopted_from"] == fanout_id
    assert adopted[0].payload["reason"] == "superseded_by_latest_fanout"
    assert not [
        event for event in events
        if event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == progress.id
    ]
    # dev.build.done remains unsettled until the mechanical task-ref result.
    assert not [
        event for event in events
        if event.type in {"fanout.child.completed", "fanout.child.failed"}
        and event.payload.get("fanout_id") == new_fanout_id
    ]
    ref_rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        causation_id=progress.id,
        payload={
            "task_id": "TASK-1",
            "trigger_event_id": progress.id,
            "reason": "missing source_commit",
        },
    )
    orch._maybe_update_writer_fanout(ref_rejected)  # type: ignore[attr-defined]

    # The canonical rejection terminates the adopted child in the new fanout.
    events = log.read_all()
    assert [
        event for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("fanout_id") == new_fanout_id
        and event.payload.get("reason") == "missing source_commit"
    ]
    # 旧代 manifest 不被回写
    final_child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    assert final_child["status"] == "dispatched"


def test_recorded_writer_completion_is_not_adopted_into_later_generation(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
        },
    )
    log.append(progress)
    EventWriter(log).append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "task_id": "TASK-1",
            "result_event_id": progress.id,
        },
        causation_id=progress.id,
        correlation_id="trace-1",
    ))

    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]

    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.completion_adopted"
        and event.payload.get("result_event_id") == progress.id
    ]


def test_superseded_writer_fanout_manifest_is_closed_out_before_binding_recovery(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        status="in_progress",
        assigned_to="orchestrator",
        active_dispatch_id="old-run",
    )
    new_fanout_id = "fanout-dev-fanout-new"
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = new_fanout_id
    new_payload["trigger_event_id"] = "task-map-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))

    orch.run_once(events=[])
    orch.run_once(events=[])

    events = log.read_all()
    cancelled = [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "superseded_by_latest_fanout"
    assert cancelled[0].payload["superseded_by"] == new_fanout_id
    assert not [
        event for event in events
        if event.type == "task.dispatch_context.bound"
        and event.payload.get("source") == "writer_fanout_task_binding_recovery"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("child_id") == child["child_id"]
    ]
    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["status"] == "cancelled"
    assert final_manifest["aggregate"]["status"] == "cancelled"


def test_writer_slot_reconcile_cancels_older_replan_generation_before_dispatch(
    tmp_path: Path,
):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    _seed_tasks(state_dir)
    writer = EventWriter(log)
    old_fanout_id = "fanout-dev-old-replan"
    new_fanout_id = "fanout-dev-new-replan"
    old_run_id = f"run-{old_fanout_id}-old-task-1"
    common_payload = {
        "workflow_run_id": "workflow-1",
        "pdd_id": "F-11111111",
        "feature_id": "F-11111111",
        "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        "assignment_strategy": "affinity_stage_slots",
        "lane_profile": "refactor-2",
        "stage_slot": "impl",
    }
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="workflow-1",
        payload={
            "fanout_id": old_fanout_id,
            "stage_id": "dev-fanout",
            "topology": "fanout_writer_scoped",
            "trace_id": "workflow-1",
            "trigger_event_id": "task-map-old",
            "target_ref": "main",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "expected_children": [
                {
                    "child_id": "old-task-1",
                    "role_instance": "dev-1",
                    "target_ref": "main",
                    "payload": {
                        **common_payload,
                        "task_id": "TASK-1",
                        "scope": "core",
                        "affinity_tag": "pi-core",
                        "rework_attempt": 4,
                    },
                },
                {
                    "child_id": "old-task-2",
                    "role_instance": "dev-2",
                    "target_ref": "main",
                    "payload": {
                        **common_payload,
                        "task_id": "TASK-2",
                        "scope": "runtime",
                        "affinity_tag": "gateway",
                        "rework_attempt": 4,
                    },
                },
            ],
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="workflow-1",
        payload={
            "fanout_id": old_fanout_id,
            "stage_id": "dev-fanout",
            "child_id": "old-task-1",
            "run_id": old_run_id,
            "role_instance": "dev-1",
            "task_id": "TASK-1",
            "lane_id": "lane0",
            "stage_slot": "impl",
            "assignment_strategy": "affinity_stage_slots",
        },
    ))
    writer.append(ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-1",
        correlation_id="workflow-1",
        payload={
            "fanout_id": old_fanout_id,
            "child_id": "old-task-1",
            "run_id": old_run_id,
            "dispatch_id": old_run_id,
            "reason": "old generation ended before replan",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.queued",
        actor="zf-cli",
        task_id="TASK-2",
        correlation_id="workflow-1",
        payload={
            "fanout_id": old_fanout_id,
            "stage_id": "dev-fanout",
            "child_id": "old-task-2",
            "role_instance": "dev-2",
            "task_id": "TASK-2",
            "lane_profile": "refactor-2",
            "stage_slot": "impl",
            "assignment_strategy": "affinity_stage_slots",
        },
    ))
    target_commit = _git(tmp_path, "rev-parse", "HEAD")
    writer.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="workflow-1",
        payload={
            "fanout_id": new_fanout_id,
            "stage_id": "dev-fanout",
            "topology": "fanout_writer_scoped",
            "trace_id": "workflow-1",
            "trigger_event_id": "task-map-new",
            "target_ref": target_commit,
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "expected_children": [{
                "child_id": "new-task-2",
                "role_instance": "dev-2",
                "target_ref": target_commit,
                "payload": {
                    **common_payload,
                    "task_id": "TASK-2",
                    "scope": "runtime",
                    "affinity_tag": "gateway",
                    "rework_attempt": 5,
                },
            }],
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.queued",
        actor="zf-cli",
        task_id="TASK-2",
        correlation_id="workflow-1",
        payload={
            "fanout_id": new_fanout_id,
            "stage_id": "dev-fanout",
            "child_id": "new-task-2",
            "role_instance": "dev-2",
            "task_id": "TASK-2",
            "lane_profile": "refactor-2",
            "stage_slot": "impl",
            "assignment_strategy": "affinity_stage_slots",
        },
    ))
    store = TaskStore(state_dir / "kanban.json")
    store.update(
        "TASK-1",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id=old_run_id,
    )
    orch._last_worker_state["dev-1"] = "busy"  # type: ignore[attr-defined]
    orch._last_worker_task_id["dev-1"] = "TASK-1"  # type: ignore[attr-defined]
    stage = orch.config.workflow.stages[0]

    assert orch._reconcile_affinity_writer_slots(  # type: ignore[attr-defined]
        fanout_id=old_fanout_id,
        stage=stage,
        stage_slot="impl",
        causation_id="task-map-new",
    ) == 0
    assert transport.sent == []
    cancelled = [
        event for event in log.read_all()
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == old_fanout_id
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "superseded_by_newer_replan_attempt"
    assert cancelled[0].payload["superseded_by"] == new_fanout_id
    assert any(
        event.type == "fanout.child.dispatch_lost"
        and event.payload.get("run_id") == old_run_id
        for event in log.read_all()
    )
    released = store.get("TASK-1")
    assert released is not None
    assert released.active_dispatch_id == ""
    assert released.assigned_to == ""

    assert orch._reconcile_affinity_writer_slots(  # type: ignore[attr-defined]
        fanout_id=new_fanout_id,
        stage=stage,
        stage_slot="impl",
        causation_id="task-map-new",
    ) == 1
    assert len(transport.sent) == 1
    new_child = _child(_manifest(state_dir, new_fanout_id), "TASK-2")
    assert new_child["status"] == "dispatched"
    assert transport.sent[0][0] == new_child["role_instance"]


def test_orphan_writer_fanout_manifest_is_cancelled_not_rebound(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    fanout_id = "fanout-dev-fanout-orphan"
    fanout_dir = state_dir / "fanouts" / fanout_id
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(json.dumps({
        "fanout_id": fanout_id,
        "trace_id": "trace-orphan",
        "stage_id": "dev-fanout",
        "topology": "fanout_writer_scoped",
        "trigger_event_id": "missing-task-map-ready",
        "target_ref": "main",
        "pdd_id": "F-11111111",
        "feature_id": "F-11111111",
        "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        "status": "started",
        "aggregate": {"status": "pending"},
        "children": [{
            "child_id": "dev-1-TASK-1",
            "task_id": "TASK-1",
            "role_instance": "dev-1",
            "run_id": "run-orphan-TASK-1",
            "status": "dispatched",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        }],
    }), encoding="utf-8")

    orch._recover_writer_fanout_task_bindings()  # type: ignore[attr-defined]

    events = log.read_all()
    cancelled = [
        event for event in events
        if event.type == "fanout.cancelled"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "fanout_manifest_without_started_event"
    assert not [
        event for event in events
        if event.type == "task.dispatch_context.bound"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert TaskStore(state_dir / "kanban.json").get("TASK-1").status == "backlog"
    assert _manifest(state_dir, fanout_id)["status"] == "cancelled"


def test_heartbeat_with_stale_identity_is_not_adopted(
    tmp_path: Path,
):
    """BF-1 修补(断点续跑实弹):heartbeat 等观察型事件携带旧 fanout
    身份不得触发收编审计(假审计+无去重刷屏);走原 stale 路径。"""
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    new_payload = dict(started.payload)
    new_payload["fanout_id"] = "fanout-dev-fanout-new"
    new_payload["trigger_event_id"] = "task-map-new"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-2",
        payload=new_payload,
    ))
    heartbeat = ZfEvent(
        type="worker.heartbeat",
        actor=task1["role_instance"],
        task_id="TASK-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "status": "completed",
        },
    )

    orch._maybe_update_writer_fanout(heartbeat)  # type: ignore[attr-defined]

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.child.completion_adopted"
    ]
    stale = [
        event for event in events
        if event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == heartbeat.id
    ]
    assert len(stale) == 1


def test_rotated_run_completion_is_adopted_while_child_awaits(
    tmp_path: Path,
):
    """BF-1(r6.1 16:44 实弹):child 被重派换 run_id 后,携带旧 run_id
    的真交付被收编为当前 run 的完成,不再丢弃(丢弃曾致 6h 死锁)。"""
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    EventWriter(log).append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": task1["child_id"],
            "run_id": f"{task1['run_id']}-retry",
            "role_instance": task1["role_instance"],
            "task_id": "TASK-1",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
        correlation_id="trace-1",
    ))
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    orch.run_once(events=[progress])
    orch.run_once(events=[progress])

    events = log.read_all()
    adopted = [
        event for event in events
        if event.type == "fanout.child.completion_adopted"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert len(adopted) == 1
    assert adopted[0].payload["reason"] == "run_id_rotated"
    assert not [
        event for event in events
        if event.type == "fanout.child.stale_completion"
    ]
    completed = [
        event for event in events
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert len(completed) == 1
    assert completed[0].payload["run_id"] == f"{task1['run_id']}-retry"
    final_child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    assert final_child["status"] == "completed"
    assert final_child["run_id"] == f"{task1['run_id']}-retry"


def test_late_writer_completion_recovers_failed_fanout_child(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    EventWriter(log).append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "role_instance": task1["role_instance"],
            "task_id": "TASK-1",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "reason": "idle",
        },
        correlation_id="trace-1",
    ))
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "failed"
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "TASK-1\n", "TASK-1")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
        },
    )

    orch.run_once(events=[progress])

    recovered = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ][-1]
    assert recovered.payload["recovered_from_status"] == "failed"
    assert recovered.payload["recovery_reason"] == "late_dev_build_done_after_child_failed"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_task_ref_rework_completion_recovers_failed_child_without_fanout_identity(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "dev-fanout",
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "role_instance": task1["role_instance"],
            "task_id": "TASK-1",
            "reason": "dirty worktree",
        },
        correlation_id="trace-1",
    ))
    first_rework = writer.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "base_dispatch_id": task1["run_id"],
            "dispatch_id": "repair-1",
        },
        correlation_id="trace-1",
    ))
    writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "role": task1["role_instance"],
            "assignee": task1["role_instance"],
            "source": "rework",
            "trigger_event": "task.ref.repair.requested",
            "rework_request_event_id": first_rework.id,
            "dispatch_id": "repair-1",
        },
        correlation_id="trace-1",
    ))
    second_rework = writer.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "base_dispatch_id": "repair-1",
            "dispatch_id": "repair-2",
        },
        correlation_id="trace-1",
    ))
    writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "role": task1["role_instance"],
            "assignee": task1["role_instance"],
            "source": "rework",
            "trigger_event": "task.ref.repair.requested",
            "rework_request_event_id": second_rework.id,
            "dispatch_id": "repair-2",
        },
        correlation_id="trace-1",
    ))

    commit1 = _commit(Path(task1["workdir"]), "a.txt", "repaired\n", "repair")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": "repair-2",
            "summary": "clean task-ref repair handoff",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
            "files_touched": ["a.txt"],
        },
    )
    log.append(progress)
    ref = orch._process_task_ref_for_progress_event(progress)  # type: ignore[attr-defined]
    assert ref is not None and ref.status == "updated"
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload=ref.payload,
        causation_id=progress.id,
        correlation_id="trace-1",
    ))

    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]

    recovered = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ][-1]
    assert recovered.payload["recovered_from_status"] == "failed"
    assert recovered.payload["run_id"] == task1["run_id"]
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"


def test_unrelated_rework_dispatch_does_not_recover_failed_writer_child(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "role_instance": task1["role_instance"],
            "task_id": "TASK-1",
            "reason": "failed",
        },
    ))
    unrelated = writer.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "base_dispatch_id": "another-run",
            "dispatch_id": "unrelated-repair",
        },
    ))
    writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "role": task1["role_instance"],
            "assignee": task1["role_instance"],
            "source": "rework",
            "trigger_event": "task.ref.repair.requested",
            "rework_request_event_id": unrelated.id,
            "dispatch_id": "unrelated-repair",
        },
    ))
    commit1 = _commit(Path(task1["workdir"]), "a.txt", "unrelated\n", "unrelated")
    progress = ZfEvent(
        type="dev.build.done",
        actor=task1["role_instance"],
        task_id="TASK-1",
        payload={
            "dispatch_id": "unrelated-repair",
            "source_commit": commit1,
            "source_branch": task1["source_branch"],
            "workdir": task1["workdir"],
            "files_touched": ["a.txt"],
        },
    )

    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]

    assert not [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == progress.id
    ]
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "failed"


def test_timeout_writer_fanout_aggregate_can_recover_after_all_late_completions(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    writer = EventWriter(log)
    for task_id in ("TASK-1", "TASK-2"):
        task = _child(manifest, task_id)
        writer.append(ZfEvent(
            type="fanout.child.failed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": "trace-1",
                "stage_id": "dev-fanout",
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "role_instance": task["role_instance"],
                "task_id": task_id,
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                "reason": "timeout",
            },
            correlation_id="trace-1",
        ))
    writer.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "trace_id": "trace-1",
            "stage_id": "dev-fanout",
            "status": "failed",
            "reason": "timeout",
            "failure_event": "integration.failed",
            "pending_children": [task["child_id"] for task in manifest["children"]],
            "timeout_seconds": 120,
        },
        correlation_id="trace-1",
    ))

    commits = {}
    for task_id, file_name in (("TASK-1", "a.txt"), ("TASK-2", "b.txt")):
        task = _child(manifest, task_id)
        commits[task_id] = _commit(
            Path(task["workdir"]),
            file_name,
            f"{task_id}\n",
            task_id,
        )
        orch.run_once(events=[ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commits[task_id],
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )])

    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["aggregate"]["status"] == "completed"
    assert final_manifest["aggregate"]["recovered_from_aggregate_status"] == "failed"
    assert any(event.type == "candidate.ready" for event in log.read_all())


def test_writer_candidate_ready_payload_matches_schema_contract(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    commits = {}
    for task_id, file_name in (("TASK-1", "a.txt"), ("TASK-2", "b.txt")):
        task = _child(manifest, task_id)
        commits[task_id] = _commit(
            Path(task["workdir"]),
            file_name,
            f"{task_id}\n",
            task_id,
        )
        orch.run_once(events=[ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commits[task_id],
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )])

    candidate_ready = [
        event for event in log.read_all()
        if event.type == "candidate.ready"
    ][-1]
    payload = candidate_ready.payload
    assert payload["pdd_id"] == "F-11111111"
    assert payload["feature_id"] == "F-11111111"
    assert payload["candidate_ref"] == "candidate/F-11111111"
    assert payload["candidate_base_commit"]
    assert payload["candidate_head_commit"]
    assert payload["diff_ref"] == (
        f"{payload['candidate_base_commit']}..{payload['candidate_head_commit']}"
    )
    assert payload["completed_task_ids"] == ["TASK-1", "TASK-2"]
    assert payload["task_map_ref"] == ".zf/artifacts/F-11111111/task_map.json"
    assert payload["quality_status"] == "skipped"
    assert payload["quality_check_count"] == 0
    assert payload["quality_gates_passed"] == []
    assert payload["quality_gates_failed"] == []


def test_lane_final_ready_targets_candidate_ref_from_root_aggregate(
    tmp_path: Path,
) -> None:
    state_dir, log, _transport, orch = _state(tmp_path)
    root_fanout_id = "fanout-prd-lanes-impl-x"
    manifest_dir = state_dir / "fanouts" / root_fanout_id
    manifest_dir.mkdir(parents=True)
    manifest_dir.joinpath("manifest.json").write_text(json.dumps({
        "fanout_id": root_fanout_id,
        "trace_id": "trace-1",
        "stage_id": "prd-lanes-impl",
        "target_ref": "main",
        "pdd_id": "F-11111111",
        "feature_id": "F-11111111",
        "children": [{"task_id": "TASK-1"}],
        "aggregate": {
            "status": "completed",
            "candidate_ref": "candidate/F-11111111",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "diff_ref": "base123..head456",
        },
    }), encoding="utf-8")
    lane_event = ZfEvent(
        type="lane.stage.completed",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "pipeline_id": "prd-lanes",
            "root_fanout_id": root_fanout_id,
            "stage_slot": "verify",
            "task_id": "TASK-1",
            "status": "completed",
        },
    )
    log.append(lane_event)
    pipeline = SimpleNamespace(
        pipeline_id="prd-lanes",
        stages=[
            SimpleNamespace(stage_id="impl"),
            SimpleNamespace(stage_id="verify"),
        ],
    )

    ready_event = orch._maybe_publish_lane_stage_final_ready(
        lane_event=lane_event,
        pipeline=pipeline,
    )

    assert ready_event is not None
    assert ready_event.type == "test.passed"
    assert ready_event.payload["target_ref"] == "candidate/F-11111111"
    assert ready_event.payload["candidate_ref"] == "candidate/F-11111111"
    assert ready_event.payload["candidate_head_commit"] == "head456"
    assert ready_event.payload["diff_ref"] == "base123..head456"


def test_writer_candidate_conflict_fails_fanout_aggregate(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task1 = _child(manifest, "TASK-1")
    task2 = _child(manifest, "TASK-2")
    commits = {
        "TASK-1": _commit(Path(task1["workdir"]), "README.md", "one\n", "TASK-1"),
        "TASK-2": _commit(Path(task2["workdir"]), "README.md", "two\n", "TASK-2"),
    }

    for task in (task1, task2):
        task_id = task["task_id"]
        orch.run_once(events=[ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commits[task_id],
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )])

    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["aggregate"]["status"] == "failed"
    assert final_manifest["aggregate"]["candidate_status"] == "conflict"
    assert any(event.type == "candidate.conflict" for event in log.read_all())
    assert any(event.type == "integration.failed" for event in log.read_all())


def test_failed_writer_candidate_aggregate_retries_after_new_task_ref(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    task1 = _child(manifest, "TASK-1")
    task2 = _child(manifest, "TASK-2")
    commits = {
        "TASK-1": _commit(Path(task1["workdir"]), "README.md", "one\n", "TASK-1"),
        "TASK-2": _commit(Path(task2["workdir"]), "README.md", "two\n", "TASK-2"),
    }

    for task in (task1, task2):
        task_id = task["task_id"]
        orch.run_once(events=[ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commits[task_id],
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )])
    assert _manifest(state_dir, fanout_id)["aggregate"]["status"] == "failed"
    failed = [
        event for event in log.read_all()
        if event.type == "integration.failed"
        and event.payload.get("fanout_id") == fanout_id
    ][-1]

    task2_workdir = Path(task2["workdir"])
    _git(task2_workdir, "checkout", "-q", "-B", "worker/TASK-2-clean", "main")
    clean_commit = _commit(task2_workdir, "b.txt", "two\n", "TASK-2 clean")
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "source_commit": clean_commit,
            "source_branch": "worker/TASK-2-clean",
            "workdir": task2["workdir"],
        },
    )])
    log.append(ZfEvent(
        type="workflow.resume.applied",
        actor="zf-cli",
        task_id="TASK-2",
        payload={
            "source_event_id": failed.id,
            "safe_resume_action": "needs_stage_dispatch",
        },
    ))

    orch.run_once(events=[])

    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["aggregate"]["status"] == "completed"
    assert final_manifest["aggregate"]["recovered_from_aggregate_status"] == "failed"
    assert final_manifest["aggregate"]["recovered_from_aggregate_reason"] == (
        "retry_requested"
    )
    assert any(
        event.type == "candidate.ready"
        and event.payload.get("fanout_id") == fanout_id
        for event in log.read_all()
    )


def test_writer_candidate_quality_failure_blocks_candidate_ready(tmp_path: Path):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["test -f missing.txt"],
            ),
        },
    )
    _seed_tasks(state_dir)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    commits = {}
    for task_id in ("TASK-1", "TASK-2"):
        task = _child(manifest, task_id)
        file_name = "a.txt" if task_id == "TASK-1" else "b.txt"
        commits[task_id] = _commit(
            Path(task["workdir"]),
            file_name,
            f"{task_id}\n",
            task_id,
        )
        orch.run_once(events=[ZfEvent(
            type="dev.build.done",
            actor=task["role_instance"],
            task_id=task_id,
            correlation_id="trace-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": task["child_id"],
                "run_id": task["run_id"],
                "pdd_id": "F-11111111",
                "source_commit": commits[task_id],
                "source_branch": task["source_branch"],
                "workdir": task["workdir"],
            },
        )])

    final_manifest = _manifest(state_dir, fanout_id)
    assert final_manifest["aggregate"]["status"] == "failed"
    assert final_manifest["aggregate"]["candidate_status"] == "quality_failed"
    event_types = [event.type for event in log.read_all()]
    assert "candidate.quality.failed" in event_types
    assert "integration.failed" in event_types
    assert "candidate.ready" not in event_types
    failed = [event for event in log.read_all() if event.type == "integration.failed"][-1]
    assert failed.payload["failure_class"] == "candidate_product_quality_failed"
    assert failed.payload["failure_scope"] == "candidate"
    assert failed.payload["integration_attempt_id"]
    assert failed.payload["failure_fingerprint"]
    assert failed.causation_id

    orch._evaluate_writer_fanout(fanout_id, force_retry=True)
    terminals = [
        event for event in log.read_all()
        if event.type == "integration.failed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(terminals) == 1
    assert any(
        event.type == "candidate.integration.duplicate_suppressed"
        and event.payload.get("integration_attempt_id")
        == failed.payload["integration_attempt_id"]
        for event in log.read_all()
    )


def test_writer_fanout_completion_payload_pins_base_git_head(tmp_path: Path):
    """The child completion payload must carry the workdir HEAD at dispatch.
    Lane branches keep prior-round commits that never merge back to the
    candidate base; without this pin, the task-ref scope gate diffs the
    whole lane history and rejects every handoff (HIC-6B747D9856)."""
    state_dir, log, transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    dispatched = [
        event for event in log.read_all()
        if event.type == "fanout.child.dispatched"
    ]
    assert dispatched
    for event in dispatched:
        workdir_head = _git(Path(event.payload["workdir"]), "rev-parse", "HEAD")
        briefing = Path(event.payload["briefing_path"]).read_text(encoding="utf-8")
        assert f'"base_git_head": "{workdir_head}"' in briefing


def test_affinity_stage_slots_lane_released_on_child_failure(tmp_path: Path):
    # 2026-06-10 review P1-9: a terminally-failed affinity child previously
    # never released its lane (release only ran on dev.build.done), so the
    # queued overflow child starved until the stage timeout.
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
    )
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": "TASK-1",
                "scope": "pi-core",
                "affinity_tag": "pi-core",
                "allowed_paths": ["a.txt"],
            },
            {
                "task_id": "TASK-2",
                "scope": "gateway",
                "affinity_tag": "gateway",
                "allowed_paths": ["b.txt"],
            },
            {
                "task_id": "TASK-3",
                "scope": "web-tui",
                "affinity_tag": "web-tui",
                "allowed_paths": ["c.txt"],
            },
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=("TASK-1", "TASK-2", "TASK-3"))

    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    assert _child(manifest, "TASK-3")["status"] == "queued"
    task2 = _child(manifest, "TASK-2")

    failure_event = ZfEvent(
        id="dev-failed-fanout-task-2",
        type="dev.failed",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task2["child_id"],
            "run_id": task2["run_id"],
            "pdd_id": "F-11111111",
            "status": "failed",
            "reason": "authoritative verification failed before evidence gate",
            "failure_reason": (
                "package smoke failed and the legal fix requires protected "
                "workspace/package metadata outside this child"
            ),
            "failure_classification": "blocked_by_protected_paths",
            "blocked_rework_findings": [
                "package smoke output is missing expected workspace package name",
                "fix requires protected assembly-owned package files",
            ],
        },
    )
    log.append(failure_event)
    orch.run_once(events=[failure_event])
    orch.run_once(events=[ZfEvent(
        type="worker.heartbeat",
        actor=task2["role_instance"],
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={"source": "post-fanout-failure-tick"},
    )])

    events = log.read_all()
    failed = [
        event for event in events
        if event.type == "fanout.child.failed"
        and event.payload.get("child_id") == task2["child_id"]
    ]
    assert failed, "child failure must be recorded"
    assert any(
        event.type == "fanout.slot.released"
        and event.payload.get("child_id") == task2["child_id"]
        for event in events
    ), "failed affinity child must release its lane"
    assert not [
        event for event in events
        if event.type in {
            "task.rework.requested",
            "task.evidence.reissue.requested",
        }
        and event.payload.get("trigger_event_id") == failure_event.id
    ], "fanout-scoped dev.failed must not also enter per-task rework"
    assert not [
        event for event in events
        if event.type == "task.dispatched"
        and event.task_id == "TASK-2"
        and event.payload.get("source") in {"rework", "evidence_reissue"}
    ], "fanout-scoped failure must not redispatch the failed task outside fanout"
    final_manifest = _manifest(state_dir, fanout_id)
    task3 = _child(final_manifest, "TASK-3")
    assert task3["status"] == "dispatched"
    assert task3["lane_id"] == task2["lane_id"]
    assert task3["role_instance"] == task2["role_instance"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "dev-2"]


def test_writer_fanout_child_failed_preserves_structured_dev_failure(tmp_path: Path):
    state_dir, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state_dir)

    _start(orch)

    fanout_id = _fanout_id(log)
    task1 = _child(_manifest(state_dir, fanout_id), "TASK-1")
    failure_reason = (
        "Authoritative verification cannot run because the package gate "
        "requires workspace/package files outside this slice's allowed_paths."
    )
    failure_event = ZfEvent(
        type="dev.failed",
        actor=task1["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": task1["child_id"],
            "run_id": task1["run_id"],
            "status": "blocked",
            "failure_reason": failure_reason,
            "failure_classification": "blocked_by_protected_paths",
            "blocked_rework_findings": [
                "pnpm --filter @cj-min/state reports no matching project",
                "fix requires assembly-owned package/workspace metadata",
            ],
            "protected_paths_required_for_fix": [
                "pnpm-workspace.yaml",
                "packages/state/package.json",
            ],
        },
    )

    orch.run_once(events=[failure_event])

    failed = [
        event for event in log.read_all()
        if event.type == "fanout.child.failed"
        and event.payload.get("child_id") == task1["child_id"]
    ]
    assert len(failed) == 1
    payload = failed[0].payload
    assert payload["reason"] == failure_reason
    assert payload["failure_classification"] == "blocked_by_protected_paths"
    assert payload["blocked_rework_findings"] == [
        "pnpm --filter @cj-min/state reports no matching project",
        "fix requires assembly-owned package/workspace metadata",
    ]
    assert payload["protected_paths_required_for_fix"] == [
        "pnpm-workspace.yaml",
        "packages/state/package.json",
    ]
    findings = orch._fanout_failure_findings(_manifest(state_dir, fanout_id))
    messages = {
        finding["message"] for finding in findings
        if finding.get("task_id") == "TASK-1"
    }
    assert set(payload["blocked_rework_findings"]).issubset(messages)
    assert failure_reason in messages


def test_fanout_scoped_dev_failed_is_not_task_rework_trigger(tmp_path: Path):
    state_dir, log, transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        include_orchestrator=True,
    )
    _seed_tasks(state_dir, task_ids=("TASK-1", "TASK-2"))
    task = TaskStore(state_dir / "kanban.json").get("TASK-2")
    assert task is not None
    trigger = ZfEvent(
        id="fanout-scoped-dev-failed",
        type="dev.failed",
        actor="dev-2",
        task_id="TASK-2",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-dev-fanout",
            "child_id": "dev-2-TASK-2",
            "dispatch_id": "run-fanout-dev-2-TASK-2",
            "status": "failed",
            "reason": "fanout child failed",
            "missing": ["child report missing"],
        },
    )

    decision = orch._route_rework_trigger(task, trigger, reason="unit")

    assert decision.action == "ignore"
    assert decision.task_id == "TASK-2"
    events = log.read_all()
    assert not [
        event for event in events
        if event.type in {
            "task.rework.requested",
            "task.evidence.reissue.requested",
            "task.dispatched",
        }
        and event.task_id == "TASK-2"
    ]
    assert transport.sent == []


def test_affinity_stage_slots_overflow_dispatches_on_first_released_lane(
    tmp_path: Path,
):
    # Hermes refactor uses 5 impl lanes. The sixth task must wait in the
    # fanout queue and attach to whichever impl lane releases first.
    state_dir, log, _transport, orch = _state(
        tmp_path,
        affinity_stage_slots=True,
        affinity_lane_count=5,
    )
    task_ids = tuple(f"TASK-{index}" for index in range(1, 7))
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.write_text(json.dumps({
        "tasks": [
            {
                "task_id": task_id,
                "scope": f"slice-{index}",
                "affinity_tag": f"slice-{index}",
                "allowed_paths": [f"slice-{index}.txt"],
            }
            for index, task_id in enumerate(task_ids, start=1)
        ],
    }), encoding="utf-8")
    _seed_tasks(state_dir, task_ids=task_ids)

    _start(orch)

    fanout_id = _fanout_id(log)
    manifest = _manifest(state_dir, fanout_id)
    dispatched = [
        child for child in manifest["children"]
        if child["status"] == "dispatched"
    ]
    assert [child["task_id"] for child in dispatched] == list(task_ids[:5])
    queued = _child(manifest, "TASK-6")
    assert queued["status"] == "queued"
    assert int(queued["queue_order"]) == 0

    first = _child(manifest, "TASK-1")
    commit = _commit(
        Path(first["workdir"]),
        "slice-1.txt",
        "TASK-1\n",
        "task-1",
    )
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=first["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": first["child_id"],
            "run_id": first["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": first["source_branch"],
            "workdir": first["workdir"],
        },
    )])

    final_manifest = _manifest(state_dir, fanout_id)
    task6 = _child(final_manifest, "TASK-6")
    assert task6["status"] == "dispatched"
    assert task6["lane_id"] == first["lane_id"]
    assert task6["role_instance"] == first["role_instance"]
    assert any(
        event.type == "fanout.child.queued"
        and event.payload.get("task_id") == "TASK-6"
        for event in log.read_all()
    )
    assert any(
        event.type == "fanout.slot.released"
        and event.payload.get("task_id") == "TASK-1"
        for event in log.read_all()
    )
    assert any(
        event.type == "fanout.slot.assigned"
        and event.payload.get("task_id") == "TASK-6"
        and event.payload.get("lane_id") == first["lane_id"]
        for event in log.read_all()
    )


# ---------------------------------------------------------------- B14


def _approval_config(state_dir, *, enabled: bool):
    cfg = _config(state_dir, synthesize_canonical=True)
    cfg.workflow.plan_approval_enabled = enabled
    return cfg


def _approval_orch(tmp_path, *, enabled: bool):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "TASK-1", "affinity_tag": "pi-core",
             "allowed_paths": ["a.txt"],
             "source_key": "plan.md#t1"},
            {"task_id": "TASK-2", "affinity_tag": "gateway",
             "allowed_paths": ["b.txt"],
             "source_key": "plan.md#t2"},
        ],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir, _approval_config(state_dir, enabled=enabled), transport,
    )  # type: ignore[arg-type]
    return state_dir, log, orch


def _approval_start(orch, log):
    """approval 系测试的保真驱动:触发事件先落 log(真实路径 synth
    emit 必持久化)再喂 run_once —— resume 重入要从 log 找原事件。"""
    from zf.core.events.writer import EventWriter

    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    EventWriter(log).append(trigger)
    orch.run_once(events=[trigger])
    return trigger


def test_plan_approval_disabled_auto_mints_and_proceeds(tmp_path):
    # B14: enabled=False(缺省)行为与现状等价 + auto approved 可审计
    _, log, orch = _approval_orch(tmp_path, enabled=False)
    _approval_start(orch, log)
    events = log.read_all()
    assert any(e.type == "fanout.started" for e in events)
    approved = [e for e in events if e.type == "plan.approved"]
    assert approved and approved[0].payload.get("auto") is True


def test_plan_approval_enabled_holds_until_operator(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    events = log.read_all()
    assert not any(e.type == "fanout.started" for e in events), "must hold"
    requested = [e for e in events if e.type == "plan.approval.requested"]
    assert requested and requested[0].payload.get("task_count") == 2
    plan_id = requested[0].payload["plan_id"]

    # operator approves(真实路径经 zf emit/ControlledAction 先落 log)
    from zf.core.events.writer import EventWriter

    approved = ZfEvent(
        type="plan.approved",
        actor="operator",
        payload={"plan_id": plan_id},
    )
    EventWriter(log).append(approved)
    orch.run_once(events=[approved])
    events = log.read_all()
    assert any(e.type == "fanout.started" for e in events)


def test_plan_approval_rejected_stays_held_no_respam(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    plan_id = [
        e for e in log.read_all() if e.type == "plan.approval.requested"
    ][0].payload["plan_id"]
    orch.run_once(events=[ZfEvent(
        type="plan.rejected",
        actor="operator",
        payload={"plan_id": plan_id, "reason": "缺 assembly"},
    )])
    # 同 plan_id 的 trigger 再来(重放) → 不孵化、不重发 requested
    orch.run_once(events=[ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        id=plan_id,
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )])
    events = log.read_all()
    assert not any(e.type == "fanout.started" for e in events)
    assert len([
        e for e in events if e.type == "plan.approval.requested"
    ]) == 1


def test_plan_minting_suppressed_for_pending_same_fingerprint(tmp_path):
    """FIX-12(bizsim r4 F12):同 stage+pdd+task 集的未决 plan 已在队时,
    新 trigger 不再铸新审批单(r4 冻结期 14 请求/批一生一的实锚)。"""
    from zf.core.events.writer import EventWriter

    _, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    assert len([
        e for e in log.read_all() if e.type == "plan.approval.requested"
    ]) == 1

    # 第二个 trigger(新 event id,同 task 集)→ 应被指纹判重抑制
    second = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )
    EventWriter(log).append(second)
    orch.run_once(events=[second])
    events = log.read_all()
    assert len([
        e for e in events if e.type == "plan.approval.requested"
    ]) == 1, "同指纹未决 plan 在队,不得再铸"
    suppressed = [e for e in events if e.type == "plan.minting.suppressed"]
    assert suppressed
    assert suppressed[0].payload["reason"] == "pending_plan_same_fingerprint"
    assert not any(e.type == "fanout.started" for e in events)

    # 首单批准后,老 pending 落定;同指纹新 trigger 可以再铸(非永久封印)
    first_plan_id = [
        e for e in events if e.type == "plan.approval.requested"
    ][0].payload["plan_id"]
    approved = ZfEvent(
        type="plan.approved", actor="operator", payload={"plan_id": first_plan_id},
    )
    EventWriter(log).append(approved)
    orch.run_once(events=[approved])
    assert any(e.type == "fanout.started" for e in log.read_all())
