"""ZF-PWF-SESSION-ISO-001 — operator session registry + resolver tests.

Covers acceptance §1 (registry bind/resolve/unbind/list_all) and §2
(resolver 4-level priority + ambiguity fail-closed) from sprint 0844.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.state.operator_sessions import (
    OperatorSession,
    OperatorSessionRegistry,
)
from zf.runtime.operator_target_resolver import (
    TargetAmbiguous,
    resolve_active_target,
)


# ---------------------------------------------------------------------------
# OperatorSessionRegistry — atomic_write + load/save symmetry
# ---------------------------------------------------------------------------


def _new_registry(tmp_path: Path) -> OperatorSessionRegistry:
    return OperatorSessionRegistry(tmp_path / "operator_sessions.yaml")


def test_bind_creates_yaml_file(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    sess = OperatorSession(
        operator_session_id="op-cli-1",
        source="cli",
        task_id="TASK-42",
    )
    bound = reg.bind(sess)
    assert (tmp_path / "operator_sessions.yaml").exists()
    assert bound.bound_at != ""
    assert bound.last_active != ""


def test_bind_then_resolve_roundtrip(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-web-tab-3",
        source="web",
        run_id="run-abc",
        task_id="TASK-7",
        role_name="dev",
        instance_id="dev-1",
    ))
    fetched = reg.resolve("op-web-tab-3")
    assert fetched is not None
    assert fetched.task_id == "TASK-7"
    assert fetched.role_name == "dev"
    assert fetched.instance_id == "dev-1"
    assert fetched.run_id == "run-abc"


def test_resolve_unknown_returns_none(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    assert reg.resolve("op-never-bound") is None


def test_rebind_overwrites_existing(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-1",
        source="cli",
        task_id="TASK-OLD",
    ))
    reg.bind(OperatorSession(
        operator_session_id="op-1",
        source="cli",
        task_id="TASK-NEW",
    ))
    fetched = reg.resolve("op-1")
    assert fetched is not None
    assert fetched.task_id == "TASK-NEW"


def test_unbind_removes_entry(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(operator_session_id="op-x", source="cli"))
    assert reg.unbind("op-x") is True
    assert reg.resolve("op-x") is None
    # Idempotent second unbind
    assert reg.unbind("op-x") is False


def test_list_all_returns_all_active(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(operator_session_id="a", source="cli"))
    reg.bind(OperatorSession(operator_session_id="b", source="web"))
    reg.bind(OperatorSession(operator_session_id="c", source="feishu"))
    ids = sorted(s.operator_session_id for s in reg.list_all())
    assert ids == ["a", "b", "c"]


def test_persistence_survives_new_registry_instance(tmp_path: Path) -> None:
    """Second Registry pointed at the same path must read prior binds."""
    reg1 = _new_registry(tmp_path)
    reg1.bind(OperatorSession(
        operator_session_id="op-persistent",
        source="cli",
        task_id="TASK-P",
    ))
    reg2 = _new_registry(tmp_path)
    fetched = reg2.resolve("op-persistent")
    assert fetched is not None
    assert fetched.task_id == "TASK-P"


def test_yaml_schema_shape(tmp_path: Path) -> None:
    """File layout must be ``{sessions: {<id>: {...fields...}}}``."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-schema",
        source="web",
        task_id="TASK-Y",
    ))
    raw = yaml.safe_load(
        (tmp_path / "operator_sessions.yaml").read_text(encoding="utf-8")
    )
    assert "sessions" in raw
    assert "op-schema" in raw["sessions"]
    # operator_session_id is the dict key, not a field inside the value
    assert "operator_session_id" not in raw["sessions"]["op-schema"]
    assert raw["sessions"]["op-schema"]["task_id"] == "TASK-Y"
    assert raw["sessions"]["op-schema"]["source"] == "web"


def test_malformed_yaml_treated_as_empty(tmp_path: Path) -> None:
    """If operator_sessions.yaml is corrupted, the registry comes up
    empty rather than crashing — protects 'zf start' uptime."""
    p = tmp_path / "operator_sessions.yaml"
    p.write_text("not: valid: yaml: at all\n  [broken")
    reg = OperatorSessionRegistry(p)
    assert reg.list_all() == []


def test_operator_session_is_frozen() -> None:
    """OperatorSession must be immutable so registry state cannot be
    mutated through a returned snapshot."""
    sess = OperatorSession(operator_session_id="x", source="cli")
    with pytest.raises((AttributeError, TypeError)):
        sess.task_id = "TASK-Z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_active_target — 4-level priority ladder
# ---------------------------------------------------------------------------


def test_resolver_level1_explicit_task_id_wins(tmp_path: Path) -> None:
    """Explicit task id beats the session binding."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-1",
        source="cli",
        task_id="TASK-SESSION",
    ))
    out = resolve_active_target(
        registry=reg,
        operator_session_id="op-1",
        explicit_task_id="TASK-EXPLICIT",
    )
    assert isinstance(out, OperatorSession)
    assert out.task_id == "TASK-EXPLICIT"


def test_resolver_level1_explicit_without_session_returns_synthetic(
    tmp_path: Path,
) -> None:
    """Explicit task id with no session binding still yields a session."""
    reg = _new_registry(tmp_path)
    out = resolve_active_target(
        registry=reg,
        operator_session_id=None,
        explicit_task_id="TASK-RAW",
    )
    assert isinstance(out, OperatorSession)
    assert out.task_id == "TASK-RAW"
    assert out.source == "explicit"


def test_resolver_level2_session_binding(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-1",
        source="cli",
        task_id="TASK-1",
    ))
    reg.bind(OperatorSession(
        operator_session_id="op-2",
        source="web",
        task_id="TASK-2",
    ))
    out = resolve_active_target(
        registry=reg,
        operator_session_id="op-2",
    )
    assert isinstance(out, OperatorSession)
    assert out.task_id == "TASK-2"


def test_resolver_level3_single_active_fallback(tmp_path: Path) -> None:
    """Single binding total → return it even without explicit
    operator_session_id (degenerate single-campaign install)."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(
        operator_session_id="op-lone",
        source="cli",
        task_id="TASK-SOLO",
    ))
    out = resolve_active_target(
        registry=reg,
        operator_session_id=None,
    )
    assert isinstance(out, OperatorSession)
    assert out.task_id == "TASK-SOLO"


def test_resolver_level4_fail_closed_no_bindings(tmp_path: Path) -> None:
    reg = _new_registry(tmp_path)
    out = resolve_active_target(
        registry=reg,
        operator_session_id=None,
    )
    assert isinstance(out, TargetAmbiguous)
    assert out.candidates == []
    assert out.reason == "no_operator_session_bound"


def test_resolver_level4_fail_closed_multiple_active(tmp_path: Path) -> None:
    """Multiple bindings + no operator_session_id → fail closed,
    never guess 'most recent'."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(operator_session_id="a", source="cli"))
    reg.bind(OperatorSession(operator_session_id="b", source="web"))
    out = resolve_active_target(
        registry=reg,
        operator_session_id=None,
    )
    assert isinstance(out, TargetAmbiguous)
    assert {s.operator_session_id for s in out.candidates} == {"a", "b"}
    assert out.reason == "multiple_operator_sessions_active"


def test_resolver_unknown_operator_session_falls_through(tmp_path: Path) -> None:
    """If the operator_session_id is unknown and there's no explicit
    target, level 2 misses → fall through to level 3 or 4. With
    multiple sessions present, we get the ambiguous sentinel."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(operator_session_id="a", source="cli"))
    reg.bind(OperatorSession(operator_session_id="b", source="web"))
    out = resolve_active_target(
        registry=reg,
        operator_session_id="op-never-bound",
    )
    assert isinstance(out, TargetAmbiguous)


def test_resolver_session_dimensions_independent_of_role_sessions(
    tmp_path: Path,
) -> None:
    """OperatorSessionRegistry uses its own file; RoleSessionRegistry
    is untouched. We verify the file path is operator_sessions.yaml,
    not role_sessions.yaml — acceptance §6 dimension-independence."""
    reg = _new_registry(tmp_path)
    reg.bind(OperatorSession(operator_session_id="op-x", source="cli"))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "operator_sessions.yaml" in files
    assert "role_sessions.yaml" not in files
