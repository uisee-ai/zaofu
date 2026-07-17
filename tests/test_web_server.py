"""Tests for F-WEB-MVP-01: FastAPI dashboard endpoints.

Uses TestClient (httpx-backed) to hit each /api/* route and verify
shape. SSE streaming is exercised separately in test_web_sse.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import (
    FanoutAssignmentConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    channel_event_schema_rules,
)
from zf.core.workspace import stable_project_id
from zf.runtime.project_spine_review import write_spine_review_artifact
from zf.runtime.run_archive import archive_run
from zf.web.operator_session import OperatorSessionManager
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    (sd / "feature_list.json").write_text("[]")
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    app = create_app(state_dir)
    return TestClient(app)


class TestRootIndex:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "<title>zaofu</title>" in r.text

    def test_index_prefers_react_dist_when_present(
        self,
        state_dir,
        tmp_path,
        monkeypatch,
    ):
        dist = tmp_path / "web-dist"
        dist.mkdir()
        (dist / "index.html").write_text("<title>react-dist</title>")
        monkeypatch.setattr("zf.web.server._REACT_DIST_DIR", dist)
        local_client = TestClient(create_app(state_dir))

        r = local_client.get("/")

        assert r.status_code == 200
        assert "<title>react-dist</title>" in r.text

    def test_index_falls_back_when_react_dist_missing(
        self,
        state_dir,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr("zf.web.server._REACT_DIST_DIR", tmp_path / "missing-dist")
        local_client = TestClient(create_app(state_dir))

        r = local_client.get("/")

        assert r.status_code == 200
        assert "<title>zaofu</title>" in r.text


class TestApiState:
    def test_empty_state_returns_4_keys(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200
        data = r.json()
        assert set(data.keys()) >= {"tasks", "features", "cost", "workers"}
        assert data["tasks"] == []
        assert data["features"] == []
        assert data["workers"] == []

    def test_web_perf_summary_records_api_timing(self, state_dir, client):
        assert client.get("/api/state").status_code == 200

        summary = client.get("/api/web/perf/summary").json()

        assert summary["schema_version"] == "web-perf-summary.v1"
        assert summary["count"] >= 1
        assert any(row["route"] == "/api/state" for row in summary["routes"])
        timing_log = state_dir / "logs" / "web-api-timing.jsonl"
        assert timing_log.exists()
        assert "/api/state" in timing_log.read_text(encoding="utf-8")

    def test_delivery_contract_projection_endpoints(self, state_dir: Path, tmp_path: Path):
        project_root = state_dir.parent
        matrix_ref = "docs/real-e2e-matrix.json"
        (project_root / "docs").mkdir()
        (project_root / matrix_ref).write_text(
            json.dumps({
                "schema_version": "real-e2e-matrix.v1",
                "status": "ready",
                "rows": [{"id": "web-chat", "status": "required"}],
            }),
            encoding="utf-8",
        )
        (state_dir / "config").mkdir()
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-web",
                "refs": {"real_e2e_matrix": [matrix_ref]},
            }),
            encoding="utf-8",
        )
        (state_dir / "failure-candidates").mkdir()
        (state_dir / "failure-candidates" / "fail-web.json").write_text(
            json.dumps({
                "schema_version": "failure-candidate.v1",
                "failure_id": "fail-web",
                "event": {"type": "run.manager.action.failed"},
            }),
            encoding="utf-8",
        )
        local_client = TestClient(create_app(state_dir, project_root=project_root))

        run_contract = local_client.get("/api/run-contract").json()
        candidates = local_client.get("/api/failure-candidates").json()
        matrix = local_client.get("/api/real-e2e-matrix").json()

        assert run_contract["status"] == "present"
        assert run_contract["contract"]["contract_digest"] == "digest-web"
        assert candidates["count"] == 1
        assert candidates["items"][0]["failure_id"] == "fail-web"
        assert matrix["status"] == "present"
        assert matrix["summary"]["loaded"] == 1
        assert matrix["matrices"][0]["summary"]["case_count"] == 1


class TestWorkflowIntakeSubmitApi:
    def test_project_workflow_api_intake_classify_submit_apply(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        project_root = tmp_path
        config_path = project_root / "zf.yaml"
        config_path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""")
        state = project_root / ".zf"
        state.mkdir()
        (state / "kanban.json").write_text("[]\n", encoding="utf-8")
        EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
        app = create_app(state, config=load_config(config_path), project_root=project_root)
        client = TestClient(app)

        intake_response = client.post(
            "/api/projects/default/workflow-intake",
            headers={"x-zf-web-token": "test-token"},
            json={
                "kind": "issue",
                "request_id": "wf-web",
                "objective": "fix checkout regression",
                "backend": "mock",
            },
        )

        assert intake_response.status_code == 200
        intake_result = intake_response.json()["result"]
        intake = intake_result["intake_ref"]
        manifest = json.loads(
            Path(intake_result["workflow_input_manifest_ref"]).read_text(encoding="utf-8")
        )
        assert manifest["requested_lanes"] == 1
        classify_response = client.post(
            "/api/projects/default/workflow-classify",
            headers={"x-zf-web-token": "test-token"},
            json={"intake_ref": intake},
        )
        assert classify_response.status_code == 200
        submit_response = client.post(
            "/api/projects/default/workflow-submit",
            headers={"x-zf-web-token": "test-token"},
            json={
                "intake_ref": intake,
                "apply": True,
                "task_id": "TASK-WEB",
                "pattern_id": "issue-triage",
                "allow_missing_env": True,
            },
        )

        assert submit_response.status_code == 202
        events = EventLog(state / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "workflow.submit.requested" in types
        assert "workflow.submit.accepted" in types
        assert "workflow.invoke.requested" in types

    def test_channel_request_proposes_then_project_submit_explicitly_ignites(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        config_path = tmp_path / "zf.yaml"
        config_path.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/channel.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""", encoding="utf-8")
        state = tmp_path / ".zf"
        state.mkdir()
        (state / "kanban.json").write_text("[]\n", encoding="utf-8")
        log = EventLog(state / "events.jsonl")
        app = create_app(state, config=load_config(config_path), project_root=tmp_path)
        client = TestClient(app)

        proposed = client.post(
            "/api/channels/ch-product/workflow-request",
            headers={"x-zf-web-token": "test-token"},
            json={
                "request_id": "REQ-CHANNEL",
                "kind": "issue",
                "objective": "Fix checkout timeout and add a regression test",
                "backend": "mock",
                "allow_missing_env": True,
            },
        )

        assert proposed.status_code == 202, proposed.text
        proposal = proposed.json()
        assert proposal["status"] == "proposal_ready"
        assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]
        intake_ref = proposal["result"]["payload"]["workflow_prompt_ref"]

        submitted = client.post(
            "/api/projects/default/workflow-submit",
            headers={"x-zf-web-token": "test-token"},
            json={
                "intake_ref": intake_ref,
                "kind": "issue",
                "apply": True,
                "allow_missing_env": True,
            },
        )

        assert submitted.status_code == 202, submitted.text
        invokes = [
            event for event in log.read_all()
            if event.type == "workflow.invoke.requested"
        ]
        assert len(invokes) == 1
        assert invokes[0].payload["flow_kind"] == "issue"

    def test_with_tasks(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="hello", status="backlog"),
        )
        r = client.get("/api/state")
        tasks = r.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["id"] == "T1"
        assert tasks[0]["status"] == "backlog"
        # phase is None when no events
        assert tasks[0]["phase"] is None

    def test_agent_view_includes_owner_visible_inbox(self, state_dir, client):
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="owner.visible_message.requested",
            actor="zf-supervisor",
            task_id="TASK-OWNER",
            payload={
                "message_id": "omsg-owner",
                "severity": "high",
                "title": "verify stalled",
                "delivery_targets": ["web", "feishu"],
            },
        ))

        data = client.get("/api/views/workers").json()

        inbox = data["owner_visible_inbox"]
        assert inbox["summary"]["pending"] == 1
        assert inbox["pending"][0]["message_id"] == "omsg-owner"

    def test_project_config_render_endpoint_is_readonly(self, state_dir, tmp_path):
        config_path = tmp_path / "zf.yaml"
        config_path.write_text("""\
version: "1.0"
project:
  name: render-api-demo
  state_dir: .zf
roles:
- name: scan
  instance_id: scan
  backend: mock
  role_kind: reader
workflow:
  dag:
    external_triggers: [scan.requested]
  stages:
  - id: scan
    trigger: scan.requested
    topology: fanout_reader
    roles: [scan]
    aggregate:
      mode: wait_for_all
      success_event: scan.completed
      failure_event: scan.failed
""")
        cfg = ZfConfig(
            project=ProjectConfig(name="render-api-demo", state_dir=".zf"),
            roles=[RoleConfig(name="scan", instance_id="scan", backend="mock", role_kind="reader")],
            workflow=WorkflowConfig(stages=[
                WorkflowStageConfig(
                    id="scan",
                    trigger="scan.requested",
                    topology="fanout_reader",
                    roles=["scan"],
                )
            ]),
        )
        local_client = TestClient(create_app(state_dir, config=cfg, project_root=tmp_path))

        data = local_client.get("/api/projects/default/config/render").json()

        assert data["schema_version"] == "config-inspection.v1"
        assert data["project"]["name"] == "render-api-demo"
        assert data["summary"]["roles"] == 1

    def test_phase_derivation_in_state(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress",
                 assigned_to="review"),
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="dev.build.done", actor="dev-1",
                    task_id="T1", payload={}),
        )
        r = client.get("/api/state")
        tasks = r.json()["tasks"]
        assert tasks[0]["phase"] == "build_done"

    def test_state_projects_test_failed_as_visible_failure(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-VERIFY-FAIL", title="verify fail", status="in_progress",
                 assigned_to="dev-core"),
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="test.failed",
                actor="verify",
                task_id="TASK-VERIFY-FAIL",
                payload={"reason": "pytest failed", "rework_target": "dev-core"},
            )
        )

        data = client.get("/api/state").json()

        task = next(row for row in data["tasks"] if row["id"] == "TASK-VERIFY-FAIL")
        assert task["status"] == "in_progress"
        assert task["blocked_reason"] == ""
        assert task["kanban_column"] == "blocked"
        assert task["verify_state"] == "failed"
        assert task["kanban_column_reason"] == "workflow_failure:pytest failed"
        assert any(badge["tone"] == "err" for badge in task["workflow_badges"])

    def test_state_includes_delivery_features_from_feature_index_without_feature_list(
        self,
        tmp_path: Path,
    ):
        sd = tmp_path / ".zf"
        sd.mkdir()
        store = TaskStore(sd / "kanban.json")
        store.add(Task(
            id="TASK-DONE",
            title="done",
            status="done",
            contract=TaskContract(feature_id="F-DELIVERY"),
        ))
        refs = sd / "refs"
        refs.mkdir()
        (refs / "feature-index.json").write_text(json.dumps({
            "F-DELIVERY": {
                "feature_id": "F-DELIVERY",
                "current_bundle": {
                    "current_task_map_ref": "artifacts/F-DELIVERY/v1/task_map.json",
                    "plan_ref": "docs/product/delivery-plan.md",
                },
                "bundle_history": [{}],
            },
        }), encoding="utf-8")
        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="task.done.evidence", actor="zf-cli", task_id="TASK-DONE")
        )
        local_client = TestClient(create_app(sd))

        data = local_client.get("/api/state").json()

        assert data["features"][0]["id"] == "F-DELIVERY"
        assert data["features"][0]["status"] == "done"
        assert data["delivery_features"][0]["current_task_map_ref"].endswith("task_map.json")

    def test_state_discovers_delivery_features_from_runtime_fallback(
        self,
        tmp_path: Path,
    ):
        sd = tmp_path / ".zf"
        sd.mkdir()
        store = TaskStore(sd / "kanban.json")
        store.add(Task(
            id="TASK-RUNTIME",
            title="runtime lane task",
            status="in_progress",
            contract=TaskContract(feature_id="", owner_role="dev"),
        ))
        EventLog(sd / "events.jsonl").append(ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            task_id="TASK-RUNTIME",
            correlation_id="trace-runtime",
            payload={"fanout_id": "FX-RUNTIME", "pdd_id": "PDD-RUNTIME"},
        ))
        local_client = TestClient(create_app(sd))

        data = local_client.get("/api/state").json()

        ids = {row["id"]: row for row in data["delivery_features"]}
        assert "PDD-RUNTIME" in ids
        assert ids["PDD-RUNTIME"]["source"] == "fallback:candidate-ref"
        assert ids["PDD-RUNTIME"]["degraded"] is True
        assert data["features"][0]["id"] == "PDD-RUNTIME"


class TestApiSnapshot:
    def test_snapshot_wraps_state_with_seq_and_runtime(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="ready task", status="backlog"),
        )

        r = client.get("/api/snapshot")

        assert r.status_code == 200
        data = r.json()
        assert data["seq"] >= 1
        assert data["project"]["state_dir"] == str(state_dir.resolve())
        assert data["runtime"]["mode"] == "read-only"
        assert data["tasks"][0]["id"] == "T1"
        assert data["tasks"][0]["ready"] is True

    def test_snapshot_uses_explicit_project_root_when_state_dir_is_external(
        self,
        tmp_path: Path,
    ):
        project_root = tmp_path / "project"
        state_dir = tmp_path / "runtime-state"
        project_root.mkdir()
        state_dir.mkdir()
        (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
        (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        local_client = TestClient(create_app(state_dir, project_root=project_root))

        data = local_client.get("/api/snapshot").json()
        runtime = local_client.get("/api/runtime").json()

        assert data["project"]["root"] == str(project_root.resolve())
        assert data["project"]["state_dir"] == str(state_dir.resolve())
        assert (
            runtime["agent_surface"]["shared_context"]["project_root"]
            == str(project_root.resolve())
        )
        assert (
            runtime["agent_surface"]["shared_project_workdir"]
            == str(project_root.resolve())
        )

    def test_snapshot_contains_react_workbench_domains(self, client):
        r = client.get("/api/snapshot")

        data = r.json()
        assert set(data) >= {
            "seq",
            "project",
            "tasks",
            "archive_tasks",
            "features",
            "traces",
            "fanouts",
            "candidates",
            "runs",
            "active_runs",
            "agents",
            "roles",
            "workdirs",
            "skills",
            "runtime",
        }

    def test_light_snapshot_does_not_build_full_runtime_projection(
        self,
        state_dir: Path,
        monkeypatch,
    ):
        def fail_runtime(*_args, **_kwargs):
            raise AssertionError("light snapshot must not build full runtime")

        monkeypatch.setattr("zf.web.server._runtime", fail_runtime)
        local_client = TestClient(create_app(state_dir, project_root=state_dir.parent))

        data = local_client.get("/api/snapshot/light").json()

        assert data["snapshot_slice"] == "light"
        assert data["runtime"]["mode"] == "snapshot-light"
        assert data["event_projection"]["schema_version"] == "event-read-model.v4"

    def test_snapshot_includes_runtime_snapshot_projection(
        self,
        state_dir: Path,
        client,
    ):
        from zf.runtime.runtime_snapshot import (
            RuntimeSnapshotInput,
            build_runtime_snapshot,
            runtime_snapshot_event_payload,
            write_runtime_snapshot,
        )

        task = Task(
            id="TASK-SNAP",
            title="snapshot task",
            active_dispatch_id="disp-1",
            contract=TaskContract(
                source_revision="src-rev",
                contract_revision="contract-rev",
                capsule_revision="capsule-rev",
            ),
        )
        snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
            source="dispatch",
            project_id="project",
            project_root=state_dir.parent,
            state_dir=state_dir,
            task=task,
            dispatch_id="disp-1",
            refs={"briefing": "briefings/TASK-SNAP.md"},
            output_contract={
                "deliverables": ["patch"],
                "expected_event": "dev.build.done",
            },
        ))
        result = write_runtime_snapshot(
            snapshot,
            state_dir=state_dir,
            project_root=state_dir.parent,
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="runtime.snapshot.recorded",
                actor="orchestrator",
                task_id="TASK-SNAP",
                payload=runtime_snapshot_event_payload(result),
            )
        )

        data = client.get("/api/snapshot").json()
        projection = data["runtime_snapshots"]

        assert projection["latest_by_task"]["TASK-SNAP"]["snapshot_ref"] == result.snapshot_ref
        assert projection["latest_by_task"]["TASK-SNAP"]["source"] == "dispatch"
        assert projection["snapshots"][0]["refs_count"] == 1
        assert projection["snapshots"][0]["expected_event"] == "dev.build.done"

    def test_snapshot_traces_fall_back_to_task_event_groups(self, state_dir, client):
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="dev.build.done", actor="dev-1", task_id="TASK-TRACE")
        )

        data = client.get("/api/snapshot").json()
        trace_ids = {item["trace_id"] for item in data["traces"]}

        assert "task:TASK-TRACE" in trace_ids
        detail = client.get("/api/traces/task:TASK-TRACE").json()
        assert detail["empty"] is False
        assert detail["tasks"] == ["TASK-TRACE"]

    def test_snapshot_includes_spine_review_insight(
        self,
        state_dir: Path,
        tmp_path: Path,
    ):
        project_id = stable_project_id(name=tmp_path.name, root=tmp_path)
        review = {
            "schema_version": "project-spine-review.v1",
            "review_id": "sprev-test",
            "project_id": project_id,
            "project_name": "project",
            "project_root": str(tmp_path),
            "state_dir": str(state_dir),
            "reviewed_at": "2026-05-26T00:00:00+00:00",
            "verdict": "correct_task",
            "confidence": "medium",
            "drift": ["contract_evidence_debt"],
            "design_spine": {"status": "needs_attention", "findings": ["missing refs"]},
            "delivery_spine": {"status": "blocked", "findings": ["partial task"]},
            "runtime_spine": {"status": "healthy", "findings": []},
            "reflection": {
                "better_solution": "补齐 evidence refs。",
                "verify": "step -> verify: workflow audit passes",
            },
            "corrective_actions": [{
                "action_id": "A1",
                "kind": "correct_task",
                "priority": "P1",
                "target": "task contract",
                "evidence_refs": ["task:TASK-1"],
            }],
        }
        context = ProjectContext(
            project_root=tmp_path,
            config_path=tmp_path / "zf.yaml",
            config=None,
            state_dir=state_dir,
        )
        write_spine_review_artifact(context, review)
        local_client = TestClient(create_app(state_dir, project_root=tmp_path))

        data = local_client.get("/api/snapshot").json()
        detail = local_client.get(f"/api/projects/{project_id}/spine-review").json()

        assert data["spine_review"]["schema_version"] == "spine-review-insight.v1"
        assert data["spine_review"]["verdict"] == "correct_task"
        assert detail["better_solution"] == "补齐 evidence refs。"


class TestApiRunArchives:
    def test_empty_runs_are_stable(self, client):
        runs = client.get("/api/runs")
        active = client.get("/api/runs/active")

        assert runs.status_code == 200
        assert active.status_code == 200
        assert runs.json()["runs"] == []
        assert active.json()["active_runs"] == []

    def test_archived_tasks_api_uses_task_archive(self, state_dir, client):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="TASK-DONE", title="done", status="backlog"))
        store.update("TASK-DONE", status="done")

        archived = client.get("/api/archives/tasks")
        with_active = client.get("/api/archives/tasks?include_active=true")

        assert archived.status_code == 200
        assert archived.json()[0]["id"] == "TASK-DONE"
        assert with_active.status_code == 200
        assert any(item["id"] == "TASK-DONE" for item in with_active.json())
        assert "latest_run_id" not in archived.text

    def test_runs_api_reads_archived_run_detail_and_redacts(self, state_dir, tmp_path):
        live = tmp_path / "live" / ".zf"
        live.mkdir(parents=True)
        EventLog(live / "events.jsonl").append(
            ZfEvent(
                type="test.failed",
                actor="test-1",
                task_id="TASK-WEB",
                payload={"output": "OPENAI_API_KEY=sk-1234567890abcdef"},
            )
        )
        (live / "scorecard.json").write_text(
            '{"status":"failed","token":"sk-1234567890abcdef"}\n',
            encoding="utf-8",
        )
        archive_run(
            project_root=state_dir.parent,
            state_dir=state_dir,
            live_state_dir=live,
            run_id="RUN-WEB",
            status="failed",
            trace_id="trace-web",
            test_task_id="TASK-WEB",
        )
        local_client = TestClient(create_app(state_dir, project_root=state_dir.parent))

        runs = local_client.get("/api/runs")
        detail = local_client.get("/api/runs/RUN-WEB")
        events = local_client.get("/api/runs/RUN-WEB/events")
        scorecard = local_client.get("/api/runs/RUN-WEB/scorecard")
        task_runs = local_client.get("/api/tasks/TASK-WEB/runs")

        assert runs.status_code == 200
        assert runs.json()["runs"][0]["run_id"] == "RUN-WEB"
        assert detail.status_code == 200
        assert detail.json()["run"]["test_task_id"] == "TASK-WEB"
        assert events.status_code == 200
        assert "sk-1234567890abcdef" not in events.text
        assert "[REDACTED" in events.text
        assert scorecard.status_code == 200
        assert "sk-1234567890abcdef" not in scorecard.text
        assert task_runs.status_code == 200
        assert task_runs.json()["runs"][0]["run_id"] == "RUN-WEB"

    def test_active_run_and_invalid_run_id(self, state_dir):
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="run.started",
                actor="zf-cli",
                task_id="TASK-ACTIVE",
                correlation_id="trace-active",
                payload={"run_id": "RUN-ACTIVE", "status": "running"},
            )
        )
        local_client = TestClient(create_app(state_dir, project_root=state_dir.parent))

        active = local_client.get("/api/runs/active")
        invalid = local_client.get("/api/runs/bad%20id")

        assert active.status_code == 200
        assert active.json()["active_runs"][0]["run_id"] == "RUN-ACTIVE"
        assert invalid.status_code == 400


class TestApiTaskDetail:
    def test_task_detail_includes_contract_events_and_redaction(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="detail", status="in_progress",
                 assigned_to="dev-1"),
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="dev.build.done",
                actor="dev-1",
                task_id="T1",
                correlation_id="trace-1",
                payload={"output": "API_TOKEN=secret-value"},
            )
        )

        r = client.get("/api/tasks/T1")

        assert r.status_code == 200
        data = r.json()
        assert data["task"]["id"] == "T1"
        assert data["contract"]["acceptance"] == "exit_code=0"
        assert data["trace_id"] == "trace-1"
        assert data["links"]["trace"] == "trace-1"
        assert data["status_model"]["task_status"] == "in_progress"
        assert data["status_model"]["run_completed_implies_task_done"] is False
        assert data["evidence_model"]["task_status_source"] == "TaskStore(active/archive)"
        assert data["evidence_model"]["execution"]["event_count"] == 1
        assert data["evidence_model"]["execution"]["verify_state"] == "empty"
        assert data["workflow_projection"]["workflow_phase"] == "verify"
        assert data["workflow_projection"]["impl_exit_gate_state"] == "pending"
        assert data["task"]["workflow_phase"] == "verify"
        assert data["evidence_model"]["interaction"]["transcript_truth"] == "interaction_evidence_only"
        assert data["task_run_panel"]["schema_version"] == "task-run-panel.v1"
        assert data["task_run_panel"]["task_id"] == "T1"
        assert data["task_run_panel"]["route_summary"]["current_stage"] == "dev"
        assert data["handoff_summary"]["schema_version"] == "handoff-summary.v1"
        assert data["handoff_summary"]["task_id"] == "T1"
        assert "secret-value" not in r.text
        assert "[REDACTED_SECRET]" in r.text

    def test_candidate_integration_failure_projects_to_task_detail_and_snapshot(
        self,
        state_dir,
        client,
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(
                id="REF-DISCOUNTS-CORE-001",
                title="refactor discounts",
                status="in_progress",
                assigned_to="dev-core",
                contract=TaskContract(
                    feature_id="F-REFACTOR",
                    plan_ref="tm-refactor-v1",
                    evidence_contract={
                        "source_refs": {
                            "task_map_ref": "tm-refactor-v1",
                        },
                    },
                ),
            ),
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="integration.failed",
                actor="zf-cli",
                correlation_id="trace-refactor",
                payload={
                    "candidate_ref": "candidate/F-REFACTOR",
                    "pdd_id": "F-REFACTOR",
                    "task_map_ref": "tm-refactor-v1",
                    "reason": "add/add conflict",
                    "conflict_files": ["src/discounts.py"],
                },
            )
        )

        state = client.get("/api/state").json()
        row = next(
            task for task in state["tasks"]
            if task["id"] == "REF-DISCOUNTS-CORE-001"
        )
        assert row["status"] == "in_progress"
        assert row["kanban_column"] == "blocked"
        assert row["verify_state"] == "failed"
        assert row["kanban_column_reason"] == "workflow_failure:add/add conflict"

        snapshot = client.get("/api/snapshot").json()
        snap_row = next(
            task for task in snapshot["tasks"]
            if task["id"] == "REF-DISCOUNTS-CORE-001"
        )
        assert snap_row["kanban_column"] == "blocked"
        assert snap_row["status"] != "done"

        detail = client.get("/api/tasks/REF-DISCOUNTS-CORE-001").json()
        assert detail["task"]["kanban_column"] == "blocked"
        assert detail["workflow_projection"]["verify_state"] == "failed"
        assert "add/add conflict" in detail["workflow_projection"]["rework_reason"]

    def test_task_detail_includes_artifact_manifest_refs(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-REFS", title="refs", status="in_progress"),
        )
        task_map_path = state_dir / "artifacts" / "TASK-REFS" / "task-map.json"
        task_map_path.parent.mkdir(parents=True)
        task_map_path.write_text(json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "TASK-A",
                "title": "A",
                "verification": "pytest tests/a.py",
                "wave": 1,
            }],
        }), encoding="utf-8")
        refs_dir = state_dir / "refs"
        refs_dir.mkdir()
        (refs_dir / "task-index.json").write_text(json.dumps({
            "TASK-REFS": {
                "task_id": "TASK-REFS",
                "manifest_event_id": "evt-manifest",
                "manifest_role": "arch",
                "contract_refs": {
                    "spec_ref": "docs/specs/task.md",
                    "plan_ref": "docs/plans/task-plan.md",
                    "tdd_ref": "docs/plans/task-tdd.md",
                },
                "artifact_refs_by_kind": {
                    "sdd": [{"path": "docs/specs/task.md"}],
                },
                "artifact_refs": [
                    {
                        "kind": "sdd",
                        "path": "docs/specs/task.md",
                        "artifact_id": "sdd-task-refs-v1",
                        "version": 1,
                        "status": "accepted",
                    },
                    {
                        "kind": "plan",
                        "path": "docs/plans/old.md",
                        "artifact_id": "plan-task-refs-v1",
                        "version": 1,
                        "status": "superseded",
                    },
                    {
                        "kind": "task_map",
                        "path": str(task_map_path),
                        "artifact_id": "task-map-task-refs-v1",
                        "version": 1,
                        "status": "accepted",
                    },
                ],
                "hash_status": [
                    {
                        "artifact_id": "sdd-task-refs-v1",
                        "path": "docs/specs/task.md",
                        "status": "ok",
                        "ledger_status": "accepted",
                    },
                ],
                "handoff_contract": {"required_for_dev": ["spec"]},
            },
        }), encoding="utf-8")

        r = client.get("/api/tasks/TASK-REFS")

        assert r.status_code == 200
        refs = r.json()["artifact_refs"]
        assert refs["manifest_event_id"] == "evt-manifest"
        assert refs["schema_version"] == "task-artifact-ledger.v1"
        assert refs["contract_refs"]["spec_ref"] == "docs/specs/task.md"
        assert refs["artifact_refs_by_kind"]["sdd"][0]["path"] == "docs/specs/task.md"
        assert refs["accepted_artifact_refs"][0]["artifact_id"] == "sdd-task-refs-v1"
        assert refs["stale_artifact_refs"][0]["status"] == "superseded"
        assert refs["hash_status"][0]["status"] == "ok"
        assert refs["task_map_summary"]["task_count"] == 1
        assert refs["task_map_summary"]["passed"] is True

    def test_task_detail_includes_artifact_manifest_missing_warning(
        self,
        state_dir,
        client,
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-WARN", title="refs warning", status="in_progress"),
        )
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            id="evt-ref-rejected",
            type="task.ref.rejected",
            actor="zf-cli",
            task_id="TASK-WARN",
            payload={
                "trigger_event_id": "evt-arch",
                "fallback_warning": (
                    "plan artifacts were found in the actor workdir but no "
                    "artifact manifest or artifact_refs were emitted"
                ),
                "detected_artifacts": ["SPEC.md", "tasks/plan.md"],
                "required_action": (
                    "emit artifact.manifest.published with accepted contract refs"
                ),
            },
        ))

        r = client.get("/api/tasks/TASK-WARN")

        assert r.status_code == 200
        diagnostics = r.json()["artifact_refs"]["diagnostics"]
        assert diagnostics[0]["type"] == "artifact_manifest_missing"
        assert diagnostics[0]["severity"] == "warning"
        assert diagnostics[0]["detected_artifacts"] == ["SPEC.md", "tasks/plan.md"]
        assert diagnostics[0]["required_action"] == (
            "emit artifact.manifest.published with accepted contract refs"
        )

    def test_task_detail_reads_archived_terminal_task(self, state_dir, client):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="TASK-DONE", title="archived detail", status="in_progress"))
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="run.completed",
                actor="zf-cli",
                task_id="TASK-DONE",
                correlation_id="trace-done",
                payload={"run_id": "RUN-DONE", "status": "passed"},
            )
        )
        store.update("TASK-DONE", status="done")

        r = client.get("/api/tasks/TASK-DONE")

        assert r.status_code == 200
        data = r.json()
        assert data["task"]["id"] == "TASK-DONE"
        assert data["task"]["status"] == "done"
        assert data["trace_id"] == "trace-done"
        assert data["status_model"]["terminal"] is True
        assert data["evidence_model"]["run_completed_implies_task_done"] is False

    def test_run_completion_is_evidence_not_task_done(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-RUN", title="run evidence", status="in_progress"),
        )
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="run.completed",
                actor="zf-cli",
                task_id="TASK-RUN",
                correlation_id="trace-run",
                payload={"run_id": "RUN-1", "status": "passed"},
            )
        )

        detail = client.get("/api/tasks/TASK-RUN").json()
        snapshot = client.get("/api/snapshot").json()

        assert detail["task"]["status"] == "in_progress"
        assert detail["evidence_model"]["execution"]["event_count"] == 1
        assert detail["evidence_model"]["run_completed_implies_task_done"] is False
        assert any(item["id"] == "TASK-RUN" and item["status"] == "in_progress" for item in snapshot["tasks"])
        assert all(item["id"] != "TASK-RUN" for item in snapshot["archive_tasks"])

    def test_missing_task_returns_404(self, client):
        r = client.get("/api/tasks/NOPE")
        assert r.status_code == 404

    def test_task_diff_is_read_only_and_empty_without_git_ref(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="detail", status="in_progress"),
        )

        r = client.get("/api/tasks/T1/diff")

        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == "T1"
        assert data["diff"] == ""
        assert data["error"]

    def test_task_diff_does_not_rescan_task_detail(self, state_dir, client, monkeypatch):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T-DIFF", title="diff", status="in_progress"),
        )

        def fail_detail(*_args, **_kwargs):
            raise AssertionError("_task_detail should not be called by diff")

        monkeypatch.setattr("zf.web.server._task_detail", fail_detail)

        r = client.get("/api/tasks/T-DIFF/diff")

        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == "T-DIFF"
        assert data["diff"] == ""
        assert data["error"]

    def test_task_timeline_endpoint_defaults_to_exact_task_events(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-TL", title="timeline", status="in_progress"),
        )
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.created",
            actor="planner-1",
            task_id="TASK-TL",
            correlation_id="trace-tl",
        ))
        log.append(ZfEvent(
            type="diagnostic.note",
            actor="system",
            payload={"task_id": "TASK-TL", "trace_id": "trace-payload"},
        ))

        exact = client.get("/api/tasks/TASK-TL/timeline")

        assert exact.status_code == 200
        exact_data = exact.json()
        assert exact_data["schema_version"] == "task-timeline.v1"
        assert exact_data["event_count"] == 1
        assert exact_data["timeline"][0]["type"] == "task.created"
        assert exact_data["trace_id"] == "trace-tl"
        assert exact_data["execution_route"]["source_event_count"] == 1
        assert exact_data["query"]["match"] == "task_id"

        log.append(ZfEvent(
            type="dev.build.done",
            actor="dev-1",
            task_id="TASK-TL",
            correlation_id="trace-tl",
        ))
        updated = client.get("/api/tasks/TASK-TL/timeline")
        assert updated.json()["event_count"] == 2

        deep = client.get("/api/tasks/TASK-TL/timeline?deep=true")
        assert deep.status_code == 200
        deep_data = deep.json()
        assert deep_data["event_count"] == 3
        assert deep_data["query"]["match"] == "task_id_or_payload"


class TestApiWorkbenchProjections:
    def test_roles_workdirs_runtime_and_skills_projection(self, state_dir):
        import yaml

        skills_dir = state_dir / "skills" / "scan"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: scan\ndescription: scanner\n---\nbody\n",
            encoding="utf-8",
        )
        unused_dir = state_dir / "skills" / "unused"
        unused_dir.mkdir(parents=True)
        (unused_dir / "SKILL.md").write_text(
            "---\nname: unused\ndescription: disabled\n---\nbody\n",
            encoding="utf-8",
        )
        manifest_dir = state_dir / "workdirs" / "dev-1" / "runtime"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "skills-manifest.json").write_text(
            '{"instance_id":"dev-1","skills":[{"name":"scan","sha256":"old"}]}',
            encoding="utf-8",
        )
        (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
            "project_root": str(state_dir.parent),
            "instance_meta": {"dev-1": {"backend": "codex"}},
            "roles": {"dev-1": "00000000-0000-0000-0000-000000000001"},
        }))
        (state_dir / "skills.lock.json").write_text(json.dumps({
            "version": 1,
            "skills": [
                {
                    "role": "dev",
                    "instance_id": "dev-1",
                    "name": "scan",
                    "source_name": "agent-skills",
                    "status": "resolved",
                    "materialized_to": ".zf/workdirs/dev-1/runtime/skills/scan",
                    "warnings": [],
                },
            ],
        }), encoding="utf-8")
        config = ZfConfig(
            roles=[
                RoleConfig(
                    name="dev",
                    instance_id="dev-1",
                    backend="codex",
                    skills=["scan"],
                ),
            ],
        )
        local_client = TestClient(create_app(state_dir, config=config))

        roles = local_client.get("/api/roles").json()
        agents = local_client.get("/api/agents").json()
        workdirs = local_client.get("/api/workdirs").json()
        runtime = local_client.get("/api/runtime").json()
        skills = local_client.get("/api/skills").json()

        assert roles[0]["instance_id"] == "dev-1"
        assert roles[0]["skills"] == ["scan"]
        by_agent = {item["instance_id"]: item for item in agents}
        assert by_agent["kanban-agent"]["agent_kind"] == "web_surface"
        assert by_agent["kanban-agent"]["runtime_state"] == "active"
        assert by_agent["orchestrator"]["agent_kind"] == "control"
        assert by_agent["dev-1"]["agent_kind"] == "writer"
        assert by_agent["dev-1"]["branch_or_ref"] == "worker/dev-1"
        assert by_agent["dev-1"]["debug"]["state_inference"] == "debug_only_not_truth"
        assert by_agent["dev-1"]["debug"]["attach_hint"] == "zf attach dev-1"
        assert workdirs[0]["branch_or_ref"] == "worker/dev-1"
        assert runtime["actions"]["mutation_enabled"] is False
        assert runtime["web_session"]["mode"] == "read_only"
        assert runtime["agent_surface"]["id"] == "kanban-agent"
        assert runtime["agent_surface"]["shared_context"]["project_root"] == str(state_dir.parent)
        assert (
            runtime["agent_surface"]["shared_context"]["mode"]
            == "dedicated_operator_workdir_with_project_pointers"
        )
        assert runtime["agent_surface"]["shared_project_workdir"] == str(state_dir.parent)
        assert runtime["agent_surface"]["state_dir"] == str(state_dir)
        assert "scan" in runtime["agent_surface"]["skills_available"]["names"]
        assert "update-task" in runtime["agent_surface"]["allowed_actions"]
        assert runtime["agent_surface"]["boundary"]["scheduler"] is False
        assert runtime["agent_surface"]["status_model"]["run_completed_implies_task_done"] is False
        by_skill = {item["name"]: item for item in skills["pool"]}
        assert by_skill["scan"]["enabled_by"] == ["dev-1"]
        assert by_skill["unused"]["enabled_by"] == []
        assert skills["enabled"][0]["role"] == "dev-1"
        assert skills["loaded"][0]["role"] == "dev-1"
        assert skills["loaded"][0]["name"] == "scan"
        assert skills["loaded"][0]["source"] == "agent-skills"
        assert skills["warnings"][0]["status"] == "materialized_hash_mismatch"


class TestApiChannels:
    def test_default_channel_detail_is_empty_before_first_event(self, client):
        response = client.get("/api/channels/ch-zaofu")

        assert response.status_code == 200
        data = response.json()
        assert data["channel_id"] == "ch-zaofu"
        assert data["empty"] is True
        assert data["members"] == []
        assert data["messages"] == []

    def test_channel_projection_rebuilds_from_events_and_redacts(self, state_dir, client):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="channel.created",
            actor="web",
            task_id="TASK-CH",
            payload={
                "channel_id": "task-TASK-CH",
                "channel_name": "# task-TASK-CH",
                "task_id": "TASK-CH",
                "created_by": "web",
            },
        ))
        log.append(ZfEvent(
            type="channel.member.invited",
            actor="web",
            task_id="TASK-CH",
            payload={
                "channel_id": "task-TASK-CH",
                "task_id": "TASK-CH",
                "member_id": "codex-1",
                "persona": "codex-1",
                "member_type": "codex",
                "backend": "codex",
                "scope": "channel",
                "permissions": ["read", "message"],
                "reason": "TOKEN=secret-value",
                "source": "web",
            },
        ))
        log.append(ZfEvent(
            type="workflow.invoke.requested",
            actor="codex-1",
            task_id="TASK-CH",
            payload={
                "channel_id": "task-TASK-CH",
                "reason": "request kernel action",
                "created_by": "codex-1",
            },
        ))

        index = client.get("/api/channels")
        detail = client.get("/api/channels/task-TASK-CH")

        assert index.status_code == 200
        assert detail.status_code == 200
        items = {item["id"]: item for item in index.json()["channels"]}
        assert "task-task-ch" in items
        assert items["task-task-ch"]["member_count"] == 1
        assert items["task-task-ch"]["pending_workflow_requests"] == 1
        data = detail.json()
        assert data["members"][0]["member_id"] == "codex-1"
        assert data["workflow_requests"][0]["status"] == "requested"
        assert client.get("/api/events?prefix=channel.").json()["items"]
        assert "secret-value" not in detail.text
        assert "[REDACTED_SECRET]" in detail.text
        assert not (state_dir / "channels.json").exists()

    def test_channel_detail_keeps_review_gate_state_update_refs(self, state_dir, client):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="channel.created",
            actor="web",
            payload={"channel_id": "ch-review", "channel_name": "# review"},
        ))
        log.append(ZfEvent(
            type="channel.state_update.posted",
            actor="zf-autoresearch",
            payload={
                "channel_id": "ch-review",
                "thread_id": "review-gate",
                "status": "review_gate.triggered",
                "run_id": "ar-run-1",
                "summary": "fanout gate requested for high-risk runtime failure",
                "refs": {
                    "review_gate_summary": ".zf/autoresearch/runs/ar-run-1/review-gate/summary.json",
                    "failure_evidence_pack": ".zf/autoresearch/runs/ar-run-1/review-gate/failure.json",
                },
            },
        ))
        log.append(ZfEvent(
            type="channel.synthesis.proposed",
            actor="ar-synth",
            payload={
                "channel_id": "ch-review",
                "thread_id": "review-gate",
                "decision": "approve",
                "summary": "critic accepted the minimal repair plan",
                "source_refs": ["ar-diagnoser", "ar-critic-verifier"],
                "evidence_refs": [
                    ".zf/autoresearch/runs/ar-run-1/review-gate/closeout.json",
                ],
            },
        ))

        detail = client.get("/api/channels/ch-review")

        assert detail.status_code == 200
        data = detail.json()
        assert data["state_updates"][-1]["status"] == "review_gate.triggered"
        assert (
            data["state_updates"][-1]["refs"]["review_gate_summary"]
            .endswith("summary.json")
        )
        assert data["syntheses"][-1]["decision"] == "approve"
        assert data["syntheses"][-1]["evidence_refs"][0].endswith("closeout.json")

    def test_channel_event_schema_accepts_member_invite(self):
        registry = EventSchemaRegistry.from_dict(channel_event_schema_rules())
        valid = ZfEvent(
            type="channel.member.invited",
            payload={
                "channel_id": "zaofu",
                "member_id": "claude-1",
                "persona": "Claude",
                "source": "web",
            },
        )

        assert registry.validate(valid) == []

    def test_channel_create_action_only_appends_events(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.create",
            headers={"x-zf-web-token": "test-token"},
            json={
                "name": "# research",
                "channel_id": "research",
            },
        )

        assert r.status_code == 202
        assert r.json()["status"] == "created"
        assert r.json()["action"] == "channel-create"
        assert r.json()["requested_action"] == "channel.create"
        assert r.json()["channel_id"] == "ch-research"
        assert TaskStore(state_dir / "kanban.json").list_all() == []
        events = EventLog(state_dir / "events.jsonl").read_all()
        channel_events = [event for event in events if event.type == "channel.created"]
        assert len(channel_events) == 1
        assert channel_events[0].payload["source"] == "web"
        detail = local_client.get("/api/channels/ch-research").json()
        assert detail["channel_id"] == "ch-research"
        assert detail["name"] == "# research"

    def test_channel_add_member_action_only_appends_events(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "channel_name": "# zaofu",
                "member_id": "codex-1",
                "member_type": "provider_agent",
                "provider": "codex",
                "backend": "codex",
                "channel_role": "tech_leader",
                "visibility_profile": "planner",
                "permission_profile": "project_writer",
                "role_context_ref": "channel_roles/tech-leader.md",
                "permissions": ["read", "message", "summarize"],
            },
        )

        assert r.status_code == 202
        assert r.json()["status"] == "invited"
        assert r.json()["action"] == "channel-invite-member"
        assert r.json()["requested_action"] == "channel.add_member"
        assert TaskStore(state_dir / "kanban.json").list_all() == []
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.member.invited" in types
        assert "task.created" not in types
        assert "task.updated" not in types
        detail = local_client.get("/api/channels/zaofu").json()
        member = detail["members"][0]
        assert member["member_id"] == "codex-1"
        assert member["member_type"] == "provider_agent"
        assert member["provider"] == "codex"
        assert member["channel_role"] == "tech_leader"
        assert member["visibility_profile"] == "planner"
        assert member["permission_profile"] == "project_writer"
        assert member["write_policy"]["mode"] == "project_writer"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.member.permission_profile.audit" in types

    def test_channel_add_member_rejects_unsafe_permission(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "claude-1",
                "member_type": "claude-code",
                "permissions": ["read", "execute_task"],
            },
        )

        assert r.status_code == 422
        assert r.json()["status"] == "invalid_payload"
        assert "unsafe" in r.json()["reason"]

    def test_channel_update_member_permission_profile(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        added = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "codex-1",
                "member_type": "provider_agent",
                "provider": "codex",
                "backend": "codex",
                "permission_profile": "read_only",
                "permissions": ["read", "message", "summarize"],
            },
        )
        assert added.status_code == 202

        updated = local_client.post(
            "/api/actions/channel.member.permission",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "codex-1",
                "permission_profile": "project_writer",
                "reason": "allow project file edits",
            },
        )

        assert updated.status_code == 202
        assert updated.json()["status"] == "permission_updated"
        assert updated.json()["action"] == "channel-update-member-permission"
        detail = local_client.get("/api/channels/zaofu").json()
        member = detail["members"][0]
        assert member["permission_profile"] == "project_writer"
        assert member["write_policy"]["mode"] == "project_writer"
        assert member["permissions"] == ["read", "message", "summarize"]
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.member.permissions.updated" in types
        assert types.count("channel.member.permission_profile.audit") == 1

    def test_channel_add_member_rejects_dangerous_profile_without_ack(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "codex-danger",
                "member_type": "provider_agent",
                "provider": "codex",
                "permission_profile": "dangerous_full",
            },
        )

        assert r.status_code == 422
        assert r.json()["status"] == "invalid_payload"
        assert "dangerous_ack" in r.json()["reason"]

    def test_channel_add_member_rejects_bad_member_type(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "bad",
                "member_type": "shell",
                "permissions": ["read"],
            },
        )

        assert r.status_code == 422
        assert r.json()["status"] == "invalid_payload"
        assert "member_type" in r.json()["reason"]

    def test_channel_add_member_rejects_bad_provider(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "unknown-1",
                "member_type": "provider_agent",
                "provider": "unknown-provider",
                "permissions": ["read"],
            },
        )

        assert r.status_code == 422
        assert r.json()["status"] == "invalid_payload"
        assert "provider" in r.json()["reason"]

    def test_channel_add_member_rejects_unsafe_role_context_ref(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "zaofu",
                "member_id": "bad-ref",
                "member_type": "provider_agent",
                "provider": "codex",
                "role_context_ref": "../AGENTS.md",
                "permissions": ["read"],
            },
        )

        assert r.status_code == 422
        assert r.json()["status"] == "invalid_payload"
        assert "role_context_ref" in r.json()["reason"]

    def test_channel_message_routes_operator_mentions_to_runtime_role(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        invite = local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "member_id": "codex-1",
                "member_type": "runtime-role",
                "backend": "codex",
                "permissions": ["read", "message"],
                "backing_worker_session_id": "dev-1",
            },
        )
        assert invite.status_code == 202

        r = local_client.post(
            "/api/channels/ch-zaofu/messages",
            headers={"x-zf-web-token": "test-token"},
            json={"text": "@codex review current plan"},
        )

        assert r.status_code == 202
        assert r.json()["route"]["targets"] == ["codex-1"]
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "channel.mention.detected" in types
        assert "channel.context_pack.built" in types
        assert "channel.agent.reply.requested" in types
        for _ in range(20):
            if any(event.type == "worker.reply.requested" for event in EventLog(state_dir / "events.jsonl").read_all()):
                break
            time.sleep(0.05)
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "worker.reply.requested" in types
        assert TaskStore(state_dir / "kanban.json").list_all() == []
        detail = local_client.get("/api/channels/ch-zaofu").json()
        assert detail["messages"][0]["mentions"] == ["codex-1"]
        assert detail["reply_requests"][0]["target_member_id"] == "codex-1"

    def test_channel_remove_clear_and_delete_actions_are_event_gated(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        local_client.post(
            "/api/actions/channel.create",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-test", "name": "# test"},
        )
        local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-test",
                "member_id": "qa",
                "member_type": "provider_agent",
                "backend": "codex",
                "permissions": ["read", "message"],
            },
        )
        local_client.post(
            "/api/channels/ch-test/messages",
            headers={"x-zf-web-token": "test-token"},
            json={"text": "@qa verify"},
        )

        removed = local_client.post(
            "/api/actions/channel-remove-member",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-test", "member_id": "qa"},
        )
        cleared = local_client.post(
            "/api/actions/channel-clear-history",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-test", "reason": "reset"},
        )
        deleted = local_client.post(
            "/api/actions/channel-delete",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-test", "reason": "remove group"},
        )

        assert removed.status_code == 202
        assert cleared.status_code == 202
        assert deleted.status_code == 202
        detail = local_client.get("/api/channels/ch-test").json()
        assert detail["members"] == []
        assert detail["messages"] == []
        assert detail["status"] == "archived"
        listing = local_client.get("/api/channels").json()
        assert "ch-test" not in {item["channel_id"] for item in listing["channels"]}
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.member.removed" in types
        assert "channel.history.cleared" in types
        assert "channel.archived" in types

    def test_channel_history_search_and_mark_read_action(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "member_id": "qa",
                "member_type": "provider_agent",
                "backend": "codex",
                "permissions": ["read", "message"],
            },
        )
        local_client.post(
            "/api/channels/ch-zaofu/messages",
            headers={"x-zf-web-token": "test-token"},
            json={"text": "@qa verify launch plan"},
        )

        search = local_client.get("/api/channels/ch-zaofu/history/search?q=launch").json()
        before = local_client.get("/api/channels/ch-zaofu").json()
        marked = local_client.post(
            "/api/actions/channel-mark-read",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-zaofu", "thread_id": "main", "member_id": "qa"},
        )
        after = local_client.get("/api/channels/ch-zaofu").json()

        assert search["items"][0]["message_id"].startswith("msg-")
        assert search["history_index"]["mentions"][0]["id"] == "qa"
        assert before["read_state"][0]["mention_count"] == 1
        assert marked.status_code == 202
        assert after["read_state"][0]["mention_count"] == 0
        assert after["attention"] == []

    def test_channel_assistant_message_does_not_auto_route_mentions(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "member_id": "qa-1",
                "member_type": "codex",
                "backend": "codex",
                "permissions": ["read", "message"],
            },
        )

        r = local_client.post(
            "/api/channels/ch-zaofu/messages",
            headers={"x-zf-web-token": "test-token"},
            json={
                "text": "@qa please verify",
                "member_id": "codex-1",
                "role": "assistant",
            },
        )

        assert r.status_code == 202
        assert r.json()["route"]["skipped"][0]["reason"] == "auto_route_not_allowed"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.message.posted" in types
        assert "channel.agent.reply.requested" not in types

    def test_channel_handoff_and_discussion_mode_actions_are_event_gated(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        local_client.post(
            "/api/actions/channel.add_member",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "member_id": "qa-1",
                "member_type": "persona",
                "backend": "fake",
                "permissions": ["read", "message"],
            },
        )

        mode = local_client.post(
            "/api/actions/channel-discussion-mode",
            headers={"x-zf-web-token": "test-token"},
            json={"channel_id": "ch-zaofu", "mode": "fanout_then_synthesis"},
        )
        handoff = local_client.post(
            "/api/actions/channel.handoff",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "message_id": "msg-1",
                "member_id": "dev-1",
                "target_member_id": "qa-1",
                "reason": "verify",
            },
        )

        assert mode.status_code == 202
        assert handoff.status_code == 202
        assert TaskStore(state_dir / "kanban.json").list_all() == []
        detail = local_client.get("/api/channels/ch-zaofu").json()
        assert detail["discussion"]["mode"] == "fanout_then_synthesis"
        assert detail["handoffs"][1]["status"] == "accepted"
        assert detail["reply_requests"][0]["status"] == "completed"

    def test_channel_discussion_mode_forwards_relay_depth_and_deadlines(self, state_dir, monkeypatch):
        # D1 regression: the projection folds max_relay_depth / participants /
        # synthesizer / phase_deadline_seconds, but the controlled action used
        # to drop them, so the operator could never tune the mention_relay depth
        # cap or phase deadlines through channel-discussion-mode.
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        resp = local_client.post(
            "/api/actions/channel-discussion-mode",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-relay",
                "mode": "mention_relay",
                "max_relay_depth": 2,
                "phase_deadline_seconds": {"phase1_blind": 120},
                "synthesizer": "arch-1",
            },
        )
        assert resp.status_code == 202
        discussion = local_client.get("/api/channels/ch-relay").json()["discussion"]
        assert discussion["mode"] == "mention_relay"
        assert discussion["max_relay_depth"] == 2
        assert discussion["phase_deadline_seconds"] == {"phase1_blind": 120}
        assert discussion["synthesizer"] == "arch-1"

    def test_channel_owner_report_action_generates_projection_without_task_mutation(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="channel.member.added",
            actor="web",
            payload={
                "channel_id": "ch-zaofu",
                "member_id": "owner:min",
                "member_type": "owner_delegate",
            },
            correlation_id="ch-zaofu",
        ))
        log.append(ZfEvent(
            type="channel.message.posted",
            actor="web",
            payload={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "message_id": "msg-1",
                "text": "Need owner summary",
                "source": "web",
            },
            correlation_id="ch-zaofu",
        ))
        log.append(ZfEvent(
            type="channel.state_update.posted",
            actor="web",
            payload={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "status": "blocked",
                "summary": "waiting on provider binding",
                "source": "web",
            },
            correlation_id="ch-zaofu",
        ))

        r = local_client.post(
            "/api/actions/channel.owner_report.request",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "owner_id": "owner:min",
                "member_id": "boss-agent",
                "period": "current",
            },
        )

        assert r.status_code == 202
        assert r.json()["action"] == "channel-owner-report"
        assert r.json()["status"] == "generated"
        assert TaskStore(state_dir / "kanban.json").list_all() == []
        detail = local_client.get("/api/channels/ch-zaofu").json()
        generated = [item for item in detail["owner_reports"] if item["status"] == "generated"][0]
        assert generated["owner_id"] == "owner:min"
        assert generated["blockers"] == ["waiting on provider binding"]
        assert generated["refs"]["request_event_id"]
        assert {
            item["event_id"] for item in generated["refs"]["preview_refs"]
        } >= {
            generated["refs"]["request_event_id"],
            generated["refs"]["channel_last_event_id"],
        }
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "channel.owner_report.requested" in types
        assert "channel.owner_report.generated" in types
        assert "task.created" not in types
        assert "task.updated" not in types

    def test_channel_owner_report_explains_replan_owner_gate(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="channel.member.added",
            actor="web",
            payload={
                "channel_id": "ch-zaofu",
                "member_id": "owner:min",
                "member_type": "owner_delegate",
            },
            correlation_id="ch-zaofu",
        ))
        log.append(ZfEvent(
            type="channel.state_update.posted",
            actor="web",
            payload={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "kind": "replan",
                "status": "revise",
                "summary": "replan proposal needs owner decision",
                "proposal_ref": ".zf/autoresearch/replan.json",
                "source": "web",
            },
            correlation_id="ch-zaofu",
        ))

        r = local_client.post(
            "/api/actions/channel.owner_report.request",
            headers={"x-zf-web-token": "test-token"},
            json={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "owner_id": "owner:min",
                "period": "current",
            },
        )

        assert r.status_code == 202
        detail = local_client.get("/api/channels/ch-zaofu").json()
        generated = [item for item in detail["owner_reports"] if item["status"] == "generated"][0]
        assert generated["replan_status"]["needs_owner_decision"] is True
        assert any("approve/defer/reject" in item for item in generated["recommended_actions"])


class TestApiTraceCandidateFanoutEventsSearchDiagnostics:
    def test_trace_candidate_fanout_events_search_and_diagnostics(self, state_dir, client):
        diagnostics_dir = state_dir / "diagnostics" / "trace-1"
        diagnostics_dir.mkdir(parents=True)
        (diagnostics_dir / "errors.jsonl").write_text(
            '{"message":"TOKEN=secret-value"}\n',
            encoding="utf-8",
        )
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="candidate.ready",
            actor="orch",
            task_id="T1",
            correlation_id="trace-1",
            payload={
                "candidate_ref": "candidate/PDD-1",
                "pdd_id": "PDD-1",
                "fanout_id": "fanout-1",
            },
        ))
        log.append(ZfEvent(
            type="fanout.child.completed",
            actor="dev-1",
            task_id="T1",
            correlation_id="trace-1",
            payload={"fanout_id": "fanout-1", "child_run": "dev-1"},
        ))

        assert client.get("/api/traces/trace-1").json()["event_count"] == 2
        assert client.get("/api/candidates/PDD-1").json()["candidate_ref"] == "candidate/PDD-1"
        assert client.get("/api/fanouts/fanout-1").json()["children"][0]["id"] == "dev-1"
        assert client.get("/api/events?task_id=T1").json()["items"]

        search = client.get("/api/search?q=task:T1").json()
        assert search["events"]

        diagnostics = client.get("/api/diagnostics/trace-1")
        assert diagnostics.status_code == 200
        assert "secret-value" not in diagnostics.text
        assert "[REDACTED_SECRET]" in diagnostics.text

    def test_fanout_projection_distinguishes_planned_verify_lanes_from_scoped_child(
        self,
        state_dir,
    ):
        fanout_id = "fanout-cj-min-candidate-verification-evt-scoped"
        fanout_dir = state_dir / "fanouts" / fanout_id
        fanout_dir.mkdir(parents=True)
        (fanout_dir / "manifest.json").write_text(json.dumps({
            "fanout_id": fanout_id,
            "trace_id": "trace-r37",
            "stage_id": "cj-min-candidate-verification",
            "topology": "fanout_reader",
            "status": "completed",
            "children": [{
                "child_id": "verify-lane-0-assembly",
                "role_instance": "verify-lane-0",
                "status": "completed",
                "task_id": "CJMIN-ASSEMBLY-001",
                "payload": {
                    "assignment_strategy": "affinity_stage_slots",
                    "lane_profile": "cj-min-refactor-5-slot",
                    "lane_id": "lane0",
                    "stage_slot": "verify",
                    "affinity_tag": "assembly",
                    "upstream_task_id": "CJMIN-ASSEMBLY-001",
                },
            }],
        }), encoding="utf-8")
        config = ZfConfig(workflow=WorkflowConfig(
            affinity_lanes={
                "cj-min-refactor-5-slot": WorkflowAffinityLaneProfileConfig(
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id=f"lane{i}",
                            impl=f"dev-lane-{i}",
                            verify=f"verify-lane-{i}",
                        )
                        for i in range(5)
                    ],
                ),
            },
            stages=[
                WorkflowStageConfig(
                    id="cj-min-candidate-verification",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=[f"verify-lane-{i}" for i in range(5)],
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="cj-min-refactor-5-slot",
                        stage_slot="verify",
                    ),
                ),
            ],
        ))
        local_client = TestClient(create_app(state_dir, config=config))

        detail = local_client.get(f"/api/fanouts/{fanout_id}").json()

        lane = detail["lane_projection"]
        assert detail["progress"]["total"] == 1
        assert detail["progress"]["planned_total"] == 5
        assert lane["planned_lane_count"] == 5
        assert lane["active_child_count"] == 1
        assert lane["active_lane_count"] == 1
        assert lane["scope"] == "scoped_reverify"
        assert lane["planned_roles"] == [f"verify-lane-{i}" for i in range(5)]
        assert lane["active_roles"] == ["verify-lane-0"]
        assert detail["children"][0]["lane_id"] == "lane0"

        summary = local_client.get("/api/snapshot").json()["fanouts"][0]
        assert summary["fanout_id"] == fanout_id
        assert summary["lane_projection"]["planned_lane_count"] == 5
        assert summary["lane_projection"]["active_child_count"] == 1

    def test_task_and_trace_project_execution_route(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-ROUTE", title="route", status="in_progress"),
        )
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.created",
            actor="planner-1",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
            payload={"assignee": "dev-1"},
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
            payload={"assignee": "dev-2"},
        ))
        log.append(ZfEvent(
            type="dev.build.done",
            actor="dev-1",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
        ))
        log.append(ZfEvent(
            type="dev.build.done",
            actor="dev-2",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
        ))
        log.append(ZfEvent(
            type="review.approved",
            actor="critic-1",
            task_id="TASK-ROUTE",
            correlation_id="trace-route",
        ))

        snapshot = client.get("/api/snapshot").json()
        task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-ROUTE")
        assert task["route_summary"]["parallel"] is True
        assert task["route_summary"]["summary"] == "planner-1 -> dev-1/dev-2 -> critic-1"

        detail = client.get("/api/tasks/TASK-ROUTE").json()
        assert detail["execution_route"]["linear"][1]["label"] == "Dev Fanout"
        assert detail["execution_route"]["dag"]["edges"]

        trace = client.get("/api/traces/trace-route").json()
        assert trace["execution_route"]["summary"] == task["route_summary"]["summary"]
        assert trace["execution_route"]["dag"]["nodes"]

    def test_candidate_detail_reads_manifest_projection(self, state_dir, client):
        manifest_dir = state_dir / "candidates" / "F-11111111"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(json.dumps({
            "pdd_id": "F-11111111",
            "branch": "candidate/F-11111111",
            "base_ref": "main",
            "base_commit": "abc123",
            "status": "updated",
            "included_tasks": [
                {"task_id": "TASK-1", "task_ref": "task/TASK-1"},
            ],
        }), encoding="utf-8")

        detail = client.get("/api/candidates/F-11111111").json()
        snapshot = client.get("/api/snapshot").json()

        assert detail["candidate_ref"] == "candidate/F-11111111"
        assert detail["base_main"] == "abc123"
        assert detail["task_refs"] == ["task/TASK-1"]
        assert detail["tasks"] == ["TASK-1"]
        assert detail["status"] == "updated"
        assert snapshot["candidates"][0]["candidate_ref"] == "candidate/F-11111111"

    def test_events_exposes_malformed_and_cursor_paginates(self, state_dir, client):
        path = state_dir / "events.jsonl"
        path.write_text(
            '{"type":"task.created","id":"e1","ts":"2026-04-27T00:00:00",'
            '"actor":"x","task_id":"T1","payload":{},'
            '"causation_id":null,"correlation_id":null}\n'
            'garbage line not json\n'
            '{"type":"task.dispatched","id":"e2","ts":"2026-04-27T00:00:01",'
            '"actor":"x","task_id":"T1","payload":{},'
            '"causation_id":null,"correlation_id":null}\n',
            encoding="utf-8",
        )

        first = client.get("/api/events?limit=2&cursor=0").json()
        second = client.get(
            f"/api/events?limit=2&cursor={first['next_cursor']}"
        ).json()

        assert [item["seq"] for item in first["items"]] == [1, 2]
        assert first["items"][1]["type"] == "event.malformed"
        assert [item["seq"] for item in second["items"]] == [3]

    def test_event_detail_hydrates_full_payload_from_id(self, state_dir, client):
        event = ZfEvent(
            type="agent.session.part.delta",
            actor="dev-1",
            task_id="TASK-EVENT",
            payload={
                "run_id": "run-1",
                "content": "full payload value",
                "large": "x" * 4000,
            },
        )
        EventLog(state_dir / "events.jsonl").append(event)

        list_payload = client.get("/api/events?limit=5").json()
        listed = next(item for item in list_payload["items"] if item["id"] == event.id)
        assert listed["payload_slim"] is True

        detail = client.get(f"/api/events/{event.id}").json()

        assert detail["schema_version"] == "event-detail.v1"
        assert detail["event"]["id"] == event.id
        assert detail["event"]["payload_slim"] is False
        assert detail["event"]["payload"]["content"] == "full payload value"
        assert detail["event"]["payload"]["large"] == "x" * 4000


class TestApiWebActions:
    def test_action_disabled_without_token(self, client):
        r = client.post("/api/actions/dispatch", json={"task_id": "T1"})

        assert r.status_code == 403
        assert r.json()["status"] == "disabled"

    def test_action_with_token_fails_closed_and_emits_audit(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/dispatch",
            headers={"x-zf-web-token": "test-token"},
            json={"task_id": "T1"},
        )

        assert r.status_code == 501
        assert r.json()["status"] == "not_implemented"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "web.action.requested" in types
        assert "runtime.action.accepted" in types
        assert "runtime.action.failed" in types
        assert "web.action.failed" in types

    def test_ship_action_reports_blocked_reason(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/ship",
            headers={"x-zf-web-token": "test-token"},
            json={"task_id": "T1"},
        )

        assert r.status_code == 409
        data = r.json()
        assert data["status"] == "blocked"
        assert "ship blocked" in data["reason"]
        assert data["blockers"]

    def test_maintenance_prepare_action_pauses_dispatch(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/maintenance.prepare",
            headers={"x-zf-web-token": "test-token"},
            json={"trigger_id": "trig-web", "reason": "repair harness"},
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "prepared"
        assert data["trigger_id"] == "trig-web"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "runtime.maintenance.entered" in types
        assert "dispatch.paused" in types
        assert "web.action.completed" in types

    def test_failure_closeout_action_materializes_candidates(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        (state_dir / "failure-candidates").mkdir()
        (state_dir / "failure-candidates" / "fail-web-action.json").write_text(
            json.dumps({
                "schema_version": "failure-candidate.v1",
                "failure_id": "fail-web-action",
                "summary": "run.manager.action.failed: resume failed",
                "classification": {"problem_class": "runtime_recovery"},
                "event": {"type": "run.manager.action.failed"},
                "evidence_refs": [],
            }),
            encoding="utf-8",
        )
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/failure.closeout",
            headers={"x-zf-web-token": "test-token"},
            json={"kinds": ["backlog", "eval"], "output_root": "artifacts/failure-closeout"},
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "materialized"
        assert data["materialized_count"] == 1
        assert Path(data["manifest_ref"]).exists()
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "failure.closeout.materialized" in types
        assert "web.action.completed" in types

    def test_failure_closeout_activate_action_requires_approval_and_promotes_tasks(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        project_root = state_dir.parent
        (state_dir / "failure-candidates").mkdir()
        (state_dir / "failure-candidates" / "fail-web-activate.json").write_text(
            json.dumps({
                "schema_version": "failure-candidate.v1",
                "failure_id": "fail-web-activate",
                "summary": "run.manager.action.failed: resume failed",
                "classification": {"problem_class": "runtime_recovery"},
                "event": {"type": "run.manager.action.failed"},
                "evidence_refs": [],
            }),
            encoding="utf-8",
        )
        local_client = TestClient(create_app(state_dir, project_root=project_root))
        materialized = local_client.post(
            "/api/actions/failure.closeout",
            headers={"x-zf-web-token": "test-token"},
            json={"kinds": ["backlog"], "output_root": "artifacts/failure-closeout"},
        ).json()

        blocked = local_client.post(
            "/api/actions/failure.closeout.activate",
            headers={"x-zf-web-token": "test-token"},
            json={"manifest_ref": materialized["manifest_ref"]},
        )
        assert blocked.status_code == 403
        assert blocked.json()["status"] == "approval_required"

        r = local_client.post(
            "/api/actions/failure.closeout.activate",
            headers={"x-zf-web-token": "test-token"},
            json={
                "manifest_ref": materialized["manifest_ref"],
                "approval_ref": "owner-approved-web",
            },
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "activated"
        assert data["promoted_count"] == 1
        assert (project_root / "tasks" / "active").exists()
        assert Path(data["report_ref"]).exists()
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "failure.closeout.activated" in types
        assert "web.action.completed" in types

    def test_real_e2e_run_action_uses_declared_run_contract_matrix(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        project_root = state_dir.parent
        matrix_ref = "docs/real-e2e-matrix.json"
        (project_root / "docs").mkdir()
        (project_root / matrix_ref).write_text(
            json.dumps({
                "schema_version": "real-e2e-matrix.v1",
                "rows": [{
                    "id": "cli-smoke",
                    "surface": "cli",
                    "command": "printf ok",
                }],
            }),
            encoding="utf-8",
        )
        (state_dir / "config").mkdir()
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-e2e",
                "run_tag": "test-e2e",
                "refs": {"real_e2e_matrix": [matrix_ref]},
            }),
            encoding="utf-8",
        )
        local_client = TestClient(create_app(state_dir, project_root=project_root))

        r = local_client.post(
            "/api/actions/real.e2e.run",
            headers={"x-zf-web-token": "test-token"},
            json={"timeout_seconds": 5},
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "passed"
        assert data["passed"] is True
        assert Path(data["result_matrix_ref"]).exists()
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "real_e2e.run.completed" in types
        assert "web.action.completed" in types

    def test_run_contract_review_action_records_operator_review(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        (state_dir / "config").mkdir()
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-review",
                "run_tag": "review-test",
            }),
            encoding="utf-8",
        )
        local_client = TestClient(create_app(state_dir, project_root=state_dir.parent))

        r = local_client.post(
            "/api/actions/run.contract.review",
            headers={"x-zf-web-token": "test-token"},
            json={"decision": "reviewed", "reason": "operator checked"},
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "reviewed"
        assert data["contract_digest"] == "digest-review"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "run_contract.review.recorded" in types
        assert "web.action.completed" in types

    def test_attention_ack_action_records_lifecycle_event(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/attention.ack",
            headers={"x-zf-web-token": "test-token"},
            json={
                "attention_id": "attn-1",
                "fingerprint": "fp-1",
                "task_id": "TASK-1",
                "reason": "operator inspected",
            },
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "recorded"
        assert data["action"] == "attention-ack"
        assert data["requested_action"] == "attention.ack"
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "runtime.attention.acknowledged" in types
        assert "web.action.completed" in types
        attention_event = next(
            event for event in events
            if event.type == "runtime.attention.acknowledged"
        )
        assert attention_event.task_id == "TASK-1"
        assert attention_event.payload["attention_id"] == "attn-1"
        assert attention_event.payload["fingerprint"] == "fp-1"

    def test_chat_orchestrator_records_message_without_runtime_attach(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/chat-orchestrator",
            headers={"x-zf-web-token": "test-token"},
            json={"message": "summarize current plan"},
        )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "queued_no_runtime"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert types[-3:] == [
            "user.message",
            "runtime.action.completed",
            "web.action.completed",
        ]

    def test_projection_first_chat_emits_kanban_agent_reply(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-123", title="x", status="in_progress", assigned_to="dev-1"),
        )
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/chat-orchestrator",
            headers={"x-zf-web-token": "test-token"},
            json={
                "task_id": "TASK-123",
                "message": "why is this still in progress?",
                "mode": "projection_first",
            },
        )

        assert r.status_code == 202
        data = r.json()
        assert data["reply"]["mutates_task_state"] is False
        assert "TASK-123 is in_progress" in data["reply"]["answer"]
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "kanban.agent.reply" in types

    def test_agent_session_cancel_records_token_gated_event(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/agent-session-cancel",
            headers={"x-zf-web-token": "test-token"},
            json={
                "conversation_id": "kanban:default",
                "thread_id": "main",
                "run_id": "run-1",
                "backend": "codex-headless",
                "reason": "operator test cancel",
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "cancel_requested"
        events = EventLog(state_dir / "events.jsonl").read_all()
        cancelled = [event for event in events if event.type == "agent.session.run.cancelled"]
        assert len(cancelled) == 1
        assert cancelled[0].payload["run_id"] == "run-1"

    def test_kanban_agent_lifecycle_probe_creates_and_moves_task(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/chat-orchestrator",
            headers={"x-zf-web-token": "test-token"},
            json={
                "message": (
                    "创建一个测试任务: Kanban Agent lifecycle probe pytest. "
                    "要求先放到 backlog,然后移动到 in_progress,最后移动到 done."
                ),
                "mode": "projection_first",
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        task_id = data["task_id"]
        assert data["reply"]["mutates_task_state"] is True
        assert data["reply"]["status_sequence"] == ["backlog", "in_progress", "done"]
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        assert task is not None
        assert task.title == "Kanban Agent lifecycle probe pytest"
        assert task.status == "done"
        snapshot = local_client.get("/api/snapshot").json()
        assert all(item["id"] != task_id for item in snapshot["tasks"])
        assert any(item["id"] == task_id and item["status"] == "done" for item in snapshot["archive_tasks"])

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert types.count("task.created") == 1
        update_statuses = [
            event.payload["updates"]["status"]
            for event in events
            if event.type == "task.updated"
        ]
        assert update_statuses == ["in_progress", "done"]
        child_requests = [
            event for event in events
            if event.type == "web.action.requested"
            and event.actor == "kanban-agent"
        ]
        assert [event.payload["action"] for event in child_requests] == [
            "create-task",
            "update-task",
            "update-task",
        ]

    def test_create_task_action_uses_task_store_and_events(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/create-task",
            headers={"x-zf-web-token": "test-token"},
            json={"title": "Investigate fan-in blocker", "skills": ["debug"]},
        )

        assert r.status_code == 201
        data = r.json()
        task_id = data["task_id"]
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        assert task is not None
        assert task.title == "Investigate fan-in blocker"
        assert task.skills_required == ["debug"]
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "task.created" in types
        assert "runtime.action.completed" in types

    def test_update_task_action_can_move_status(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-MOVE", title="move me", status="backlog"),
        )
        local_client = TestClient(create_app(state_dir))

        moved = local_client.post(
            "/api/actions/update-task",
            headers={"x-zf-web-token": "test-token"},
            json={"task_id": "TASK-MOVE", "status": "in_progress"},
        )

        assert moved.status_code == 200
        assert moved.json()["status"] == "completed"
        task = TaskStore(state_dir / "kanban.json").get("TASK-MOVE")
        assert task is not None
        assert task.status == "in_progress"
        events = EventLog(state_dir / "events.jsonl").read_all()
        update_events = [event for event in events if event.type == "task.updated"]
        assert update_events
        assert update_events[-1].payload["updates"]["status"] == "in_progress"

    def test_passcode_unlock_authorizes_cookie_session(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_PASSCODE", "open-sesame")
        local_client = TestClient(create_app(state_dir))

        locked_runtime = local_client.get("/api/runtime").json()
        assert locked_runtime["web_session"]["mode"] == "remote_passcode"
        assert locked_runtime["web_session"]["unlocked"] is False
        assert locked_runtime["actions"]["mutation_enabled"] is True

        blocked = local_client.post(
            "/api/actions/create-task",
            json={"title": "blocked until unlock"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["status"] == "unauthorized"

        unlocked = local_client.post(
            "/api/web-session/unlock",
            json={"passcode": "open-sesame"},
        )
        assert unlocked.status_code == 200
        assert unlocked.json()["status"] == "unlocked"
        assert "session_token" not in unlocked.json()

        unlocked_runtime = local_client.get("/api/runtime").json()
        assert unlocked_runtime["web_session"]["unlocked"] is True
        assert unlocked_runtime["web_session"]["actions_enabled"] is True

        created = local_client.post(
            "/api/actions/create-task",
            json={"title": "created through passcode cookie"},
        )
        assert created.status_code == 201
        assert TaskStore(state_dir / "kanban.json").get(created.json()["task_id"]) is not None

    def test_passcode_unlock_rate_limits_failed_attempts(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_PASSCODE", "open-sesame")
        monkeypatch.setenv("ZF_WEB_PASSCODE_MAX_ATTEMPTS", "2")
        monkeypatch.setenv("ZF_WEB_PASSCODE_WINDOW_SECONDS", "60")
        local_client = TestClient(create_app(state_dir))

        first = local_client.post(
            "/api/web-session/unlock",
            json={"passcode": "wrong"},
        )
        second = local_client.post(
            "/api/web-session/unlock",
            json={"passcode": "wrong"},
        )
        third = local_client.post(
            "/api/web-session/unlock",
            json={"passcode": "wrong"},
        )

        assert first.status_code == 403
        assert second.status_code == 403
        assert third.status_code == 429
        assert third.json()["status"] == "rate_limited"

    def test_start_operator_session_projects_profile(
        self,
        state_dir,
        monkeypatch,
    ):
        import sys

        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        monkeypatch.setenv(
            "ZF_KANBAN_AGENT_CODEX_CMD",
            f"{sys.executable} -u -c \"import time; print('fake codex ready', flush=True); time.sleep(60)\"",
        )
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/start-operator-session",
            headers={"x-zf-web-token": "test-token"},
            json={"backend": "codex", "scope": "project"},
        )

        assert r.status_code == 202
        assert r.json()["status"] == "runtime_accepted"
        session = r.json()["result"]
        assert session["session_id"].startswith("kanban-agent:")
        assert ":project:codex" in session["session_id"]
        assert session["shared_context"]["project_root"] == str(state_dir.parent)
        assert session["shared_context"]["state_dir"] == str(state_dir)
        assert session["boundary"]["scheduler"] is False
        assert "update-task" in session["allowed_actions"]
        assert session["status_model"]["run_completed_implies_task_done"] is False
        runtime = local_client.get("/api/runtime").json()
        assert runtime["agent_surface"]["backend"] == "codex"
        assert runtime["agent_surface"]["session_id"] == session["session_id"]
        assert runtime["agent_surface"]["terminal_backed"] is True
        assert runtime["agent_surface"]["alive"] is True
        profile = Path(session["workdir"]) / "AGENTS.md"
        claude_profile = Path(session["workdir"]) / "CLAUDE.md"
        assert profile.exists()
        assert claude_profile.exists()
        assert "same project root and state dir" in profile.read_text(encoding="utf-8")
        assert "You are NOT a coding agent" in profile.read_text(encoding="utf-8")
        assert "operator/action requester" in profile.read_text(encoding="utf-8")
        assert "Run completion, operator exit, or backend completion is evidence only" in profile.read_text(encoding="utf-8")
        assert "`zf kanban list`" in profile.read_text(encoding="utf-8")
        assert claude_profile.read_text(encoding="utf-8") == profile.read_text(encoding="utf-8")
        assert (Path(session["workdir"]) / "STATE_DIR").read_text(encoding="utf-8").strip() == str(state_dir)
        shared_context = json.loads((Path(session["workdir"]) / "SHARED_CONTEXT.json").read_text(encoding="utf-8"))
        assert shared_context["boundary"]["scheduler"] is False
        assert "transcript_as_business_truth" in shared_context["forbidden_capabilities"]
        assert "Skills are shared context" in (Path(session["workdir"]) / "SKILLS.md").read_text(encoding="utf-8")
        local_client.post(
            "/api/operator/stop",
            headers={"x-zf-web-token": "test-token"},
            json={"reason": "test complete"},
        )

    def test_start_operator_session_keeps_project_descriptor_with_task_context(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-AGENT", title="agent task", status="backlog"),
        )
        local_client = TestClient(create_app(state_dir))

        r = local_client.post(
            "/api/actions/start-operator-session",
            headers={"x-zf-web-token": "test-token"},
            json={
                "backend": "deterministic",
                "scope": "task",
                "task_id": "TASK-AGENT",
                "force": True,
            },
        )

        assert r.status_code == 202
        session = r.json()["result"]
        assert ":project:deterministic" in session["session_id"]
        assert ":task:" not in session["session_id"]
        assert session["scope"] == "project"
        assert session["task_id"] == ""
        assert session["context_task_id"] == "TASK-AGENT"
        assert Path(session["workdir"]).exists()
        profile = Path(session["workdir"]) / "AGENTS.md"
        assert "Context task: `TASK-AGENT`" in profile.read_text(encoding="utf-8")
        runtime = local_client.get("/api/runtime").json()
        assert runtime["agent_surface"]["scope"] == "project"
        assert runtime["agent_surface"]["task_id"] == ""
        assert runtime["agent_surface"]["context_task_id"] == "TASK-AGENT"
        assert runtime["agent_surface"]["shared_context"]["shared_project_workdir"] == str(state_dir.parent)
        assert runtime["agent_surface"]["boundary"]["direct_role_dispatch"] is False
        local_client.post(
            "/api/operator/stop",
            headers={"x-zf-web-token": "test-token"},
            json={"reason": "test complete"},
        )

    def test_operator_terminal_starts_accepts_input_and_stops(
        self,
        state_dir,
        monkeypatch,
    ):
        import time

        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        started = local_client.post(
            "/api/actions/start-operator-session",
            headers={"x-zf-web-token": "test-token"},
            json={"backend": "deterministic", "scope": "project"},
        )

        assert started.status_code == 202
        assert started.json()["status"] == "runtime_accepted"
        session = local_client.get("/api/operator/session").json()
        assert session["alive"] is True

        text = ""
        for _ in range(20):
            output = local_client.get("/api/operator/output?cursor=0").json()
            text = "".join(chunk["text"] for chunk in output["chunks"])
            if "deterministic operator ready" in text:
                break
            time.sleep(0.05)
        assert "deterministic operator ready" in text
        assert "Forbidden: direct .zf writes" in text

        submitted = local_client.post(
            "/api/operator/input",
            headers={"x-zf-web-token": "test-token"},
            json={"text": "status please"},
        )

        assert submitted.status_code == 200
        assert submitted.json()["status"] == "submitted"
        for _ in range(20):
            output = local_client.get("/api/operator/output?cursor=0").json()
            text = "".join(chunk["text"] for chunk in output["chunks"])
            if "operator> status please" in text:
                break
            time.sleep(0.05)
        assert "operator> status please" in text

        stopped = local_client.post(
            "/api/operator/stop",
            headers={"x-zf-web-token": "test-token"},
            json={"reason": "test complete"},
        )

        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "operator.session.started" in types
        assert "operator.input.submitted" in types
        assert "operator.session.stopped" in types

    def test_operator_session_rebind_does_not_fanout_stopped_notice(
        self,
        state_dir,
        tmp_path,
    ):
        manager = OperatorSessionManager(state_dir=state_dir, project_root=tmp_path)
        first = manager.start(backend="deterministic", scope="project")
        assert first.ok

        fanout: list[str] = []
        detach = manager.attach_raw_output(
            lambda data: fanout.append(data.decode("utf-8", errors="replace"))
        )
        try:
            second = manager.start(
                backend="deterministic",
                scope="task",
                task_id="TASK-REBIND",
            )

            assert second.ok
            assert second.status == "rebound"
            assert second.session["scope"] == "project"
            assert second.session["task_id"] == ""
            assert second.session["context_task_id"] == "TASK-REBIND"
            assert ":project:deterministic" in second.session["session_id"]
            assert ":task:" not in second.session["session_id"]
            joined = "".join(fanout)
            assert "operator session stopped" not in joined

            manager.stop(reason="test complete")
            assert "operator session stopped: test complete" in "".join(fanout)
        finally:
            detach()
            manager.stop(reason="cleanup", announce=False)

    def test_operator_action_helper_invokes_controlled_web_action(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="TASK-ACTION", title="controlled action", status="backlog"),
        )
        local_client = TestClient(create_app(state_dir))

        submitted = local_client.post(
            "/api/operator/input",
            headers={"x-zf-web-token": "test-token"},
            json={
                "text": '/action update-task {"task_id":"TASK-ACTION","status":"in_progress"}',
            },
        )

        assert submitted.status_code == 200
        assert submitted.json()["status"] == "completed"
        task = TaskStore(state_dir / "kanban.json").get("TASK-ACTION")
        assert task is not None
        assert task.status == "in_progress"
        output = local_client.get("/api/operator/output?cursor=0").json()
        text = "".join(chunk["text"] for chunk in output["chunks"])
        assert "action update-task" in text
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert "operator.action.completed" in types

    def test_web_action_idempotency_replays_same_request(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        local_client = TestClient(create_app(state_dir))

        first = local_client.post(
            "/api/actions/chat-orchestrator",
            headers={
                "x-zf-web-token": "test-token",
                "x-idempotency-key": "same-key",
            },
            json={"message": "hello"},
        )
        second = local_client.post(
            "/api/actions/chat-orchestrator",
            headers={
                "x-zf-web-token": "test-token",
                "x-idempotency-key": "same-key",
            },
            json={"message": "hello"},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["idempotency"]["status"] == "replayed"
        types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
        assert types.count("user.message") == 1

    def test_request_fanout_validates_yaml_stage_and_projects_request(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        config = ZfConfig(
            workflow=WorkflowConfig(
                stages=[
                    WorkflowStageConfig(
                        id="review-candidate",
                        trigger="candidate.ready",
                        topology="fanout_reader",
                        roles=["review"],
                        target_ref="candidate/F-1",
                    ),
                ],
            )
        )
        local_client = TestClient(create_app(state_dir, config=config))

        r = local_client.post(
            "/api/actions/request-fanout",
            headers={"x-zf-web-token": "test-token"},
            json={
                "stage_id": "review-candidate",
                "target_ref": "candidate/F-1",
                "reason": "manual comparison",
            },
        )

        assert r.status_code == 202
        fanout_id = r.json()["fanout_id"]
        detail = local_client.get(f"/api/fanouts/{fanout_id}").json()
        assert detail["status"] == "requested"
        assert detail["stage_id"] == "review-candidate"
        assert detail["trigger"]["requested_by"] == "kanban"

    def test_kanban_agent_fanout_request_is_request_only_not_dispatch(
        self,
        state_dir,
        monkeypatch,
    ):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        config = ZfConfig(
            workflow=WorkflowConfig(
                stages=[
                    WorkflowStageConfig(
                        id="review-candidate",
                        trigger="candidate.ready",
                        topology="fanout_reader",
                        roles=["review"],
                        target_ref="candidate/F-2",
                    ),
                ],
            )
        )
        local_client = TestClient(create_app(state_dir, config=config))

        r = local_client.post(
            "/api/actions/request-fanout",
            headers={"x-zf-web-token": "test-token"},
            json={
                "stage_id": "review-candidate",
                "target_ref": "candidate/F-2",
                "reason": "kanban agent requested star review",
                "requested_by": "kanban-agent",
                "source": "kanban-agent",
                "task_id": "TASK-KA-FANOUT",
            },
        )

        assert r.status_code == 202
        assert r.json()["status"] == "requested"
        fanout_id = r.json()["fanout_id"]

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "fanout.requested" in types
        assert "fanout.started" not in types
        assert "fanout.child.dispatched" not in types
        requested = next(event for event in events if event.type == "fanout.requested")
        assert requested.task_id == "TASK-KA-FANOUT"
        assert requested.payload["fanout_id"] == fanout_id
        assert requested.payload["stage_id"] == "review-candidate"
        assert requested.payload["requested_by"] == "kanban-agent"
        assert requested.payload["runtime_delivery"] == "queued_no_runtime"

        detail = local_client.get(f"/api/fanouts/{fanout_id}").json()
        assert detail["status"] == "requested"
        assert detail["trigger"]["requested_by"] == "kanban-agent"
        runtime = local_client.get("/api/runtime").json()
        assert "request-fanout" in runtime["agent_surface"]["allowed_actions"]
        assert runtime["agent_surface"]["boundary"]["direct_role_dispatch"] is False


class TestApiViewsTasks:
    def test_returns_taskview_shape(self, state_dir, client):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress",
                 assigned_to="dev-1"),
        )
        r = client.get("/api/views/tasks")
        assert r.status_code == 200
        views = r.json()
        assert len(views) == 1
        v = views[0]
        # TaskView fields from feishu/views.py
        assert v["task_id"] == "T1"
        assert v["title"] == "x"
        assert v["status"] == "in_progress"
        assert v["assigned_to"] == "dev-1"


class TestApiRecent:
    def test_returns_event_payloads(self, state_dir, client):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="task.created", actor="zf-cli",
                           task_id="T1", payload={}))
        r = client.get("/api/views/recent")
        events = r.json()
        types = [e["type"] for e in events]
        assert "loop.started" in types
        assert "task.created" in types

    def test_limit_param(self, state_dir, client):
        log = EventLog(state_dir / "events.jsonl")
        for i in range(50):
            log.append(ZfEvent(type="agent.tool.use", actor="dev-1",
                               task_id=f"T{i}", payload={}))
        r = client.get("/api/views/recent?limit=5")
        events = r.json()
        assert len(events) == 5

    def test_recent_events_redact_obvious_secrets(self, state_dir, client):
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(
                type="agent.tool.result",
                actor="dev",
                payload={"output": "TOKEN=secret-value"},
            )
        )

        r = client.get("/api/views/recent")

        text = r.text
        assert "secret-value" not in text
        assert "[REDACTED_SECRET]" in text


class TestApiProgress:
    def test_progress_md_returned(self, state_dir, client):
        (state_dir / "progress.md").write_text("# progress\nhello\n")
        r = client.get("/api/progress")
        assert r.status_code == 200
        assert "# progress" in r.text

    def test_missing_progress_returns_empty(self, client):
        r = client.get("/api/progress")
        assert r.status_code == 200
        assert r.text == ""


class TestApiInstructions:
    def test_role_md_returned(self, state_dir, client):
        instr = state_dir / "instructions"
        instr.mkdir()
        (instr / "dev.md").write_text("dev role md")
        r = client.get("/api/instructions/dev")
        assert r.status_code == 200
        assert "dev role md" in r.text

    def test_missing_role_404(self, state_dir, client):
        (state_dir / "instructions").mkdir()
        r = client.get("/api/instructions/nonexistent")
        assert r.status_code == 404

    def test_path_traversal_blocked(self, state_dir, client):
        (state_dir / "instructions").mkdir()
        r = client.get("/api/instructions/..%2Fkanban")
        # Even if URL decodes to something funky, our defensive check
        # rejects '/' in role names → 400 (or fastapi's 404 for
        # missing route)
        assert r.status_code in (400, 404)


class TestApiBriefings:
    def test_briefing_returned(self, state_dir, client):
        b = state_dir / "briefings"
        b.mkdir()
        (b / "dev-1-TASK-A.md").write_text("briefing A")
        r = client.get("/api/briefings/dev-1-TASK-A.md")
        assert r.status_code == 200
        assert "briefing A" in r.text

    def test_missing_briefing_404(self, state_dir, client):
        (state_dir / "briefings").mkdir()
        r = client.get("/api/briefings/nope.md")
        assert r.status_code == 404


class TestApiCost:
    def test_no_cost_jsonl_returns_zero(self, client):
        r = client.get("/api/cost")
        assert r.status_code == 200
        data = r.json()
        assert data["total_usd"] == 0.0
        assert data["per_role"] == {}

    def test_nonempty_cost_jsonl_returns_projection(self, state_dir, client):
        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage(
            role="dev",
            input_tokens=1_000_000,
            output_tokens=500_000,
            model="default",
        )

        r = client.get("/api/cost")

        assert r.status_code == 200
        data = r.json()
        assert data["total_usd"] > 0
        assert data["per_role"]["dev"]["usd"] > 0
        assert data["per_role"]["dev"]["input_tokens"] == 1_000_000
        assert data["per_role"]["dev"]["output_tokens"] == 500_000
        assert data["per_role"]["dev"]["entries"] == 1

    def test_state_embeds_nonempty_cost_projection(self, state_dir, client):
        CostTracker(state_dir / "cost.jsonl").record_usage(
            role="review",
            input_tokens=500_000,
            output_tokens=100_000,
            model="default",
        )

        r = client.get("/api/state")

        cost = r.json()["cost"]
        assert cost["total_usd"] > 0
        assert cost["per_role"]["review"]["usd"] > 0


class TestApiWorkers:
    def test_returns_workers_from_role_sessions(self, state_dir, client):
        import yaml
        path = state_dir / "role_sessions.yaml"
        path.write_text(yaml.safe_dump({
            "project_root": str(state_dir.parent),
            "instance_meta": {
                "dev-1": {
                    "backend": "claude-code",
                    "spawned_at": "2026-04-27T00:00:00",
                },
                "dev-2": {
                    "backend": "codex",
                    "spawned_at": "2026-04-27T00:00:01",
                },
            },
            "roles": {},
        }))
        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="worker.state.changed", actor="dev-1",
                    payload={"from": "idle", "to": "busy"}),
        )
        r = client.get("/api/state")
        workers = r.json()["workers"]
        assert len(workers) == 2
        by_id = {w["instance_id"]: w for w in workers}
        assert by_id["dev-1"]["backend"] == "claude-code"
        assert by_id["dev-1"]["state"] == "busy"
        assert by_id["dev-2"]["state"] == "unknown"


# --- F5: /api/snapshot cache regression (doc 65 §20.4 / F5 backlog) -----------
# Authored 2026-05-29 against the CURRENT behavior (global /api/snapshot caches
# under snapshot_lock with a TTL; project_snapshot is NOT cached anymore — the
# F5 backlog's older premise was stale, see its §0). The whole module is
# importorskip("fastapi")-guarded, so these skip where fastapi is absent and run
# under `.[web]`. They are deterministic (no threads): they assert the cache
# coalesces recompute, which is the regression net the snapshot path lacked.
class TestSnapshotCacheRegression:
    def test_snapshot_cache_hit_does_not_recompute(self, state_dir, client, monkeypatch):
        """Second call within TTL is served from cache — _snapshot runs once."""
        monkeypatch.setenv("ZF_WEB_SNAPSHOT_CACHE_SECONDS", "300")
        calls = {"n": 0}

        def counting(*args, **kwargs):
            calls["n"] += 1
            return {"computed_call": calls["n"]}

        monkeypatch.setattr("zf.web.server._snapshot", counting)

        a = client.get("/api/snapshot")
        b = client.get("/api/snapshot")
        assert a.status_code == 200 and b.status_code == 200
        assert calls["n"] == 1  # 2nd request hit the cache, did not recompute
        assert a.json() == b.json()

    def test_snapshot_cache_disabled_when_ttl_zero(self, state_dir, client, monkeypatch):
        """TTL=0 disables caching — each request recomputes (no stale serve)."""
        monkeypatch.setenv("ZF_WEB_SNAPSHOT_CACHE_SECONDS", "0")
        calls = {"n": 0}

        def counting(*args, **kwargs):
            calls["n"] += 1
            return {"computed_call": calls["n"]}

        monkeypatch.setattr("zf.web.server._snapshot", counting)

        client.get("/api/snapshot")
        client.get("/api/snapshot")
        assert calls["n"] == 2  # no caching → recomputed each time


class TestPlanApprovalWebAction:
    """B-93-01 (doc 93 §7.1): plan-approve/plan-reject 必须过 web allowlist。

    回归点(cd-2 复核 #1):后端 ControlledActionService 早已认这俩 action,
    但 _ALLOWED_WEB_ACTIONS 缺它们 → /api/actions/plan-approve 直接 404,
    B15 Web 审批不可达。本测试钉死入口可达 + 落 operator 事件。
    """

    def test_plan_approve_passes_allowlist_and_records(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        c = TestClient(create_app(state_dir))
        r = c.post(
            "/api/actions/plan-approve",
            headers={"x-zf-web-token": "test-token"},
            json={"plan_id": "evt-plan-1"},
        )
        assert r.status_code != 404, "allowlist 不应再挡 plan-approve(回归)"
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "plan-approve" and body["plan_id"] == "evt-plan-1"
        events = EventLog(state_dir / "events.jsonl").read_all()
        approved = [e for e in events if e.type == "plan.approved"]
        assert len(approved) == 1 and approved[0].actor == "operator"

    def test_plan_reject_requires_reason_then_records(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        c = TestClient(create_app(state_dir))
        # 缺 reason → 400(过了 allowlist,被业务校验拦,证明已可达)
        r0 = c.post(
            "/api/actions/plan-reject",
            headers={"x-zf-web-token": "test-token"},
            json={"plan_id": "evt-plan-1"},
        )
        assert r0.status_code == 400
        # 带 reason → 200 + plan.rejected
        r = c.post(
            "/api/actions/plan-reject",
            headers={"x-zf-web-token": "test-token"},
            json={"plan_id": "evt-plan-1", "reason": "缺 root owner,回 synth 重拆"},
        )
        assert r.status_code == 200
        events = EventLog(state_dir / "events.jsonl").read_all()
        rejected = [e for e in events if e.type == "plan.rejected"]
        assert len(rejected) == 1 and rejected[0].payload.get("reason")

    def test_plan_approve_canonicalizes_dotted_alias(self, state_dir, monkeypatch):
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
        c = TestClient(create_app(state_dir))
        r = c.post(
            "/api/actions/plan.approve",
            headers={"x-zf-web-token": "test-token"},
            json={"plan_id": "evt-plan-2"},
        )
        assert r.status_code == 200 and r.json()["action"] == "plan-approve"


class TestWorkflowSpineEndpoint:
    """131-P0-5:shadow spine 四投影的只读 Web 解释端点。"""

    def test_workflow_spine_serves_projections(self, state_dir: Path, tmp_path: Path):
        from zf.core.workspace import stable_project_id as _spid
        from zf.runtime.workflow_spine_projection import refresh_spine_projections

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="refactor.scan.ready", payload={"pdd_id": "PDD-W"}))
        log.append(ZfEvent(type="verify.failed", payload={"pdd_id": "PDD-W"}))
        log.append(ZfEvent(type="task.dispatched", task_id="T-W",
                           payload={"role": "dev-1"}))
        refresh_spine_projections(state_dir, log)

        local_client = TestClient(create_app(state_dir, project_root=tmp_path))
        project_id = _spid(name=tmp_path.name, root=tmp_path)
        data = local_client.get(f"/api/projects/{project_id}/workflow-spine").json()

        assert data["runs"]["PDD-W"]["milestones"] == 2
        assert data["runs"]["PDD-W"]["attention"] is True
        assert data["tasks"]["T-W"]["attempt_count"] == 1
        assert "counters" in data["health"]
