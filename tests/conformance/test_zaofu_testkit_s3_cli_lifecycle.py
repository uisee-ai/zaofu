"""CLI stop/start/recover through candidate freeze; no real provider or tmux."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _blocking_runtime_at,
    _submit_generation_stage,
)
from tests.test_task_pipeline_generation import _git
from zf.cli.main import main
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.fixture
def blocking_runtime(monkeypatch):
    # Real --simulation validation requires a private /tmp/zf-* project.
    with TemporaryDirectory(prefix="zf-k469-cli-") as directory:
        project = Path(directory)
        project.rmdir()  # Empty owned directory; the Git fixture creates it.
        yield _blocking_runtime_at(project, monkeypatch)


@pytest.mark.parametrize("interrupted_stage", ["impl", "verify"])
@pytest.mark.parametrize("stop_mode", ["graceful", "fast"])
def test_cli_stop_start_recover_reaches_candidate(
    blocking_runtime, monkeypatch, capsys, interrupted_stage, stop_mode,
):
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.session.tmux_session = "zf-k469-isolated"
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    config.project.state_dir = "runtime-state"
    (project / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
    )
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
        "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    store = TaskAttemptStore(state / "task_attempts.json")

    # CLI dispatch uses the same recording worker, while startup/shutdown pane
    # effects are recorded by the production dry-run tmux adapter.
    cli_transport = TmuxTransport(TmuxSession(config.session.tmux_session, dry_run=True))
    monkeypatch.setattr(cli_transport, "send_task", transport.send_task)
    monkeypatch.setattr(cli_transport, "is_alive", lambda _role: False)
    for factory in ("zf.cli.start.make_transport", "zf.cli.stop.make_transport",
                    "zf.runtime.transport.make_transport"):
        monkeypatch.setattr(factory, lambda *a, **kw: cli_transport)

    def bounded_watcher(watcher, **_kwargs):
        # Startup catch-up has already executed the real run_once. Only the
        # indefinitely blocking watcher loop is bounded, not its callbacks.
        watcher.poll_once()
        watcher.stop()

    monkeypatch.setattr("zf.runtime.watcher.EventWatcher.run", bounded_watcher)
    monkeypatch.chdir(project)
    assert any(d.action == "task_pipeline_dispatch" for d in runtime.run_once())
    if interrupted_stage == "verify":
        _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
        runtime.run_once()
    first = transport.contexts[-1]
    assert first.task_pipeline_stage == interrupted_stage
    before_count = len(transport.contexts)
    stop_args = ["stop", "--include-run-manager"]
    if stop_mode == "fast":
        stop_args.append("--fast")
    assert main(stop_args) == 0
    assert SessionStore(state / "session.yaml").load().runtime_state == "stopped"
    assert reduce_workflow_operations(runtime.event_log.read_all())[first.operation_id]["status"] == "suspended"
    assert not (state / "loop.lock").exists()

    # Real foreground startup path, including boot reconciliation/catch-up.
    # The explicit flag excludes workflow inspection and static/environment
    # preflights. Config loading, run-contract checks and dispatch remain real.
    assert main(["start", "--simulation", "--skip-workflow-inspect"]) == 0
    assert not (state / "loop.lock").exists()
    assert len(transport.contexts) == before_count
    capsys.readouterr()
    assert main(["recover", "workflow", "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    [checkpoint] = inspected["projection"]["operation_checkpoints"]
    assert checkpoint["operation_id"] == first.operation_id
    assert checkpoint["safe_resume_action"] == "redrive_interrupted_task_pipeline_operation"
    recover_args = ["recover", "workflow", "--resume-pending", "--json",
                    "--checkpoint-id", checkpoint["checkpoint_id"]]
    before_dry = [e.id for e in runtime.event_log.read_all()]
    assert main([*recover_args, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 0
    assert [e.id for e in runtime.event_log.read_all()] == before_dry
    assert main(recover_args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 1
    runtime = Orchestrator(state, config, transport, project_root=project)
    assert any(d.action == "task_pipeline_dispatch" for d in runtime.run_once())
    second = transport.contexts[-1]
    assert len(transport.contexts) == before_count + 1
    assert second.operation_id == first.operation_id
    assert second.attempt_id != first.attempt_id
    assert second.dispatch_id != first.dispatch_id
    assert second.task_pipeline_stage == interrupted_stage
    assert store.get(first.attempt_id)["superseded_by"] == second.attempt_id
    assert main(recover_args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 0
    if interrupted_stage == "impl":
        _submit_generation_stage(runtime, second, tokens["impl-1"], repair=True)
        runtime.run_once()
    verify = transport.contexts[-1]
    assert verify.task_pipeline_stage == "verify"
    _submit_generation_stage(runtime, verify, tokens["verify-1"])
    runtime.run_once()
    assert runtime.task_store.get(task.id).status == "done"
    [ready] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 3
    assert not (project / ".zf").exists()
    assert not any(e.type in {"kernel.housekeeping.failed", "orchestrator.tick.failed"}
                   for e in runtime.event_log.read_all())
