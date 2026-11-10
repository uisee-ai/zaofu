"""Read-only identity and approval oracle for the browser-generated PRD/Plan."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.config.loader import load_config
from zf.core.events import EventLog
from zf.core.task.store import TaskStore
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.workflow_operation import reduce_workflow_operations


def check_preview(preview: dict[str, Any], *, expected_plan_id: str, expected_markdown: str) -> None:
    """Reject summary-only, wrong-identity and truncated previews independently."""
    assert preview.get("plan_id") == expected_plan_id, "preview belongs to another Plan"
    assert preview.get("content_profile") == "derived_plan_full", "full Plan claim missing"
    assert preview.get("markdown") == expected_markdown, "preview content differs from its generated artifact"
    assert expected_markdown.strip(), "empty Plan"
    for section in ("PRD", "SDD", "TDD"):
        assert section in expected_markdown, f"missing {section}"


def check_dispatches(attempts: list[dict], operations: dict, role_kinds: dict) -> set[str]:
    dispatched = set()
    for attempt in attempts:
        operation = operations.get(attempt.get("operation_id"), {})
        role = operation.get("role_instance", "")
        assert role and role in role_kinds, "dispatch has no known provider role"
        assert role_kinds[role] == "reader", "implementation dispatched before approval"
        dispatched.add(role)
    assert dispatched, "no actual planning dispatch"
    return dispatched


def check_config_handoff(events: list) -> dict[str, Any]:
    invokes = [i for i, e in enumerate(events) if e.type == "workflow.invoke.requested"]
    loops = [(i, e) for i, e in enumerate(events) if e.type == "loop.started"]
    starts = [(i, e) for i, e in enumerate(events) if e.type == "runtime.start.requested"]
    assert len(invokes) == 1 and len(loops) == 2 and len(starts) == 1, "missing unique warm consumer handoff"
    (old_index, old), (new_index, new) = loops
    start_index, start = starts[0]
    assert old.payload["pid"] != new.payload["pid"] == start.payload["pid"], "old consumer reused"
    assert old.payload["effective_config_sha256"] != new.payload["effective_config_sha256"], "policy unchanged"
    assert old_index < start_index < new_index < invokes[0], "invoke published before new consumer ready"
    assert any(e.type == "loop.shutdown_requested" for e in events[old_index:start_index]), "old consumer not stopped"
    assert not any(e.type == "workflow.invoke.rejected" for e in events), "new consumer rejected invoke"
    return {"old_pid": old.payload["pid"], "new_pid": new.payload["pid"],
            "old_config_sha256": old.payload["effective_config_sha256"],
            "new_config_sha256": new.payload["effective_config_sha256"]}


def audit(project: Path, *, journey: str) -> dict[str, Any]:
    config = load_config(project / "zf.yaml")
    state = project / config.project.state_dir
    events = EventLog(state / "events.jsonl").read_all()
    invokes = [e for e in events if e.type == "workflow.invoke.requested"]
    assert len(invokes) == 1, "UI must start exactly one Workflow"
    invoked = invokes[0]
    run_id = invoked.payload.get("workflow_run_id") or invoked.correlation_id
    approvals = [e for e in events if e.type == "plan.approval.requested"]
    assert len(approvals) == 1, "expected one generated Plan awaiting approval"
    approval = approvals[0]
    assert run_id in {approval.correlation_id, approval.payload.get("trace_id"), approval.payload.get("workflow_run_id")}, "Plan has wrong Run"
    assert config.version == "5.0.1" and config.workflow.plan_approval_enabled
    assert not any(e.type == "plan.approved" for e in events), "Plan approved beyond test scope"
    operations = reduce_workflow_operations(events)
    roles = {role.instance_id: role.role_kind for role in config.roles}
    dispatched = check_dispatches(
        [event.payload for event in events if event.type == "task.attempt.sent"], operations, roles,
    )
    plan_id = str(approval.payload["plan_id"])
    preview = build_plan_preview(state, events, plan_id=plan_id, project_root=project)
    ref = str(approval.payload.get("plan_artifact_ref") or "")
    assert ref, "approval has no full Plan artifact"
    from zf.runtime.plan_preview_artifacts import preview_artifact_path
    path = preview_artifact_path(ref, state_dir=state, project_root=project)
    markdown = path.read_text()
    check_preview(preview, expected_plan_id=plan_id, expected_markdown=markdown)
    digest = hashlib.sha256(markdown.encode()).hexdigest()
    assert digest == str(approval.payload.get("plan_artifact_digest", "")).removeprefix("sha256:"), "Plan digest mismatch"
    tasks = TaskStore(state / "kanban.json").list_all()
    assert len(tasks) == 1, "only the parent Task may exist before plan approval"
    task = tasks[0]
    assert task.id == invoked.task_id, "Workflow is bound to another Task"
    confirmations = [e for e in events if e.type == "channel.consensus.reached"]
    if journey == "kanban":
        assert not any(e.type == "channel.created" for e in events), "Kanban independence was replaced with Channel"
        assert str(task.contract.source_ref).endswith("docs/prd/ui-requirement.md")
    else:
        assert len(confirmations) == 1, "Channel has no unique owner-confirmed PRD"
        binding = task.contract.evidence_contract
        confirmed = confirmations[0].payload
        assert binding.get("channel_prd_digest") == confirmed.get("artifact_digest"), "Channel digest changed at Task handoff"
        assert binding.get("prd_revision") == confirmed.get("prd_revision"), "Channel revision changed at Task handoff"
        assert binding.get("channel_id") == confirmed.get("channel_id"), "Task belongs to another Channel"
        assert confirmed.get("confirmed_by") == "web", "PRD was not confirmed through UI"
        assert confirmed.get("product_mode") == "multi_lens", "PRD did not come from the multi-role discussion"
        assert len(set(confirmed.get("signed_by", []))) >= 4, "missing discussion role signatures"
    return {"audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "workflow_run_id": run_id, "plan_id": plan_id, "plan_sha256": digest,
            "plan_artifact_ref": ref, "parent_task_id": task.id, "provider_roles_dispatched": sorted(dispatched),
            "channel_confirmation_event_id": confirmations[0].id if confirmations else None}
