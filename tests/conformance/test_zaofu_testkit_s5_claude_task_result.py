"""Opt-in real Claude authoring/submission inside the blocking Task Pipeline.

Plan admission and transport scheduling are fixture inputs. The driver delivers
the unchanged production briefing through ClaudeHeadlessBackend; it never writes
the worker's patch, result, required-read ledger, or terminal event for it.
This is not the native tmux/CLI-start lifecycle tier.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _blocking_runtime_at
from tests.conformance.test_zaofu_testkit_s5_real_provider import (
    _direct_claude_children,
    _real_claude_command,
)
from tests.test_task_pipeline_generation import _git
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.cli_command import default_zf_cli_cmd
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.web.headless_agent import ClaudeHeadlessBackend


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_claude_authors_submits_and_verifies_candidate(monkeypatch, record_property):
    if os.environ.get("ZF_RUN_REAL_CLAUDE_TASK_RESULT", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("set ZF_RUN_REAL_CLAUDE_TASK_RESULT=1 for real task authoring")
    command = _real_claude_command()
    monkeypatch.setenv("ZF_CLI_CMD", default_zf_cli_cmd())
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS", "Bash,Read,Write,Edit")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE", "dontAsk")
    before_children = _direct_claude_children()
    with TemporaryDirectory(prefix="zf-k484-claude-task-20260923-") as directory:
        project = Path(directory) / "project"
        runtime, transport, _admission = _blocking_runtime_at(project, monkeypatch)
        config, state = runtime.config, runtime.state_dir
        config.roles[0].backend = "claude-code"
        config.roles.append(RoleConfig(
            name="verify", instance_id="verify-1", backend="claude-code", role_kind="reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ))
        worker_envs = {}
        for role in config.roles:
            worker_envs[role.instance_id] = {
                "ZF_PROJECT_ROOT": str(project), "ZF_STATE_DIR": str(state),
                "ZF_ROLE_NAME": role.name, "ZF_ROLE_INSTANCE": role.instance_id,
                "ZF_ROLE_BACKEND": role.backend,
                "ZF_RESULT_SUBMIT_TOKEN_FILE": str(provision_role_submit_credential(state, role.instance_id)),
                "ZF_ARTIFACT_READ_TOKEN_FILE": str(provision_role_artifact_read_credential(
                    state, role.instance_id, role_name=role.name, provider=role.backend,
                )),
            }
        marker = f"ZF_REAL_TASK_{uuid.uuid4().hex[:12]}"
        # Keep the worker-owned verification command stable and allowlisted;
        # the test itself independently checks the exact candidate bytes.
        probe = "git cat-file -e HEAD:README.md"
        task = runtime.task_store.get("TASK-BLOCKING")
        task.contract.scope = ["README.md"]
        task.contract.acceptance_criteria = [f"README.md contains exactly {marker} and a newline"]
        task.contract.verification = probe
        task.contract.validation = {"commands": [{
            "id": "readme-content", "command": probe, "rolling_smoke": True,
            "tier": "runtime", "deterministic": True, "reusable": True,
        }]}
        runtime.task_store.update(task.id, title=f"Write {marker} to README.md", contract=task.contract)
        (project / "zf.yaml").write_text(
            yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
        )
        provider_sessions = []
        try:
            runtime.run_once()
            for index, stage in enumerate(("impl", "verify")):
                assert len(transport.contexts) == index + 1
                context = transport.contexts[index]
                assert context.task_pipeline_stage == stage
                operation = reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]
                assert operation["status"] == "running"
                [dispatch] = [e for e in runtime.event_log.read_all()
                              if e.type == "task.pipeline.stage.dispatched"
                              and e.payload["attempt_id"] == context.attempt_id]
                workspace = Path(dispatch.payload["workdir"])
                messages, sessions = [], []
                backend = ClaudeHeadlessBackend(
                    command=shlex.join([
                        *shlex.split(command), "--allowedTools", "Read", "Write", "Edit",
                        f"Bash({default_zf_cli_cmd()}:*)", "Bash(git:*)", "Bash(test:*)",
                    ]), max_turns=48,
                )
                backend.set_telemetry_env(worker_envs[context.instance_id])
                result = backend.run_turn(
                    prompt=context.briefing_path.read_text(encoding="utf-8"), cwd=workspace,
                    system_prompt=(
                        "Execute this isolated ZaoFu task, including its required artifact reads "
                        "and exact Success CLI command. Do not merely describe the steps. "
                        f"All task writes must stay under {project}. Never edit the ZaoFu source "
                        "checkout, access credentials other than the operation credential-file "
                        "used by the CLI, access remote services, push git refs, or delegate. "
                        "Do not write canonical runtime state directly. Only author the permitted "
                        "task file, result scratch and evidence artifacts; use ZaoFu CLI for state. "
                        "Save real command stdout/exit code evidence, do not invent receipts. "
                        "The transport already supplies role credentials via environment file paths. "
                        "Never print env or credential contents, never search for credentials. "
                        "If a capability is missing or denied, stop and report it immediately. "
                        "Use Write/Edit for artifacts, not shell scripts or Python. "
                        "Bash permits only the printed zf CLI, git and the exact test command. "
                        "Author the complete result with a single Write where practical; do not "
                        "spend a separate turn editing each scalar. No post-authoring audit. "
                        "Finish after the result submit command succeeds."
                    ),
                    thread_id=str(uuid.uuid4()), provider_session_id="",
                    on_session_id=sessions.append, on_message=messages.append,
                    timeout_s=360, thinking_level="low", permission_profile="isolated_writer",
                )
                record_property(f"provider_{stage}", json.dumps(redact_obj({
                    "ok": result.ok, "status": result.status, "error": result.error,
                    "provider_session_id": result.provider_session_id,
                    "reply": result.reply, "usage": result.usage,
                    "messages": [asdict(message) for message in messages],
                }), ensure_ascii=False, default=str))
                assert result.ok and not result.incomplete, (result.status, result.error, result.reply)
                assert result.provider_session_id and sessions
                assert any(message.tool for message in messages), "real worker must execute tools"
                provider_sessions.append(result.provider_session_id)
                # Only a durable admitted result counts; provider prose cannot finish a task.
                operation = reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]
                assert operation["status"] == "settled", (operation["status"], result.reply)
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
            assert len(set(provider_sessions)) == 2, "verify must be independent of impl session"
            events = runtime.event_log.read_all()
            [candidate] = [e for e in events if e.type == "candidate.ready"]
            assert _git(project, "show", f"{candidate.payload['commit']}:README.md") == marker
            assert subprocess.check_output(
                ["git", "show", f"{candidate.payload['commit']}:README.md"], cwd=project, timeout=10,
            ) == (marker + "\n").encode()
            assert runtime.task_store.get(task.id).status == "done"
            [implementation] = [e for e in events if e.type == "dev.build.done"]
            control = hydrate_sidecar_ref(state, implementation.payload["control_result_ref"]).payload
            assert control["changed_files"] == ["README.md"]
            assert control["self_check"]["command_receipts"]
            for _ in range(2):
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
            assert len(transport.contexts) == 2
            assert sum(e.type == "candidate.ready" for e in runtime.event_log.read_all()) == 1
            record_property("candidate", json.dumps({
                "commit": candidate.payload["commit"], "marker": marker,
                "dispatches": len(transport.contexts), "provider_sessions": provider_sessions,
            }))
        finally:
            runtime.event_writer.append(ZfEvent(
                type="simulation.done", actor="testkit", payload={"reason": "real-task-test-finished"},
            ))
            assert _direct_claude_children() == before_children
