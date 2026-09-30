"""S4 production persistence crash boundaries.

These cases deliberately execute the production EventLog rotation path in a
fresh process.  They are separate from the synthetic commit-point matrix:
the process exits between the archive write and removal of the active file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


class ProductionArchiveRecoveryBug(AssertionError):
    """Expected assertion used to keep a known production defect visible."""


def _event(*, event_id: str, event_type: str) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type=event_type,
        actor="testkit",
        payload={"source": "s4-persistence"},
    )


def _seed_collision(root: Path) -> tuple[Path, Path]:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    active = state / "events.jsonl"
    old_timestamp = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).timestamp()
    archive_day = datetime.fromtimestamp(
        old_timestamp, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    archive = state / "events" / f"{archive_day}.jsonl"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        _event(event_id="archive-seed", event_type="recovery.archive").to_json()
        + "\n",
        encoding="utf-8",
    )
    active.write_text(
        _event(event_id="active-seed", event_type="recovery.active").to_json()
        + "\n",
        encoding="utf-8",
    )
    os.utime(active, (old_timestamp, old_timestamp))
    return active, archive


def _run_child(root: Path, *, crash_between_archive_and_unlink: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ZF_TESTKIT_ARCHIVE_UNLINK_CRASH"] = (
        "1" if crash_between_archive_and_unlink else "0"
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s4_persistence",
            "--child",
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _run_child_transaction(root: Path) -> None:
    active = root / "state" / "events.jsonl"
    if not active.exists():
        raise SystemExit("parent must seed the collision fixture")
    if os.environ.get("ZF_TESTKIT_ARCHIVE_UNLINK_CRASH") == "1":
        original_unlink = Path.unlink

        def crash_after_archive_write(
            path: Path, *, missing_ok: bool = False
        ) -> None:
            if path == active:
                # The production collision branch has already written the
                # concatenated archive at this point, but active still exists.
                os._exit(79)
            original_unlink(path, missing_ok=missing_ok)

        Path.unlink = crash_after_archive_write  # type: ignore[method-assign]
    EventLog(active).append(
        _event(event_id="dispatch", event_type="task.dispatched")
    )


def _run_atomic_child(root: Path) -> None:
    target = root / "state" / "snapshot.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ZF_TESTKIT_ATOMIC_REPLACE_CRASH") == "1":
        from zf.core.state import atomic_io

        original_replace = atomic_io.os.replace

        def replace_then_crash(source: str, destination: str) -> None:
            original_replace(source, destination)
            os._exit(83)

        atomic_io.os.replace = replace_then_crash
    atomic_write_text(target, json.dumps({"version": 2}) + "\n")


def _run_size_rotation_child(root: Path) -> None:
    active = root / "state" / "events.jsonl"
    if os.environ.get("ZF_TESTKIT_SIZE_ROTATION_CRASH") == "1":
        original_rename = Path.rename

        def rename_then_crash(path: Path, destination: Path) -> Path:
            result = original_rename(path, destination)
            if path == active:
                os._exit(89)
            return result

        Path.rename = rename_then_crash  # type: ignore[method-assign]
    os.environ["ZF_EVENT_LOG_MAX_ACTIVE_BYTES"] = "1"
    EventLog(active).append(
        _event(event_id="size-dispatch", event_type="task.dispatched")
    )


@pytest.mark.xfail(
    strict=True,
    raises=ProductionArchiveRecoveryBug,
    reason=(
        "ZF-TEST-013: EventLog collision rotation can duplicate archive rows "
        "after a crash before active unlink"
    ),
)
def test_archive_collision_crash_recovers_without_duplicate_events(tmp_path: Path) -> None:
    """A restart after archive write must not replay the active rows twice."""
    active, archive = _seed_collision(tmp_path)
    first = _run_child(tmp_path, crash_between_archive_and_unlink=True)
    assert first.returncode == 79, first.stderr
    assert active.exists(), "crash must occur before active unlink"
    assert archive.exists(), "archive write must be durable before crash"

    second = _run_child(tmp_path, crash_between_archive_and_unlink=False)
    assert second.returncode == 0, second.stderr
    ids = [event.id for event in EventLog(active).read_all()]
    expected = ["archive-seed", "active-seed", "dispatch"]
    if ids != expected:
        raise ProductionArchiveRecoveryBug(
            f"archive collision recovery produced {ids!r}; expected {expected!r}"
        )


def test_atomic_snapshot_is_complete_after_replace_then_crash(tmp_path: Path) -> None:
    """A crash after os.replace must leave a parseable canonical snapshot."""
    target = tmp_path / "state" / "snapshot.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"version": 1}\n', encoding="utf-8")
    env = os.environ.copy()
    env["ZF_TESTKIT_ATOMIC_REPLACE_CRASH"] = "1"
    crashed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s4_persistence",
            "--atomic-child",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert crashed.returncode == 83, crashed.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
    assert not list(target.parent.glob("snapshot.json.*.tmp"))

    recovered = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s4_persistence",
            "--atomic-child",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}


def test_size_rotation_crash_after_rename_preserves_archive_and_restarts(
    tmp_path: Path,
) -> None:
    """A post-rename crash must leave the old row readable on restart."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    active = state / "events.jsonl"
    active.write_text(
        _event(event_id="size-seed", event_type="recovery.seed").to_json()
        + "\n",
        encoding="utf-8",
    )
    first_env = os.environ.copy()
    first_env["ZF_TESTKIT_SIZE_ROTATION_CRASH"] = "1"
    first = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s4_persistence",
            "--size-child",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=first_env,
        timeout=15,
    )
    assert first.returncode == 89, first.stderr
    archives = sorted((state / "events").glob("*-0001.jsonl"))
    assert len(archives) == 1
    assert not active.exists()

    second_env = os.environ.copy()
    second_env["ZF_TESTKIT_SIZE_ROTATION_CRASH"] = "0"
    second = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.conformance.test_zaofu_testkit_s4_persistence",
            "--size-child",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=second_env,
        timeout=15,
    )
    assert second.returncode == 0, second.stderr
    assert [event.id for event in EventLog(active).read_all()] == [
        "size-seed",
        "size-dispatch",
    ]


if __name__ == "__main__" and len(sys.argv) == 3:
    if sys.argv[1] == "--child":
        _run_child_transaction(Path(sys.argv[2]))
    elif sys.argv[1] == "--atomic-child":
        _run_atomic_child(Path(sys.argv[2]))
    elif sys.argv[1] == "--size-child":
        _run_size_rotation_child(Path(sys.argv[2]))
