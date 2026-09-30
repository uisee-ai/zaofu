"""Opt-in independent CLI stop/recover/start around a real in-flight Claude turn."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from tests.conformance.test_zaofu_testkit_s5_native_claude_pipeline import (
    _native_pipeline_timeout_seconds,
)
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import _assert_gone
from tests.testkit.native_pipeline_recovery import transcript_rows
from tests.testkit.runner import _terminate
from tests.testkit.source_observation import capture_source
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import ProviderSessionConfig, RoleConfig, RoleLifecycleConfig
from zf.core.events import EventLog, ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.security.redaction import redact_obj
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.tmux import TmuxSession
from zf.runtime.workflow_operation import reduce_workflow_operations


def assert_replacement(old: dict, new: dict, attempts: dict) -> None:
    """A new dispatch must supersede the interrupted attempt, not a new operation."""
    assert new["operation_id"] == old["operation_id"]
    assert new["task_pipeline_stage"] == old["task_pipeline_stage"]
    assert new["task_id"] == old["task_id"]
    for field in ("task_stage_session_binding", "provider_session_id", "workdir"):
        assert old[field] and new[field] == old[field], field
    if old["task_pipeline_stage"] == "verify":
        assert old["target_commit"] and new["target_commit"] == old["target_commit"]
    for field in ("attempt_id", "dispatch_id", "lease_id"):
        assert new[field] != old[field], field
    prior, current = attempts[old["attempt_id"]], attempts[new["attempt_id"]]
    assert prior["status"] == "superseded"
    assert prior["superseded_by"] == new["attempt_id"]
    assert current["status"] == "succeeded"


def graceful_snapshot_evidence(state: Path, operation_id: str, role: str, memory: dict) -> dict:
    """Check diagnostic copies against the stopped ledger, never restore from them."""
    snapshot = state / "last-shutdown"
    copied = [ZfEvent.from_json(line) for line in (snapshot / "events.jsonl").read_text().splitlines()]
    current = EventLog(state / "events.jsonl").read_all()
    assert [asdict(e) for e in copied] == [asdict(e) for e in current[:len(copied)]]
    operation = reduce_workflow_operations(copied)[operation_id]
    assert operation["status"] == "suspended" and operation["reason"] == "graceful_stop"
    assert not any(e.type == "loop.stopped" for e in copied)
    assert any(e.type == "loop.stopped" for e in current[len(copied):])
    assert yaml.safe_load((snapshot / "session.yaml").read_text())["runtime_state"] == "shutdown_requested"
    assert json.loads((snapshot / "kanban.json").read_text())
    assert (snapshot / "snapshot_at").read_text().strip()
    for name, content in memory.items():
        assert (state / "memory" / name).read_bytes() == content
        assert (snapshot / "memory" / name).read_bytes() == content
    assert (state / "memory/.snapshotted").is_file()
    assert (snapshot / "memory/.snapshotted").is_file()
    role_log = state / "logs" / f"{role}.log"
    assert role_log.read_text().strip(), "graceful stop must retain the active role log"
    files = [p for p in snapshot.rglob("*") if p.is_file()] + [role_log]
    return {"operation_id": operation_id, "reason": operation["reason"],
            "snapshot_event_count": len(copied), "ledger_event_count": len(current),
            "memory_preserved": sorted(memory), "role_log": str(role_log.relative_to(state)),
            "digests": {str(p.relative_to(state)): sha256(p.read_bytes()).hexdigest() for p in files}}


class NativeCLI:
    def __init__(self, runtime, output: Path):
        self.runtime, self.output = runtime, output
        self.process = None
        self.starts = []
        self.pids: set[str] = set()
        self.tmux = TmuxSession(runtime.config.session.tmux_session)
        self.env = {**os.environ, "ZF_CLI_CMD": f"{sys.executable} -m zf.cli.main",
                    "PYTHONUNBUFFERED": "1"}
        self.commands = []

    def events(self):
        return EventLog(self.runtime.state_dir / "events.jsonl").read_all()

    def command(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", *args],
            cwd=self.runtime.project_root, env=self.env, text=True,
            capture_output=True, timeout=45,
        )
        self.commands.append({"args": args, "exit_code": result.returncode,
                              "stdout": redact_obj(result.stdout), "stderr": redact_obj(result.stderr)})
        assert result.returncode == 0, self.commands[-1]
        return result.stdout

    def observe_pids(self):
        if self.tmux.has_session():
            for role in self.runtime.config.roles:
                probe = self.tmux.pane_process_probe(role.instance_id)
                if not str(probe.get("pane_pid", "")).isdigit():
                    continue  # on_demand roles do not own a pane before activation.
                assert probe["available"], probe
                self.pids.update({str(probe["pane_pid"]),
                                  *(str(row["pid"]) for row in probe["processes"])})

    def start(self):
        assert self.process is None
        log = self.output / f"start-{len(self.starts) + 1}.log"
        with log.open("w") as stream:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "zf.cli.main", "start", "--simulation"],
                cwd=self.runtime.project_root, env=self.env, stdout=stream, stderr=stream,
                start_new_session=True,
            )
        self.starts.append({"pid": self.process.pid, "log": str(log)})
        self.pids.add(str(self.process.pid))

    def wait(self, predicate, deadline):
        while True:
            assert self.process is not None and self.process.poll() is None, self.starts
            self.observe_pids()
            events = self.events()
            value = predicate(events)
            if value:
                return value
            failures = [e for e in events if e.type in {
                "dev.blocked", "task.pipeline.verify.failed", "orchestrator.tick.failed",
            }]
            assert not failures, [(e.type, redact_obj(e.payload)) for e in failures]
            assert time.monotonic() < deadline, "native CLI lifecycle budget expired"
            time.sleep(0.25)

    def stop(self, *, mode="fast"):
        assert mode in {"fast", "graceful"}
        self.observe_pids()
        self.command("stop", *(("--fast",) if mode == "fast" else ()), "--include-run-manager")
        if self.process is not None:
            self.starts[-1]["exit_code"] = self.process.wait(timeout=10)
            self.process = None
        assert not self.tmux.has_session()
        assert not (self.runtime.state_dir / "loop.lock").exists()
        _assert_gone(self.pids)


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_cli_fast_stop_recover_restart_candidate(monkeypatch, record_property):
    _exercise_native_cli_lifecycle(monkeypatch, record_property, stage="impl")


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_cli_verify_fast_stop_recover_restart_candidate(monkeypatch, record_property):
    _exercise_native_cli_lifecycle(monkeypatch, record_property, stage="verify")


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
@pytest.mark.parametrize("stage", ["impl", "verify"])
def test_native_cli_graceful_stop_recover_restart_candidate(monkeypatch, record_property, stage):
    _exercise_native_cli_lifecycle(monkeypatch, record_property, stage=stage, stop_mode="graceful")


def _exercise_native_cli_lifecycle(monkeypatch, record_property, *, stage, stop_mode="fast", network=None,
                                   workload=None):
    if os.environ.get("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("set ZF_RUN_NATIVE_CLAUDE_PIPELINE=1 for native CLI lifecycle")
    if not shutil.which("claude") or not shutil.which("tmux"):
        pytest.skip("native CLI lifecycle requires claude and tmux")
    repo = Path(__file__).resolve().parents[2]
    source = capture_source(repo)
    deadline = time.monotonic() + _native_pipeline_timeout_seconds()
    with TemporaryDirectory(prefix="zf-k543-cli-20260924-") as directory:
        root = Path(directory)
        project = root
        project.rmdir()  # Empty owned directory; the Git fixture creates it.
        # The fixture supplies only canonical task/Plan inputs. Its monkeypatches
        # never enter the independent production CLI interpreter.
        with monkeypatch.context() as given:
            runtime, _, _ = _blocking_runtime_at(project, given)
        state, config = runtime.state_dir, runtime.config
        config.project.state_dir = "runtime-state"
        config.session.tmux_session = f"zf-k543-{uuid4().hex}"
        config.roles.append(RoleConfig(
            name="verify", instance_id="verify-1", role_kind="reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ))
        cli_command = f"{sys.executable} -m zf.cli.main"
        for role in config.roles:
            role.backend, role.transport, role.permission_mode = "claude-code", "tmux", "allowlist"
            role.allowed_tools = ["Read", "Write", "Edit", f"Bash({cli_command}:*)", "Bash(git:*)"]
            role.constraints.allowed_paths = [str(project)]
            role.spawn_ready_timeout_seconds = 30
            role.provider_session = ProviderSessionConfig(effort="low")
            role.guardrails = [
                f"Isolated test: all task writes must stay under {project}.",
                "Do not delegate, push, access remote services or edit the ZaoFu source checkout.",
                "Use the printed ZaoFu CLI; never modify canonical runtime state directly.",
                "Do not print credentials/env or search for credentials. Stop if permission is denied.",
                "Execute the briefing and Success CLI; save real command evidence, not invented receipts.",
            ]
        marker = f"ZF_CLI_RESTART_{uuid4().hex[:12]}"
        task = runtime.task_store.get("TASK-BLOCKING")
        task.contract.scope = ["README.md"]
        task.contract.acceptance_criteria = [f"README.md contains exactly {marker} and a newline"]
        task.contract.verification = "git cat-file -e HEAD:README.md"
        task.contract.validation = {"commands": [{
            "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
            "tier": "runtime", "deterministic": True, "reusable": True,
        }]}
        runtime.task_store.update(task.id, title=f"Write {marker} to README.md", contract=task.contract)
        if workload is not None:
            assert stage == "impl" and network is None
            workload.configure(runtime, task, marker)
        memory = {}
        if stop_mode == "graceful":
            MemoryStore(state / "memory").add(None, "decision", f"Isolated lifecycle memory {marker}")
            memory = {p.name: p.read_bytes() for p in (state / "memory").iterdir() if p.is_file()}
        (project / "zf.yaml").write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
        cli = NativeCLI(runtime, root)
        if network is not None:
            network.prepare(cli, root, monkeypatch)
        observation = {"stage": stage, "stop_mode": stop_mode, "root": str(root), "complete": False}

        def inflight(events):
            for event in events:
                if event.type != "task.pipeline.stage.dispatched" or event.payload["task_pipeline_stage"] != stage:
                    continue
                body = event.payload
                binding = RoleSessionRegistry(state / "role_sessions.yaml", str(project)).task_stage_bindings()[
                    body["task_stage_session_binding"]
                ]
                transcript = claude_session_path(body["workdir"], binding["session_id"])
                assistant = [r for r in transcript_rows(transcript)
                             if r.get("type") == "assistant" and r.get("sessionId") == binding["session_id"]]
                if not assistant:
                    continue
                assert TaskAttemptStore(state / "task_attempts.json").get(body["attempt_id"])["status"] == "sent"
                assert not any(e.type == "workflow.operation.settled" and
                               e.payload.get("operation_id") == body["operation_id"] for e in events)
                observation.update(old_dispatch=body, assistant_count=len(assistant),
                                   old_session_id=binding["session_id"])
                return event
            return None

        try:
            cli.start()
            first = cli.wait(inflight, deadline)
            if workload is not None:
                workload.wait_before_stop(cli, first, deadline)
            if network is not None:
                network.interrupt(cli, first, deadline)
            completed_impl = None
            if stage == "verify":
                before_stop = cli.events()
                [impl] = [e for e in before_stop if e.type == "task.pipeline.stage.dispatched"
                          and e.payload["task_pipeline_stage"] == "impl"]
                completed_impl = TaskAttemptStore(state / "task_attempts.json").get(impl.payload["attempt_id"])
                assert completed_impl["status"] == "succeeded"
                impl_settlements = [asdict(e) for e in before_stop
                                    if e.type == "workflow.operation.settled"
                                    and e.payload["operation_id"] == impl.payload["operation_id"]]
                assert len(impl_settlements) == 1
                assert subprocess.check_output(
                    ["git", "show", f"{first.payload['target_commit']}:README.md"], cwd=project, timeout=10,
                ) == (marker + "\n").encode()
                observation["preserved_impl"] = {
                    "dispatch_id": impl.id, "attempt": completed_impl,
                    "settlements": impl_settlements, "target_commit": first.payload["target_commit"],
                }
            cli.stop(mode=stop_mode)
            if workload is not None:
                workload.after_stop(cli)
            old = first.payload
            assert SessionStore(state / "session.yaml").load().runtime_state == "stopped"
            assert reduce_workflow_operations(cli.events())[old["operation_id"]]["status"] == "suspended"
            if stop_mode == "graceful":
                record_property("native_graceful_snapshot", json.dumps(
                    graceful_snapshot_evidence(state, old["operation_id"], old["role_instance"], memory),
                ))
            assert not any(e.type == "candidate.ready" for e in cli.events())
            inspect = json.loads(cli.command("recover", "workflow", "--json"))
            [checkpoint] = [c for c in inspect["projection"]["operation_checkpoints"]
                            if c["operation_id"] == old["operation_id"]]
            assert checkpoint["safe_resume_action"] == "redrive_interrupted_task_pipeline_operation"
            args = ("recover", "workflow", "--resume-pending", "--json",
                    "--checkpoint-id", checkpoint["checkpoint_id"])
            before = [e.id for e in cli.events()]
            assert json.loads(cli.command(*args, "--dry-run"))["applied"] == 0
            assert [e.id for e in cli.events()] == before
            assert json.loads(cli.command(*args))["applied"] == 1
            after = [e.id for e in cli.events()]
            assert json.loads(cli.command(*args))["applied"] == 0
            assert [e.id for e in cli.events()] == after
            if network is not None:
                network.restore()
            cli.start()
            cli.wait(lambda events: any(e.type == "candidate.ready" for e in events), deadline)
            events = cli.events()
            dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"]
            expected_stages = ["impl", "impl", "verify"] if stage == "impl" else ["impl", "verify", "verify"]
            assert [e.payload["task_pipeline_stage"] for e in dispatches] == expected_stages
            replacement = dispatches[1 if stage == "impl" else 2].payload
            attempts = TaskAttemptStore(state / "task_attempts.json")
            assert_replacement(old, replacement, {
                e.payload["attempt_id"]: attempts.get(e.payload["attempt_id"]) for e in dispatches
            })
            if completed_impl is not None:
                assert attempts.get(impl.payload["attempt_id"]) == completed_impl
                assert [asdict(e) for e in events
                        if e.type == "workflow.operation.settled"
                        and e.payload["operation_id"] == impl.payload["operation_id"]] == impl_settlements
            operations = reduce_workflow_operations(events)
            sessions = []
            registry = RoleSessionRegistry(state / "role_sessions.yaml", str(project))
            for dispatched in (e for e in dispatches if e.id != first.id):
                body = dispatched.payload
                assert operations[body["operation_id"]]["status"] == "settled"
                binding = registry.task_stage_bindings()[body["task_stage_session_binding"]]
                assert binding["status"] == "archived"
                record_property(f"native_{body['task_pipeline_stage']}_binding", json.dumps({
                    "binding": binding, "dispatch_session_id": body.get("provider_session_id"),
                    "workdir": body["workdir"], "attempt_id": body["attempt_id"],
                }))
                assert body["provider_session_id"] == binding["session_id"]
                sessions.append(binding["session_id"])
                rows = transcript_rows(claude_session_path(body["workdir"], binding["session_id"]))
                assert any(r.get("type") == "assistant" and r.get("sessionId") == binding["session_id"] for r in rows)
            assert len(set(sessions)) == 2
            [candidate] = [e for e in events if e.type == "candidate.ready"]
            assert subprocess.check_output(
                ["git", "show", f"{candidate.payload['commit']}:README.md"], cwd=project, timeout=10,
            ) == (marker + "\n").encode()
            assert runtime.task_store.get(task.id).status == "done"
            if workload is not None:
                workload.assert_candidate(cli, candidate)
            cli.stop(mode=stop_mode)
            if network is not None:
                network.assert_recovered()
            cli.start()
            # An actual post-terminal startup/watcher tick must finish before comparing.
            baseline_started = sum(e.type == "loop.started" for e in events)
            cli.wait(lambda rows: sum(e.type == "loop.started" for e in rows) > baseline_started,
                     min(deadline, time.monotonic() + 60))
            cli.stop(mode=stop_mode)
            final = cli.events()
            for kind in ("task.pipeline.stage.dispatched", "candidate.ready"):
                assert [e.id for e in final if e.type == kind] == [e.id for e in events if e.type == kind]
            observation.update(complete=True, candidate_id=candidate.id,
                               candidate_commit=candidate.payload["commit"], new_dispatch=replacement,
                               checkpoint_id=checkpoint["checkpoint_id"], provider_sessions=sessions)
        finally:
            runtime.event_writer.append(ZfEvent(type="simulation.done", actor="testkit",
                                                payload={"reason": "native-cli-lifecycle-finished"}))
            try:
                cli.stop()
            finally:
                if cli.process is not None:
                    _terminate(cli.process)
                if cli.tmux.has_session():
                    cli.tmux.kill_session()
                logs = {p.name: redact_obj(p.read_text()[-16000:]) for p in root.glob("start-*.log")}
                record_property("native_final_state", json.dumps(redact_obj({
                    "events": [{"id": e.id, "type": e.type, "ts": e.ts,
                                "payload": e.payload} for e in cli.events() if e.type in {
                        "task.pipeline.stage.dispatched", "candidate.ready", "task.done",
                        "workflow.operation.settled", "task.attempt.superseded",
                        "role.lifecycle.continuity.downgraded", "simulation.done",
                        "role.lifecycle.activation.failed", "loop.started", "loop.stopped",
                    }],
                    "bindings": RoleSessionRegistry(state / "role_sessions.yaml", str(project)).task_stage_bindings(),
                }), default=str))
                record_property("native_cli_lifecycle", json.dumps(redact_obj({
                    **observation, "starts": cli.starts, "commands": cli.commands,
                    "pids": sorted(cli.pids), "logs": logs,
                }), default=str))
                _assert_gone(cli.pids)
                record_property("native_cleanup", json.dumps({
                    "simulation_done": True, "tmux_stopped": not cli.tmux.has_session(),
                    "lock_released": not (state / "loop.lock").exists(), "owned_pids_gone": True,
                }))
                after = capture_source(repo)
                stable = source["git_head"] == after["git_head"] and source["files_sha256"] == after["files_sha256"]
                record_property("native_source", json.dumps({"files_sha256": source["files_sha256"],
                                                              "stable_observed": stable}))
                assert stable, "source changed during native lifecycle"
