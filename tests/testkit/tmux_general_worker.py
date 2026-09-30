"""Scripted General semantics, activated only by real tmux stdin delivery.

Installed as an isolated PATH-local ``cat`` for the existing mock backend.
No scheduler is constructed here; only the existing semantic helpers are reused.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from tests.testkit.evidence import write_json_atomic
from tests.testkit.general_route import GeneralRoute
from zf.core.events import EventLog
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry


class GeneralWorker(GeneralRoute):
    def __init__(self) -> None:
        self.state = Path(os.environ["ZF_STATE_DIR"])
        self.log = EventLog(self.state / "events.jsonl")
        self.role = os.environ["ZF_ROLE_INSTANCE"]

    def emit(self, dispatch, child, payload):
        assert child["role_instance"] == self.role
        profile = ControlResultAdapterRegistry().profile(
            payload["output_profile_id"], payload["output_profile_revision"],
        )
        scratch = self.state / payload["result_scratch_ref"]
        scratch.write_text(json.dumps(payload[profile.semantic_field]))
        result = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "result", "submit", "--operation",
             payload["operation_id"], "--scratch"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    def complete(self, dispatch):
        return (self.complete_verify(dispatch) if dispatch.payload["stage_id"] == "verify"
                else self.complete_reader(dispatch))


def _write_worker_receipt(path: Path, receipt: dict) -> None:
    """Publish a worker receipt only after its complete bytes are synced."""
    atomic_write_text(
        path,
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def run_worker(worker) -> None:
    receipts = worker.state / "testkit-transport"
    receipts.mkdir(exist_ok=True)
    for line in sys.stdin:
        match = re.search(r"Read the fanout (child|synthesis) briefing at (.+?\.md)\. Use (?:its|the) ", line)
        if not match:
            continue
        synthesis = match[1] == "synthesis"
        kind = "fanout.synth.dispatched" if synthesis else "fanout.child.dispatched"
        briefing = Path(match[2]).resolve()
        assert briefing.is_relative_to(worker.state.resolve() / "briefings")
        raw = briefing.read_bytes()
        run_match = re.search(r"^- run_id: `([^`]+)`$", raw.decode(), re.MULTILINE)
        assert run_match, "delivered briefing has no run identity"
        delivered_run_id = run_match[1]
        # send_task writes the physical prompt before appending its dispatch
        # record. Wait for that exact receipt, never select an unrelated/latest Run.
        deadline = time.monotonic() + 15
        while True:
            rows = [event for event in worker.events(kind)
                    if event.payload.get("briefing_path") == str(briefing)
                    and event.payload.get("role_instance") == worker.role
                    and event.payload.get("run_id") == delivered_run_id]
            if rows:
                break
            assert time.monotonic() < deadline, "delivered briefing has no dispatch receipt"
            time.sleep(0.05)
        [dispatch] = rows
        task_id = str(dispatch.task_id or dispatch.payload.get("task_id") or "")
        if synthesis:
            assert raw.splitlines()[0].decode() == f"# Fanout Synth: {dispatch.payload['fanout_id']}"
            assert f"- run_id: `{dispatch.payload['run_id']}`" in raw.decode()
        else:
            assert task_id and raw.splitlines()[0].decode() == f"Active task: {task_id}"
        receipt = {
            "pid": os.getpid(), "role": worker.role, "dispatch_event_id": dispatch.id,
            "briefing_path": str(briefing), "briefing_sha256": hashlib.sha256(raw).hexdigest(),
            "stdin_prompt": line.strip(), "stage_id": dispatch.payload["stage_id"],
            "dispatch_type": kind,
            "run_id": delivered_run_id,
        }
        path = receipts / f"{dispatch.id}.json"
        _write_worker_receipt(path, receipt)
        if (worker.state / "testkit-worker-gate").exists():
            gate_path = worker.state / "testkit-worker-release"
            deadline = time.monotonic() + 120
            while not gate_path.exists():
                assert time.monotonic() < deadline, (
                    f"worker gate was not released: {gate_path}"
                )
                time.sleep(0.05)
        receipt["submission"] = worker.complete(dispatch)
        _write_worker_receipt(path, receipt)


def observe_worker(factory) -> None:
    """Keep the first process failure even when runtime replaces its pane."""
    state = Path(os.environ["ZF_STATE_DIR"])
    path = state / "testkit-worker-processes" / f"{os.getpid()}-{time.time_ns()}.json"
    record = {"pid": os.getpid(), "role": os.environ["ZF_ROLE_INSTANCE"],
              "status": "running", "started_at_ns": time.time_ns()}
    write_json_atomic(path, record)
    try:
        run_worker(factory())
    except BaseException as exc:
        record.update(status="failed", error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
        raise
    else:
        record["status"] = "stdin_eof"
    finally:
        record["finished_at_ns"] = time.time_ns()
        write_json_atomic(path, record)


def main() -> None:
    observe_worker(GeneralWorker)


if __name__ == "__main__":
    main()
