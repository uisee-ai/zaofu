"""Native sample collection and scoped frozen milestone projections."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from zf.core.events import ZfEvent
from zf.runtime.backend_session_reader import CodexSessionReader
from zf.runtime.milestone_task_lifecycles import task_lifecycle_evidence


def stamp(second):
    return (datetime(2026, 11, 23, tzinfo=timezone.utc) + timedelta(seconds=second)).isoformat()


def rollout(state, role, sid, tid, cwd, start, end, *, transform=lambda rows: rows):
    path = state / "workdirs" / role / "codex-home/sessions/2026/11/23" / f"rollout-2026-11-23T00-00-01-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("session_meta", 1, {"id": sid, "cwd": cwd}),
        ("event_msg", start, {"type": "task_started", "turn_id": tid}),
        ("turn_context", start, {"turn_id": tid, "cwd": cwd, "model": "vendor/native-2"}),
        ("response_item", start, {"type":"message", "text":"private transcript text"}),
        ("event_msg", end, {"type": "token_count", "info": {
            "model_context_window": 300, "last_token_usage": {"input_tokens": 22, "output_tokens": 3}}}),
        ("event_msg", end, {"type": "task_complete", "turn_id": tid}),
    ]
    path.write_text("".join(json.dumps({"type": k, "timestamp": stamp(s), "payload": p})+"\n"
                            for k,s,p in transform(rows)), encoding="utf-8")
    return path


def read(state, role, session, path):
    return CodexSessionReader().read_latest_usage(path, registered_rollout=(state, role, session))


def dispatch_input(state, task, stage, start=3, end=12, run="run", generation="gen", package="pkg"):
    sid, tid = str(uuid4()), str(uuid4())
    role, cwd = "lane-" + task, str(state / "private-workspaces" / task)
    path = rollout(state, role, sid, tid, cwd, start, end)
    sample = read(state, role, sid, path)
    key = {"workflow_run_id": run, "task_id": task, "operation_id": "op-" + tid,
           "attempt_id": "attempt-" + tid, "dispatch_id": "dispatch-" + tid, "lease_id": "lease-" + tid}
    d = ZfEvent(type="task.pipeline.stage.dispatched", actor="orchestrator", origin="kernel", task_id=task, ts=stamp(start-2),
                payload={**key, "request_hash": tid, "stage_id": stage, "task_pipeline_stage": stage,
                         "task_pipeline_entry_mode": "standard", "workdir": cwd, "role_instance": role,
                         "workspace_generation": 1, "placement_epoch": 1,
                         "task_map_generation": generation, "plan_artifact_package_digest": package})
    h = ZfEvent(type="codex.hook.user_prompt_submit", actor=role, origin="kernel", ts=stamp(start+1), causation_id=d.id,
                payload={"cwd": cwd, "session_id": sid, "turn_id": tid})
    u = ZfEvent(type="agent.usage", actor=role, origin="kernel", task_id=task, ts=stamp(end+1),
                payload={**key, "usage_sample_id": "sample-"+tid, "source": "disk_reader", "capture_phase": "lifecycle_poll",
                         "backend": "codex", "schema_version": "task-attempt.v1", "identity_version": "operation-v2",
                         "usage_timestamp": sample.timestamp, "provider_session_id": sid, "native_turn_sample": sample.native_turn_sample,
                         "placement_epoch": "1", "model": sample.model})
    return d, h, u


def frozen(events, members, **kwargs):
    freeze = ZfEvent(type="candidate.ready", origin="kernel", ts=stamp(100), payload={
        "workflow_run_id": "run", "task_map_generation": "gen", "plan_artifact_package_digest": "pkg", "target_commit": "head"})
    return task_lifecycle_evidence([*events, freeze], freeze_id=freeze.id, run="run", generation="gen",
        target=kwargs.get("target", "head"), members=[{"task_id": t, "task_map_generation": "gen"} for t in members])


@pytest.mark.parametrize("stages", [["inspect"], ["build","review"], ["impl","verify","review","inspect","inspect"]])
def test_native_window_is_frozen_for_any_roster_and_stage(tmp_path, stages):
    events, tasks = [], []
    for index, stage in enumerate(stages):
        task = str(index)
        tasks.append(task)
        events.extend(dispatch_input(tmp_path, task, stage))
    body = frozen(events, tasks)
    result = body["summary"]["provider_activity"]
    window = result["generations"][0]["peak_window"]
    assert window["observed_concurrent_tasks"] == len(stages)
    assert sum(window["stage_counts"].values()) == len(stages)
    assert result["unobserved_task_ids"] == []
    for task in body["tasks"]:
        (interval,) = task["provider_turns"]
        assert interval["stage"] in stages and interval["model"] == "vendor/native-2"
        assert interval["usage_event_id"] in window["usage_event_ids"]
    output = json.dumps(body)
    assert str(tmp_path) not in output and "private transcript text" not in output
    assert frozen(events, tasks, target="old") is None


@pytest.mark.parametrize("change", ["forged", "unbound_hook", "other_package", "other_generation", "old_attempt",
                                    "other_lease", "workspace", "placement", "other_session", "timestamp", "postfreeze", "invalid_hook", "invalid_sample"])
def test_native_window_cannot_be_fabricated_from_dispatch_or_stale_samples(tmp_path, change):
    d, h, u = dispatch_input(tmp_path,"A","impl")
    if change == "forged": u.origin = "worker"
    if change == "unbound_hook": h.causation_id = "old"
    if change == "other_package": d.payload["plan_artifact_package_digest"] = "other"
    if change == "other_generation": d.payload["task_map_generation"] = "other"
    if change == "old_attempt": u.payload["attempt_id"] = "old"
    if change == "other_lease": u.payload["lease_id"] = "old"
    if change == "workspace": d.payload["workdir"] = "/other"
    if change == "placement": u.payload["placement_epoch"] = "2"
    if change == "other_session": h.payload["session_id"] = str(uuid4())
    if change == "timestamp": u.payload["usage_timestamp"] = stamp(25)
    if change == "postfreeze": u.ts = stamp(101)
    if change == "invalid_hook": h.payload["turn_id"] = {}
    if change == "invalid_sample": u.payload["native_turn_sample"]["session_id"] = []
    body = frozen([d, h, u], ["A"])
    if change == "other_generation":
        assert body is None
    else:
        assert len(body["tasks"][0]["operations"]) == 1 and body["tasks"][0]["provider_turns"] == []
        assert body["summary"]["provider_activity"]["status"] == "unknown"


def test_touching_turns_and_overlapping_retries_never_add_task_peaks(tmp_path):
    # A's two separate attempts overlap B's first instant, but only one
    # distinct Task A and an exclusive lane could ever be counted.
    a = dispatch_input(tmp_path, "A", "shape", 3, 12)
    retry = dispatch_input(tmp_path, "A", "shape", 5, 12)
    b = dispatch_input(tmp_path, "B", "test", 11, 15)
    body = frozen([*a, *retry, *b], ["A","B"])
    assert len(body["tasks"][0]["provider_turns"]) == 2
    window = body["summary"]["provider_activity"]["generations"][0]["peak_window"]
    assert window["observed_concurrent_tasks"] == 1


@pytest.mark.parametrize("change", ["header", "no_context", "changed_context", "other_token_turn", "zero", "other_role",
                                     "outside", "symlink", "parent_symlink", "hard_link"])
def test_only_exact_private_rollout_with_turn_context_can_supply_receipt(tmp_path, change):
    sid, tid = str(uuid4()), str(uuid4())
    def mutate(rows):
        if change == "header": rows[0][2]["id"] = str(uuid4())
        if change == "no_context": del rows[2]
        if change == "changed_context": rows.insert(3, (rows[2][0], 3, {**rows[2][2], "cwd": "/other"}))
        if change == "other_token_turn": rows[-2][2]["turn_id"] = str(uuid4())
        if change == "zero": rows[-2][2]["info"]["last_token_usage"] = {"input_tokens": 0, "output_tokens": 0}
        return rows
    p = rollout(tmp_path, "lane", sid, tid, str(tmp_path/"project"), 2, 9, transform=mutate)
    if change == "outside":
        outside = tmp_path / "outside.jsonl"
        p.rename(outside)
        p = outside
    if change == "symlink":
        target = tmp_path / "other"
        p.rename(target)
        p.symlink_to(target)
    if change == "parent_symlink":
        target = tmp_path / "other"
        p.parent.rename(target)
        p.parent.symlink_to(target, target_is_directory=True)
    if change == "hard_link": (tmp_path / "alias").hardlink_to(p)
    sample = read(tmp_path, "other" if change == "other_role" else "lane", sid, p)
    assert sample is None or sample.native_turn_sample == {}


def test_actual_lifecycle_poll_seals_native_sample_to_current_lease(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_scheduler_task_attempt import _runtime
    from zf.runtime.task_attempt_runtime import mark_task_attempt_sent, prepare_task_attempt
    from zf.runtime.transport import DispatchContext

    runtime, _, state = _runtime(tmp_path, mode="enforce")
    role = replace(runtime.config.roles[0], backend="codex")
    runtime.config.roles[0] = role
    runtime._session_readers["codex"] = CodexSessionReader()
    sid, tid = str(uuid4()), str(uuid4())
    cwd, stage, task, run = str(tmp_path / "pinned-workspace"), "analyze", "TASK-1", "RUN-1"
    path = rollout(state, role.instance_id, sid, tid, cwd, 4, 16)
    registry = runtime._role_lifecycle_registry()
    bound = registry.bind_task_stage_session(workflow_run_id=run, task_id=task, stage=stage,
        rework_affinity_id="trial", role_instance=role.instance_id, role_config_digest="role",
        workspace_generation=1, placement_epoch=1, backend="codex")
    registry.activate_task_stage_session(binding_key=bound["binding_key"], role_instance=role.instance_id)
    assert registry.bind_codex_session(role.instance_id, sid, session_path=path)
    briefing = state / "briefing.md"
    briefing.write_text("Test.", encoding="utf-8")
    ctx = DispatchContext(task_id=task, role_name=role.name, instance_id=role.instance_id,
        dispatch_id="dispatched", run_id=run, operation_id="operation", task_pipeline_stage=stage, placement_epoch=1)
    attempt = prepare_task_attempt(runtime, context=ctx, briefing_path=briefing)
    monkeypatch.setattr("zf.runtime.task_attempt_runtime._now", lambda: stamp(2))
    mark_task_attempt_sent(runtime, attempt)
    key = {"workflow_run_id": run, "task_id": task, "operation_id": ctx.operation_id,
           "dispatch_id": ctx.dispatch_id, "role_instance": role.instance_id, "request_hash": "request"}
    for kind in ("requested", "started"):
        runtime.event_writer.append(ZfEvent(type="workflow.operation."+kind, origin="kernel", payload={**key,
            "operation_type": "task_pipeline", "parent_stage_id": stage, "active_attempt_id": attempt.context.attempt_id}))
    d = runtime.event_writer.append(ZfEvent(type="task.pipeline.stage.dispatched", actor="orchestrator", origin="kernel",
        task_id=task, ts=stamp(2), payload={**key, "attempt_id": attempt.context.attempt_id, "lease_id": attempt.context.lease_id,
        "workdir": cwd, "task_pipeline_stage": stage, "stage_id": stage, "workspace_generation": 1, "placement_epoch": 1,
        "task_map_generation":"gen", "plan_artifact_package_digest":"pkg"}))
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", origin="kernel", actor=role.instance_id,
        ts=stamp(5), causation_id=d.id, payload={"session_id": sid, "turn_id": tid, "cwd": cwd}))
    runtime._check_context_thresholds()
    sample = runtime.event_log.query(type="agent.usage")[-1]
    assert sample.origin == "kernel" and sample.payload["native_turn_sample"]["turn_id"] == tid
    assert sample.payload["attempt_id"] == attempt.context.attempt_id and sample.payload["lease_id"] == attempt.context.lease_id
    freeze = runtime.event_writer.append(ZfEvent(type="candidate.ready", origin="kernel", payload={
        "workflow_run_id":run, "task_map_generation":"gen", "plan_artifact_package_digest":"pkg", "target_commit":"head"}))
    body = task_lifecycle_evidence(runtime.event_log.read_all(), freeze_id=freeze.id, run=run, generation="gen",
                                   target="head", members=[{"task_id":task, "task_map_generation":"gen"}])
    assert body["tasks"][0]["provider_turns"][0]["usage_event_id"] == sample.id


def test_frozen_packages_do_not_pool_independent_turns_and_missing_tasks(tmp_path):
    old = dispatch_input(tmp_path, "old", "review", generation="old", package="oldpkg")
    current = dispatch_input(tmp_path, "now", "review")
    unbound = dispatch_input(tmp_path, "missing", "review")[:1]
    authority = ZfEvent(type="task.pipeline.verify.completed", task_id="old", origin="kernel",
        payload={"workflow_run_id":"run", "task_map_generation":"old", "plan_artifact_package_digest":"oldpkg"})
    freeze = ZfEvent(type="candidate.ready", origin="kernel", ts=stamp(100), payload={
        "workflow_run_id":"run", "task_map_generation":"gen", "plan_artifact_package_digest":"pkg", "target_commit":"head"})
    events = [*old, *current, *unbound, authority, freeze]
    members = [{"task_id":"old", "task_map_generation":"old", "verification_event_id":authority.id},
               {"task_id":"now", "task_map_generation":"gen"}, {"task_id":"missing", "task_map_generation":"gen"}]
    body = task_lifecycle_evidence(events, run="run", generation="gen", target="head", freeze_id=freeze.id, members=members)
    observation = body["summary"]["provider_activity"]
    assert observation["unobserved_task_ids"] == ["missing"]
    assert [(g["task_map_generation"], g["peak_window"]["observed_concurrent_tasks"]) for g in observation["generations"]] == [
        ("gen", 1), ("old", 1)]


def test_candidate_and_judge_receive_same_native_evidence_ref(tmp_path):
    from tests.test_candidate_host_consumption import prepare
    from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
    from tests.test_plan_artifact_package import _package
    from zf.runtime.milestone_task_lifecycles import SOURCE, judge_candidate_lifecycles
    from zf.runtime.plan_artifact_package import package_event_payload, write_plan_artifact_package
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["milestone_handoff"] = True
    run, generation = "run-1", "g1"
    package = _package(runtime.state_dir, generation=generation)
    descriptor = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_log.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel", correlation_id=run,
        payload=package_event_payload(package, descriptor, status="admitted")))
    fields = {"plan_artifact_package_id":descriptor["package_id"], "plan_artifact_package_ref":descriptor["ref"],
              "plan_artifact_package_digest":descriptor["sha256"]}
    for ev in dispatch_input(runtime.state_dir, "TASK-1", "build", run=run, generation=generation, package=descriptor["sha256"]):
        runtime.event_log.append(ev)
    seeded = _seed_frozen_candidate(runtime, run_id=run, generation=generation, package_fields=fields)
    _, reader = prepare(runtime, seeded, "reader")
    manifest = hydrate_sidecar_ref(runtime.state_dir, reader["attempt_source_manifest"]).payload
    source = next(row for row in manifest["sources"] if row["source_id"] == SOURCE)
    facts = hydrate_sidecar_ref(runtime.state_dir, {"ref": source["ref"], "sha256": source["sha256"]}).payload
    assert facts["summary"]["provider_activity"]["generations"][0]["peak_window"]["stage_counts"] == {"build": 1}
    common = {"workflow_run_id":run, "task_map_generation":generation, "target_commit":seeded["candidate_head"],
              "fanout_id":reader["fanout_id"], **fields}
    runtime.event_log.append(ZfEvent(type="fanout.child.dispatched", origin="kernel",
        payload={**common, "operation_id":reader["operation_id"], "payload":reader}))
    gate = runtime.event_writer.append(ZfEvent(type="test.passed", origin="kernel",
        payload={**common, "candidate_snapshot_event_id":"evt-candidate-ready"}))
    source_event = runtime.event_writer.append(ZfEvent(type="flow.discovery.completed", origin="kernel", payload=common))
    projection = judge_candidate_lifecycles(runtime.state_dir, runtime.event_log.read_all(), source_event=source_event,
        run=run, generation=generation, target=seeded["candidate_head"], package=descriptor["sha256"])
    assert projection["candidate_passed_event_id"] == gate.id and projection["lifecycle_digest"] == source["sha256"]
    assert projection["summary"]["provider_activity"] == facts["summary"]["provider_activity"]


def test_native_start_before_post_send_occurrence_needs_exact_transport_claim(tmp_path):
    d, h, u = dispatch_input(tmp_path, "T", "stage", start=3, end=8)
    h.ts = stamp(5)
    d.ts = stamp(4)
    claim = ZfEvent(type="task.attempt.delivery_claimed", actor="orchestrator", origin="kernel",
                    task_id=d.task_id, ts=stamp(2), payload=d.payload.copy())
    body = frozen([claim, d, h, u], ["T"])
    (row,) = body["tasks"][0]["provider_turns"]
    assert row["delivery_claim_event_id"] == claim.id and datetime.fromisoformat(row["observed_from"]) == datetime.fromisoformat(h.ts)
    claim.payload["lease_id"] = "other-lease"
    assert frozen([claim, d, h, u], ["T"])["tasks"][0]["provider_turns"] == []


def test_closed_corrupt_line_invalidates_registered_native_usage(tmp_path):
    sid, tid = str(uuid4()), str(uuid4())
    p = rollout(tmp_path, "lane", sid, tid, str(tmp_path / "project"), 3, 8)
    with p.open("a", encoding="utf-8") as stream:
        stream.write("{unfinished")
    # Polls may race a live append. Only the previously complete counters are available.
    partial = read(tmp_path, "lane", sid, p)
    assert partial.raw["output_tokens"] == 3 and partial.native_turn_sample == {}
    with p.open("a", encoding="utf-8") as stream:
        stream.write("\n")
    assert read(tmp_path, "lane", sid, p) is None
