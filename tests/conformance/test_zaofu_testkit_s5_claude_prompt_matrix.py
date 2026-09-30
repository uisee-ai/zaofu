"""Claude readiness blocker diagnostics and role-isolation matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from tests.test_role_lifecycle_runtime import RecordingTransport
from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _blocking_runtime_at,
    _submit_generation_stage,
)
from tests.test_task_pipeline_generation import _git
from tests.test_tmux_transport_lifecycle import _FakeTmux
from tests.conformance.test_zaofu_testkit_s5_claude_readiness import (
    ONBOARDING_SCREEN,
    TRUST_SCREEN,
)
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.backend import ClaudeCodeAdapter
from zf.runtime.provider_interactive_prompt import provider_interactive_prompt_marker
from zf.runtime.tmux import TmuxError, TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential


class ScreenTmux(_FakeTmux):
    """Replace pane I/O only; use the production readiness path."""

    wait_for_prompt = TmuxSession.wait_for_prompt

    def __init__(self, screen: str) -> None:
        super().__init__(command="claude")
        self.screen = screen

    def capture_pane(self, role_name: str, *, lines: int = 120) -> str:
        return self.screen


BLOCKERS = [
    pytest.param(
        "Hit your usage limit. Try again later.",
        "usage_limit_reached",
        "provider_quota_interactive",
        id="quota",
    ),
    pytest.param(
        "Paste code here if prompted >",
        "login_required",
        "provider_auth_interactive",
        id="oauth-code",
    ),
    pytest.param(
        "OAuth error: Invalid code",
        "login_required",
        "provider_auth_interactive",
        id="oauth-error",
    ),
    pytest.param(
        "Please sign in to continue",
        "login_required",
        "provider_auth_interactive",
        id="sign-in",
    ),
    pytest.param(
        "Session expired", "login_required", "provider_auth_interactive", id="expired"
    ),
    pytest.param(
        "Please log in", "login_required", "provider_auth_interactive", id="log-in"
    ),
    pytest.param(
        "Contains shell syntax ($) that cannot be statically analyzed\nDo you want to proceed?",
        "permission_confirmation",
        "provider_interactive_blocked",
        id="permission",
    ),
    pytest.param(
        "Do you trust the files in this folder?",
        "trust_prompt",
        "provider_trust_interactive",
        id="legacy-trust",
    ),
    pytest.param(
        TRUST_SCREEN, "trust_prompt", "provider_trust_interactive", id="current-trust",
    ),
    pytest.param(
        ONBOARDING_SCREEN, "onboarding_prompt", "provider_interactive_blocked", id="onboarding",
    ),
    pytest.param(
        "This command requires approval\n\nDo you want to proceed?\n❯ 1. Yes",
        "permission_confirmation",
        "provider_interactive_blocked",
        id="permission-generic-command",
    ),
]


@pytest.mark.parametrize("screen,marker,failure_class", BLOCKERS)
def test_blocker_diagnostics_and_retry_are_scoped_to_one_role(
    screen: str, marker: str, failure_class: str
) -> None:
    tmux = ScreenTmux("❯ stale prompt\n" + screen)
    transport = TmuxTransport(tmux)
    for instance in ("impl-1", "impl-2"):
        transport._launch_records[instance] = {
            "backend": "claude-code",
            "launch_attempts": 1,
        }
    assert provider_interactive_prompt_marker(screen) == marker
    pattern = ClaudeCodeAdapter().ready_pattern
    for instance in ("impl-1", "impl-2"):
        assert transport.wait_ready(instance, pattern, timeout=0.1) is False
        diagnostics = transport.readiness_diagnostics(instance)
        assert diagnostics["failure_class"] == failure_class
        assert diagnostics["interactive_prompt_marker"] == marker
        assert diagnostics["launch_attempts"] == 1
        assert screen.strip() in diagnostics["last_screen_excerpt"]
    assert not tmux.sent
    second_failure = transport.readiness_diagnostics("impl-2")
    tmux.screen = "❯ Ready for a task"
    assert transport.wait_ready("impl-1", pattern, timeout=0.1) is True
    assert "failure_class" not in transport.readiness_diagnostics("impl-1")
    assert transport.readiness_diagnostics("impl-2") == second_failure
    assert not tmux.sent


@pytest.mark.parametrize(
    "screen",
    [
        "❯ Ready for a task",
        "You have 4 usage limit resets available. Run /usage to use one.\n❯",
        "Security notes: read the documentation.\n❯",
        "This command requires approval in other environments.\n❯",
        "Do you want to proceed?\n❯",
    ],
    ids=["ready", "quota-banner", "nonblocking-notes", "approval-note", "question-only"],
)
def test_nonblocking_banner_is_not_a_readiness_failure(screen: str) -> None:
    tmux = ScreenTmux(screen)
    transport = TmuxTransport(tmux)
    assert provider_interactive_prompt_marker(screen) == ""
    assert transport.wait_ready(
        "impl-1", ClaudeCodeAdapter().ready_pattern, timeout=0.1
    )
    assert not tmux.sent


@pytest.mark.parametrize(
    "screen,marker,failure_class",
    [BLOCKERS[0], BLOCKERS[3], BLOCKERS[6], BLOCKERS[7], BLOCKERS[8], BLOCKERS[9], BLOCKERS[10]],
)
def test_on_demand_blocker_cleanup_and_retry_keep_session_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    screen: str,
    marker: str,
    failure_class: str,
) -> None:
    provider_home = tmp_path / "provider-home"
    provider_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: provider_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(provider_home))
    runtime, role, physical = _runtime(tmp_path)
    tmux = ScreenTmux("❯ stale prompt\n" + screen)
    readiness = TmuxTransport(tmux)
    readiness._launch_records[role.instance_id] = {
        "backend": "claude-code",
        "launch_attempts": 1,
    }
    monkeypatch.setattr(physical, "wait_ready", readiness.wait_ready)
    monkeypatch.setattr(
        physical, "readiness_diagnostics", readiness.readiness_diagnostics
    )
    # Only physical spawn/send are recorded; lifecycle, SpawnCoordinator,
    # readiness, RoleSessionRegistry and EventWriter use production code.
    with pytest.raises(TmuxError, match=f"failure_class={failure_class}"):
        runtime._ensure_role_active(role, task_id="TASK-BLOCKED")
    assert physical.terminated == [role.instance_id]
    assert physical.alive == set()
    assert physical.sent == []
    events = runtime.event_log.read_all()
    [failed] = [e for e in events if e.type == "role.lifecycle.activation.failed"]
    assert failed.task_id == "TASK-BLOCKED"
    assert failed.payload["failure_class"] == failure_class
    # Event diagnostics expose the stable failure_class, not the raw marker.
    assert (
        readiness.readiness_diagnostics(role.instance_id)["interactive_prompt_marker"]
        == marker
    )
    assert not any(e.type == "role.lifecycle.ready" for e in events)
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    session_id = registry.get(role.instance_id)
    assert session_id is not None
    assert registry.instance_meta()[role.instance_id]["lifecycle_state"] == "dormant"

    # Externally cleared screen, not an automatic trust/login confirmation.
    tmux.screen = "❯ Ready for a task"
    assert runtime._ensure_role_active(role, task_id="TASK-RETRY") is True
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    assert registry.get(role.instance_id) == session_id
    assert registry.instance_meta()[role.instance_id]["lifecycle_state"] == "active"
    assert registry.instance_meta()[role.instance_id]["lifecycle_last_error"] == ""
    assert len(physical.spawned) == 2
    for _, argv, _ in physical.spawned:
        assert "--resume" not in argv
        assert argv[argv.index("--session-id") + 1] == str(session_id)
    [ready] = [
        e for e in runtime.event_log.read_all() if e.type == "role.lifecycle.ready"
    ]
    assert ready.task_id == "TASK-RETRY"
    assert physical.sent == []  # Activation is not task delivery.
    assert not tmux.sent
    assert runtime._ensure_role_active(role, task_id="TASK-RETRY") is False
    assert len(physical.spawned) == 2
    assert (
        len(
            [
                e
                for e in runtime.event_log.read_all()
                if e.type == "role.lifecycle.ready"
            ]
        )
        == 1
    )


def test_trust_blocker_fails_closed_before_task_pipeline_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property,
) -> None:
    """Classify the actual trust screen, reject delivery, then retry to candidate.

    Pane I/O and worker semantic results remain fixtures, not real providers.
    """
    provider_home = tmp_path / "provider-home"
    provider_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: provider_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(provider_home))
    original_activation = Orchestrator._ensure_role_active
    runtime, _inert, _admission = _blocking_runtime_at(
        tmp_path / "project", monkeypatch,
    )
    monkeypatch.setattr(Orchestrator, "_ensure_role_active", original_activation)
    role = runtime.config.roles[0]
    role.backend = "claude-code"
    role.permission_mode = "allowlist"
    role.allowed_tools = ["Read"]
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="claude-code", role_kind="reader",
        permission_mode="allowlist", allowed_tools=["Read"],
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
        "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {instance: provision_role_submit_credential(runtime.state_dir, instance)
              for instance in ("impl-1", "verify-1")}
    physical = RecordingTransport()
    tmux = ScreenTmux(TRUST_SCREEN)
    readiness = TmuxTransport(tmux)
    monkeypatch.setattr(physical, "wait_ready", readiness.wait_ready)
    monkeypatch.setattr(physical, "readiness_diagnostics", readiness.readiness_diagnostics)
    runtime.transport = physical

    decisions = runtime._dispatch_ready()
    events = runtime.event_log.read_all()
    record_property("trust_fail_closed", json.dumps({
        "decision_count": len(decisions),
        "sent": list(physical.sent),
        "spawned": len(physical.spawned),
        "deferred": [
            event.payload.get("reason", "")
            for event in events
            if event.type == "task.pipeline.stage.dispatch_deferred"
        ],
    }))
    assert len(physical.spawned) == 1
    assert physical.terminated == [role.instance_id]
    assert physical.alive == set()
    assert physical.sent == []
    assert runtime.task_store.get("TASK-BLOCKING").status == "backlog"
    assert not any(event.type == "task.pipeline.stage.dispatched" for event in events)
    assert not any(event.type == "task.attempt.sent" for event in events)
    assert not any(event.type == "candidate.ready" for event in events)
    assert any(
        event.type == "task.pipeline.stage.dispatch_deferred"
        and "provider_trust_interactive" in str(event.payload.get("reason"))
        for event in events
    )
    assert any(
        event.type == "role.lifecycle.activation.failed"
        and event.payload.get("failure_class") == "provider_trust_interactive"
        for event in events
    )
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    assert store.current_rows() == []
    assert not any(event.type == "workflow.operation.requested" for event in events)

    # External confirmation is Given; never automatically send trust keystrokes.
    assert not tmux.sent
    tmux.screen = "❯ Ready for a task"
    original_send = physical.send_task

    def record_send(instance, briefing, prompt, *, context=None):
        original_send(instance, briefing, prompt, context=context)
        _inert.send_task(instance, briefing, prompt, context=context)

    monkeypatch.setattr(physical, "send_task", record_send)
    try:
        for stage in ("impl", "verify"):
            runtime._react_to_events()
            assert any(d.action == "task_pipeline_dispatch" for d in runtime._dispatch_ready())
            context = _inert.contexts[-1]
            assert context.task_pipeline_stage == stage
            _submit_generation_stage(
                runtime, context, tokens[context.instance_id], repair=stage == "impl",
            )
        runtime._react_to_events()
        runtime._dispatch_ready()
        assert runtime.task_store.get(task.id).status == "done"
        [candidate] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
        assert _git(runtime.project_root, "show", f"{candidate.payload['commit']}:README.md") == "K462 repaired"
        for _ in range(2):
            runtime = Orchestrator(runtime.state_dir, runtime.config, physical, project_root=runtime.project_root)
            runtime._react_to_events()
            assert runtime._dispatch_ready() == []
        assert len(_inert.contexts) == 2
        assert len(store.current_rows()) == 2
        assert sum(e.type == "candidate.ready" for e in runtime.event_log.read_all()) == 1
        record_property("trust_retry_candidate", json.dumps({
            "readiness": "production classifier on trust then ready screen",
            "provider": "recording transport",
            "stages": [c.task_pipeline_stage for c in _inert.contexts],
            "candidate_commit": candidate.payload["commit"], "attempt_count": 2,
        }))
    finally:
        for instance in tuple(physical.alive):
            physical.terminate(instance)
