"""P2/K4 (docs/impl/22-zaofu-canonical-dag.md): dispatcher preflight
checks the 6 required_backlog_refs are populated before dispatching a
writer task (e.g. dev).

The preflight runs inside `validate_task_contract` → `validate_backlog_refs`.
Both are called by `_strict_contract_preflight_errors` in
`orchestrator_dispatch.py:543` before the actual dispatch.

If any required ref is missing AND
  workflow.dag.dev_requires_orchestrator_backlog: true,
the kernel emits `task.contract.invalid` with the specific missing-refs
list and refuses to dispatch.

Reader roles (arch / critic / review / test / judge) are NOT subjected to
this preflight — their tasks exist before stage ④ backlog runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.task.contract_validation import (
    validate_backlog_refs,
    validate_task_contract,
)
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator


class _NoopTransport:
    pass


def _config(*, enforce: bool = True) -> ZfConfig:
    """Build a ZfConfig with workflow.dag.dev_requires_orchestrator_backlog
    set per parameter. dev is the only writer."""
    return ZfConfig(
        project=ProjectConfig(name="preflight-test"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
            RoleConfig(name="review", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                dev_requires_orchestrator_backlog=enforce,
                required_backlog_refs=[
                    "spec_ref",
                    "plan_ref",
                    "tdd_ref",
                    "critic_event_id",
                    "critic_gate_ref",
                    "evidence_contract",
                ],
            ),
        ),
    )


def _full_contract() -> TaskContract:
    """A contract with all 6 refs filled — should pass."""
    return TaskContract(
        behavior="impl x",
        verification="pytest",
        verification_tiers=["runtime"],
        scope=["src/x.py"],
        owner_role="dev",
        spec_ref="docs/specs/x.md",
        plan_ref="arch.proposal.done:evt-xxx",
        tdd_ref="pytest 6 cases",
        critic_event_id="evt-yyy",
        critic_gate_ref="approve: fixes applied",
        evidence_contract={"runtime": "pytest"},
    )


# ---------- validate_backlog_refs unit tests ----------


def test_all_6_refs_present_no_errors():
    task = Task(id="T1", title="t", contract=_full_contract(), assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config())
    assert errors == []


def test_missing_spec_ref_flagged():
    contract = _full_contract()
    contract.spec_ref = ""
    task = Task(id="T2", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config())
    assert len(errors) == 1
    assert "spec_ref" in errors[0]
    assert "required_backlog_refs" in errors[0]


def test_whitespace_only_ref_flagged_as_missing():
    contract = _full_contract()
    contract.critic_event_id = "   "
    task = Task(id="T3", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config())
    assert len(errors) == 1
    assert "critic_event_id" in errors[0]


def test_empty_evidence_contract_dict_flagged():
    contract = _full_contract()
    contract.evidence_contract = {}
    task = Task(id="T4", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config())
    assert len(errors) == 1
    assert "evidence_contract" in errors[0]


def test_all_6_missing_all_flagged():
    contract = TaskContract(behavior="x", verification="y", verification_tiers=["runtime"], owner_role="dev")
    task = Task(id="T5", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config())
    assert len(errors) == 6
    joined = " ".join(errors)
    for ref in [
        "spec_ref", "plan_ref", "tdd_ref",
        "critic_event_id", "critic_gate_ref", "evidence_contract",
    ]:
        assert ref in joined, f"expected {ref} in errors"


# ---------- enforcement flag tests ----------


def test_disabled_flag_skips_check():
    """When dev_requires_orchestrator_backlog=false, no enforcement."""
    contract = TaskContract(behavior="x", verification="y", verification_tiers=["runtime"], owner_role="dev")
    task = Task(id="T6", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=_config(enforce=False))
    assert errors == []


def test_dag_disabled_skips_check():
    """When workflow.dag is absent / WorkflowDagConfig defaults, no enforce."""
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(),  # default dag = disabled
    )
    contract = TaskContract(behavior="x", verification="y", verification_tiers=["runtime"], owner_role="dev")
    task = Task(id="T7", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=cfg)
    assert errors == []


# ---------- role kind gating ----------


def test_reader_role_not_subject_to_preflight():
    """arch is a reader; even with empty refs, no preflight errors."""
    contract = TaskContract(behavior="design x", verification="manual", verification_tiers=["manual_evidence"], owner_role="arch")
    task = Task(id="T8", title="design", contract=contract, assigned_to="arch")
    errors = validate_backlog_refs(task, config=_config())
    assert errors == []


def test_critic_review_test_judge_all_skipped():
    """All reader roles bypass the preflight."""
    cfg = _config()
    for role_name in ["arch", "critic", "review"]:
        contract = TaskContract(
            behavior="x", verification="y", verification_tiers=["runtime"],
            owner_role=role_name,
        )
        task = Task(id=f"T-{role_name}", title="t", contract=contract, assigned_to=role_name)
        errors = validate_backlog_refs(task, config=cfg)
        assert errors == [], f"{role_name} should not be checked, got {errors}"


def test_writer_role_via_owner_instance():
    """Task uses owner_instance (e.g. dev-1) instead of owner_role."""
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="dev", backend="mock", role_kind="writer",
                instance_id="dev-1",
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                dev_requires_orchestrator_backlog=True,
                required_backlog_refs=["spec_ref"],
            ),
        ),
    )
    contract = TaskContract(
        behavior="x", verification="y", verification_tiers=["runtime"],
        owner_instance="dev-1",
        # spec_ref deliberately empty
    )
    task = Task(id="TINST", title="t", contract=contract, assigned_to="dev-1")
    errors = validate_backlog_refs(task, config=cfg)
    assert len(errors) == 1
    assert "spec_ref" in errors[0]


# ---------- empty required_backlog_refs list ----------


def test_empty_required_refs_list_is_noop():
    """If config flags enforcement ON but lists 0 refs, treat as disabled."""
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                dev_requires_orchestrator_backlog=True,
                required_backlog_refs=[],
            ),
        ),
    )
    contract = TaskContract(behavior="x", verification="y", verification_tiers=["runtime"], owner_role="dev")
    task = Task(id="T9", title="t", contract=contract, assigned_to="dev")
    errors = validate_backlog_refs(task, config=cfg)
    assert errors == []


# ---------- validate_task_contract integration ----------


def test_validate_task_contract_includes_backlog_ref_errors(tmp_path: Path):
    """The 6-ref check must run as part of validate_task_contract, not just
    when called standalone. This is what the dispatcher actually calls."""
    contract = TaskContract(
        behavior="x", verification="y", verification_tiers=["runtime"],
        owner_role="dev",
        scope=["src/x.py"],
        # All 6 refs empty
    )
    task = Task(id="T10", title="t", contract=contract, assigned_to="dev")
    errors = validate_task_contract(
        task,
        config=_config(),
        project_root=tmp_path,
    )
    # All 6 refs should be flagged
    ref_errors = [e for e in errors if "required_backlog_refs" in e]
    assert len(ref_errors) == 6


def test_validate_task_contract_passes_full_contract(tmp_path: Path):
    """Full 6 refs + valid other fields → no errors."""
    task = Task(
        id="T11", title="t", contract=_full_contract(), assigned_to="dev",
    )
    errors = validate_task_contract(
        task, config=_config(), project_root=tmp_path,
    )
    # No backlog-ref errors. Other errors (if any from path validation etc)
    # are not related.
    ref_errors = [e for e in errors if "required_backlog_refs" in e]
    assert ref_errors == []


def test_arch_design_intake_skips_final_contract_preflight(tmp_path: Path):
    """Arch intake may start before the final implementation contract exists."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "memory").mkdir()
    cfg = _config()
    cfg.verification.contract.required = True
    cfg.roles[0].publishes = ["arch.proposal.done"]
    task = Task(
        id="T-ARCH",
        title="design intake",
        status="backlog",
        assigned_to="arch",
    )

    raw_errors = validate_task_contract(
        task,
        config=cfg,
        project_root=tmp_path,
    )
    assert any("contract.behavior is required" in e for e in raw_errors)

    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]
    assert orch._strict_contract_preflight_errors(  # type: ignore[attr-defined]
        task,
        cfg.roles[0],
    ) == []


# ---------- wire-up grep self-check ----------


def test_grep_validate_backlog_refs_called_in_validate_task_contract():
    """Wire-up self-check (CLAUDE.md anti-orphan): validate_task_contract
    must call validate_backlog_refs so the dispatcher preflight picks it up."""
    src = Path(__file__).resolve().parents[1] / "src/zf/core/task/contract_validation.py"
    text = src.read_text(encoding="utf-8")
    assert "def validate_backlog_refs" in text
    assert "validate_backlog_refs(task" in text  # call site
    # Both should appear:
    assert text.count("validate_backlog_refs") >= 2


def test_grep_dispatcher_uses_validate_task_contract():
    """Dispatcher MUST call validate_task_contract before dispatch."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/orchestrator_dispatch.py"
    )
    text = src.read_text(encoding="utf-8")
    assert "validate_task_contract" in text
    assert "_strict_contract_preflight_errors" in text
    assert "task.contract.invalid" in text
