"""Independent stale-contract closure proof for design 149."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import WorkflowAffinityLaneConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    effective_contract_revision,
    write_target_snapshot,
)


TASK_ID = "STALE-CONTRACT-001"
SENTINEL_AC = "AC-R2-UNIQUE-SENTINEL"


class _Transport:
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


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


def _latest(log: EventLog, event_type: str) -> ZfEvent:
    return next(event for event in reversed(log.read_all()) if event.type == event_type)


def _stage_manifest(state_dir: Path, log: EventLog, stage_id: str) -> dict:
    started = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == stage_id
    )
    return _manifest(state_dir, str(started.payload["fanout_id"]))


def _record_required_reads(state_dir: Path, payload: dict) -> None:
    manifest = hydrate_sidecar_ref(
        state_dir,
        payload["attempt_source_manifest"],
    ).payload
    for required in payload.get("required_reads") or []:
        read_attempt_artifact(
            state_dir,
            manifest=manifest,
            source_id=required["source_id"],
            artifact_id=required["artifact_id"],
            json_path=required["json_path"],
            max_items=int(required.get("max_items") or 0),
            max_chars=int(required.get("max_chars") or 0),
        )


def _dispatch_payload(child: dict) -> dict:
    payload = dict(child.get("payload") or {})
    payload.update({key: value for key, value in child.items() if key != "payload"})
    return payload


def _task_map(*, revision: str, lane_id: str, sentinel: bool) -> dict:
    content = "r2-sentinel" if sentinel else "r1-obsolete"
    acceptance = [{
        "id": SENTINEL_AC if sentinel else "AC-R1-OBSOLETE",
        "text": f"result.txt contains {content}",
        "verification_owner": "task_verify",
        "verification_tier": "task_non_smoke",
        "verification_command_ids": [f"result-{revision.lower()}"],
    }]
    return {
        "schema_version": "task-map.v1",
        "feature_id": "STALE-CONTRACT",
        "tasks": [{
            "task_id": TASK_ID,
            "title": f"stale contract proof {revision}",
            "scope": ["result.txt"],
            "allowed_paths": ["result.txt"],
            "allowed_paths_reason": "isolated stale-contract proof",
            "owner_role": "dev",
            "affinity_tag": lane_id,
            "acceptance_criteria": acceptance,
            "verification": f"grep -qx {content} result.txt",
            "validation": {"commands": [{
                "id": f"result-{revision.lower()}",
                "command": f"grep -qx {content} result.txt",
                "acceptance_ids": [acceptance[0]["id"]],
                "owner": "impl_self_check",
                "tier": "task_non_smoke",
                "deterministic": True,
                "reusable": True,
                "timeout_seconds": 30,
            }]},
        }],
    }


def _impl_completion(
    child: dict,
    *,
    contract: dict,
    source_commit: str,
    evidence_prefix: str,
) -> ZfEvent:
    command = contract["verification_commands"][0]
    criterion = contract["acceptance_criteria"][0]
    receipt_id = f"receipt-{evidence_prefix}"
    return ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "fanout_id": child["fanout_id"],
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "attempt_id": child["run_id"],
            "dispatch_id": child["run_id"],
            "pdd_id": "STALE-CONTRACT",
            "source_commit": source_commit,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
            "files_touched": ["result.txt"],
            "contract_revision": contract["contract_revision"],
            "task_map_generation": contract["task_map_generation"],
            "contract_snapshot_ref": child["contract_snapshot_ref"],
            "contract_snapshot_digest": child["contract_snapshot_digest"],
            "impl_self_check": {
                "schema_version": "impl-self-check.v1",
                "workflow_run_id": contract["workflow_run_id"],
                "task_id": TASK_ID,
                "attempt_id": child["run_id"],
                "contract_revision": contract["contract_revision"],
                "task_map_generation": contract["task_map_generation"],
                "source_commit": source_commit,
                "target_commit": source_commit,
                "contract_snapshot_ref": child["contract_snapshot_ref"],
                "contract_snapshot_digest": child["contract_snapshot_digest"],
                "command_receipts": [{
                    "receipt_id": receipt_id,
                    "command_id": command["command_id"],
                    "command_digest": command["command_digest"],
                    "target_commit": source_commit,
                    "status": "passed",
                    "exit_code": 0,
                    "evidence_refs": [f"mock://{evidence_prefix}/command"],
                }],
                "acceptance_results": [{
                    "acceptance_id": criterion["acceptance_id"],
                    "status": "passed",
                    "command_receipt_ids": [receipt_id],
                    "evidence_refs": [f"mock://{evidence_prefix}/acceptance"],
                    "residual_risks": [],
                }],
                "evidence_refs": [f"mock://{evidence_prefix}/impl"],
                "residual_risks": [],
            },
        },
    )


def _verification_result(payload: dict, contract: dict, *, marker: str) -> dict:
    return {
        "schema_version": "verification-result.v1",
        **{
            key: payload[key]
            for key in (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "target_snapshot_ref",
                "target_commit",
                "target_snapshot_digest",
            )
        },
        "execution_status": "completed",
        "verdict": "passed",
        "verification_owner": "task_verify",
        "verification_tier": "task_non_smoke",
        "summary": marker,
        "evidence_refs": [f"mock://{marker}"],
        "reused_command_receipt_ids": [],
        "probe_receipts": [{
            "probe_id": f"probe-{marker}",
            "status": "passed",
            "evidence_refs": [f"mock://{marker}/probe"],
        }],
        "rework_items": [],
        "requirement_results": [{
            "acceptance_id": item["acceptance_id"],
            "status": "passed",
            "verification_owner": item["verification_owner"],
            "verification_tier": item["verification_tier"],
            "evidence_refs": [f"mock://{marker}/acceptance"],
            "findings": [],
            "reproduction_commands": [],
        } for item in contract["acceptance_criteria"]],
    }


def test_stale_contract_results_cannot_advance_current_generation(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "stale-contract@example.com")
    _git(tmp_path, "config", "user.name", "ZF Stale Contract E2E")
    (tmp_path / "README.md").write_text("stale contract proof\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".zf-stale-contract/\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "branch", "-M", "main")

    config = load_config(
        Path(__file__).resolve().parents[2]
        / "examples" / "prod" / "controller" / "prd-light-v3.yaml"
    )
    state_dir = tmp_path / ".zf-stale-contract"
    config.project.root = str(tmp_path)
    config.project.state_dir = str(state_dir)
    config.runtime.git.candidate_base_ref = "main"
    config.runtime.git.ship_target_branch = "main"
    config.runtime.git.auto_ship_on_judge_passed = False
    config.orchestrator.backend = "mock"
    for role in config.roles:
        role.backend = "mock"
    dev_lane_0 = next(role for role in config.roles if role.instance_id == "dev-lane-0")
    verify_lane_0 = next(role for role in config.roles if role.instance_id == "verify-lane-0")
    dev_lane_1 = deepcopy(dev_lane_0)
    dev_lane_1.name = dev_lane_1.instance_id = "dev-lane-1"
    verify_lane_1 = deepcopy(verify_lane_0)
    verify_lane_1.name = verify_lane_1.instance_id = "verify-lane-1"
    config.roles.extend((dev_lane_1, verify_lane_1))
    config.workflow.affinity_lanes["prd-lanes-slot"].lanes.append(
        WorkflowAffinityLaneConfig(
            id="lane1",
            impl="dev-lane-1",
            verify="verify-lane-1",
        )
    )
    for stage in config.workflow.stages:
        if stage.id == "prd-lanes-impl":
            stage.roles.append("dev-lane-1")
        elif stage.id == "prd-lanes-verify":
            stage.roles.append("verify-lane-1")

    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    judge_credential = provision_role_submit_credential(state_dir, "judge-prd")
    log = EventLog(state_dir / "events.jsonl")
    orchestrator = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    log.append(ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id="run-stale-contract",
        payload={
            "run_id": "run-stale-contract",
            "goal_id": "STALE-CONTRACT",
            "objective": "prove stale contracts cannot advance",
        },
    ))

    refs: dict[str, str] = {}
    for revision, lane_id, sentinel in (
        ("R1", "lane0", False),
        ("R2", "lane1", True),
    ):
        ref = f"{state_dir.name}/artifacts/STALE-CONTRACT/task-map-{revision}.json"
        path = tmp_path / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_task_map(revision=revision, lane_id=lane_id, sentinel=sentinel)),
            encoding="utf-8",
        )
        refs[revision] = ref

    r1_trigger = ZfEvent(
        type="task_map.ready",
        actor="orchestrator",
        correlation_id="run-stale-contract",
        payload={
            "pdd_id": "STALE-CONTRACT",
            "feature_id": "STALE-CONTRACT",
            "task_map_ref": refs["R1"],
            "flow_kind": "prd",
        },
    )
    log.append(r1_trigger)
    orchestrator.run_once(events=[r1_trigger])
    r1_manifest = _stage_manifest(state_dir, log, "prd-lanes-impl")
    r1_child = next(child for child in r1_manifest["children"] if child["task_id"] == TASK_ID)
    r1_child["fanout_id"] = r1_manifest["fanout_id"]
    r1_contract = json.loads(
        (state_dir / r1_child["contract_snapshot_ref"]).read_text(encoding="utf-8")
    )

    store = TaskStore(state_dir / "kanban.json")
    current_task = store.get(TASK_ID)
    assert current_task is not None
    r2_contract_authority = deepcopy(current_task.contract)
    r2_contract_authority.contract_revision = ""
    r2_contract_authority.behavior = "implement the R2 sentinel only"
    r2_contract_authority.acceptance_criteria = _task_map(
        revision="R2", lane_id="lane1", sentinel=True,
    )["tasks"][0]["acceptance_criteria"]
    r2_contract_authority.verification = "grep -qx r2-sentinel result.txt"
    r2_contract_authority.validation = _task_map(
        revision="R2", lane_id="lane1", sentinel=True,
    )["tasks"][0]["validation"]
    r2_contract_authority.evidence_contract = deepcopy(
        r2_contract_authority.evidence_contract,
    )
    source_refs = dict(r2_contract_authority.evidence_contract.get("source_refs") or {})
    source_refs.update({
        "task_map_ref": refs["R2"],
        "plan_ref": refs["R2"],
        "task_map_generation": "G2",
    })
    r2_contract_authority.evidence_contract["source_refs"] = source_refs
    r2_contract_authority.plan_ref = refs["R2"]
    updated_r2 = store.update(
        TASK_ID,
        status="backlog",
        assigned_to="dev-lane-1",
        active_dispatch_id="",
        contract=r2_contract_authority,
    )
    assert updated_r2 is not None
    r2_revision = effective_contract_revision(updated_r2)
    contract_update = EventWriter(log).append(ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "contract_revision": r2_revision,
            "task_map_generation": "G2",
            "task_map_ref": refs["R2"],
            "reason": "stale-contract E2E replan",
        },
    ))
    # Replan supersedes the old dispatch lease while the provider may still
    # return later. The late result remains useful audit input, not current work.
    EventWriter(log).append(ZfEvent(
        type="fanout.child.dispatch_lost",
        actor="orchestrator",
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "fanout_id": r1_manifest["fanout_id"],
            "stage_id": "prd-lanes-impl",
            "child_id": r1_child["child_id"],
            "run_id": r1_child["run_id"],
            "role_instance": r1_child["role_instance"],
            "task_id": TASK_ID,
            "reason": "contract revision superseded by R2/G2",
        },
    ))
    orchestrator._set_worker_state(  # type: ignore[attr-defined]
        r1_child["role_instance"],
        "idle",
        task_id=TASK_ID,
        reason="R1/G1 dispatch superseded",
        force=True,
    )
    r2_trigger = ZfEvent(
        type="task_map.ready",
        actor="orchestrator",
        correlation_id="run-stale-contract",
        payload={
            "pdd_id": "STALE-CONTRACT",
            "feature_id": "STALE-CONTRACT",
            "task_map_ref": refs["R2"],
            "flow_kind": "prd",
            "rework_of": contract_update.id,
            "task_map_generation": "G2",
        },
    )
    log.append(r2_trigger)
    orchestrator.run_once(events=[r2_trigger])
    r2_manifest = _stage_manifest(state_dir, log, "prd-lanes-impl")
    assert r2_manifest["fanout_id"] != r1_manifest["fanout_id"]
    r2_child = next(child for child in r2_manifest["children"] if child["task_id"] == TASK_ID)
    r2_child["fanout_id"] = r2_manifest["fanout_id"]
    r2_payload = _dispatch_payload(r2_child)
    r2_contract = json.loads(
        (state_dir / r2_child["contract_snapshot_ref"]).read_text(encoding="utf-8")
    )
    assert r2_contract["contract_revision"] == r2_revision
    assert r2_contract["task_map_generation"] == "G2"
    assert [item["acceptance_id"] for item in r2_contract["acceptance_criteria"]] == [
        SENTINEL_AC,
    ]
    assert {item["source_id"] for item in r2_payload["required_reads"]} == {
        "contract",
    }

    r1_workdir = Path(r1_child["workdir"])
    (r1_workdir / "result.txt").write_text("r1-obsolete\n", encoding="utf-8")
    _git(r1_workdir, "add", "result.txt")
    _git(r1_workdir, "commit", "-q", "-m", "feat: obsolete R1 result")
    r1_commit = _git(r1_workdir, "rev-parse", "HEAD")
    late_r1 = _impl_completion(
        r1_child,
        contract=r1_contract,
        source_commit=r1_commit,
        evidence_prefix="r1-late",
    )
    log.append(late_r1)
    orchestrator.run_once(events=[late_r1])
    events = log.read_all()
    assert any(
        event.type == "task.ref.rejected"
        and event.payload.get("trigger_event_id") == late_r1.id
        and "stale task contract" in str(event.payload.get("reason") or "")
        for event in events
    )
    assert any(
        event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == late_r1.id
        for event in events
    )
    assert not any(
        event.type == "fanout.child.completed"
        and event.payload.get("result_event_id") == late_r1.id
        for event in events
    )

    _record_required_reads(state_dir, r2_payload)
    r2_workdir = Path(r2_child["workdir"])
    (r2_workdir / "result.txt").write_text("r2-sentinel\n", encoding="utf-8")
    _git(r2_workdir, "add", "result.txt")
    _git(r2_workdir, "commit", "-q", "-m", "feat: deliver R2 sentinel")
    r2_commit = _git(r2_workdir, "rev-parse", "HEAD")
    valid_r2_impl = _impl_completion(
        r2_child,
        contract=r2_contract,
        source_commit=r2_commit,
        evidence_prefix="r2-current",
    )
    log.append(valid_r2_impl)
    orchestrator.run_once(events=[valid_r2_impl])

    candidate = _latest(log, "candidate.ready")
    assert candidate.payload["candidate_head_commit"] != r1_commit
    orchestrator.run_once(events=[candidate])
    verify_manifest = _stage_manifest(state_dir, log, "prd-lanes-verify")
    verify_child = next(
        child for child in verify_manifest["children"] if child["task_id"] == TASK_ID
    )
    verify_payload = _dispatch_payload(verify_child)
    assert {item["source_id"] for item in verify_payload["required_reads"]} == {
        "contract",
        "target",
        "impl-self-check",
    }
    assert verify_payload["target_commit"] == candidate.payload["candidate_head_commit"]

    r1_target_descriptor = write_target_snapshot(
        state_dir,
        build_target_snapshot(
            {
                "ref": r1_child["contract_snapshot_ref"],
                "sha256": r1_child["contract_snapshot_digest"],
            },
            target_commit=r1_commit,
            contract_snapshot=r1_contract,
        ),
        source_event_id=late_r1.id,
    )
    stale_verify_payload = {
        **{
            key: r1_contract[key]
            for key in (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
            )
        },
        "contract_snapshot_ref": r1_child["contract_snapshot_ref"],
        "contract_snapshot_digest": r1_child["contract_snapshot_digest"],
        "target_snapshot_ref": r1_target_descriptor["ref"],
        "target_snapshot_digest": r1_target_descriptor["sha256"],
        "target_commit": r1_commit,
    }
    stale_verify = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "fanout_id": verify_manifest["fanout_id"],
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "status": "completed",
            **stale_verify_payload,
            "report": {
                "status": "passed",
                "summary": "obsolete R1 verify",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["mock://r1-stale-verify"],
            },
            "verification_result": _verification_result(
                stale_verify_payload,
                r1_contract,
                marker="r1-stale-verify",
            ),
        },
    )
    log.append(stale_verify)
    orchestrator.run_once(events=[stale_verify])
    stale_invalid = [
        event for event in log.read_all()
        if event.type == "workflow.call.result.invalid"
        and event.payload.get("reason") == "stale_call_result_superseded"
    ]
    assert len(stale_invalid) == 1
    assert {issue["code"] for issue in stale_invalid[0].payload["issues"]} & {
        "stale_contract_revision",
        "stale_task_map_generation",
        "stale_contract_snapshot",
        "stale_target_commit",
    }
    assert next(
        child for child in _manifest(
            state_dir, verify_manifest["fanout_id"],
        )["children"] if child["child_id"] == verify_child["child_id"]
    )["status"] == "dispatched"

    r2_prior_target_descriptor = write_target_snapshot(
        state_dir,
        build_target_snapshot(
            {
                "ref": r2_child["contract_snapshot_ref"],
                "sha256": r2_child["contract_snapshot_digest"],
            },
            target_commit=r2_commit,
            contract_snapshot=r2_contract,
        ),
        source_event_id=valid_r2_impl.id,
    )
    stale_target_payload = {
        **{
            key: r2_contract[key]
            for key in (
                "workflow_run_id",
                "task_id",
                "contract_revision",
                "task_map_generation",
                "base_commit",
                "task_ref",
            )
        },
        "contract_snapshot_ref": r2_child["contract_snapshot_ref"],
        "contract_snapshot_digest": r2_child["contract_snapshot_digest"],
        "target_snapshot_ref": r2_prior_target_descriptor["ref"],
        "target_snapshot_digest": r2_prior_target_descriptor["sha256"],
        "target_commit": r2_commit,
    }
    stale_target_verify = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "fanout_id": verify_manifest["fanout_id"],
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "status": "completed",
            **stale_target_payload,
            "report": {
                "status": "passed",
                "summary": "current contract but obsolete target",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["mock://r2-obsolete-target"],
            },
            "verification_result": _verification_result(
                stale_target_payload,
                r2_contract,
                marker="r2-obsolete-target",
            ),
        },
    )
    log.append(stale_target_verify)
    orchestrator.run_once(events=[stale_target_verify])
    stale_invalid = [
        event for event in log.read_all()
        if event.type == "workflow.call.result.invalid"
        and event.payload.get("reason") == "stale_call_result_superseded"
    ]
    assert len(stale_invalid) == 2
    assert "stale_target_commit" in {
        issue["code"] for issue in stale_invalid[-1].payload["issues"]
    }
    assert next(
        child for child in _manifest(
            state_dir, verify_manifest["fanout_id"],
        )["children"] if child["child_id"] == verify_child["child_id"]
    )["status"] == "dispatched"

    current_verification = _verification_result(
        verify_payload,
        r2_contract,
        marker="r2-current-verify",
    )
    unread_verify = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            "fanout_id": verify_manifest["fanout_id"],
            "child_id": verify_child["child_id"],
            "run_id": verify_child["run_id"],
            "status": "completed",
            "evidence_refs": ["mock://r2-current-verify"],
            "report": {
                "status": "passed",
                "summary": "current R2 verify before required reads",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["mock://r2-current-verify"],
            },
            "verification_result": current_verification,
        },
    )
    log.append(unread_verify)
    orchestrator.run_once(events=[unread_verify])
    repairs = [
        event for event in log.read_all()
        if event.type == "workflow.call.result.repair.requested"
        and event.payload.get("semantic_attempt_incremented") is False
    ]
    assert len(repairs) == 1
    repair_codes = {issue["code"] for issue in repairs[0].payload["issues"]}
    assert "required_read_missing" in repair_codes
    assert not repair_codes & {
        "stale_contract_revision",
        "stale_task_map_generation",
        "stale_target_commit",
    }
    assert not [
        event for event in log.read_all()
        if event.type in {"task.rework.requested", "candidate.rework.requested"}
    ]

    _record_required_reads(state_dir, verify_payload)
    valid_verify = ZfEvent(
        type="verify.child.completed",
        actor=verify_child["role_instance"],
        task_id=TASK_ID,
        correlation_id="run-stale-contract",
        payload={
            **unread_verify.payload,
            "report": {
                **unread_verify.payload["report"],
                "summary": "current R2 verify after required reads",
            },
        },
    )
    log.append(valid_verify)
    orchestrator.run_once(events=[valid_verify])

    test_passed = _latest(log, "test.passed")
    orchestrator.run_once(events=[test_passed])
    goal_closed = _latest(log, "flow.goal.closed")
    orchestrator.run_once(events=[goal_closed])
    judge_manifest = _stage_manifest(state_dir, log, "prd-lanes-final")
    judge_child = next(
        child for child in judge_manifest["children"] if child["status"] == "dispatched"
    )
    judge_payload = _dispatch_payload(judge_child)
    assert judge_payload["required_reads"]
    _record_required_reads(state_dir, judge_payload)
    judge_briefing = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == judge_manifest["fanout_id"]
    ).payload["briefing_path"]
    text = Path(judge_briefing).read_text(encoding="utf-8")
    command = text.split("Success command:\n```bash\n", 1)[1].split("\n```", 1)[0]
    semantic_result = json.loads(
        command[command.index("{"):].rsplit("\nZF_RESULT", 1)[0]
    )
    submitted = SemanticResultSubmitService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    ).submit(
        operation_id=judge_payload["operation_id"],
        semantic_result=semantic_result,
        role_instance="judge-prd",
        credential=judge_credential.read_text().strip(),
    )
    judged = next(event for event in log.read_all() if event.id == submitted.canonical_event_id)
    orchestrator.run_once(events=[judged])
    orchestrator.run_once(events=[_latest(log, "goal.closure.synthesized")])
    orchestrator.run_once(events=[_latest(log, "run.delivery.settled")])

    events = log.read_all()
    terminals = [event for event in events if event.type == "run.goal.completed"]
    assert len(terminals) == 1
    assert terminals[0].payload["verified_target_commit"] == verify_payload["target_commit"]
    assert not [event for event in events if event.type in {"verify.failed", "judge.failed"}]
    assert not [
        event for event in events
        if event.type in {"task.rework.requested", "candidate.rework.requested"}
    ]
