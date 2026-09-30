"""Opt-in CLI/native tmux Task Pipeline; only admission and stop bounds are Given.

The real watcher, SpawnCoordinator, TmuxTransport and workers own all dispatch,
artifact reads, result submission and settlement. Observers delegate unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from tests.conformance.test_zaofu_testkit_s5_claude_readiness import (
    ClaudeInteractiveReadinessBug, blocked_claude_screen,
)
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import _assert_gone, _process_ids
from zf.cli.main import main
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import ProviderSessionConfig, RoleConfig, RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.cli_command import default_zf_cli_cmd
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.tmux import TmuxError, TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.watcher import EventWatcher
from zf.runtime.workflow_operation import reduce_workflow_operations


def _require_native_candidate(events, *, budget_expired, record_property):
    """Report an unfinished run before checking terminal-only invariants."""
    types = [event.type for event in events]
    blockers = [kind for kind in types if kind in {
        "task.pipeline.stage.dispatch_deferred", "dev.blocked", "task.pipeline.verify.failed",
    }]
    reason = ("runtime_blocked" if blockers else "candidate_ready" if "candidate.ready" in types
              else "budget_expired" if budget_expired else "stopped_without_candidate")
    record_property("native_outcome", json.dumps({
        "reason": reason, "budget_expired": budget_expired, "blocker_types": blockers,
        "dispatched_stages": [event.payload.get("task_pipeline_stage") for event in events
                              if event.type == "task.pipeline.stage.dispatched"],
        "settled_operations": types.count("workflow.operation.settled"),
        "candidate_count": types.count("candidate.ready"),
    }))
    assert reason == "candidate_ready", f"native pipeline incomplete: {reason}; blockers={blockers}"


def _native_terminal_events(events):
    """Keep result-to-TaskRef waiting evidence before the isolated root is removed."""
    selected = {
        "task.pipeline.stage.dispatched", "task.pipeline.stage.dispatch_deferred",
        "workflow.operation.settled", "candidate.ready", "dev.blocked",
        "kernel.housekeeping.failed", "worker.launch_artifact.written",
        "dev.build.done", "task.ref.updated", "task.pipeline.stage.waiting",
    }
    return redact_obj([
        {"id": e.id, "ts": e.ts, "type": e.type, "payload": e.payload,
         "causation_id": e.causation_id, "correlation_id": e.correlation_id}
        for e in events if e.type in selected
    ])


def test_native_terminal_events_preserve_commit_wait_chain():
    types = ["dev.build.done", "task.ref.updated", "task.pipeline.stage.waiting"]
    events = [ZfEvent(type=kind, payload={"source_commit": "b" * 40},
                      causation_id="parent", correlation_id="run-1") for kind in types]
    rows = _native_terminal_events([*events, ZfEvent(type="worker.heartbeat")])
    assert [row["type"] for row in rows] == types
    for row, event in zip(rows, events, strict=True):
        assert row == {"id": event.id, "ts": event.ts, "type": event.type,
                       "payload": event.payload, "causation_id": "parent",
                       "correlation_id": "run-1"}


def _native_pipeline_timeout_seconds() -> float:
    """Allow a bounded host-tier budget without changing the default contract."""
    raw = os.environ.get("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", "720")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS must be numeric") from exc
    if not 0 < value <= 1800:
        raise ValueError("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS must be in (0, 1800]")
    return value


@pytest.mark.parametrize("event_types,budget_expired,reason", [
    ([], True, "budget_expired"),
    ([], False, "stopped_without_candidate"),
    (["task.pipeline.verify.failed"], True, "runtime_blocked"),
    (["candidate.ready", "dev.blocked"], False, "runtime_blocked"),
    (["candidate.ready"], True, "candidate_ready"),
])
def test_native_candidate_diagnostic(event_types, budget_expired, reason):
    properties = {}
    events = [ZfEvent(type=kind, actor="testkit", payload={}) for kind in event_types]
    if reason == "candidate_ready":
        _require_native_candidate(events, budget_expired=budget_expired,
                                  record_property=properties.__setitem__)
    else:
        with pytest.raises(AssertionError, match=f"native pipeline incomplete: {reason}"):
            _require_native_candidate(events, budget_expired=budget_expired,
                                      record_property=properties.__setitem__)
    outcome = json.loads(properties["native_outcome"])
    assert outcome["reason"] == reason
    assert outcome["budget_expired"] == budget_expired
    assert outcome["candidate_count"] == event_types.count("candidate.ready")


@pytest.mark.parametrize("value,expected", [(None, 720.0), ("1440", 1440.0)])
def test_native_pipeline_timeout_budget(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", value)
    assert _native_pipeline_timeout_seconds() == expected


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number", "nan", "inf", "1801"])
def test_native_pipeline_timeout_budget_rejects_invalid(monkeypatch, value):
    monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        _native_pipeline_timeout_seconds()


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_claude_start_dispatch_submit_verify_candidate(monkeypatch, record_property):
    _exercise_native_pipeline(monkeypatch, record_property)


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
@pytest.mark.parametrize("interrupted_stage", ["impl", "verify"])
def test_native_claude_interrupted_stage_recovers_to_candidate(
    monkeypatch, record_property, interrupted_stage,
):
    _exercise_native_pipeline(monkeypatch, record_property, interrupted_stage=interrupted_stage)


def _exercise_native_pipeline(monkeypatch, record_property, *, interrupted_stage=None, recovery_factory=None):
    if os.environ.get("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("set ZF_RUN_NATIVE_CLAUDE_PIPELINE=1 for the real native lifecycle tier")
    if not shutil.which("claude") or not shutil.which("tmux"):
        pytest.skip("native lifecycle requires claude and tmux")
    pipeline_timeout = _native_pipeline_timeout_seconds()
    from tests.testkit.native_run import capture_source
    source_root = Path(__file__).resolve().parents[2]
    source_before = capture_source(source_root)
    cli = default_zf_cli_cmd()
    monkeypatch.setenv("ZF_CLI_CMD", cli)
    with TemporaryDirectory(prefix="zf-k486-native-20260923-") as directory:
        project = Path(directory)
        project.rmdir()
        original_activation = Orchestrator._ensure_role_active
        runtime, _, _ = _blocking_runtime_at(project, monkeypatch)
        monkeypatch.setattr(Orchestrator, "_ensure_role_active", original_activation)
        state, config = runtime.state_dir, runtime.config
        config.project.state_dir = "runtime-state"
        config.session.tmux_session = f"zf-k486-{uuid.uuid4().hex}"
        config.roles.append(RoleConfig(
            name="verify", instance_id="verify-1", role_kind="reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ))
        for role in config.roles:
            role.backend = "claude-code"
            role.transport = "tmux"
            role.permission_mode = "allowlist"
            role.allowed_tools = ["Read", "Write", "Edit", f"Bash({cli}:*)", "Bash(git:*)"]
            role.constraints.allowed_paths = [str(project)]
            role.spawn_ready_timeout_seconds = 30
            role.provider_session = ProviderSessionConfig(effort="low")
            role.guardrails = [
                f"This is an isolated test. All task writes must stay under {project}.",
                "Do not delegate, push, access remote services, or edit the ZaoFu source checkout.",
                "Do not modify canonical runtime state directly; use the printed ZaoFu CLI.",
                "Use Read/Write/Edit for artifacts; Bash only for the printed zf CLI and local git.",
                "Never print credentials/env or search for credentials; role capabilities are provisioned.",
                "If permission/capability is denied, stop and report; do not work around it.",
                "Execute the briefing and its Success CLI, not just describe it. Save real command evidence.",
            ]
        marker = f"ZF_NATIVE_TASK_{uuid.uuid4().hex[:12]}"
        task = runtime.task_store.get("TASK-BLOCKING")
        task.contract.scope = ["README.md"]
        task.contract.acceptance_criteria = [f"README.md contains exactly {marker} and a newline"]
        task.contract.verification = "git cat-file -e HEAD:README.md"
        task.contract.validation = {"commands": [{
            "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
            "tier": "runtime", "deterministic": True, "reusable": True,
        }]}
        runtime.task_store.update(task.id, title=f"Write {marker} to README.md", contract=task.contract)
        (project / "zf.yaml").write_text(
            yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
        )
        tmux = TmuxSession(config.session.tmux_session)
        recovery = None
        assert interrupted_stage is None or recovery_factory is None
        if interrupted_stage is not None:
            from tests.testkit.native_pipeline_recovery import NativePipelineRecovery
            recovery = NativePipelineRecovery(runtime, tmux, interrupted_stage, record_property)
        elif recovery_factory is not None:
            recovery = recovery_factory(runtime, tmux, record_property)
        contexts, snapshots = [], []
        pane_tails = {}
        observed_transports = {}
        blocked_screens = {}
        observed_pids = set()
        deadline = time.monotonic() + pipeline_timeout
        original_send = TmuxTransport.send_task
        original_poll = EventWatcher.poll_once

        def observe_send(transport, role_name, briefing_path, prompt, *, context=None):
            if transport.session_name == tmux.session_name:
                if context is not None and context.task_pipeline_stage:
                    contexts.append(context)
                observed_transports[role_name] = transport
                snapshots.append(transport.lifecycle_snapshot(role_name).to_payload())
                observed_pids.update(_process_ids(transport.tmux, role_name))
            return original_send(transport, role_name, briefing_path, prompt, context=context)

        def bounded_poll(watcher):
            lines = original_poll(watcher)
            events = runtime.event_log.read_all()
            if recovery is not None:
                recovery.poll(contexts, events)
                observed_pids.update(recovery.old_pids)
            for context in contexts:
                if tmux.has_session():
                    visible_screen = ""
                    try:
                        visible_screen = observed_transports[context.instance_id].capture_log(
                            context.instance_id, lines=0,
                        )
                        pane_tails[context.instance_id] = redact_obj(
                            observed_transports[context.instance_id].capture_log(
                                context.instance_id, lines=80,
                            )[-6000:],
                        )
                    except TmuxError:
                        # A completed stage may already have released its owned pane.
                        pass
                    if blocked_claude_screen(visible_screen):
                        blocked_screens[context.instance_id] = pane_tails[context.instance_id]
                        watcher.stop()
            if any(e.type == "candidate.ready" for e in events) or time.monotonic() >= deadline:
                watcher.stop()
            if any(e.type in {"task.pipeline.stage.dispatch_deferred", "dev.blocked",
                              "task.pipeline.verify.failed", "worker.respawn.failed",
                              "orchestrator.tick.failed"} for e in events):
                watcher.stop()
            return lines

        monkeypatch.setattr(TmuxTransport, "send_task", observe_send)
        monkeypatch.setattr(EventWatcher, "poll_once", bounded_poll)
        monkeypatch.chdir(project)
        try:
            rc = main(["start", "--simulation"])
            events = runtime.event_log.read_all()
            record_property("native_timeout_seconds", str(pipeline_timeout))
            assert rc == 0
            assert any(e.type == "session.started" for e in events)
            if blocked_screens:
                raise ClaudeInteractiveReadinessBug(json.dumps(blocked_screens, ensure_ascii=False))
            _require_native_candidate(events, budget_expired=time.monotonic() >= deadline,
                                      record_property=record_property)
            completed_contexts = recovery.verify(contexts, events) if recovery else contexts
            assert [c.task_pipeline_stage for c in completed_contexts] == ["impl", "verify"]
            assert all(s["alive"] and str(s["pane_pid"]).isdigit() for s in snapshots)
            registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(project))
            operations = reduce_workflow_operations(events)
            provider_sessions = []
            for context in completed_contexts:
                binding = registry.task_stage_bindings()[context.task_stage_session_binding]
                assert binding["status"] == "archived"
                session_id = binding["session_id"]
                provider_sessions.append(session_id)
                [dispatch] = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                              and e.payload["attempt_id"] == context.attempt_id]
                transcript = claude_session_path(dispatch.payload["workdir"], session_id)
                rows = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
                assert any(r.get("sessionId") == session_id and r.get("type") == "assistant" for r in rows)
                tools = [block.get("name") for row in rows
                         for block in (row.get("message", {}).get("content", [])
                                       if isinstance(row.get("message", {}).get("content"), list) else [])
                         if isinstance(block, dict) and block.get("type") == "tool_use"]
                assert "Bash" in tools and "Read" in tools
                assert operations[context.operation_id]["status"] == "settled"
                record_property(f"native_{context.task_pipeline_stage}", json.dumps({
                    "binding": binding, "session_id": session_id, "transcript": str(transcript),
                    "tool_names": tools, "attempt_id": context.attempt_id,
                }, default=str))
            assert len(set(provider_sessions)) == 2
            [candidate] = [e for e in events if e.type == "candidate.ready"]
            assert subprocess.check_output(
                ["git", "show", f"{candidate.payload['commit']}:README.md"], cwd=project, timeout=10,
            ) == (marker + "\n").encode()
            implementations = [e for e in events if e.type == "dev.build.done"]
            assert len(implementations) == (2 if getattr(recovery, "prior_impl", None) else 1)
            [implementation] = [e for e in implementations
                                if e.payload.get("operation_id") == completed_contexts[0].operation_id]
            result = hydrate_sidecar_ref(state, implementation.payload["control_result_ref"]).payload
            assert result["changed_files"] == ["README.md"] and result["self_check"]["command_receipts"]
            assert runtime.task_store.get(task.id).status == "done"
            for _ in range(2):
                Orchestrator(state, config, TmuxTransport(tmux), project_root=project).run_once()
            expected_contexts = getattr(recovery, "expected_contexts", 3) if recovery else 2
            assert len(contexts) == expected_contexts
            assert sum(e.type == "candidate.ready" for e in runtime.event_log.read_all()) == 1
        finally:
            record_property("native_events", json.dumps(
                _native_terminal_events(runtime.event_log.read_all()),
                ensure_ascii=False, default=str,
            ))
            if recovery is not None:
                observed_pids.update(recovery.old_pids)
                record_property("native_recovery_terminal_events", json.dumps(redact_obj([
                    {"id": e.id, "type": e.type, "payload": e.payload}
                    for e in runtime.event_log.read_all()
                    if e.type.startswith(("worker.respawn.", "workflow.operation.", "task.attempt."))
                ]), default=str))
            record_property("native_processes", json.dumps({
                "session": tmux.session_name, "dispatch_snapshots": snapshots,
                "observed_pids": sorted(observed_pids), "pane_tails": pane_tails,
            }))
            runtime.event_writer.append(ZfEvent(
                type="simulation.done", actor="testkit", payload={"reason": "native-pipeline-test-finished"},
            ))
            assert main(["stop", "--force"]) == 0
            assert not tmux.has_session()
            assert not (state / "loop.lock").exists()
            _assert_gone(observed_pids)
            record_property("native_cleanup", json.dumps({
                "simulation_done": True, "tmux_stopped": True,
                "lock_released": True, "owned_pids_gone": True,
            }))
            source_after = capture_source(source_root)
            source_identity = {
                key: source_before[key] for key in ("git_head", "files_sha256")
            }
            source_stable = all(source_after[key] == value for key, value in source_identity.items())
            record_property("native_source", json.dumps({
                **source_identity, "stable_observed": source_stable,
                "scope": source_before["scope"],
            }))
            assert source_stable, "source changed during native testcase"
