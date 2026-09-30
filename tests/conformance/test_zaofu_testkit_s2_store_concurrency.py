"""STO-06: two spawned owners update real stores, then a cold owner checks all fields."""

from __future__ import annotations

from dataclasses import asdict
import multiprocessing as mp
from pathlib import Path

import pytest

from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.state.locks import locked_path
from zf.core.state.session import SessionStore, WorkerState
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


pytestmark = pytest.mark.serial


def _update(path: str, kind: str, patch: dict, barrier, replies) -> None:
    owner = FeatureStore(Path(path)) if kind == "feature" else SessionStore(Path(path))
    # Both owners observe the seed, but neither substitutes its own stale
    # read for the production read-modify-write inside the real file lock.
    before = asdict(owner.get("F-ONE") if kind == "feature" else owner.load())
    replies.put({"phase": "ready", "patch": patch, "before": before})
    barrier.wait(timeout=15)
    if kind == "feature":
        result = owner.update("F-ONE", **patch)
    elif kind == "workers":
        result = owner.upsert_worker(WorkerState(**patch))
    else:
        result = owner.update(**patch)
    replies.put({"phase": "updated", "patch": patch, "result": asdict(result)})


@pytest.mark.parametrize("kind", ["feature", "session", "workers"])
def test_two_process_updates_preserve_disjoint_fields(tmp_path: Path, request, kind: str) -> None:
    state = tmp_path / "custom-state"
    attach_runtime_case_evidence(
        request, state,
        given=f"two owners share a valid {kind} canonical store",
        when="spawn two public API updates behind a synchronization barrier",
        then="disjoint updates are both preserved after cold reload",
        forbidden=["lost update", "deadlock", "child process leak"],
        inputs={"store_kind": kind},
    )
    path = state / ("feature_list.json" if kind == "feature" else "session.yaml")
    if kind == "feature":
        owner = FeatureStore(path)
        owner.add(Feature(id="F-ONE", title="seed", priority=3, user_message="keep me"))
        before = asdict(owner.get("F-ONE"))
        patches = [{"title": "A"}, {"priority": 1}]
        expected = before | patches[0] | patches[1]
    else:
        owner = SessionStore(path)
        owner.create(str(tmp_path))
        owner.upsert_worker(WorkerState(role="seed", turn_count=9))
        before = asdict(owner.load())
        if kind == "session":
            patches = [{"runtime_state": "active"}, {"latest_event_offset": 7}]
            expected = before | patches[0] | patches[1]
        else:
            patches = [{"role": "dev", "turn_count": 2}, {"role": "review", "turn_count": 3}]
            expected = before | {"workers": sorted(
                before["workers"] + [asdict(WorkerState(**p)) for p in patches],
                key=lambda row: row["role"],
            )}
    write_json_atomic(tmp_path / "before.json", before)
    model = ModelEvidence(
        root=tmp_path, model_name="FixedTwoStoreOwners", source=Path(__file__),
        settings="two spawn children; public API barrier; bounded joins; not schedule exhaustion",
        reference=lambda: {"kind": kind, "patches": patches, "expected": expected},
    )
    context = mp.get_context("spawn")
    barrier, replies = context.Barrier(3), context.Queue()
    children = []
    try:
        with model.guard("concurrent-public-updates", {"kind": kind, "patches": patches}):
            # Hold the real canonical lock while both children reach the
            # public mutation boundary; never barrier inside the locked load.
            with locked_path(path):
                for patch in patches:
                    child = context.Process(target=_update,
                                            args=(str(path), kind, patch, barrier, replies))
                    child.start()
                    children.append(child)
                ready = [replies.get(timeout=15) for _ in children]
                assert all(row["phase"] == "ready" and row["before"] == before for row in ready)
                barrier.wait(timeout=15)
            receipts = [replies.get(timeout=15) for _ in children]
            assert all(row["phase"] == "updated" for row in receipts)
            assert sorted(str(row["patch"]) for row in receipts) == sorted(map(str, patches))
            for child in children:
                child.join(timeout=15)
                assert not child.is_alive() and child.exitcode == 0
            if kind == "feature":
                rows = FeatureStore(path).list_all()
                assert len(rows) == 1
                actual = asdict(rows[0])
            else:
                actual = asdict(SessionStore(path).load())
                if kind == "workers":
                    actual["workers"].sort(key=lambda row: row["role"])
            write_json_atomic(tmp_path / "observed.json", {
                "ready": ready, "receipts": receipts, "actual": actual,
                "child_exitcodes": [child.exitcode for child in children],
            })
            assert actual == expected
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=3)
            if child.is_alive():
                child.kill()
                child.join(timeout=3)
            assert not child.is_alive(), "owned test process leaked"
            child.close()
        replies.close()
        replies.join_thread()
