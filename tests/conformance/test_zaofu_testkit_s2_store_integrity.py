"""STO-08: corruption is not an empty canonical store or a new project.

See store_integrity_concurrency_research.md. All damage is test-owned input;
the production reader/mutation remain untouched. Fixed-case evidence only.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
import json
from pathlib import Path
from typing import Callable

import pytest
import yaml

from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.core.task.schema import Task, TaskContract, TaskEvidence, TaskExecutionBinding
from zf.core.task.store import TaskStore
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


class CorruptCanonicalAccepted(AssertionError):
    """A damaged nonempty canonical store was read as empty, then overwritten."""


WRONG_ROOT = pytest.mark.xfail(
    strict=True, raises=CorruptCanonicalAccepted,
    reason="ZF-TEST-041: wrong-root canonical state is treated as empty and overwritten",
)


FILENAMES = {"task": "kanban.json", "feature": "feature_list.json",
             "session": "session.yaml", "role": "role_sessions.yaml"}


def _open(kind: str, path: Path):
    if kind == "task":
        return TaskStore(path)
    if kind == "feature":
        return FeatureStore(path)
    if kind == "session":
        return SessionStore(path)
    return RoleSessionRegistry(path, project_root=str(path.parent))


def _read(kind: str, store):
    if kind in {"task", "feature"}:
        return store.list_all()
    return store.load() if kind == "session" else store.all()


def _mutate(kind: str, store, *, seed: bool = False):
    identifier = "SEED" if seed else "NEW"
    if kind == "task":
        return store.add(Task(id=identifier, title=identifier))
    if kind == "feature":
        return store.add(Feature(id=identifier, title=identifier))
    if kind == "session":
        return store.update(latest_event_offset=7)
    return store.get_or_create(identifier)


def _outcome(call: Callable, phase: str, rejected_types: tuple[type[Exception], ...]) -> dict:
    try:
        value = call()
    except rejected_types as error:
        return {"rejected": True, "phase": phase, "type": type(error).__name__,
                "message": str(error)}
    if is_dataclass(value):
        value = asdict(value)
    # repr also supports UUID and lists of dataclasses; raw canonical bytes
    # in ModelEvidence remain the authoritative persistence observation.
    return {"rejected": False, "phase": phase, "returned": repr(value)}


@pytest.mark.parametrize("shape,kind", [
    *(pytest.param("syntax", kind) for kind in FILENAMES),
    *(pytest.param("wrong-root", kind, marks=WRONG_ROOT if kind != "session" else ())
      for kind in FILENAMES),
])
@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm-mutation"])
def test_corrupt_store_is_rejected_without_overwrite(
    tmp_path: Path, request, kind: str, shape: str, warm: bool,
) -> None:
    state = tmp_path / "custom-state"
    attach_runtime_case_evidence(
        request, state,
        given=f"{kind} canonical store exists and is then damaged ({shape}, warm={warm})",
        when="read and attempt mutation through a fresh owner",
        then="corruption is rejected and original bytes remain unchanged",
        forbidden=["overwrite damaged canonical bytes", "accept invalid state"],
        inputs={"kind": kind, "shape": shape, "warm": warm},
    )
    state.mkdir()
    path = state / FILENAMES[kind]
    owner = _open(kind, path)
    if kind == "session":
        owner.create(str(tmp_path))
    else:
        _mutate(kind, owner, seed=True)
    assert _read(kind, owner), "valid nonempty seed must be observable"
    (tmp_path / "seed.bytes").write_bytes(path.read_bytes())
    broken = (b'{"broken":' if kind in {"task", "feature"} else b"roles: [\n")
    if shape == "wrong-root":
        broken = (b'{"sentinel":"damaged-root"}\n' if kind in {"task", "feature"}
                  else b"[sentinel]\n")
    # Do not count an arbitrary fixture TypeError as a successful rejection.
    rejected_types = ((TypeError,) if kind == "session" and shape == "wrong-root"
                      else (json.JSONDecodeError,) if kind in {"task", "feature"}
                      else (yaml.YAMLError,))
    model = ModelEvidence(
        root=tmp_path, model_name="FixedStoreCorruption", source=Path(__file__),
        settings="four owners x syntax/root x cold/warm mutation; no generation",
        reference=lambda: {"kind": kind, "shape": shape, "warm": warm,
                           "expected_bytes_hex": broken.hex(), "expected_rejection": True},
    )
    with model.guard("damage-and-reject", {"kind": kind, "shape": shape, "warm": warm}):
        path.write_bytes(broken)
        read = _outcome(lambda: _read(kind, _open(kind, path)),
                        "fresh-read-or-construct", rejected_types)
        # Role cached get/all intentionally do not reload. A warm mutation
        # DOES reload under its own lock; test that real boundary separately.
        mutation = _outcome(
            lambda: _mutate(kind, owner if warm else _open(kind, path)),
            "warm-mutation" if warm else "construct-or-mutation",
            rejected_types,
        )
        after = path.read_bytes()
        write_json_atomic(tmp_path / "observed.json", {
            "read": read, "mutation": mutation, "bytes_preserved": after == broken,
            "after_hex": after.hex(),
        })
        if (not read["rejected"] and not mutation["rejected"] and after != broken
                and b"NEW" in after and b"SEED" not in after):
            raise CorruptCanonicalAccepted(
                f"{kind}/{shape}: fresh read accepted damaged state; "
                "successful mutation overwrote damaged bytes with NEW instead of refusing"
            )
        assert read["rejected"] and read["message"], read
        assert mutation["rejected"] and mutation["message"], mutation
        assert after == broken, "rejection must not replace damaged canonical bytes"


@pytest.mark.parametrize("kind", list(FILENAMES))
@pytest.mark.parametrize("initial", ["missing", "valid-empty"])
def test_initialization_is_distinct_from_corrupted_existing_state(
    tmp_path: Path, kind: str, initial: str,
) -> None:
    path = tmp_path / FILENAMES[kind]
    if initial == "valid-empty" and kind != "session":
        path.write_text("[]\n" if kind in {"task", "feature"} else "roles: {}\n")
    owner = _open(kind, path)
    if kind == "session":
        if initial == "missing":
            with pytest.raises(ZfNotInitialized):
                owner.load()
            with pytest.raises(ZfNotInitialized):
                owner.update(latest_event_offset=7)
            assert not path.exists()
        created = owner.create(str(tmp_path))
        assert created.workers == []
        if initial == "valid-empty":
            owner = _open(kind, path)
            assert owner.load() == created
    else:
        assert not _read(kind, owner)
    _mutate(kind, owner)
    result = _read(kind, _open(kind, path))
    if kind in {"task", "feature"}:
        assert [row.id for row in result] == ["NEW"]
    elif kind == "role":
        assert set(result) == {"NEW"}
    else:
        assert result == type(created)(**(asdict(created) | {"latest_event_offset": 7}))


@pytest.mark.parametrize("terminal", [None, "done", "cancelled"])
def test_task_full_payload_roundtrip_archive_and_explicit_reopen(tmp_path: Path, terminal: str | None) -> None:
    # Storage fidelity, NOT an assertion that arbitrary semantic contracts
    # pass workflow admission. Every current field carries a non-default value.
    contract = TaskContract()
    for item in fields(contract):
        default = getattr(contract, item.name)
        if isinstance(default, bool):
            value = True
        elif isinstance(default, int):
            value = 2
        elif isinstance(default, list):
            value = [f"{item.name}-one", f"{item.name}-two"]
        elif isinstance(default, dict):
            value = {item.name: ["keep", {"nested": True}]}
        else:
            value = f"{item.name}-sentinel"
        setattr(contract, item.name, value)
    contract.verification_tiers = ["static", "runtime"]
    contract.acceptance_criteria = ["text criterion", {"id": "AC-2", "text": "structured criterion"}]
    contract.acceptance_evidence = {"AC-2": ["event-1"]}
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="PRE", title="precondition"))
    original = Task(
        id="ROUNDTRIP", title="Unicode 状态", key="stable-key", status="in_progress", priority=1,
        assigned_to="dev", skills_required=["impl"], blocked_by=["PRE"], contract=contract,
        execution_binding=TaskExecutionBinding("workflow", "request-1", 2, "run-1", "binding-digest", "task-digest"),
        contract_authority_revision="authority-1", contract_authority_sequence=2,
        evidence=TaskEvidence("abc", "verified output", "2026-09-14T01:00:00Z", ["abc"], ["src/a.py"]),
        created_at="2026-09-14T00:00:00Z", dispatched_at="2026-09-14T00:01:00Z",
        started_at="2026-09-14T00:02:00Z", completed_at="recorded-completion",
        cancelled_at="recorded-cancellation", active_dispatch_id="dispatch-1", retry_count=2,
        blocked_reason="keep reason",
    )
    store.add(original)
    assert asdict(TaskStore(store.path).get(original.id)) == asdict(original)
    expected = replace(original, title="updated title")
    store.update(original.id, title=expected.title)
    if terminal:
        expected = replace(expected, status=terminal)
        store.update(original.id, status=terminal)
        assert original.id not in {t.id for t in store.list_all()}
    assert asdict(TaskStore(store.path).get(original.id)) == asdict(expected)
    assert asdict(next(t for t in store.list_all_with_archive() if t.id == original.id)) == asdict(expected)
    if terminal:
        archived = {p: p.read_bytes() for p in (tmp_path / "kanban").glob("*.json")}
        reopened = replace(expected, status="backlog", active_dispatch_id="", retry_count=3)
        store.reopen(reopened)  # Explicit owner API; not a worker terminal-backslide event.
        assert asdict(TaskStore(store.path).get(original.id)) == asdict(reopened)
        assert original.id not in json.loads((tmp_path / "kanban-terminal-index.json").read_text())
        assert all(path.read_bytes() == body for path, body in archived.items())
