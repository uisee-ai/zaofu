"""The mock executable must not act merely because a dispatch exists."""

from io import StringIO
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tests.testkit.tmux_general_worker import main, observe_worker, run_worker
from tests.testkit.tmux_issue_worker import main as issue_main
from tests.testkit.tmux_prd_worker import main as prd_main
from tests.testkit.tmux_refactor_worker import main as refactor_main
from tests.testkit.tmux_refactor_strong_worker import main as refactor_strong_main
from tests.testkit.tmux_refactor_two_lane_worker import main as refactor_two_lane_main
from tests.testkit.prd_route import PrdRoute
from zf.core.events import EventLog, EventWriter, ZfEvent


def _receipt_worker(tmp_path, monkeypatch, synthesis=False):
    briefing = tmp_path / "briefings" / "耐久.md"
    briefing.parent.mkdir()
    briefing.write_text(("# Fanout Synth: fanout-test" if synthesis else
                        "Active task: TASK-DURABLE") + "\n- run_id: `run-durable`\n")
    dispatch = ZfEvent(
        type="fanout.synth.dispatched" if synthesis else "fanout.child.dispatched",
        actor="kernel", task_id="TASK-DURABLE", payload={
            "briefing_path": str(briefing), "role_instance": "worker",
            "stage_id": "scope", "run_id": "run-durable", "fanout_id": "fanout-test",
        },
    )
    path = tmp_path / "testkit-transport" / f"{dispatch.id}.json"
    path.parent.mkdir()
    path.write_text('{"previous": true}\n')
    completed = []

    def complete(event):
        assert json.loads(path.read_bytes())["dispatch_event_id"] == event.id
        assert "submission" not in json.loads(path.read_bytes())
        completed.append(event.id)
        return {"canonical_event_id": "fixture-result", "text": "完整结果" * 4096}

    worker = SimpleNamespace(state=tmp_path, role="worker", events=lambda _: [dispatch],
                             complete=complete)
    kind = "synthesis" if synthesis else "child"
    monkeypatch.setattr("sys.stdin", StringIO(
        f"Read the fanout {kind} briefing at {briefing}. Use its task contract.\n"))
    return worker, path, completed


@pytest.mark.parametrize("synthesis", [False, True])
def test_receipt_fsyncs_complete_bytes_before_each_publication(tmp_path, monkeypatch, synthesis):
    worker, path, completed = _receipt_worker(tmp_path, monkeypatch, synthesis)
    fsync, replace = os.fsync, os.replace
    synced, published = [], []

    def sync(fd):
        raw = Path(f"/proc/self/fd/{fd}").read_bytes()
        assert json.loads(raw)["run_id"] == "run-durable"
        fsync(fd)
        synced.append(raw)

    def publish(source, target):
        assert Path(target) == path
        raw = Path(source).read_bytes()
        assert len(synced) == len(published) + 1, "receipt was published without fsync"
        assert synced[-1] == raw
        published.append(raw)
        replace(source, target)

    # Limit syscall spies to the worker; pytest report writers run before
    # fixture teardown and must not be mistaken for receipt publications.
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        patch.setattr(os, "replace", publish)
        run_worker(worker)
    assert len(synced) == len(published) == 2
    assert len(completed) == 1
    assert "submission" not in json.loads(published[0])
    assert json.loads(path.read_bytes())["submission"]["text"] == "完整结果" * 4096


@pytest.mark.parametrize("error", [errno.EIO, errno.ENOSPC])
@pytest.mark.parametrize("failure_write", [1, 2])
def test_receipt_fsync_error_preserves_previous_publication(
    tmp_path, monkeypatch, error, failure_write,
):
    worker, path, completed = _receipt_worker(tmp_path, monkeypatch)
    old = path.read_bytes()
    fsync, calls = os.fsync, []

    def sync(fd):
        calls.append(path.read_bytes())
        if len(calls) == failure_write:
            raise OSError(error, "receipt sync failed")
        fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        with pytest.raises(OSError, match="receipt sync failed") as caught:
            run_worker(worker)
    assert caught.value.errno == error
    assert len(calls) == failure_write
    assert path.read_bytes() == calls[-1]
    assert len(completed) == failure_write - 1
    assert "submission" not in json.loads(path.read_bytes())
    if failure_write == 1:
        assert path.read_bytes() == old
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("stdin", ["", "ordinary chatter\n"])
@pytest.mark.parametrize("family", ["general", "issue", "prd", "refactor", "refactor-strong", "refactor-two-lane"])
def test_no_matching_stdin_means_no_semantic_submission(tmp_path: Path, monkeypatch, stdin: str, family: str):
    log = EventLog(tmp_path / "events.jsonl")
    EventWriter(log).append(ZfEvent(
        type="fanout.child.dispatched", actor="kernel",
        payload={"role_instance": "scoper", "stage_id": "scope"},
    ))
    before = (tmp_path / "events.jsonl").read_bytes()
    monkeypatch.setenv("ZF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "scoper")
    monkeypatch.setattr("sys.stdin", StringIO(stdin))
    monkeypatch.setattr("tests.testkit.tmux_issue_worker.git", lambda *_args: "fixture-main")

    def forbidden(*_args, **_kwargs):
        pytest.fail("worker must not construct a scheduler or submit without stdin delivery")

    monkeypatch.setattr("zf.runtime.orchestrator.Orchestrator.__init__", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    {"general": main, "issue": issue_main, "prd": prd_main, "refactor": refactor_main,
     "refactor-strong": refactor_strong_main, "refactor-two-lane": refactor_two_lane_main}[family]()
    assert (tmp_path / "events.jsonl").read_bytes() == before
    assert list((tmp_path / "testkit-transport").iterdir()) == []


@pytest.mark.parametrize("top_level", [True, False])
def test_delivered_briefing_binds_top_level_or_payload_task_id(tmp_path: Path, monkeypatch, top_level: bool):
    briefing = tmp_path / "briefings" / "worker.md"
    briefing.parent.mkdir()
    briefing.write_text("Active task: TASK-CHILD\n- run_id: `run-child`\n")
    dispatch = ZfEvent(
        type="fanout.child.dispatched", actor="kernel",
        task_id="TASK-CHILD" if top_level else None,
        payload={"briefing_path": str(briefing), "role_instance": "worker",
                 "stage_id": "impl", "task_id": "TASK-CHILD", "run_id": "run-child"},
    )
    completed = []

    def complete(event):
        completed.append(event.id)
        return {"canonical_event_id": "fixture-result"}

    worker = SimpleNamespace(state=tmp_path, role="worker", events=lambda _: [dispatch], complete=complete)
    prompt = f"Read the fanout child briefing at {briefing}. Use its task contract.\n"
    monkeypatch.setattr("sys.stdin", StringIO(prompt))
    run_worker(worker)
    assert completed == [dispatch.id]
    receipt = json.loads((tmp_path / "testkit-transport" / f"{dispatch.id}.json").read_text())
    assert receipt["dispatch_event_id"] == dispatch.id
    assert receipt["briefing_sha256"] == hashlib.sha256(briefing.read_bytes()).hexdigest()
    assert receipt["submission"] == {"canonical_event_id": "fixture-result"}


@pytest.mark.parametrize("wrong_run", [False, True])
def test_synthesis_stdin_binds_exact_dispatch_and_run(tmp_path: Path, monkeypatch, wrong_run: bool):
    briefing = tmp_path / "briefings" / "synth.md"
    briefing.parent.mkdir()
    briefing.write_text("# Fanout Synth: fanout-plan\n- run_id: `run-synth`\n")
    dispatch = ZfEvent(type="fanout.synth.dispatched", actor="kernel", payload={
        "briefing_path": str(briefing), "role_instance": "critic", "stage_id": "prd-plan",
        "fanout_id": "fanout-plan", "run_id": "wrong" if wrong_run else "run-synth",
    })
    completed = []
    worker = SimpleNamespace(state=tmp_path, role="critic",
        events=lambda kind: [dispatch] if kind == dispatch.type else [],
        complete=lambda event: completed.append(event.id))
    monkeypatch.setattr("sys.stdin", StringIO(
        f"Read the fanout synthesis briefing at {briefing}. Use the listed child reports.\n"))
    if wrong_run:
        times = iter((0.0, 16.0))
        monkeypatch.setattr("tests.testkit.tmux_general_worker.time.monotonic", lambda: next(times))
        with pytest.raises(AssertionError):
            run_worker(worker)
        assert not completed
    else:
        run_worker(worker)
        assert completed == [dispatch.id]
        receipt = json.loads((tmp_path / "testkit-transport" / f"{dispatch.id}.json").read_text())
        assert receipt["dispatch_type"] == "fanout.synth.dispatched"


@pytest.mark.parametrize("failure_at", ["initialization", "submission"])
def test_original_worker_failure_is_not_overwritten_by_replacement(tmp_path: Path, monkeypatch, failure_at: str):
    monkeypatch.setenv("ZF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "verify-lane-0")

    def fail(*_args):
        raise RuntimeError(f"first worker {failure_at} failure")

    monkeypatch.setattr("tests.testkit.tmux_general_worker.run_worker", fail)
    with pytest.raises(RuntimeError, match="first worker"):
        observe_worker(fail if failure_at == "initialization" else lambda: object())
    [original] = (tmp_path / "testkit-worker-processes").glob("*.json")
    before = original.read_bytes()
    record = json.loads(before)
    assert record["status"] == "failed" and record["error_type"] == "RuntimeError"
    assert failure_at in record["error"] and "RuntimeError" in record["traceback"]
    monkeypatch.setattr("tests.testkit.tmux_general_worker.run_worker", lambda _: None)
    observe_worker(lambda: object())
    assert original.read_bytes() == before
    records = [json.loads(path.read_text())
               for path in (tmp_path / "testkit-worker-processes").glob("*.json")]
    assert sorted(item["status"] for item in records) == ["failed", "stdin_eof"]


def test_sigkill_keeps_unrecorded_exit_distinct_from_eof(tmp_path: Path):
    """SIGKILL leaves an unrecorded exit, not EOF or proof of liveness."""
    repo = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": f"{repo}:{repo / 'src'}",
        "PYTHONUNBUFFERED": "1",
        "ZF_STATE_DIR": str(tmp_path),
        "ZF_ROLE_INSTANCE": "verify-lane-0",
    }
    code = "from tests.testkit.tmux_general_worker import main; main()"

    killed = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    original_path: Path | None = None
    try:
        deadline = time.monotonic() + 15
        process_dir = tmp_path / "testkit-worker-processes"
        while time.monotonic() < deadline:
            paths = list(process_dir.glob("*.json")) if process_dir.exists() else []
            if paths:
                original_path = paths[0]
                break
            assert killed.poll() is None, "worker exited before startup checkpoint"
            time.sleep(0.01)
        assert original_path is not None, "worker did not checkpoint startup"

        killed.kill()
        assert killed.wait(timeout=5) == -signal.SIGKILL
        original_bytes = original_path.read_bytes()
        original = json.loads(original_bytes)
        assert original["pid"] == killed.pid
        assert original["role"] == "verify-lane-0"
        assert original["started_at_ns"] > 0
        assert original["status"] == "running"
        assert "finished_at_ns" not in original
        assert "traceback" not in original
        assert "error_type" not in original

        replacement = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert replacement.returncode == 0, replacement.stderr
        assert original_path.read_bytes() == original_bytes
        records = [json.loads(path.read_text()) for path in process_dir.glob("*.json")]
        assert sorted(item["status"] for item in records) == ["running", "stdin_eof"]
        [new] = [item for item in records if item["status"] == "stdin_eof"]
        assert new["pid"] != killed.pid
        assert original["started_at_ns"] < new["started_at_ns"] <= new["finished_at_ns"]
    finally:
        if killed.poll() is None:
            killed.kill()
            killed.wait(timeout=5)
        killed.stdin.close()
        killed.stderr.close()


@pytest.mark.parametrize("mode", ["eof", "initialization", "briefing", "submission"])
def test_real_worker_exit_evidence_survives_replacement(tmp_path: Path, mode: str):
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": f"{repo}:{repo / 'src'}",
           "ZF_STATE_DIR": str(tmp_path), "ZF_ROLE_INSTANCE": "verify-lane-0"}
    env.pop("ZF_PROJECT_ROOT", None)  # Issue initialization must fail before git or CLI.
    module = "tmux_issue_worker" if mode == "initialization" else "tmux_general_worker"
    prompt = ""
    if mode in {"briefing", "submission"}:
        briefing = tmp_path / "briefings" / "missing-run.md"
        briefing.parent.mkdir()
        briefing.write_text("Active task: TASK-MISSING-RUN\n" + (
            "- run_id: `run-malformed-payload`\n" if mode == "submission" else ""
        ))
        prompt = f"Read the fanout child briefing at {briefing}. Use its task contract.\n"
    if mode == "submission":
        dispatch = ZfEvent(type="fanout.child.dispatched", actor="kernel",
            task_id="TASK-MISSING-RUN", payload={"briefing_path": str(briefing),
                "role_instance": "verify-lane-0", "stage_id": "scope",
                "run_id": "run-malformed-payload"})
        EventWriter(EventLog(tmp_path / "events.jsonl")).append(dispatch)
    log_path = tmp_path / "events.jsonl"
    original_events = log_path.read_bytes() if log_path.exists() else None
    first = subprocess.run(
        [sys.executable, "-m", f"tests.testkit.{module}"], cwd=repo, env=env,
        input=prompt, capture_output=True, text=True, timeout=15,
    )
    [path] = (tmp_path / "testkit-worker-processes").glob("*.json")
    before = path.read_bytes()
    record = json.loads(before)
    assert str(record["pid"]) == path.name.split("-", 1)[0]
    assert record["role"] == "verify-lane-0"
    assert 0 < record["started_at_ns"] <= record["finished_at_ns"]
    if mode == "eof":
        assert first.returncode == 0, first.stderr
        assert record["status"] == "stdin_eof" and "traceback" not in record
    else:
        assert first.returncode != 0
        expected = "AssertionError" if mode == "briefing" else "KeyError"
        message = {"initialization": "ZF_PROJECT_ROOT", "submission": "payload",
                   "briefing": "delivered briefing has no run identity"}[mode]
        assert record["status"] == "failed" and record["error_type"] == expected
        assert message in record["error"] and message in first.stderr
        assert expected in record["traceback"] and "Traceback" in record["traceback"]
        assert ("__init__" if mode == "initialization" else "run_worker") in record["traceback"]
    if mode == "submission":
        receipt = json.loads((tmp_path / "testkit-transport" / f"{dispatch.id}.json").read_bytes())
        assert receipt["pid"] == record["pid"]
        assert receipt["dispatch_event_id"] == dispatch.id and "submission" not in receipt
    replacement = subprocess.run(
        [sys.executable, "-m", "tests.testkit.tmux_general_worker"], cwd=repo, env=env,
        input="", capture_output=True, text=True, timeout=15,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_bytes() == before
    [new_path] = [other for other in path.parent.glob("*.json") if other != path]
    new = json.loads(new_path.read_bytes())
    assert new["status"] == "stdin_eof" and new["pid"] != record["pid"]
    assert new["started_at_ns"] >= record["finished_at_ns"]
    assert (log_path.read_bytes() if log_path.exists() else None) == original_events


def test_worker_gate_writes_handoff_receipt_before_waiting(tmp_path: Path, monkeypatch):
    briefing = tmp_path / "briefings" / "worker.md"
    briefing.parent.mkdir()
    briefing.write_text("Active task: TASK-GATED\n- run_id: `run-gated`\n")
    dispatch = ZfEvent(
        type="fanout.child.dispatched", actor="kernel", task_id="TASK-GATED",
        payload={"briefing_path": str(briefing), "role_instance": "worker",
                 "stage_id": "scope", "task_id": "TASK-GATED", "run_id": "run-gated"},
    )
    completed: list[str] = []
    (tmp_path / "testkit-worker-gate").touch()
    worker = SimpleNamespace(
        state=tmp_path, role="worker", events=lambda _: [dispatch],
        complete=lambda event: completed.append(event.id) or {"ok": True},
    )
    # First reading is dispatch receipt wait, then gate deadline/check.
    times = iter((0.0, 0.0, 121.0))
    monkeypatch.setattr("tests.testkit.tmux_general_worker.time.monotonic", lambda: next(times))
    monkeypatch.setattr("sys.stdin", StringIO(
        f"Read the fanout child briefing at {briefing}. Use its task contract.\n"
    ))
    with pytest.raises(AssertionError, match="worker gate"):
        run_worker(worker)
    receipt = json.loads(
        (tmp_path / "testkit-transport" / f"{dispatch.id}.json").read_text()
    )
    assert receipt["dispatch_event_id"] == dispatch.id
    assert "submission" not in receipt
    assert completed == []


def test_reader_consumes_dispatched_handoff_before_manifest_projection():
    """EventWriter appends the dispatch before projecting its child manifest."""
    descriptor = {"ref": "artifacts/attempt/current.json", "sha256": "current-digest"}
    dispatch = ZfEvent(type="fanout.child.dispatched", actor="kernel", payload={"payload": {
        "role_instance": "module-parity-scan", "run_id": "current-run",
        "operation_id": "current-operation", "attempt_source_manifest": descriptor,
    }, "attempt_source_manifest": descriptor, "run_id": "current-run",
        "operation_id": "current-operation"})
    before = dispatch.to_json()
    worker = PrdRoute.__new__(PrdRoute)
    worker.child = lambda _: {"payload": {"role_instance": "module-parity-scan"}}
    consumed = []

    def consume(payload):
        assert payload["attempt_source_manifest"] == descriptor
        assert payload["run_id"] == "current-run"
        assert payload["operation_id"] == "current-operation"
        consumed.append(payload["run_id"])
        return {"ref": "ledger", "sha256": "ledger-digest"}

    worker.consume = consume
    worker.publish_result = lambda event, payload: payload
    result = worker.complete_reader(dispatch)
    assert consumed == ["current-run"]
    assert result["read_ledger_ref"] == "ledger"
    assert result["input_consumption_status"] == "satisfied"
    assert dispatch.to_json() == before


@pytest.mark.parametrize("new_event_already_present", [False, True])
def test_reused_briefing_waits_for_exact_retry_and_preserves_old_receipt(
    tmp_path: Path, monkeypatch, new_event_already_present: bool,
):
    briefing = tmp_path / "briefings" / "reader.md"
    briefing.parent.mkdir()
    briefing.write_text("Active task: TASK-RETRY\n- run_id: `reader-retry-1`\n")
    original = ZfEvent(type="fanout.child.dispatched", actor="kernel", task_id="TASK-RETRY",
        payload={"briefing_path": str(briefing), "role_instance": "reader",
                 "stage_id": "scope", "run_id": "reader-original"})
    retry = ZfEvent(type=original.type, actor="kernel", task_id=original.task_id,
        payload={**original.payload, "run_id": "reader-retry-1",
                 "retry_of_run_id": "reader-original"})
    receipts = tmp_path / "testkit-transport"
    receipts.mkdir()
    old_path = receipts / f"{original.id}.json"
    old_path.write_text(json.dumps({"dispatch_event_id": original.id, "pid": 123}))
    before = old_path.read_bytes()
    polls = iter([[original, retry]] if new_event_already_present
                 else [[original], [original, retry]])
    completed = []

    def complete(event):
        assert event.id == retry.id, "new briefing must not consume the old dispatch"
        completed.append(event.id)
        return {"canonical_event_id": "retry-result"}

    worker = SimpleNamespace(state=tmp_path, role="reader", events=lambda _: next(polls),
                             complete=complete)
    monkeypatch.setattr("tests.testkit.tmux_general_worker.time.sleep", lambda _: None)
    monkeypatch.setattr("sys.stdin", StringIO(
        f"Read the fanout child briefing at {briefing}. Use its task contract.\n"))
    run_worker(worker)
    assert completed == [retry.id]
    assert old_path.read_bytes() == before
    receipt = json.loads((receipts / f"{retry.id}.json").read_text())
    assert receipt["dispatch_event_id"] == retry.id
    assert receipt["briefing_sha256"] == hashlib.sha256(briefing.read_bytes()).hexdigest()
