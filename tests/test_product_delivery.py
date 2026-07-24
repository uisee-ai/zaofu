from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.replan_contract_eval import evaluate_replan_contract
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "F-PROD",
        "goal_claims": [
            {"goal_claim_id": "CLAIM-A", "text": "Slice A works", "mandatory": True},
            {"goal_claim_id": "CLAIM-B", "text": "Slice B works", "mandatory": True},
        ],
        "source_refs": {
            "spec_ref": "docs/design/product-spec.md",
            "plan_ref": "docs/design/product-plan.md",
            "critic_event_id": "evt-critic",
            "critic_gate_ref": "docs/design/critic.md",
        },
        "tasks": [
            {
                "task_id": "TASK-PROD-A",
                "title": "build product slice A",
                "owner_role": "dev",
                "phase": "impl-core",
                "wave": 1,
                "scope": ["src/a.py"],
                "verification": "uv run pytest tests/test_a.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["A works"],
                "goal_claim_ids": ["CLAIM-A"],
            },
            {
                "task_id": "TASK-PROD-B",
                "title": "build product slice B",
                "owner_role": "dev",
                "wave": 2,
                "blocked_by": ["TASK-PROD-A"],
                "scope": ["src/b.py"],
                "verification": "uv run pytest tests/test_b.py",
                "verification_tiers": ["runtime"],
                "goal_claim_ids": ["CLAIM-B"],
            },
        ],
    }


def _source_index() -> dict:
    return {
        "schema_version": "source-index.v1",
        "feature_id": "F-PROD",
        "tasks": [
            {
                "task_id": "TASK-PROD-A",
                "source_key": "docs/design/product-plan.md#task-a",
                "source_ref": "docs/design/product-plan.md#task-a",
                "source_task_id": "task-a",
                "source_title": "Task A",
                "source_excerpt": (
                    "Task A: build product slice A\n"
                    "Scope: src/a.py\n"
                    "Verification: uv run pytest tests/test_a.py"
                ),
                "source_mode": "canonical",
            },
            {
                "task_id": "TASK-PROD-B",
                "source_key": "docs/design/product-plan.md#task-b",
                "source_ref": "docs/design/product-plan.md#task-b",
                "source_task_id": "task-b",
                "source_title": "Task B",
                "source_excerpt": (
                    "Task B: build product slice B\n"
                    "Scope: src/b.py\n"
                    "Verification: uv run pytest tests/test_b.py"
                ),
                "source_mode": "canonical",
            },
        ],
    }


def _replan_task_map_v2() -> dict:
    return {
        **_task_map(),
        "source_refs": {
            **_task_map()["source_refs"],
            "supersedes_task_map_ref": "tm-v1",
        },
        "tasks": [
            {
                "task_id": "TASK-PROD-C",
                "title": "build replacement product slice C",
                "behavior": "replace slice A/B with a safer combined implementation",
                "owner_role": "dev",
                "phase": "impl-core",
                "wave": 1,
                "scope": ["src/c.py"],
                "exclusive_files": ["src/c.py"],
                "verification": "uv run pytest tests/test_c.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["C works"],
            },
        ],
    }


def _source_index_v2() -> dict:
    return {
        "schema_version": "source-index.v1",
        "feature_id": "F-PROD",
        "tasks": [
            {
                "task_id": "TASK-PROD-C",
                "source_key": "docs/design/product-plan-v2.md#task-c",
                "source_ref": "docs/design/product-plan-v2.md#task-c",
                "source_task_id": "task-c",
                "source_title": "Task C",
                "source_excerpt": "Task C replaces slice A/B with safer combined implementation.",
                "source_mode": "canonical",
            },
        ],
    }


def test_ingest_task_map_to_kanban_creates_contract_tasks(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    task_map = _task_map()
    task_map["required_plan_ports"] = [
        "requirement_spec",
        "acceptance_matrix",
        "test_matrix",
    ]

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        source_index=_source_index(),
        source_index_ref=".zf/artifacts/F-PROD/source-index.json",
        task_map_ref=".zf/artifacts/F-PROD/task-map.json",
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is True
    assert result.created_task_ids == ["TASK-PROD-A", "TASK-PROD-B"]
    tasks = {task.id: task for task in TaskStore(state_dir / "kanban.json").list_all()}
    # doc 69 S-b: phase now comes from the task-map item, not hardcoded
    assert tasks["TASK-PROD-A"].contract.phase == "impl-core"
    assert tasks["TASK-PROD-B"].contract.phase == "default"  # no phase set → fallback
    assert tasks["TASK-PROD-A"].contract.spec_ref == "docs/design/product-spec.md"
    assert tasks["TASK-PROD-A"].contract.source_index_ref == ".zf/artifacts/F-PROD/source-index.json"
    assert tasks["TASK-PROD-A"].contract.source_mode == "canonical"
    assert "Task A: build product slice A" in tasks["TASK-PROD-A"].contract.source_excerpt
    assert tasks["TASK-PROD-A"].contract.product_contract_ref == "docs/design/product-spec.md"
    assert tasks["TASK-PROD-A"].contract.goal_claim_ids == ["CLAIM-A"]
    assert tasks["TASK-PROD-B"].contract.goal_claim_ids == ["CLAIM-B"]
    assert tasks["TASK-PROD-B"].blocked_by == ["TASK-PROD-A"]
    assert tasks["TASK-PROD-A"].contract.evidence_contract[
        "required_plan_ports"
    ] == task_map["required_plan_ports"]
    assert result.summary["task_doc_failure_count"] == 0
    assert result.summary["feature_projection"]["status"] == "created"
    features = FeatureStore(state_dir / "feature_list.json").list_all()
    assert [(feature.id, feature.status) for feature in features] == [("F-PROD", "active")]
    assert (state_dir / "task_docs" / "TASK-PROD-A" / "task.md").exists()
    assert (state_dir / "task_docs" / "TASK-PROD-B" / "manifest.json").exists()
    assert tasks["TASK-PROD-A"].contract.task_doc_ref.endswith(
        "task_docs/TASK-PROD-A/task.md"
    )
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert [event.type for event in events].count("task.created") == 2
    assert any(event.type == "feature.created" for event in events)
    wave_ready = next(event for event in events if event.type == "product_delivery.wave.ready")
    assert wave_ready.payload["feature_id"] == "F-PROD"
    assert wave_ready.payload["pdd_id"] == "F-PROD"
    assert wave_ready.payload["task_ids"] == ["TASK-PROD-A"]


def test_ingest_normalizes_refactor_lane_contract_fields(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    task_map = {
        "schema_version": "task-map.v1",
        "feature_id": "CJMIN-R37",
        "tasks": [
            {
                "task_id": "CJMIN-GATEWAY-001",
                "title": "Port gateway slice",
                "owner_role": "dev",
                "preferred_impl_role": "dev-lane-0",
                "scope": ["packages/gateway/**"],
                "source_refs": ["docs/plans/cj-min-refactor-prompt.md:1"],
                "verification": "pnpm --dir packages/gateway test",
                "verification_tiers": [
                    "static",
                    "contract",
                    "parity",
                    "live_smoke_optional",
                    "review",
                    "judge",
                ],
            },
        ],
    }

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        task_map_ref=".zf/artifacts/cj-min/task_map.json",
    )

    assert result.passed is True
    task = TaskStore(state_dir / "kanban.json").get("CJMIN-GATEWAY-001")
    assert task is not None
    assert task.assigned_to == "dev-lane-0"
    assert task.contract.owner_role == ""
    assert task.contract.owner_instance == "dev-lane-0"
    assert task.contract.verification_tiers == [
        "static",
        "runtime",
        "e2e",
        "manual_evidence",
    ]
    assert task.contract.plan_ref == ".zf/artifacts/cj-min/task_map.json"
    assert "docs/plans/cj-min-refactor-prompt.md:1" in task.contract.handoff_artifacts


def test_ingest_task_map_to_kanban_normalizes_verification_command_list(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    task_map = _task_map()
    task_map["tasks"][0]["verification"] = ["npm run check", "npm test"]

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        source_index=_source_index(),
        source_index_ref=".zf/artifacts/F-PROD/source-index.json",
        task_map_ref=".zf/artifacts/F-PROD/task-map.json",
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is True
    task = TaskStore(state_dir / "kanban.json").get("TASK-PROD-A")
    assert task is not None
    assert task.contract.verification == "npm run check"
    assert [item["command"] for item in task.contract.validation["commands"]] == [
        "npm run check",
        "npm test",
    ]
    assert task.contract.evidence_contract["success_criteria"] == [
        {
            "kind": "command_passed",
            "command_id": "contract-verification-1",
            "command": "npm run check",
            "acceptance_ids": [],
        },
        {
            "kind": "command_passed",
            "command_id": "contract-verification-2",
            "command": "npm test",
            "acceptance_ids": [],
        },
    ]
    contract_updates = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "task.contract.update"
        and event.task_id == "TASK-PROD-A"
    ]
    assert contract_updates[-1].payload["contract"]["verification"] == (
        "npm run check"
    )
    assert len(
        contract_updates[-1].payload["contract"]["validation"]["commands"]
    ) == 2


def test_product_delivery_accepts_structured_expected_red_validation(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    task_map = {
        "schema_version": "task-map.v1",
        "feature_id": "F-RED",
        "tasks": [
            {
                "task_id": "TASK-RED-1",
                "title": "write expected-red API test",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["tests/test_api.py"],
                "verification_tiers": ["runtime", "manual_evidence"],
                "validation": {
                    "kind": "command",
                    "command": "false",
                    "expected_result": "red",
                },
            },
        ],
    }

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is True
    task = TaskStore(state_dir / "kanban.json").get("TASK-RED-1")
    assert task is not None
    assert task.contract.verification == "false"
    assert task.contract.validation["expected_result"] == "red"
    assert (state_dir / "task_docs" / "TASK-RED-1" / "task.md").exists()


def test_product_delivery_rejects_prose_in_verification_command(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    task_map = {
        **_task_map(),
        "tasks": [
            {
                "task_id": "TASK-PROSE-1",
                "title": "bad verification",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["src/api.py"],
                "verification": "false；这是 expected red 证据",
                "verification_tiers": ["runtime"],
            },
        ],
    }

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is False
    assert any("executable command only" in error for error in result.errors)
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_product_delivery_rejects_shell_invalid_verification_tail(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    task_map = {
        **_task_map(),
        "tasks": [
            {
                "task_id": "TASK-PROSE-2",
                "title": "bad verification tail",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["src/api.py"],
                "verification": (
                    "uv run pytest tests/e2e/test_log_counter.py -q "
                    "(red before TASK-BUG-B)"
                ),
                "verification_tiers": ["runtime"],
            },
        ],
    }

    result = ingest_task_map_to_kanban(
        state_dir,
        task_map,
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is False
    assert any("valid shell syntax" in error for error in result.errors)
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_product_delivery_manifest_starts_spine_through_orchestrator(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    task_map_path = state_dir / "artifacts" / "F-PROD" / "task-map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps(_task_map(), ensure_ascii=False), encoding="utf-8")
    source_index_path = state_dir / "artifacts" / "F-PROD" / "source-index.json"
    source_index_path.write_text(json.dumps(_source_index(), ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(task_map_path.read_bytes()).hexdigest()
    source_index_digest = hashlib.sha256(source_index_path.read_bytes()).hexdigest()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-PLAN",
        title="accepted product plan",
        status="in_progress",
        contract=TaskContract(phase="handoff"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    approval = writer.emit(
        "design.critique.done",
        actor="critic",
        task_id="TASK-PLAN",
        payload={
            "verdict": "approve",
            "checks": [{"name": "source_coverage", "passed": True}],
        },
    )
    event = writer.emit(
        "artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "feature_id": "F-PROD",
            "artifact_refs": [{
                "kind": "task_map",
                "path": str(task_map_path),
                "sha256": digest,
                "summary": "final task map",
                "status": "accepted",
            }, {
                "kind": "source_index",
                "path": str(source_index_path),
                "sha256": source_index_digest,
                "summary": "final source index",
                "status": "accepted",
            }],
            "handoff_contract": {
                "delivery_mode": "product_delivery",
                "critic_event_id": approval.id,
            },
        },
    )
    config = ZfConfig(
        project=ProjectConfig(name="product-demo"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev",
                role_kind="writer",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done"],
            ),
        ],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )

    decision = orch._on_artifact_manifest_published(event)

    assert decision is not None
    assert decision.action == "move"
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-A") is not None
    assert TaskStore(state_dir / "kanban.json").get("TASK-PLAN").status == "done"
    types = [item.type for item in log.read_all()]
    assert "product_delivery.task_map.accepted" in types
    assert "product_delivery.spine.started" in types


def test_product_delivery_replan_manifest_runs_after_plan_task_done(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    artifact_root = state_dir / "artifacts" / "F-PROD"
    v1_dir = artifact_root / "v1"
    v2_dir = artifact_root / "v2"
    v1_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)
    v1_task_map_path = v1_dir / "task-map.json"
    v1_source_index_path = v1_dir / "source-index.json"
    v2_task_map_path = v2_dir / "task-map.json"
    v2_source_index_path = v2_dir / "source-index.json"
    v1_task_map_path.write_text(json.dumps(_task_map(), ensure_ascii=False), encoding="utf-8")
    v1_source_index_path.write_text(json.dumps(_source_index(), ensure_ascii=False), encoding="utf-8")
    v2_map = {
        **_task_map(),
        "tasks": [
            {
                "task_id": "TASK-PROD-A2",
                "title": "build replacement product slice A",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["src/a2.py"],
                "verification": "uv run pytest tests/test_a2.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["A2 works"],
            },
            {
                "task_id": "TASK-PROD-B2",
                "title": "build replacement product slice B",
                "owner_role": "dev",
                "wave": 2,
                "blocked_by": ["TASK-PROD-A2"],
                "scope": ["src/b2.py"],
                "verification": "uv run pytest tests/test_b2.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["B2 works"],
            },
        ],
    }
    v2_source_index = {
        "schema_version": "source-index.v1",
        "feature_id": "F-PROD",
        "tasks": [
            {
                "task_id": "TASK-PROD-A2",
                "source_key": "docs/design/product-plan-v2.md#task-a2",
                "source_ref": "docs/design/product-plan-v2.md#task-a2",
                "source_excerpt": "Task A2: replacement product slice A.",
                "source_mode": "canonical",
            },
            {
                "task_id": "TASK-PROD-B2",
                "source_key": "docs/design/product-plan-v2.md#task-b2",
                "source_ref": "docs/design/product-plan-v2.md#task-b2",
                "source_excerpt": "Task B2: replacement product slice B.",
                "source_mode": "canonical",
            },
        ],
    }
    v2_task_map_path.write_text(json.dumps(v2_map, ensure_ascii=False), encoding="utf-8")
    v2_source_index_path.write_text(
        json.dumps(v2_source_index, ensure_ascii=False),
        encoding="utf-8",
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-PLAN",
        title="accepted product plan",
        status="in_progress",
        contract=TaskContract(phase="handoff"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    config = ZfConfig(
        project=ProjectConfig(name="product-demo"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev",
                role_kind="writer",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done"],
            ),
        ],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )

    approval_v1 = writer.emit(
        "design.critique.done",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "checks": [{"name": "source_coverage", "passed": True}]},
    )
    event_v1 = writer.emit(
        "artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "feature_id": "F-PROD",
            "artifact_refs": [
                {
                    "kind": "task_map",
                    "path": str(v1_task_map_path),
                    "sha256": hashlib.sha256(v1_task_map_path.read_bytes()).hexdigest(),
                    "summary": "v1 task map",
                    "status": "accepted",
                },
                {
                    "kind": "source_index",
                    "path": str(v1_source_index_path),
                    "sha256": hashlib.sha256(v1_source_index_path.read_bytes()).hexdigest(),
                    "summary": "v1 source index",
                    "status": "accepted",
                },
            ],
            "handoff_contract": {
                "delivery_mode": "product_delivery",
                "critic_event_id": approval_v1.id,
            },
        },
    )
    assert orch._on_artifact_manifest_published(event_v1).action == "move"
    assert store.get("TASK-PLAN").status == "done"
    store.update("TASK-PROD-A", status="in_progress", active_dispatch_id="dispatch-v1")

    approval_v2 = writer.emit(
        "design.critique.done",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "checks": [{"name": "source_coverage", "passed": True}]},
    )
    event_v2 = writer.emit(
        "artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "feature_id": "F-PROD",
            "artifact_refs": [
                {
                    "kind": "task_map",
                    "path": str(v2_task_map_path),
                    "sha256": hashlib.sha256(v2_task_map_path.read_bytes()).hexdigest(),
                    "summary": "v2 task map",
                    "status": "accepted",
                    "supersedes": str(v1_task_map_path),
                },
                {
                    "kind": "source_index",
                    "path": str(v2_source_index_path),
                    "sha256": hashlib.sha256(v2_source_index_path.read_bytes()).hexdigest(),
                    "summary": "v2 source index",
                    "status": "accepted",
                },
            ],
            "handoff_contract": {
                "delivery_mode": "product_delivery",
                "critic_event_id": approval_v2.id,
                "supersedes_task_map_ref": str(v1_task_map_path),
            },
        },
    )

    decision = orch._on_artifact_manifest_published(event_v2)

    assert decision is not None
    assert decision.action == "move"
    assert store.get("TASK-PROD-A2") is not None
    assert store.get("TASK-PROD-B2") is not None
    assert store.get("TASK-PROD-A").status == "cancelled"
    assert store.get("TASK-PROD-B").status == "cancelled"
    events = log.read_all()
    assert [item.type for item in events].count("product_delivery.spine.started") == 2
    assert "task.superseded" in [item.type for item in events]
    assert not [
        item for item in events
        if item.type == "task.invalid_transition" and item.task_id == "TASK-PLAN"
    ]


def test_replan_adoption_rejects_stale_eval(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    assert ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        task_map_ref="tm-v1",
        writer=writer,
    ).passed
    eval_result = evaluate_replan_contract(
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-stale",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-stale",
    ).to_dict()

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
    )

    assert result.passed is False
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-C") is None
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-A").status == "backlog"
    assert "replan.adoption.stale_rejected" in [event.type for event in log.read_all()]


def test_replan_adoption_accepts_matching_eval(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    assert ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        task_map_ref="tm-v1",
        writer=writer,
    ).passed
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-v1",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-accept",
    ).to_dict()

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
    )

    assert result.passed is True
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-PROD-C") is not None
    assert store.get("TASK-PROD-A").status == "cancelled"
    assert "replan.adoption.prepared" in [event.type for event in log.read_all()]
    assert "replan.adoption.completed" in [event.type for event in log.read_all()]


def test_replan_eval_emits_doc94_behavior_eval_overlay_events(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index={},
        eval_id="eval-doc94",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-doc94",
    ).to_dict()

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
        causation_id="evt-proposal",
        correlation_id="trace-doc94",
    )

    assert result.passed is False
    events = log.read_all()
    by_type = {}
    for event in events:
        by_type.setdefault(event.type, []).append(event)
    assert by_type["replan.contract_eval.completed"][0].payload["failed_checks"]
    assert by_type["eval.contract_completeness.completed"][0].payload["status"] == "passed"
    source_gap = by_type["behavior.source_coverage_gap.detected"][0]
    assert source_gap.payload["check_name"] == "source_coverage_no_invention"
    assert source_gap.payload["status"] == "failed"
    assert source_gap.payload["refs"]["new_task_map_ref"] == "tm-v2"

    ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
        causation_id="evt-proposal",
        correlation_id="trace-doc94",
    )

    deduped = log.read_all()
    assert [event.type for event in deduped].count("replan.contract_eval.completed") == 1
    assert [event.type for event in deduped].count("behavior.source_coverage_gap.detected") == 1


def _owner_gated_eval() -> dict:
    """An adopt-verdict replan eval marked owner-gated (doc 84 §4.3)."""
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-v1",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-owner",
    ).to_dict()
    eval_result["owner_approval_required"] = True
    return eval_result


def _seed_v1(state_dir: Path, writer: EventWriter) -> None:
    assert ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        task_map_ref="tm-v1",
        writer=writer,
    ).passed


def test_owner_gated_adoption_awaits_owner_decision(tmp_path: Path) -> None:
    """doc 84 A: owner-gated replan with no owner decision must not silently
    dangle — it blocks with a visible ``awaiting_owner`` and adopts nothing."""
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_v1(state_dir, writer)

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=_owner_gated_eval(),
        writer=writer,
    )

    assert result.passed is False
    types = [event.type for event in log.read_all()]
    assert "replan.adoption.awaiting_owner" in types
    assert "replan.adoption.completed" not in types
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-PROD-A").status != "cancelled"
    assert store.get("TASK-PROD-C") is None


def test_owner_gated_adoption_proceeds_after_owner_approves(tmp_path: Path) -> None:
    """The token-gated owner approve (``replan.owner_decision.approved``)
    recorded in events is what bridges the human into the deterministic gate."""
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_v1(state_dir, writer)
    writer.emit(
        "replan.owner_decision.approved",
        actor="owner",
        payload={
            "decision": "approved",
            "candidate_task_map_ref": "tm-v2",
            "eval_ref": "idem-owner",
            "proposal_ref": "rp-1",
        },
    )

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=_owner_gated_eval(),
        writer=writer,
    )

    assert result.passed is True
    types = [event.type for event in log.read_all()]
    assert "replan.adoption.completed" in types
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-PROD-C") is not None
    assert store.get("TASK-PROD-A").status == "cancelled"


def test_owner_gated_adoption_blocks_on_owner_reject(tmp_path: Path) -> None:
    """Owner reject → original plan continues, nothing superseded."""
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_v1(state_dir, writer)
    writer.emit(
        "replan.owner_decision.rejected",
        actor="owner",
        payload={
            "decision": "rejected",
            "candidate_task_map_ref": "tm-v2",
            "eval_ref": "idem-owner",
        },
    )

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=_owner_gated_eval(),
        writer=writer,
    )

    assert result.passed is False
    types = [event.type for event in log.read_all()]
    assert "replan.adoption.owner_rejected" in types
    assert "replan.adoption.completed" not in types
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-PROD-A").status != "cancelled"


def test_non_owner_gated_adoption_skips_owner_gate(tmp_path: Path) -> None:
    """Regression: a replan that is not owner-gated adopts on eval alone and
    never emits an owner-gate event (unchanged behaviour)."""
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_v1(state_dir, writer)
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-v1",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-plain",
    ).to_dict()

    result = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
    )

    assert result.passed is True
    types = [event.type for event in log.read_all()]
    assert "replan.adoption.completed" in types
    assert "replan.adoption.awaiting_owner" not in types


def test_replan_adoption_is_idempotent(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    assert ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        task_map_ref="tm-v1",
        writer=writer,
    ).passed
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-v1",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
        idempotency_key="idem-repeat",
    ).to_dict()

    first = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
    )
    second = ingest_task_map_to_kanban(
        state_dir,
        _replan_task_map_v2(),
        source_index=_source_index_v2(),
        task_map_ref="tm-v2",
        replan_eval=eval_result,
        writer=writer,
    )

    assert first.created_task_ids == ["TASK-PROD-C"]
    assert second.passed is True
    assert second.summary["idempotent"] is True
    assert [event.type for event in log.read_all()].count("replan.adoption.completed") == 1
    assert [task.id for task in TaskStore(state_dir / "kanban.json").list_all()].count(
        "TASK-PROD-C"
    ) == 1


def test_product_delivery_rejects_missing_required_source_index(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))

    result = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        task_map_ref=".zf/artifacts/F-PROD/task-map.json",
        writer=writer,
        actor="zf-cli",
        require_source_index=True,
    )

    assert result.passed is False
    assert any("source_index missing task_id" in error for error in result.errors)
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_product_delivery_rejects_coverage_report_missing_task_map_ids(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    coverage_report = {
        "schema_version": "coverage-report.v1",
        "tasks": [
            {
                "task_id": "TASK-PROD-A",
                "source_status": "covered",
            },
        ],
        "unresolved_unknowns": [],
    }

    result = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        coverage_report=coverage_report,
        coverage_report_ref=".zf/artifacts/F-PROD/coverage-report.json",
        writer=writer,
        actor="zf-cli",
    )

    assert result.passed is False
    assert any(
        "coverage_report missing task_id 'TASK-PROD-B'" in error
        for error in result.errors
    )
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_invalid_product_delivery_approval_does_not_start_spine(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    task_map_path = state_dir / "artifacts" / "F-PROD" / "task-map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps(_task_map(), ensure_ascii=False), encoding="utf-8")
    source_index_path = state_dir / "artifacts" / "F-PROD" / "source-index.json"
    source_index_path.write_text(json.dumps(_source_index(), ensure_ascii=False), encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-PLAN",
        title="accepted product plan",
        status="in_progress",
        contract=TaskContract(phase="handoff"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit(
        "design.critique.done",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve"},
    )
    event = writer.emit(
        "artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "feature_id": "F-PROD",
            "artifact_refs": [{
                "kind": "task_map",
                "path": str(task_map_path),
                "sha256": hashlib.sha256(task_map_path.read_bytes()).hexdigest(),
                "summary": "final task map",
                "status": "accepted",
            }, {
                "kind": "source_index",
                "path": str(source_index_path),
                "sha256": hashlib.sha256(source_index_path.read_bytes()).hexdigest(),
                "summary": "final source index",
                "status": "accepted",
            }],
            "handoff_contract": {"delivery_mode": "product_delivery"},
        },
    )
    config = ZfConfig(
        project=ProjectConfig(name="product-demo"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[RoleConfig(name="dev", role_kind="writer", backend="mock")],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )

    decision = orch._on_artifact_manifest_published(event)

    assert decision is not None
    assert decision.action == "block"
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-A") is None
    assert "product_delivery.task_map.accepted" not in [item.type for item in log.read_all()]


def test_product_delivery_manifest_without_approval_emits_blocked_event(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    task_map_path = state_dir / "artifacts" / "F-PROD" / "task-map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps(_task_map(), ensure_ascii=False), encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-PLAN",
        title="accepted product plan",
        status="in_progress",
        contract=TaskContract(phase="handoff"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    event = writer.emit(
        "artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "feature_id": "F-PROD",
            "artifact_refs": [{
                "kind": "task_map",
                "path": str(task_map_path),
                "sha256": hashlib.sha256(task_map_path.read_bytes()).hexdigest(),
                "summary": "final task map",
                "status": "accepted",
            }],
            "handoff_contract": {"delivery_mode": "product_delivery"},
        },
    )
    config = ZfConfig(
        project=ProjectConfig(name="product-demo"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[RoleConfig(name="dev", role_kind="writer", backend="mock")],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )

    decision = orch._on_artifact_manifest_published(event)

    assert decision is not None
    assert decision.action == "block"
    blocked = [
        item for item in log.read_all()
        if item.type == "artifact.manifest.blocked"
    ]
    assert blocked
    assert blocked[-1].payload["reason"] == "missing_approved_design_critique"


def test_superseding_task_map_cancels_old_active_tasks_and_creates_v2(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    v1_ref = ".zf/artifacts/F-PROD/v1/task-map.json"
    v2_ref = ".zf/artifacts/F-PROD/v2/task-map.json"

    v1 = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        source_index_ref=".zf/artifacts/F-PROD/v1/source-index.json",
        task_map_ref=v1_ref,
        writer=writer,
        actor="zf-cli",
    )
    assert v1.passed is True
    store = TaskStore(state_dir / "kanban.json")
    store.update("TASK-PROD-A", status="in_progress", active_dispatch_id="old-dispatch")
    v2_map = {
        **_task_map(),
        "tasks": [
            {
                "task_id": "TASK-PROD-A2",
                "title": "build replacement slice A",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["src/a2.py"],
                "verification": "uv run pytest tests/test_a2.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["A2 works"],
            },
            {
                "task_id": "TASK-PROD-B2",
                "title": "build replacement slice B",
                "owner_role": "dev",
                "wave": 1,
                "scope": ["src/b2.py"],
                "verification": "uv run pytest tests/test_b2.py",
                "verification_tiers": ["runtime"],
                "acceptance": ["B2 works"],
            },
        ],
    }
    v2_source_index = {
        "schema_version": "source-index.v1",
        "tasks": [
            {
                "task_id": "TASK-PROD-A2",
                "source_key": "plan-v2.md#a2",
                "source_ref": "plan-v2.md#a2",
                "source_excerpt": "Replacement Task A2.",
            },
            {
                "task_id": "TASK-PROD-B2",
                "source_key": "plan-v2.md#b2",
                "source_ref": "plan-v2.md#b2",
                "source_excerpt": "Replacement Task B2.",
            },
        ],
    }

    v2 = ingest_task_map_to_kanban(
        state_dir,
        v2_map,
        source_refs={"supersedes_task_map_ref": v1_ref},
        source_index=v2_source_index,
        source_index_ref=".zf/artifacts/F-PROD/v2/source-index.json",
        task_map_ref=v2_ref,
        writer=writer,
        actor="zf-cli",
    )

    assert v2.passed is True
    assert v2.created_task_ids == ["TASK-PROD-A2", "TASK-PROD-B2"]
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-A").status == "cancelled"
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-B").status == "cancelled"
    assert "task.superseded" in [item.type for item in EventLog(state_dir / "events.jsonl").read_all()]


def test_wave_blocker_is_scoped_to_task_map_ref(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-A1",
        title="feature A wave 1",
        status="in_progress",
        contract=TaskContract(
            feature_id="F-A",
            wave=1,
            evidence_contract={"source_refs": {"task_map_ref": "tm-a"}},
        ),
    ))
    store.add(Task(
        id="TASK-B2",
        title="feature B wave 2",
        status="backlog",
        contract=TaskContract(
            feature_id="F-B",
            wave=2,
            evidence_contract={"source_refs": {"task_map_ref": "tm-b"}},
        ),
    ))
    store.add(Task(
        id="TASK-A2",
        title="feature A wave 2",
        status="backlog",
        contract=TaskContract(
            feature_id="F-A",
            wave=2,
            evidence_contract={"source_refs": {"task_map_ref": "tm-a"}},
        ),
    ))
    config = ZfConfig(
        project=ProjectConfig(name="product-demo"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[RoleConfig(name="dev", role_kind="writer", backend="mock")],
    )
    orch = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )

    assert orch._contract_schedule_blocker(
        store.get("TASK-B2"),
        exclusive_reservations={},
    ) == ""
    blocker = orch._contract_schedule_blocker(
        store.get("TASK-A2"),
        exclusive_reservations={},
    )
    assert blocker.startswith("wave_blocked:scope=task_map_ref,scope_id=tm-a")
    assert "waiting_for=TASK-A1" in blocker
