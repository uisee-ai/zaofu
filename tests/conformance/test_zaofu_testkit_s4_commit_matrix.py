"""Deterministic commit-point recovery matrix for TestKit boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore

from tests.testkit.evidence import write_json_atomic
from tests.testkit.external_sinks import ExternalEffectSpy
from tests.testkit.failpoints import FailpointController, InjectedFailure
from tests.testkit.snapshots import assert_snapshot_equal, truth_snapshot


MATRIX_POINTS = (
    "before_event_append",
    "after_event_append_before_projection",
    "before_store_atomic_replace",
    "after_store_atomic_replace",
    "after_artifact_write_before_digest_event",
    "after_external_effect_before_result_event",
    "during_archive_rotation",
    "after_lock_acquire",
)


def _event(log: EventLog, event_id: str) -> ZfEvent | None:
    return next((item for item in log.read_all() if item.id == event_id), None)


def _run_transaction(
    root: Path,
    *,
    fail_at: str | None,
    effects: ExternalEffectSpy[str],
    crash_on_failure: bool = False,
) -> bool:
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    store = TaskStore(state_dir / "kanban.json")
    failpoints = FailpointController(fail_at=fail_at)
    try:
        # Seed an old active file so this run exercises the real lazy archive
        # rotation path.  The seed is present in both baseline and recovery
        # runs, making the comparison sensitive to lost or duplicated rows.
        if not log.path.exists():
            log.path.write_text(
                ZfEvent(
                    id="evt-matrix-seed",
                    type="recovery.seed",
                    actor="kernel",
                    payload={"fixture": True},
                ).to_json()
                + "\n",
                encoding="utf-8",
            )
            old_timestamp = (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).timestamp()
            os.utime(log.path, (old_timestamp, old_timestamp))

        if _event(log, "evt-matrix-dispatch") is None:
            # Exercise the actual advisory lock seam before EventLog obtains
            # its own append lock.  A crash here must leave the next run able
            # to acquire the lock and complete the transaction.
            with locked_path(log.path):
                failpoints.hit("after_lock_acquire")
            failpoints.hit("before_event_append")
            log.append(ZfEvent(
                id="evt-matrix-dispatch",
                type="task.dispatched",
                actor="kernel",
                task_id="TASK-MATRIX",
                payload={"attempt": 1},
            ))
            # EventLog has just performed lazy date/size rotation while
            # holding its append lock.  Injecting here models a crash in that
            # narrow commit window without changing production code.
            failpoints.hit("during_archive_rotation")
            failpoints.hit("after_event_append_before_projection")

        if store.get("TASK-MATRIX") is None:
            failpoints.hit("before_store_atomic_replace")
            store.add(Task(
                id="TASK-MATRIX",
                title="commit matrix",
                status="in_progress",
                assigned_to="dev",
            ))
            failpoints.hit("after_store_atomic_replace")

        artifact = state_dir / "artifacts" / "result.json"
        if not artifact.exists():
            digest = write_json_atomic(
                artifact,
                {"task_id": "TASK-MATRIX", "result": "ok"},
            )
            failpoints.hit("after_artifact_write_before_digest_event")
        else:
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

        if _event(log, "evt-matrix-digest") is None:
            log.append(ZfEvent(
                id="evt-matrix-digest",
                type="artifact.digest.recorded",
                actor="verifier",
                task_id="TASK-MATRIX",
                payload={"ref": "artifacts/result.json", "digest": digest},
                causation_id="evt-matrix-dispatch",
            ))

        effects.execute("controlled:matrix", lambda: "remote-result")
        if _event(log, "evt-matrix-result") is None:
            failpoints.hit("after_external_effect_before_result_event")
            log.append(ZfEvent(
                id="evt-matrix-result",
                type="controlled-action.result",
                actor="kernel",
                task_id="TASK-MATRIX",
                payload={"operation_key": "controlled:matrix", "status": "applied"},
                causation_id="evt-matrix-dispatch",
            ))
    except InjectedFailure:
        if crash_on_failure:
            os._exit(17)
        return False
    return True


def _run_child_transaction(root: Path) -> None:
    """Execute one transaction in a fresh process for crash/restart cases."""
    effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    completed = _run_transaction(
        root,
        fail_at=os.environ.get("ZF_TESTKIT_FAIL_AT") or None,
        effects=effects,
        crash_on_failure=True,
    )
    raise SystemExit(0 if completed else 17)


def _snapshot(root: Path):
    state_dir = root / "state"
    log = EventLog(state_dir / "events.jsonl")
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-MATRIX")
    return truth_snapshot(
        log,
        state={
            "task_id": task.id if task else "",
            "status": task.status if task else "",
            "artifact": json.loads(
                (state_dir / "artifacts" / "result.json").read_text(
                    encoding="utf-8",
                )
            ) if (state_dir / "artifacts" / "result.json").exists() else None,
        },
    )


@pytest.mark.parametrize("fail_at", MATRIX_POINTS)
def test_each_commit_point_recovers_to_baseline(
    tmp_path: Path,
    fail_at: str,
) -> None:
    baseline_root = tmp_path / "baseline"
    baseline_effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    assert _run_transaction(
        baseline_root,
        fail_at=None,
        effects=baseline_effects,
    )
    baseline = _snapshot(baseline_root)

    fault_root = tmp_path / "fault"
    fault_effects: ExternalEffectSpy[str] = ExternalEffectSpy()
    assert _run_transaction(
        fault_root,
        fail_at=fail_at,
        effects=fault_effects,
    ) is False
    assert _run_transaction(
        fault_root,
        fail_at=None,
        effects=fault_effects,
    )
    recovered = _snapshot(fault_root)

    assert_snapshot_equal(baseline, recovered)
    fault_effects.assert_at_most_once()
    assert fault_effects.execution_count("controlled:matrix") == 1


@pytest.mark.parametrize(
    "fail_at",
    tuple(point for point in MATRIX_POINTS if point != "after_external_effect_before_result_event"),
)
def test_pre_effect_commit_points_recover_in_fresh_process(
    tmp_path: Path,
    fail_at: str,
) -> None:
    """A real child crash must be recoverable at every pre-effect boundary."""
    module = "tests.conformance.test_zaofu_testkit_s4_commit_matrix"
    baseline_root = tmp_path / "fresh-baseline"
    baseline = subprocess.run(
        [sys.executable, "-m", module, "--child", str(baseline_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert baseline.returncode == 0, baseline.stderr

    fault_root = tmp_path / "fresh-fault"
    environment = os.environ | {"ZF_TESTKIT_FAIL_AT": fail_at}
    crashed = subprocess.run(
        [sys.executable, "-m", module, "--child", str(fault_root)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert crashed.returncode == 17, crashed.stderr

    recovered = subprocess.run(
        [sys.executable, "-m", module, "--child", str(fault_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert_snapshot_equal(_snapshot(baseline_root), _snapshot(fault_root))


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--child":
    _run_child_transaction(Path(sys.argv[2]))
