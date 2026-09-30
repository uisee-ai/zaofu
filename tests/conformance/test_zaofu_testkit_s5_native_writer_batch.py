"""Opt-in raw Plan -> native multi-task writer batch -> gap-only CLI recovery.

The first Task must remain completed while the second impl is interrupted.
Not a legacy fanout queue timeout, network failure or operating-system crash.
"""

import json
import os
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_plan_approval import planning_runtime_at
from tests.conformance.test_zaofu_testkit_s5_native_claude_pipeline import _native_pipeline_timeout_seconds
from tests.conformance.test_zaofu_testkit_s5_native_cli_lifecycle import NativeCLI, assert_replacement
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import _assert_gone
from tests.testkit.native_pipeline_recovery import transcript_rows
from tests.testkit.native_writer_batch import batch_continuity_evidence, completed_task_evidence, writer_batch_items
from tests.testkit.runner import _terminate
from tests.testkit.source_observation import capture_source
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import ProviderSessionConfig
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_native_writer_batch_recovers_only_unfinished_task(monkeypatch, record_property):
    if os.environ.get("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("set ZF_RUN_NATIVE_CLAUDE_PIPELINE=1 for native writer batch")
    if not shutil.which("claude") or not shutil.which("tmux"):
        pytest.skip("native writer batch requires claude and tmux")
    repo = Path(__file__).resolve().parents[2]
    source = capture_source(repo)
    deadline = time.monotonic() + _native_pipeline_timeout_seconds()
    marker = f"ZF_WRITER_BATCH_{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix="zf-k546-writer-20260924-") as directory:
        root = Path(directory)
        root.rmdir()  # Empty owned directory; fixture initializes Git here.
        with monkeypatch.context() as given:
            runtime, _, trigger = planning_runtime_at(root, given, task_items=writer_batch_items(marker))
        state, config = runtime.state_dir, runtime.config
        config.project.state_dir = "runtime-state"
        config.session.tmux_session = f"zf-k546-{uuid4().hex}"
        command = f"{sys.executable} -m zf.cli.main"
        for role in config.roles:
            role.backend, role.transport, role.permission_mode = "claude-code", "tmux", "allowlist"
            role.allowed_tools = ["Read", "Write", "Edit", f"Bash({command}:*)", "Bash(git:*)"]
            role.constraints.allowed_paths = [str(root)]
            role.spawn_ready_timeout_seconds = 30
            role.provider_session = ProviderSessionConfig(effort="low")
            role.guardrails = [
                f"Isolated test: all task writes must stay under {root}.",
                "Do not delegate, push, access remote services or edit the ZaoFu source checkout.",
                "Use the printed ZaoFu CLI; never modify canonical runtime state directly.",
                "Do not print credentials/env or search for credentials. Stop if permission is denied.",
                "Execute the briefing and Success CLI; save real command evidence, not invented receipts.",
            ]
        (state / "artifacts/requirement.md").write_text(
            f"# Goal\nWrite a.txt exactly {marker}-1 followed by newline. "
            f"Then write b.txt exactly {marker}-2 followed by newline. Preserve a.txt.\n",
        )
        (root / "zf.yaml").write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
        cli = NativeCLI(runtime, root)
        observed = {"root": str(root), "complete": False, "marker": marker}
        preserved = None

        def second_inflight(events):
            for event in events:
                body = event.payload
                if (event.type != "task.pipeline.stage.dispatched" or
                        (body["task_id"], body["task_pipeline_stage"]) != ("TASK-2", "impl")):
                    continue
                binding = RoleSessionRegistry(state / "role_sessions.yaml", str(root)).task_stage_bindings()[
                    body["task_stage_session_binding"]
                ]
                assistant = [r for r in transcript_rows(claude_session_path(body["workdir"], binding["session_id"]))
                             if r.get("type") == "assistant" and r.get("sessionId") == binding["session_id"]]
                if not assistant:
                    continue
                assert TaskAttemptStore(state / "task_attempts.json").get(body["attempt_id"])["status"] == "sent"
                assert not any(e.type == "workflow.operation.settled" and
                               e.payload.get("operation_id") == body["operation_id"] for e in events)
                observed.update(old_dispatch=body, assistant_count=len(assistant))
                return event
            return None

        try:
            cli.start()
            cli.wait(lambda events: any(e.type == "plan.approval.requested" for e in events), deadline)
            assert runtime.task_store.get("TASK-1") is None
            assert not any(e.type == "task.pipeline.stage.dispatched" for e in cli.events())
            # Operator action against this private test plan, not approval on a real project.
            cli.command("plan", "approve", trigger.id)
            first = cli.wait(second_inflight, deadline)
            preserved = completed_task_evidence(runtime, "TASK-1")
            before_stop = cli.events()
            assert not any(e.type == "candidate.ready" for e in before_stop)
            [generation] = [e for e in before_stop if e.type == "task.pipeline.generation.admitted"]
            assert generation.payload["task_ids"] == ["TASK-1", "TASK-2"]
            assert subprocess.check_output(
                ["git", "show", f"{first.payload['target_ref']}:a.txt"], cwd=root, timeout=10,
            ) == (marker + "-1\n").encode()
            observed["task_boundary_downgrades"] = batch_continuity_evidence(before_stop, first.id)
            cli.stop()
            old = first.payload
            assert reduce_workflow_operations(cli.events())[old["operation_id"]]["status"] == "suspended"
            inspect = json.loads(cli.command("recover", "workflow", "--json"))
            [checkpoint] = [c for c in inspect["projection"]["operation_checkpoints"]
                            if c["operation_id"] == old["operation_id"]]
            assert checkpoint["safe_resume_action"] == "redrive_interrupted_task_pipeline_operation"
            args = ("recover", "workflow", "--resume-pending", "--json", "--checkpoint-id", checkpoint["checkpoint_id"])
            for extra, applied in ((("--dry-run",), 0), ((), 1), ((), 0)):
                before = (state / "events.jsonl").read_bytes()
                assert json.loads(cli.command(*args, *extra))["applied"] == applied
                if not applied:
                    assert (state / "events.jsonl").read_bytes() == before
            assert completed_task_evidence(runtime, "TASK-1") == preserved
            cli.start()
            cli.wait(lambda events: any(e.type == "candidate.ready" for e in events), deadline)
            events = cli.events()
            dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"]
            assert [(e.payload["task_id"], e.payload["task_pipeline_stage"]) for e in dispatches] == [
                ("TASK-1", "impl"), ("TASK-1", "verify"), ("TASK-2", "impl"),
                ("TASK-2", "impl"), ("TASK-2", "verify"),
            ]
            attempts = TaskAttemptStore(state / "task_attempts.json")
            assert_replacement(old, dispatches[3].payload, {
                e.payload["attempt_id"]: attempts.get(e.payload["attempt_id"]) for e in dispatches
            })
            assert completed_task_evidence(runtime, "TASK-1") == preserved
            assert runtime.task_store.get("TASK-2").status == "done"
            [candidate] = [e for e in events if e.type == "candidate.ready"]
            candidate_contents = {}
            for index, name in enumerate(("a.txt", "b.txt"), 1):
                content = subprocess.check_output(
                    ["git", "show", f"{candidate.payload['commit']}:{name}"], cwd=root, timeout=10,
                )
                assert content == f"{marker}-{index}\n".encode()
                candidate_contents[name] = content.decode()
            observed.update(candidate_id=candidate.id, candidate_commit=candidate.payload["commit"],
                            candidate_contents=candidate_contents, checkpoint_id=checkpoint["checkpoint_id"])
            registry = RoleSessionRegistry(state / "role_sessions.yaml", str(root))
            bindings = registry.task_stage_bindings()
            sessions = []
            transcript_evidence = []
            for event in (e for e in dispatches if e.id != first.id):
                body = event.payload
                binding = bindings[body["task_stage_session_binding"]]
                assert binding["status"] == "archived"
                assert binding["session_id"] == body["provider_session_id"]
                assert attempts.get(body["attempt_id"])["status"] == "succeeded"
                path = claude_session_path(body["workdir"], binding["session_id"])
                rows = transcript_rows(path)
                count = sum(r.get("type") == "assistant" and r.get("sessionId") == binding["session_id"] for r in rows)
                assert count
                sessions.append(binding["session_id"])
                transcript_evidence.append({"task": body["task_id"], "stage": body["task_pipeline_stage"],
                                            "path": str(path), "assistant_count": count,
                                            "sha256": sha256(path.read_bytes()).hexdigest()})
            assert len(set(sessions)) == 4
            observed.update(sessions=sessions, transcripts=transcript_evidence)
            assert batch_continuity_evidence(events, first.id) == observed["task_boundary_downgrades"]
            assert not any(e.type in {"role.lifecycle.activation.failed",
                                     "orchestrator.tick.failed", "kernel.housekeeping.failed"}
                           for e in events)
            cli.stop()
            cli.start()
            started = sum(e.type == "loop.started" for e in events)
            cli.wait(lambda rows: sum(e.type == "loop.started" for e in rows) > started,
                     min(deadline, time.monotonic() + 60))
            cli.stop()
            canonical = {"task.pipeline.stage.dispatched", "workflow.operation.settled", "candidate.ready", "task.done"}
            assert [e.id for e in cli.events() if e.type in canonical] == [e.id for e in events if e.type in canonical]
            assert batch_continuity_evidence(cli.events(), first.id) == observed["task_boundary_downgrades"]
            assert completed_task_evidence(runtime, "TASK-1") == preserved
            observed["complete"] = True
        finally:
            runtime.event_writer.append(ZfEvent(type="simulation.done", actor="testkit",
                                                payload={"reason": "native-writer-batch-finished"}))
            try:
                cli.stop()
            finally:
                if cli.process is not None:
                    _terminate(cli.process)
                if cli.tmux.has_session():
                    cli.tmux.kill_session()
                record_property("native_writer_batch", json.dumps(redact_obj({
                    **observed, "preserved_task": preserved, "starts": cli.starts, "commands": cli.commands,
                    "pids": sorted(cli.pids),
                    "logs": {p.name: p.read_text()[-16000:] for p in root.glob("start-*.log")},
                    "events": [asdict(e) for e in cli.events()
                               if e.type.startswith(("task.pipeline.", "workflow.operation.", "role.lifecycle."))
                               or e.type in {"candidate.ready", "task.done", "simulation.done", "fanout.cancelled"}],
                    "bindings": RoleSessionRegistry(state / "role_sessions.yaml", str(root)).task_stage_bindings(),
                }), default=str))
                _assert_gone(cli.pids)
                record_property("native_cleanup", json.dumps({
                    "simulation_done": True, "tmux_stopped": not cli.tmux.has_session(),
                    "lock_released": not (state / "loop.lock").exists(), "owned_pids_gone": True,
                }))
                after = capture_source(repo)
                stable = source["git_head"] == after["git_head"] and source["files_sha256"] == after["files_sha256"]
                record_property("native_source", json.dumps({"files_sha256": source["files_sha256"], "stable_observed": stable}))
                assert stable, "source changed during native writer batch"
