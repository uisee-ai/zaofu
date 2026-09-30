"""Deterministic bridge lifecycle and cross-process Issue worker regression."""
from dataclasses import replace
import subprocess
import select
import shutil
import sys
import threading
from types import SimpleNamespace

import pytest
from issue_core import AgentResult, SessionStatus

from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import ZfConfig, SelfIssueConfig
from zf.core.events.factory import event_log_from_project
from zf.core.state.locks import FileLock
from zf.integrations.feishu.bridge_watch import BridgeWatch
from zf.integrations.feishu.self_issue_lifecycle import FeishuIssueLifecycle
from zf.runtime import self_issue_session_execution as execution
from zf.runtime.self_issue_session_host import SelfIssueSessionHost
from zf.web.headless_agent import HeadlessThreadStore


class BlockingAgent:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.checkpoints = []

    def run(self, *, session, working_set, tools):
        self.calls += 1
        self.checkpoints.append(working_set)
        self.started.set()
        assert self.release.wait(10), "test did not release mock worker"
        return AgentResult(status="ready", working_set=replace(working_set, report={"title": "late report"}))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    context = ProjectContext(tmp_path, tmp_path / "zf.yaml", ZfConfig(self_issue=SelfIssueConfig(enabled=True)),
                             tmp_path / "custom-state")
    agent = BlockingAgent()
    monkeypatch.setattr("zf.runtime.control_actions_self_issue.configured_structure_backend", lambda _: "mock")
    monkeypatch.setattr("zf.runtime.control_actions_self_issue.HeadlessIssueSessionAgent", lambda **_: agent)
    lifecycle = FeishuIssueLifecycle()
    lifecycle.add_context(context)
    host = SelfIssueSessionHost(context.state_dir, tmp_path, agent=agent, execution_owner="feishu")
    yield context, host, agent, lifecycle
    agent.release.set()
    runs = execution.stop_owner("feishu")
    execution.join_runs(runs, timeout=5)
    execution.open_owner("feishu")


def _orphan(host, *, status=SessionStatus.INVESTIGATING, owner="feishu", resume=False):
    session = host.store.create("orphan")
    working = host.store.load_working_set(session.session_id)
    host.store.save_working_set(replace(working, workflow={
        "phase": "investigation", "execution_owner": owner, "resume_after_restart": resume,
        "activity_log": [{"id": "saved-evidence", "command": "rg httpx", "status": "completed"}],
    }), expected_revision=working.revision)
    updates = {"status": status}
    if status is SessionStatus.COMPLETED:
        updates["final_report_ref"] = "artifacts/self-issues/orphan/report.json"
    host.store.save_session(replace(session, **updates), expected_revision=session.revision)
    return session.session_id


def test_bridge_shutdown_checkpoints_then_restart_recovers_once(setup, monkeypatch):
    context, host, agent, lifecycle = setup
    sid = host.create_and_schedule("pending")["session_id"]
    assert agent.started.wait(5)
    host._progress(sid, "investigation", "saved", detail={"kind": "tool_result", "result_detail": "evidence"})
    activity = host.store.load_working_set(sid).workflow["activity_log"][0]
    cancelled_keys = []

    def cancel(key):
        assert host.store.load_session(sid).status == SessionStatus.PAUSED
        cancelled_keys.append(key)
        agent.release.set()

    monkeypatch.setattr("zf.web.agent_session_runtime.cancel_agent_session_run", cancel)
    bridge = BridgeWatch(context, None, issue_lifecycle=lifecycle)
    bridge.shutdown()
    assert cancelled_keys and cancelled_keys[0].thread_id == f"self-issue-session:{sid}"
    paused = host.get(sid)
    assert paused["session_status"] == "paused" and paused["workflow"]["resume_after_restart"] is True
    assert paused["workflow"]["interruption_source"] == "system"
    assert host.activity_result(sid, activity["id"])["body"] == "evidence"
    assert "late report" not in str(paused["report"])
    assert not execution.running(host, sid)
    with pytest.raises(ValueError, match="正在关闭"):
        host._schedule(sid)

    agent.started.clear()
    agent.release.clear()
    restarted = FeishuIssueLifecycle()
    restarted.add_context(context)
    restarted.start()
    assert agent.started.wait(5)
    restarted.start()
    assert agent.calls == 2
    assert agent.checkpoints[-1].workflow["activity_log"][0]["id"] == activity["id"]
    resumed = host.get(sid)
    assert resumed["workflow"]["resume_after_restart"] is False
    assert resumed["workflow"]["interruption_source"] == ""
    events = event_log_from_project(context.state_dir).read_all()
    actions = {event.payload.get("action") for event in events if event.type == "feishu.issue.command.received"}
    assert {"self-issue-session-pause", "self-issue-session-recover"} <= actions


def test_startup_recovers_crash_orphan_and_keeps_checkpoint(setup):
    _, host, agent, lifecycle = setup
    sid = _orphan(host)
    before = host.store.load_session(sid).revision
    lifecycle.start()
    assert agent.started.wait(5)
    lifecycle.start()
    assert agent.calls == 1
    assert host.store.load_session(sid).revision == before + 1
    assert agent.checkpoints[0].workflow["activity_log"][0]["id"] == "saved-evidence"


@pytest.mark.parametrize("status,owner,resume", [
    (SessionStatus.PAUSED, "feishu", False), (SessionStatus.FAILED, "feishu", True),
    (SessionStatus.CANCELLED, "feishu", True), (SessionStatus.COMPLETED, "feishu", True),
    (SessionStatus.READY_FOR_CONFIRMATION, "feishu", True), (SessionStatus.WAITING_FOR_USER, "feishu", True),
    (SessionStatus.INVESTIGATING, "web", False), (SessionStatus.INVESTIGATING, "", False),
])
def test_startup_never_resumes_other_owners_or_operator_gates(setup, status, owner, resume):
    _, host, agent, lifecycle = setup
    sid = _orphan(host, status=status, owner=owner, resume=resume)
    before = host.get(sid)
    lifecycle.start()
    assert host.get(sid) == before
    assert agent.calls == 0


def test_user_pause_overrides_shutdown_resume_flag(setup):
    _, host, agent, lifecycle = setup
    sid = _orphan(host, status=SessionStatus.PAUSED, resume=True)
    host.pause_and_resume_later(sid)
    lifecycle.start()
    assert host.get(sid)["workflow"]["resume_after_restart"] is False
    assert not agent.started.is_set()


def test_foreign_process_execution_lease_prevents_duplicate_dispatch(setup):
    _, host, agent, lifecycle = setup
    sid = _orphan(host)
    lock = host.store.root / f"{sid}.execution.lock"
    child = subprocess.Popen([sys.executable, "-c",
        "import fcntl,sys; f=open(sys.argv[1], 'a+'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('ready',flush=True); sys.stdin.read()", str(lock)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "lock holder did not start"
        assert child.stdout.readline().strip() == "ready"
        before = host.get(sid)
        lifecycle.start()
        assert agent.calls == 0 and host.get(sid) == before
    finally:
        child.communicate(timeout=5)
    lifecycle.start()
    assert agent.started.wait(5)


def test_pre_upgrade_provider_lock_and_pinned_root_prevent_takeover(setup, tmp_path):
    context, host, agent, lifecycle = setup
    sid = _orphan(host)
    pinned = tmp_path / "pinned-source"
    pinned.mkdir()
    session = host.store.load_session(sid)
    host.store.save_session(replace(session, source_binding={"repository_root": str(pinned)}),
                            expected_revision=session.revision)
    threads = HeadlessThreadStore(state_dir=context.state_dir, project_root=pinned)
    tid = threads.thread_id(scope="project", thread_key=f"self-issue-session:{sid}")
    with FileLock(threads.thread_dir / f"{tid}.lock"):
        lifecycle.start()
        assert agent.calls == 0
    lifecycle.start()
    assert agent.started.wait(5)
    assert agent.calls == 1


def test_shutdown_fence_rejects_before_creating_or_changing_sessions(setup):
    _, host, _, lifecycle = setup
    waiting = _orphan(host, status=SessionStatus.WAITING_FOR_USER)
    ready = _orphan(host, status=SessionStatus.READY_FOR_CONFIRMATION)
    snapshots = {sid: host.get(sid) for sid in (waiting, ready)}
    lifecycle.stop_accepting()
    for operation in (
        lambda: host.create_and_schedule("arrived during shutdown"),
        lambda: host.answer_and_schedule(waiting, "answer"),
        lambda: host.reanalyze_and_schedule(ready, ["summary"], "check"),
    ):
        with pytest.raises(ValueError, match="正在关闭"):
            operation()
    assert len(list(host.store.root.glob("*.session.json"))) == 2
    assert {sid: host.get(sid) for sid in snapshots} == snapshots


def test_bounded_shutdown_preserves_pause_until_late_worker_exits(setup):
    _, host, agent, lifecycle = setup
    sid = host.create_and_schedule("pending")['session_id']
    assert agent.started.wait(5)
    assert lifecycle.shutdown(timeout=0) == [sid]
    assert host.get(sid)["session_status"] == "paused"
    execution.open_owner("feishu")
    lifecycle.start()
    assert agent.calls == 1 and host.get(sid)["session_status"] == "paused"
    agent.release.set()
    assert execution.join_runs(execution.stop_owner("feishu"), timeout=5) == []
    assert host.get(sid)["session_status"] == "paused"


def test_start_failure_is_failed_instead_of_orphaned_running(setup, monkeypatch):
    _, host, _, _ = setup

    def fail(_):
        raise RuntimeError("cannot start test worker")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="cannot start"):
        host.create_and_schedule("pending")
    path, = host.store.root.glob("*.session.json")
    sid = path.name.removesuffix(".session.json")
    assert host.get(sid)["session_status"] == "failed"
    assert not execution.running(host, sid)


def test_system_shutdown_cannot_reclassify_failed_as_resumable(setup):
    _, host, agent, lifecycle = setup
    sid = _orphan(host, status=SessionStatus.FAILED)
    host.pause_and_resume_later(sid, host_shutdown=True)
    lifecycle.start()
    assert host.get(sid)["session_status"] == "failed"
    assert agent.calls == 0


def test_user_pause_wins_between_recovery_scan_and_dispatch(setup, monkeypatch):
    _, host, agent, lifecycle = setup
    sid = _orphan(host)
    action = lifecycle._action

    def pause_then_recover(context, session_id, operation):
        host.pause_and_resume_later(session_id)
        return action(context, session_id, operation)

    monkeypatch.setattr(lifecycle, "_action", pause_then_recover)
    lifecycle.start()
    assert host.get(sid)["session_status"] == "paused"
    assert host.get(sid)["workflow"]["resume_after_restart"] is False
    assert agent.calls == 0


def test_same_session_id_in_distinct_state_dirs_does_not_share_worker(setup, tmp_path):
    from issue_core import JsonIssueSessionStore

    context, host, agent, lifecycle = setup
    sid = _orphan(host)
    other_context = replace(context, state_dir=tmp_path / "other-state")
    other_store = JsonIssueSessionStore(other_context.state_dir / "self-issues/portable-sessions")
    other_store.root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(host.store._session_path(sid), other_store._session_path(sid))
    shutil.copy2(host.store._working_path(sid), other_store._working_path(sid))
    lifecycle.add_context(other_context)
    lifecycle.start()
    runs = execution.stop_owner("feishu")
    assert len(runs) == 2
    assert {run.host.state_dir for run in runs} == {context.state_dir, other_context.state_dir}
    assert {run.session_id for run in runs} == {sid}
    agent.release.set()


@pytest.mark.parametrize("catchup_fails", [False, True])
def test_watch_entrypoint_recovers_and_checkpoints_on_exit(setup, monkeypatch, catchup_fails):
    """Mock E2E: real watch entrypoint -> controlled action -> Store -> worker."""
    import zf.integrations.feishu.bridge_watch as watch

    context, host, agent, _ = setup
    sid = _orphan(host)
    released = []

    class Builder:
        def register_p2_im_message_receive_v1(self, callback):
            return self

        def register_p2_card_action_trigger(self, callback):
            return self

        def build(self):
            return self

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            assert agent.started.wait(5)
            raise KeyboardInterrupt

    monkeypatch.setitem(sys.modules, "lark_oapi", SimpleNamespace(
        EventDispatcherHandler=SimpleNamespace(builder=lambda *_: Builder()),
        ws=SimpleNamespace(Client=Client), LogLevel=SimpleNamespace(WARNING="warning"),
    ))
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr("zf.core.config.project_context.resolve_project_context", lambda **_: context)
    monkeypatch.setattr("zf.core.config.project_context.load_project_env", lambda *_: None)
    monkeypatch.setattr("zf.integrations.feishu.production.bridge_production_context_issues", lambda *_: [])
    monkeypatch.setattr("zf.integrations.feishu.single_instance.acquire_provider_ws_lock",
                        lambda _: SimpleNamespace(release=lambda: released.append(True)))
    monkeypatch.setattr("zf.integrations.feishu.single_instance.refresh_ws_lock_until_stopped",
                        lambda _, stop, __: stop.wait(10))
    monkeypatch.setattr("zf.integrations.feishu.transport.FeishuHttpTransport",
                        lambda: SimpleNamespace(bot_open_id=lambda: "bot_test"))
    monkeypatch.setattr(watch, "_configured_projection_targets", lambda *_: [])
    monkeypatch.setattr(watch.signal, "signal", lambda *_: None)

    def catchup(*_):
        assert agent.started.wait(5)
        if catchup_fails:
            raise RuntimeError("catchup failed")

    def cancel(_):
        assert host.get(sid)["session_status"] == "paused"
        agent.release.set()

    monkeypatch.setattr(watch, "_catchup_on_start", catchup)
    monkeypatch.setattr("zf.web.agent_session_runtime.cancel_agent_session_run", cancel)
    if catchup_fails:
        with pytest.raises(RuntimeError, match="catchup failed"):
            watch.run_bridge_watch(SimpleNamespace())
    else:
        assert watch.run_bridge_watch(SimpleNamespace()) == 0
    assert released == [True]
    assert host.get(sid)["session_status"] == "paused"
    assert host.get(sid)["workflow"]["resume_after_restart"] is True
    assert not execution.running(host, sid)
