"""Regression tests for the 3 claude-backend prod-E2E P0 fixes (2026-06-19).

Each reproduces the runtime failure observed in the
``zaofu-e2e-workflow-tst`` run and locks the fix:

- P0-1: stream-json re-dispatch hit "Session ID already in use" because the
  stale-session lock purge only ran on the tmux spawn path.
- P0-2: refactor ``plan.ready`` never became ``task_map.ready`` (livelock) —
  the deterministic bridge now fires the impl fanout.
- P0-3: ``prd.approved`` failed "requires prd_ref" because the aggregate read
  child top-level/report but not the inherited ``trigger_payload``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from zf.runtime.spawn_coordinator import purge_stale_claude_session_lock
from zf.runtime.writer_fanout_data import WriterFanoutDataMixin


# ---------------------------------------------------------------- P0-1
def test_purge_clears_claude_json_and_jsonl(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "slug").mkdir(parents=True)
    sid = "d940dfc2-f914-51ff-8164-126ac182a0e6"
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"projects": {"/repo": {"lastSessionId": sid}}}))
    jsonl = home / ".claude" / "projects" / "slug" / f"{sid}.jsonl"
    jsonl.write_text("{}")
    monkeypatch.setattr("zf.runtime.spawn_coordinator.Path.home", lambda: home)
    monkeypatch.setattr(
        "zf.runtime.spawn_coordinator._uuid_used_by_live_process", lambda u: False
    )

    out = purge_stale_claude_session_lock(sid)

    assert json.loads(cfg.read_text())["projects"]["/repo"]["lastSessionId"] == ""
    assert not jsonl.exists()  # archived aside
    assert out["claude_json_fields_cleared"] == ["/repo.lastSessionId"]
    assert out["jsonl_archived"]


def test_purge_noop_when_live_process_owns_uuid(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    sid = "feeddead-0000-5000-8000-000000000001"
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"projects": {"/repo": {"lastSessionId": sid}}}))
    monkeypatch.setattr("zf.runtime.spawn_coordinator.Path.home", lambda: home)
    monkeypatch.setattr(
        "zf.runtime.spawn_coordinator._uuid_used_by_live_process", lambda u: True
    )

    out = purge_stale_claude_session_lock(sid)

    # never steal a lock a running process holds
    assert json.loads(cfg.read_text())["projects"]["/repo"]["lastSessionId"] == sid
    assert out == {
        "claude_json_fields_cleared": [],
        "aux_paths_removed": [],
        "jsonl_archived": [],
    }


# ---------------------------------------------------------------- P0-3
def test_prd_approved_inherits_prd_ref_from_trigger_payload():
    # critic child re-emits nothing structured; the prd_ref / artifact_refs /
    # evidence_refs live in the inherited trigger_payload (from prd.ready).
    child = {
        "trigger_payload": {
            "prd_ref": "docs/plans/training-mode-prd.md",
            "artifact_refs": ["docs/plans/training-mode-prd.md"],
            "evidence_refs": ["fanout:trigger_payload.text"],
        }
    }
    # value getter falls back to trigger_payload
    assert (
        WriterFanoutDataMixin._payload_or_report_value(child, "prd_ref")
        == "docs/plans/training-mode-prd.md"
    )
    assert WriterFanoutDataMixin._collect_payload_list([child], "evidence_refs") == [
        "fanout:trigger_payload.text"
    ]
    # and the contract gate now passes for prd.approved
    payload = {
        "prd_ref": "docs/plans/training-mode-prd.md",
        "artifact_refs": ["docs/plans/training-mode-prd.md"],
        "evidence_refs": ["e1"],
    }
    assert (
        WriterFanoutDataMixin._success_payload_contract_failure("prd.approved", payload)
        == ""
    )


def test_payload_top_level_still_wins_over_trigger_payload():
    child = {"prd_ref": "top.md", "trigger_payload": {"prd_ref": "inherited.md"}}
    assert WriterFanoutDataMixin._payload_or_report_value(child, "prd_ref") == "top.md"


def _cfg(event_schemas: dict) -> SimpleNamespace:
    return SimpleNamespace(
        workflow=SimpleNamespace(dag=SimpleNamespace(event_schemas=event_schemas))
    )


def test_handoff_ref_fields_derived_from_event_schemas():
    # 2026-06-20 de-hardcode: the fanout-child briefing slot is derived from the
    # DAG event contract, not a hardcoded {prd.ready,...} set, so it stays a
    # single source of truth with the gate and generalizes to custom DAG flows.
    from zf.runtime.orchestrator_fanout import _contract_handoff_ref_fields

    declared = _cfg({
        "prd.ready": {"required": [
            "fanout_id", "status", "prd_ref", "artifact_refs", "evidence_refs",
        ]},
        # a custom, non-PRD event name the kernel has never heard of
        "spec.approved": {"required": ["status", "evidence_refs", "artifact_refs"]},
    })
    # declared PRD event → derives exactly the handoff refs (envelope fields
    # like fanout_id/status are filtered out)
    assert _contract_handoff_ref_fields(declared, "prd.ready") == [
        "prd_ref", "artifact_refs", "evidence_refs",
    ]
    # GENERALIZATION: a custom DAG event automatically gets its slot, no kernel
    # change, no hardcoded name
    assert _contract_handoff_ref_fields(declared, "spec.approved") == [
        "evidence_refs", "artifact_refs",
    ]
    # an event with no handoff refs in its contract → no slot
    assert _contract_handoff_ref_fields(declared, "test.passed") == []


def test_handoff_ref_fields_loose_mode_falls_back_to_prd_defaults():
    # loose mode (no declared event_schemas) must NOT regress to the
    # `prd.blocked: requires evidence_refs` loop — the canonical PRD stages keep
    # a fallback slot; unrelated events stay empty.
    from zf.runtime.orchestrator_fanout import _contract_handoff_ref_fields

    loose = _cfg({})
    assert _contract_handoff_ref_fields(loose, "prd.ready") == [
        "prd_ref", "artifact_refs", "evidence_refs",
    ]
    assert _contract_handoff_ref_fields(loose, "task_map.ready") == [
        "task_map_ref", "artifact_refs", "evidence_refs",
    ]
    assert _contract_handoff_ref_fields(loose, "test.passed") == []


# ---------------------------------------------------------------- P-NEXT-1
def test_task_map_resolves_from_worktree_when_not_at_project_root(tmp_path):
    # A synth that wrote the task_map into its own worktree and emitted a
    # project-relative ref must still be admitted (the artifact lives under
    # state_dir/workdirs/<instance>/project/, not the project root).
    import json
    from types import SimpleNamespace

    from zf.core.events.model import ZfEvent
    from zf.runtime.writer_fanout_admission import (
        _resolve_in_worktrees,
        load_writer_task_map,
    )

    state_dir = tmp_path / ".zf"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    wt = state_dir / "workdirs" / "issue-plan" / "project" / "docs" / "plans"
    wt.mkdir(parents=True)
    ref = "docs/plans/issue-map-issue-plan-task-map.json"
    (wt / "issue-map-issue-plan-task-map.json").write_text(json.dumps({
        "tasks": [
            {"task_id": "T1", "scope": "T1", "allowed_paths": ["src/a.js"]},
            {"task_id": "T2", "scope": "T2", "allowed_paths": ["src/b.js"]},
        ],
    }))

    # resolver finds the single worktree copy
    found = _resolve_in_worktrees(ref, state_dir=state_dir)
    assert found is not None and found.exists()

    # and admission no longer fail-closes on the project-root miss
    loaded = load_writer_task_map(
        stage=SimpleNamespace(task_map=""),
        event=ZfEvent(type="task_map.ready", actor="zf-cli",
                      payload={"task_map_ref": ref, "pdd_id": "F-1"}),
        pdd_id="F-1",
        state_dir=state_dir,
        project_root=project_root,
    )
    assert {t["task_id"] for t in loaded.task_items} == {"T1", "T2"}


def test_worktree_resolver_stays_fail_closed_when_ambiguous(tmp_path):
    import json

    from zf.runtime.writer_fanout_admission import _resolve_in_worktrees

    state_dir = tmp_path / ".zf"
    ref = "docs/plans/tm.json"
    for inst in ("a", "b"):
        d = state_dir / "workdirs" / inst / "project" / "docs" / "plans"
        d.mkdir(parents=True)
        (d / "tm.json").write_text(json.dumps({"tasks": []}))
    # two candidates → ambiguous → None (do not silently pick one)
    assert _resolve_in_worktrees(ref, state_dir=state_dir) is None


def test_dispatch_waits_for_ready_before_sending_briefing():
    # DID-7: _send_transport_task must wait for the worker's agent prompt to be
    # ready (wait_role_ready) BEFORE send_task — otherwise the briefing races a
    # not-ready claude pane and is lost, leaving the worker idle → drift.
    from types import SimpleNamespace

    from zf.runtime.orchestrator import Orchestrator

    calls: list[str] = []
    role = SimpleNamespace(instance_id="dev-core", name="dev-core")
    stub = SimpleNamespace(
        transport=SimpleNamespace(
            send_task=lambda *a, **k: calls.append("send_task")
        ),
        _find_role_by_instance=lambda n: role,
        _find_role_by_name=lambda n: role,
        _wait_role_ready=lambda r: calls.append("wait_ready"),
        # _send_transport_task gained a transport-availability guard at its head;
        # the stub must satisfy it for the wait→send→notify ordering under test.
        _transport_dispatch_enabled=lambda: True,
        # P0-1: the primitive now consults the budget gate after role resolution;
        # under budget (False) it proceeds, preserving the wait→send→notify order.
        _budget_exceeded=lambda r: False,
        _get_spawn_coordinator=lambda: SimpleNamespace(
            notify_first_dispatch=lambda r: calls.append("notify")
        ),
    )
    Orchestrator._send_transport_task(stub, "dev-core", "/b.md", "p", None)
    assert calls == ["wait_ready", "send_task", "notify"]


def test_completion_nudge_fires_once_then_idempotent():
    # DID-9: a worker that did the work but never emitted its terminal event is
    # nudged (re-sent the completion instruction) once, instead of requeued —
    # requeue would discard its uncommitted work. The second stuck cycle returns
    # None so the caller falls through to requeue.
    from types import SimpleNamespace

    from zf.runtime.orchestrator import Orchestrator

    injected: list = []
    appended: list = []
    role = SimpleNamespace(instance_id="dev-core", name="dev-core")
    task = SimpleNamespace(id="ISSUE-CORE-001")
    stub = SimpleNamespace(
        _expected_terminal_event_for_role=lambda r: "workflow.child.completed",
        _stuck_detectors={},
        _stuck_already_reported=set(),
        _set_worker_state=lambda *a, **k: None,
        _inject_terminal_completion_nudge_prompt=lambda **k: injected.append(k),
        event_writer=SimpleNamespace(append=lambda ev: appended.append(ev)),
    )
    d1 = Orchestrator._request_terminal_completion_nudge(
        stub, role=role, task=task, dispatch_id="d1", reason="x"
    )
    assert d1 is not None and d1.action == "recover"
    assert len(injected) == 1  # nudge sent (not requeued)
    assert any(
        e.payload.get("recovery_action") == "completion_nudge_requested"
        for e in appended
    )
    d2 = Orchestrator._request_terminal_completion_nudge(
        stub, role=role, task=task, dispatch_id="d1", reason="x"
    )
    assert d2 is None  # already nudged → caller falls through to requeue
    assert len(injected) == 1  # not nudged again


def test_worktree_resolver_handles_zf_prefixed_ref(tmp_path):
    # P-NEXT-1b: a synth may write the task_map under its worktree's .zf/ and
    # emit a `.zf/...`-prefixed ref; the fallback must still find it.
    import json

    from zf.runtime.writer_fanout_admission import _resolve_in_worktrees

    state_dir = tmp_path / ".zf-issue"
    ref = ".zf/artifacts/issue-map/task_map.json"
    d = state_dir / "workdirs" / "issue-plan" / "project" / ".zf" / "artifacts" / "issue-map"
    d.mkdir(parents=True)
    (d / "task_map.json").write_text(json.dumps({"tasks": []}))
    found = _resolve_in_worktrees(ref, state_dir=state_dir)
    assert found is not None and found.exists()


# ---------------------------------------------------------------- P0-2
def test_refactor_plan_ready_bridges_to_task_map_and_starts_impl():
    from zf.runtime.orchestrator import Orchestrator

    appended = []
    started = []
    stub = SimpleNamespace(
        event_writer=SimpleNamespace(
            append=lambda ev: (appended.append(ev) or ev)
        ),
        _maybe_start_writer_fanout=lambda ev: started.append(ev),
        _refactor_replan_payload=lambda payload: {},
    )
    Orchestrator._bridge_refactor_plan_ready_to_task_map(
        stub,
        manifest={"feature_id": "F-1", "target_ref": "main"},
        projection_payload={
            "task_map_ref": ".zf/artifacts/plan/task_map.json",
            "source_index_ref": ".zf/artifacts/plan/source_index.json",
            "source_commit": "abc123",
        },
        trace_id="tr-1",
    )

    assert len(appended) == 1
    ev = appended[0]
    assert ev.type == "task_map.ready"
    assert ev.payload["task_map_ref"] == ".zf/artifacts/plan/task_map.json"
    assert ev.payload["feature_id"] == "F-1"
    assert ev.payload["source"] == "refactor_plan_bridge"
    # impl fanout started off the bridged event
    assert started == [ev]


def test_stream_json_rotates_session_id_on_already_in_use(tmp_path, monkeypatch):
    # The first (fresh-id) drain fails with claude's "already in use"; the
    # transport must rotate to a new session-id and retry once, succeeding.
    from zf.core.config.schema import RoleConfig
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime import transport_stream_json as tsj

    monkeypatch.setattr(
        tsj,
        "purge_stale_claude_session_lock",
        lambda sid: {
            "claude_json_fields_cleared": [],
            "aux_paths_removed": [],
            "jsonl_archived": [],
        },
    )

    class _Msg:  # minimal assistant-ish message the drain collects
        pass

    seen_ids = []

    def query_fn(*, prompt, options):
        sid = (options.extra_args or {}).get("session-id", "")
        seen_ids.append(sid)

        async def _gen():
            if len(seen_ids) == 1:
                raise RuntimeError("Error: Session ID %s is already in use." % sid)
            yield _Msg()

        return _gen()

    registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
    tr = tsj.StreamJsonTransport(tmp_path, registry, query_fn=query_fn)
    tr.register_role(RoleConfig(name="issue-plan", backend="claude-code"))

    tr.send_task("issue-plan", tmp_path / "b.md", "do it")

    assert len(seen_ids) == 2  # retried once
    assert seen_ids[0] != seen_ids[1]  # on a rotated, fresh session-id
    assert tr.is_alive("issue-plan")  # retry drained ok


def test_bridge_noop_without_task_map_ref():
    from zf.runtime.orchestrator import Orchestrator

    appended = []
    stub = SimpleNamespace(
        event_writer=SimpleNamespace(append=lambda ev: appended.append(ev)),
        _maybe_start_writer_fanout=lambda ev: appended.append(("start", ev)),
    )
    Orchestrator._bridge_refactor_plan_ready_to_task_map(
        stub, manifest={}, projection_payload={}, trace_id="tr-2"
    )
    assert appended == []


# ---------------------------------------------------------------- P0-1 (real root cause)
def test_pure_aggregator_skips_role_that_is_also_a_fanout_child():
    # The actual issue-flow bug: issue-plan is BOTH a fanout child and the
    # synthRole. Narrowing it to read-only tools gates its child Write and hangs
    # the fanout. Only a PURE aggregator (synthRole, never a child) is narrowed.
    from zf.core.config.schema import RoleConfig
    from zf.core.workflow.runner_policy import is_fanout_synth_role

    def cfg(child_roles, synth):
        return SimpleNamespace(
            workflow=SimpleNamespace(
                stages=[
                    SimpleNamespace(
                        roles=child_roles,
                        aggregate=SimpleNamespace(synth_role=synth),
                    )
                ]
            )
        )

    # pure aggregator: synth but never a child -> still narrowed
    assert is_fanout_synth_role(cfg(["a", "b"], "synth"), RoleConfig(name="synth"))
    # both child and synth (issue-plan shape) -> NOT narrowed
    assert not is_fanout_synth_role(cfg(["a", "issue-plan"], "issue-plan"),
                                    RoleConfig(name="issue-plan"))
    # plain child, never synth -> not a synth role
    assert not is_fanout_synth_role(cfg(["a", "b"], "synth"), RoleConfig(name="a"))
