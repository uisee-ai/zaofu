"""Resident and loop boundaries consult the same scoped terminal evidence."""
import subprocess
from pathlib import Path

import pytest

from zf.autoresearch.child_supervision import child_wait
from zf.autoresearch.loop_requests import build_loop_request_payload
from zf.autoresearch.resident import run_resident_once
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.session import SessionStore


def project(root, policy="preserve"):
    root.mkdir(exist_ok=True)
    (root / "zf.yaml").write_text(
        "project:\n  name: diagnostics\n  state_dir: state\nroles: []\n"
        f"runtime:\n  autoresearch_resident:\n    on_parent_terminal: {policy}\n"
    )
    state = root / "state"
    SessionStore(state / "session.yaml").create(str(root))
    return state, EventWriter(EventLog(state / "events.jsonl"))


def loop_request(writer, run):
    payload = build_loop_request_payload(
        {"request_id": f"diag-{run}", "run_id": run}, source_event_id="incident",
    )
    return writer.emit("autoresearch.loop.requested", payload=payload, correlation_id=run)


def completed(writer, run):
    return writer.append(ZfEvent(type="run.goal.completed", origin="kernel",
        payload={"workflow_run_id":run, "run_id":run}, correlation_id=run))


@pytest.mark.parametrize("policy,launched", [("preserve", ["diag-b"]), ("cancel", ["diag-b"]), ("continue", ["diag-a", "diag-b"])])
def test_resident_runs_only_allowed_work_from_same_watcher(tmp_path, monkeypatch, policy, launched):
    state, writer = project(tmp_path, policy)
    monkeypatch.chdir(tmp_path)
    loop_request(writer, "a")
    loop_request(writer, "b")
    terminal = completed(writer, "a")
    calls = []
    def runner(command, **kw):
        calls.append(Path(command[command.index("--worktree")+1]).name)
        return subprocess.CompletedProcess(command, 0, "", "")
    kwargs = dict(state_dir=state, worktree_root=tmp_path/"worktrees", output_root=tmp_path/"out",
        execute=True, env={"ZF_AUTORESEARCH_RESIDENT":"authorized"},runner=runner)
    run_resident_once(**kwargs)
    run_resident_once(**kwargs)
    assert calls == launched
    skipped = [e for e in writer.event_log.read_all() if e.type=="autoresearch.loop.skipped"]
    if policy == "continue":
        assert not skipped
    else:
        assert len(skipped)==1 and skipped[0].causation_id==terminal.id
        assert skipped[0].payload["loop_request_id"]=="diag-a"


def test_controlled_child_cancel_survives_old_empty_origin_but_rejects_unbound_receipt(tmp_path):
    _, writer = project(tmp_path)
    writer.append(ZfEvent(type="run.goal.started", origin="kernel", payload={"run_id":"a"}))
    cancel = writer.emit("control.action.requested", actor="zf-cli", correlation_id="a",
        payload={"action":"run-cancel","request":{"run_id":"a"}})
    payload = {"run_id":"a","workflow_run_id":"a","request_id":"a","source_event_id":cancel.id}
    writer.append(ZfEvent(type="run.cancelled", actor="zf-cli", causation_id=cancel.id,
        correlation_id="a", payload=payload))
    assert not child_wait(tmp_path)
    writer.emit("control.action.completed", actor="zf-cli", correlation_id="a",
        causation_id=cancel.id,payload={"action":"run-cancel","result":{"event_id":"wrong"}})
    assert not child_wait(tmp_path)
    writer.emit("runtime.action.completed", actor="zf-cli", correlation_id="a",
        causation_id=cancel.id, payload={"action":"run-cancel","run_id":"a","status":"cancelled"})
    assert not child_wait(tmp_path)
    receipt = next(e for e in writer.event_log.read_all() if e.type=="run.cancelled")
    writer.emit("runtime.action.completed", actor="zf-cli", correlation_id="a",
        causation_id=receipt.id, payload={"action":"run-cancel","run_id":"a","status":"cancelled"})
    assert child_wait(tmp_path)["child_event_id"] == receipt.id


def test_cli_cancellation_reaches_child_supervisor(tmp_path, monkeypatch):
    from zf.cli.main import main
    _, writer = project(tmp_path)
    monkeypatch.chdir(tmp_path)
    writer.append(ZfEvent(type="run.admission.admitted", origin="kernel", actor="orchestrator",
        correlation_id="a", payload={"run_id":"a", "workflow_run_id":"a","request_id":"request-a"}))
    writer.append(ZfEvent(type="run.goal.started", origin="kernel", payload={"run_id":"a"}))
    assert main(["runs","cancel","a","--reason","source incident closed"]) == 0
    rows = writer.event_log.read_all()
    cancel = next(e for e in rows if e.type == "run.cancelled")
    assert cancel.origin == "kernel" and cancel.causation_id == cancel.payload["source_event_id"]
    assert child_wait(tmp_path)["child_event_id"] == cancel.id


@pytest.mark.parametrize("policy", ["cancel", "preserve"])
def test_running_diagnosis_archives_only_partial_evidence_and_keeps_peer(tmp_path, monkeypatch, policy):
    from zf.autoresearch import child_supervision
    from zf.autoresearch.diagnosis_disposition import parent_disposition
    state, writer = project(tmp_path, policy)
    monkeypatch.chdir(tmp_path)
    loop_request(writer, "a")
    loop_request(writer, "b")
    def during_run(command, *, worktree, parent_stop, **kw):
        if worktree.name == "diag-b":
            assert not parent_stop()
            return subprocess.CompletedProcess(command, 0, "", ""), {}
        assert not parent_stop()
        _, child_writer = project(worktree)
        child_writer.emit("run.goal.started", payload={"run_id":"child-a"})
        terminal = completed(writer, "a")
        stopped = parent_stop()
        assert stopped["parent_event_id"] == terminal.id
        return subprocess.CompletedProcess(command, 143, "", ""), stopped
    worktrees = tmp_path/"worktrees"
    worktrees.mkdir()
    monkeypatch.setattr(child_supervision, "run_supervised", during_run)
    monkeypatch.setattr("zf.autoresearch.runtime_cleanup.stop_experiment_runtime",
        lambda *a, **kw: {"status":"stopped"})
    run_resident_once(state_dir=state, worktree_root=worktrees, output_root=tmp_path/"out",
        execute=True, env={"ZF_AUTORESEARCH_RESIDENT":"authorized"})
    skipped = next(e for e in writer.event_log.read_all() if e.type=="autoresearch.loop.skipped")
    assert skipped.payload["disposition"] == ("preserved" if policy=="preserve" else "cancelled")
    assert skipped.payload["loop_request_id"]=="diag-a"
    assert skipped.payload["worktree_lifecycle"]["archive"]["status"]=="archived"
    assert skipped.payload["worktree_lifecycle"]["archive"]["reason"]=="partial_diagnosis_archived"
    assert Path(skipped.payload["archive_refs"]["manifest"]).exists()
    assert "loop_report" not in skipped.payload["archive_refs"] and "journal" not in skipped.payload["archive_refs"]
    assert not parent_disposition(writer.event_log.read_all(),run_id="b",request_id="diag-b",policy=policy)


def test_outer_cli_refuses_extra_reflection_and_iteration_after_cancellation(tmp_path, monkeypatch):
    import json
    from zf.cli.main import main
    from zf.cli import autoresearch as cli
    state, writer = project(tmp_path)
    monkeypatch.chdir(tmp_path)
    loop_request(writer, "a")
    inner_calls = []
    def inner(**kw):
        inner_calls.append(1)
        completed(writer,"a")
        return {"status":"failed","tasks_done":0}
    monkeypatch.setattr(cli,"_real_autoresearch_fn",inner)
    monkeypatch.setattr(cli,"invoke_reflection_llm",lambda *a, **kw: pytest.fail("cancelled diagnostic reflected"))
    monkeypatch.setattr(cli,"_real_eval_collector",lambda *a, **kw: pytest.fail("cancelled diagnostic consumed eval"))
    output=tmp_path/"output"
    seed=json.dumps({"run_id":"a","diagnostic_request_id":"diag-a","source_state_dir":str(state)})
    assert main(["autoresearch","loop","--scenarios","incident-reproduction","--worktree",str(tmp_path/"child"),
        "--parent-state-dir",str(state),"--max-iterations","2","--output-dir",str(output),
        "--seed-text",seed]) == 1
    assert len(inner_calls)==1 and "preserved" in (output/"report.md").read_text()
    assert not (output/"journal.jsonl").exists()


def test_diagnostic_controller_cleans_only_its_group(tmp_path):
    import os
    import signal
    import sys
    import time
    from zf.autoresearch import diagnosis_process

    ready = tmp_path / "nested.pid"
    child = ("import pathlib, subprocess, time, sys; "
             "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
             "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as peer:
        try:
            with subprocess.Popen([sys.executable, "-I", diagnosis_process.__file__, str(os.getpid()),
                     sys.executable, "-c", child, str(ready)], start_new_session=True) as boundary:
                try:
                    deadline = time.monotonic() + 4
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    nested = int(ready.read_text())
                    boundary.terminate()
                    boundary.wait(timeout=4)
                    stat = Path(f"/proc/{nested}/stat")
                    assert not stat.exists() or stat.read_text().rsplit(")",1)[1].split()[0] == "Z"
                    assert peer.poll() is None
                finally:
                    if boundary.poll() is None:
                        os.killpg(boundary.pid, signal.SIGKILL)
        finally:
            peer.terminate()
