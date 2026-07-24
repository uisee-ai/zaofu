"""Tests for new Layer 1 housekeeper duties (E5)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.housekeeping import (
    apply_agent_usage_event,
    apply_memory_note_event,
    apply_task_contract_event,
    arch_proposal_contract_update_event,
    spec_ingest_suggested_event,
)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    return sd


# -- agent.usage → CostTracker --

def test_agent_usage_event_recorded_in_cost_tracker(state_dir: Path):
    tracker = CostTracker(state_dir / "cost.jsonl")
    event = ZfEvent(
        type="agent.usage",
        actor="dev",
        payload={
            "session_id": "abc",
            "total_cost_usd": 0.0,
            "usage": {"input_tokens": 1000, "output_tokens": 500},
            "num_turns": 1,
        },
    )
    apply_agent_usage_event(tracker, event)
    totals = tracker.per_role_totals()
    assert "dev" in totals


def test_agent_usage_with_multiple_calls_accumulates(state_dir: Path):
    tracker = CostTracker(state_dir / "cost.jsonl")
    for i in range(3):
        apply_agent_usage_event(tracker, ZfEvent(
            type="agent.usage",
            actor="dev",
            payload={"usage": {"input_tokens": 100, "output_tokens": 50}},
        ))
    totals = tracker.per_role_totals()
    assert totals["dev"].entries == 3


def test_agent_usage_event_with_missing_actor_is_skipped(state_dir: Path):
    tracker = CostTracker(state_dir / "cost.jsonl")
    apply_agent_usage_event(tracker, ZfEvent(
        type="agent.usage",
        actor=None,
        payload={"usage": {"input_tokens": 100}},
    ))
    assert tracker.per_role_totals() == {}


# -- memory.note → MemoryStore --

def test_memory_note_event_writes_to_role_memory(state_dir: Path):
    store = MemoryStore(state_dir / "memory")
    event = ZfEvent(
        type="memory.note",
        actor="dev",
        payload={"mem_type": "decision", "content": "use bcrypt for password hashing"},
    )
    apply_memory_note_event(store, event)
    entries = store.get("dev")
    assert any("bcrypt" in e.content for e in entries)


def test_memory_note_event_shared_when_no_actor(state_dir: Path):
    store = MemoryStore(state_dir / "memory")
    apply_memory_note_event(store, ZfEvent(
        type="memory.note",
        actor=None,
        payload={"mem_type": "context", "content": "project uses Python 3.12"},
    ))
    shared = store.get(None)
    assert any("Python 3.12" in e.content for e in shared)


def test_memory_note_invalid_type_skipped(state_dir: Path):
    store = MemoryStore(state_dir / "memory")
    apply_memory_note_event(store, ZfEvent(
        type="memory.note",
        actor="dev",
        payload={"mem_type": "bogus", "content": "x"},
    ))
    assert store.get("dev") == []


def test_memory_note_with_source_and_trigger_event_id(state_dir: Path):
    """Forward-compat: auto_promote memory.note carries extra payload fields
    that the kernel must accept without breaking."""
    store = MemoryStore(state_dir / "memory")
    apply_memory_note_event(store, ZfEvent(
        type="memory.note",
        actor=None,
        payload={
            "mem_type": "context",
            "content": "candidate F-x conflict on packages/core/package.json",
            "source": "auto_promote",
            "trigger_event_id": "evt-deadbeef",
        },
    ))
    shared = store.get(None)
    assert any("conflict" in e.content for e in shared)


# -- task.contract.update → TaskStore --

def test_sprint_contract_event_writes_contract_to_task(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "JWT login works",
                "verification": "pytest tests/test_login.py",
                "scope": ["src/auth.py", "tests/test_login.py"],
                "exclusions": ["do not touch session.py"],
                "acceptance": "exit_code=0",
            }
        },
    )
    apply_task_contract_event(ts, event)
    task = ts.get("T1")
    assert task.contract.behavior == "JWT login works"
    assert "src/auth.py" in task.contract.scope


def test_materialized_full_contract_update_is_lossless(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    contract = TaskContract(
        behavior="deliver exact result",
        verification="grep -qx expected app/result.txt",
        validation={
            "commands": [{
                "id": "light-verification-1",
                "command": "grep -qx expected app/result.txt",
                "acceptance_ids": ["ac-result"],
                "owner": "impl_self_check",
                "tier": "task_non_smoke",
                "deterministic": True,
                "reusable": True,
                "timeout_seconds": 900,
            }],
        },
        scope=["app/**"],
        acceptance_criteria=[{
            "acceptance_id": "ac-result",
            "statement": "result is exact",
            "mandatory": True,
        }],
        goal_claim_ids=["claim-result"],
        evidence_contract={
            "source": "refactor_task_map",
            "source_refs": {"task_map_ref": ".zf/artifacts/run/task_map.json"},
        },
    )
    ts.add(Task(id="T1", title="x", contract=contract))

    apply_task_contract_event(ts, ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id="T1",
        payload={
            "source": "task_map_materialization",
            "contract": asdict(contract),
        },
    ))

    projected = ts.get("T1").contract
    assert asdict(projected) == asdict(contract)
    assert projected.validation["commands"][0]["id"] == "light-verification-1"
    assert projected.acceptance_criteria[0]["acceptance_id"] == "ac-result"
    assert projected.goal_claim_ids == ["claim-result"]


def test_sprint_contract_event_preserves_env_prefixed_absolute_python_command(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    verification = (
        "Run `PYTHONPATH=src /path/to/zaofu/.venv/bin/python "
        "-m pytest tests/test_word_stats.py`."
    )
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "word stats works",
                "verification": verification,
            }
        },
    )

    apply_task_contract_event(ts, event)

    task = ts.get("T1")
    assert task.contract.verification == (
        "PYTHONPATH=src /path/to/zaofu/.venv/bin/python "
        "-m pytest tests/test_word_stats.py"
    )


def test_sprint_contract_event_projects_blocked_by_to_task(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="base", status="done"))
    ts.add(Task(id="T2", title="follow"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T2",
        payload={
            "contract": {
                "behavior": "follow after base",
                "verification": "pytest",
                "blocked_by": ["T1"],
            }
        },
    )

    apply_task_contract_event(ts, event)

    task = ts.get("T2")
    assert task.blocked_by == ["T1"]
    assert task.contract.behavior == "follow after base"


def test_sprint_contract_event_preserves_dag_refs_and_evidence_contract(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="dev task"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "build slice",
                "verification": "pnpm test",
                "spec_ref": "docs/specs/cangjie-agent-sdd.md",
                "plan_ref": "docs/plans/cangjie-agent-plan.md",
                "tdd_ref": "docs/plans/cangjie-agent-tdd.md",
                "critic_gate_ref": "docs/plans/cangjie-agent-critic-gate.md",
                "critic_event_id": "evt-critic",
                "dispatch_id": "pending:layer1-assignment",
                "affected_files": ["src/a.ts"],
                "explicit_non_goals": ["no live provider"],
                "evidence_contract": {
                    "dev_done_must_include": ["dispatch_id"],
                },
            }
        },
    )

    apply_task_contract_event(ts, event)

    task = ts.get("T1")
    assert task.contract.spec_ref == "docs/specs/cangjie-agent-sdd.md"
    assert task.contract.plan_ref == "docs/plans/cangjie-agent-plan.md"
    assert task.contract.tdd_ref == "docs/plans/cangjie-agent-tdd.md"
    assert task.contract.critic_gate_ref.endswith("critic-gate.md")
    assert task.contract.critic_event_id == "evt-critic"
    assert task.contract.dispatch_id == "pending:layer1-assignment"
    assert task.contract.affected_files == ["src/a.ts"]
    assert task.contract.explicit_non_goals == ["no live provider"]
    assert task.contract.evidence_contract["dev_done_must_include"] == [
        "dispatch_id",
    ]


def test_sprint_contract_event_preserves_env_prefixed_pnpm_verification(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="pnpm task"))
    command = (
        "PATH=/path/to/node/bin:$PATH "
        "pnpm install --frozen-lockfile"
    )
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={"contract": {"behavior": "install", "verification": command}},
    )

    apply_task_contract_event(ts, event)

    task = ts.get("T1")
    assert task.contract.verification == command


def test_sprint_contract_event_preserves_existing_fields_on_partial_update(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(
        id="T1",
        title="x",
        contract=TaskContract(
            behavior="hello works",
            verification="python3 -m pytest tests/test_greet.py",
            scope=["src/greet.py"],
            exclusions=[],
            acceptance="exit_code=0",
            rework_to="",
        ),
    ))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "summary": "review-only context",
                "acceptance": "exit_code=0",
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.behavior == "hello works"
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py"]


def test_sprint_contract_event_coerces_verification_list(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "verification": [
                    "python3 -m pytest tests/test_greet.py",
                    "python3 -m pytest",
                ],
            }
        },
    )

    apply_task_contract_event(ts, event)

    assert ts.get("T1").contract.verification == (
        "python3 -m pytest tests/test_greet.py\npython3 -m pytest"
    )


def test_sprint_contract_event_accepts_layer2_aliases(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "summary": "Implement greet hello.",
                "verify": ["python3 -m pytest tests/test_greet.py"],
                "files": ["src/greet.py", "tests/test_greet.py"],
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.behavior == "Implement greet hello."
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]


def test_sprint_contract_event_preserves_validation_spec(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "Create byte-exact proof.",
                "validation": {
                    "kind": "byte_exact",
                    "path": "proof.txt",
                    "expected": "autoresearch-proof",
                },
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.validation == {
        "kind": "byte_exact",
        "path": "proof.txt",
        "expected": "autoresearch-proof",
    }


def test_sprint_contract_event_coerces_wave_label_without_dropping_contract(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "Write stuck recovery note.",
                "verification": "test -f docs/stuck-recovery.md",
                "verification_tiers": ["runtime"],
                "owner_role": "arch",
                "scope": ["docs/stuck-recovery.md"],
                "wave": "wave-1",
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.behavior == "Write stuck recovery note."
    assert task.contract.verification == "test -f docs/stuck-recovery.md"
    assert task.contract.verification_tiers == ["runtime"]
    assert task.contract.wave == 1


def test_sprint_contract_event_normalizes_goal_acceptance_shape(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "goal": "Create src/greet.py with hello(name).",
                "scope": {
                    "files": ["src/greet.py", "tests/test_greet.py"],
                    "allowed_changes": "task files only",
                },
                "steps": [
                    "Inspect repository layout.",
                    "Run python3 -m pytest tests/test_greet.py and fix failures.",
                ],
                "acceptance": [
                    "hello(\"Alice\") returns \"Hello, Alice!\".",
                    "python3 -m pytest tests/test_greet.py passes.",
                ],
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.behavior == "Create src/greet.py with hello(name)."
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]
    assert "Hello, Alice!" in task.contract.acceptance


def test_sprint_contract_event_normalizes_required_command_and_scope_in(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "summary": "Implement greet hello.",
                "verification": {
                    "required_command": "python3 -m pytest tests/test_greet.py",
                    "required_behavior": "three cases pass",
                },
                "scope": {
                    "in": ["src/greet.py", "tests/test_greet.py"],
                    "out": ["unrelated files"],
                },
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.behavior == "Implement greet hello."
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]


def test_sprint_contract_event_uses_shared_files_when_scope_is_prose(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="x"))
    event = ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="T1",
        payload={
            "contract": {
                "behavior": "Plan and implement greet hello.",
                "verification": (
                    "Run the required verification command: "
                    "python3 -m pytest tests/test_greet.py"
                ),
                "verification_tiers": ["manual_evidence"],
                "scope": {
                    "include": [
                        "Repository layout check",
                        "Implementation/test plan",
                    ],
                },
                "shared_files": ["src/greet.py", "tests/test_greet.py"],
            }
        },
    )

    apply_task_contract_event(ts, event)
    task = ts.get("T1")

    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]


def test_arch_proposal_projects_final_runtime_contract(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(
        id="T1",
        title="x",
        contract=TaskContract(
            behavior="Design the implementation plan.",
            verification="Arch must hand off python3 -m pytest tests/test_greet.py",
            verification_tiers=["manual_evidence"],
            scope=[],
            owner_role="arch",
            shared_files=["src/greet.py", "tests/test_greet.py"],
        ),
    ))
    event = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "summary": "Create greet.hello and focused tests.",
            "file_plan": [
                {"path": "src/greet.py", "action": "create"},
                {"path": "tests/test_greet.py", "action": "create"},
            ],
            "test_plan": [
                {
                    "verification": "python3 -m pytest tests/test_greet.py",
                    "cases": [
                        {"name": "normal", "expected": "Hello, Alice!"},
                        {"name": "empty", "expected": "Hello, !"},
                    ],
                }
            ],
        },
    )

    update = arch_proposal_contract_update_event(ts, event)
    assert update is not None
    apply_task_contract_event(ts, update)

    task = ts.get("T1")
    assert task.contract.behavior == "Create greet.hello and focused tests."
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.verification_tiers == ["runtime"]
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]
    assert task.contract.owner_role == "dev"
    assert task.contract.rework_to == "dev"


def test_arch_proposal_contract_update_keeps_shared_exclusive_disjoint(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(
        id="T1",
        title="docs smoke",
        contract=TaskContract(
            behavior="temporary arch contract",
            exclusive_files=["docs/records/smoke.md"],
        ),
    ))
    event = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "summary": "Record docs smoke observation.",
            "file_plan": [
                {"path": "docs/records/smoke.md", "action": "create"},
            ],
            "test_plan": [
                {
                    "verification": "git diff --check",
                    "cases": [{"name": "docs formatting", "expected": "clean"}],
                }
            ],
        },
    )

    update = arch_proposal_contract_update_event(ts, event)
    assert update is not None
    apply_task_contract_event(ts, update)

    task = ts.get("T1")
    assert task.contract.scope == ["docs/records/smoke.md"]
    assert task.contract.shared_files == []
    assert task.contract.exclusive_files == ["docs/records/smoke.md"]


def test_arch_proposal_contract_update_filters_absolute_internal_refs(
    state_dir: Path,
):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="docs smoke"))
    event = ZfEvent(
        type="arch.proposal.done",
        id="evt-arch",
        actor="arch",
        task_id="T1",
        payload={
            "summary": "Record docs smoke observation.",
            "file_plan": [
                {"path": "docs/records/smoke.md", "action": "create"},
            ],
            "test_plan": [
                {
                    "verification": "git diff --check",
                    "cases": [{"name": "docs formatting", "expected": "clean"}],
                }
            ],
            "artifact_refs": [
                str(state_dir.parent / "docs/records/smoke.md"),
                "docs/records/smoke.md",
            ],
            "evidence_refs": [
                str(state_dir / "briefings/arch-T1.md"),
                "/tmp/outside-evidence.md",
            ],
        },
    )

    update = arch_proposal_contract_update_event(ts, event)
    assert update is not None
    apply_task_contract_event(ts, update)

    task = ts.get("T1")
    assert task.contract.handoff_artifacts == [
        "arch.proposal.done:evt-arch",
        "docs/records/smoke.md",
    ]


def test_sprint_contract_event_missing_task_skipped(state_dir: Path):
    ts = TaskStore(state_dir / "kanban.json")
    apply_task_contract_event(ts, ZfEvent(
        type="task.contract.update",
        actor="orchestrator",
        task_id="nonexistent",
        payload={"contract": {"behavior": "x"}},
    ))
    assert ts.get("nonexistent") is None


# ---------------------------------------------------------------------------
# P2 #2 (backlog 2026-05-14): spec.ingest.suggested hook on arch.proposal.done
# ---------------------------------------------------------------------------


class TestSpecIngestSuggested:
    def test_suggests_when_spec_path_in_payload(self, tmp_path: Path):
        spec = tmp_path / "spec.md"
        spec.write_text("---\nspec: x\ntasks:\n- t\n---\n# body\n", encoding="utf-8")
        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={"spec_path": str(spec)},
        )
        suggest = spec_ingest_suggested_event(event)
        assert suggest is not None
        assert suggest.type == "spec.ingest.suggested"
        assert suggest.task_id == "T1"
        assert suggest.payload["spec_paths"] == [str(spec)]
        assert suggest.payload["source"] == "arch.proposal.done"
        assert "zf spec ingest" in suggest.payload["command"]

    def test_suggests_when_evidence_refs_contain_docs_md(self, tmp_path, monkeypatch):
        spec = tmp_path / "docs" / "plans" / "vs1.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("---\nspec: vs1\ntasks:\n- t\n---\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={
                "evidence_refs": ["docs/plans/vs1.md", "git:abc123"],
            },
        )
        suggest = spec_ingest_suggested_event(event)
        assert suggest is not None
        assert "docs/plans/vs1.md" in suggest.payload["spec_paths"]

    def test_no_suggestion_when_spec_lacks_frontmatter(self, tmp_path):
        spec = tmp_path / "plain.md"
        spec.write_text("# just a title\nno frontmatter here\n", encoding="utf-8")
        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={"spec_path": str(spec)},
        )
        assert spec_ingest_suggested_event(event) is None

    def test_no_suggestion_when_spec_path_missing(self, tmp_path):
        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={"spec_path": str(tmp_path / "does-not-exist.md")},
        )
        assert spec_ingest_suggested_event(event) is None

    def test_no_suggestion_when_payload_empty(self):
        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={},
        )
        assert spec_ingest_suggested_event(event) is None

    def test_only_fires_on_arch_proposal_done(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("---\nspec: x\n---\n", encoding="utf-8")
        event = ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
            payload={"spec_path": str(spec)},
        )
        assert spec_ingest_suggested_event(event) is None

    def test_dedups_paths(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("---\nspec: x\n---\n", encoding="utf-8")
        event = ZfEvent(
            type="arch.proposal.done",
            actor="arch",
            task_id="T1",
            payload={
                "spec_path": str(spec),
                "specs": [str(spec)],
            },
        )
        suggest = spec_ingest_suggested_event(event)
        assert suggest is not None
        assert suggest.payload["spec_paths"] == [str(spec)]  # not duplicated
