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
    # This oracle proves artifact identity and completeness of the rendered copy.
    # Semantic quality belongs to Critic and the recorded artifact assessment;
    # valid Shared Spec / Task Spec documents need not use PRD/SDD/TDD headings.


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


def check_owner_checkpoint(events: list, run_id: str) -> dict[str, str]:
    owners = [e for e in events if e.type == "approval.requested" and e.payload.get("owner_route") == "plan_synth_owner_decision"]
    assert len(owners) == 1, "expected one real Owner checkpoint"
    owner = owners[0]
    decisions = [e for e in events if e.type in {"replan.owner_decision.approved", "replan.owner_decision.rejected"} and e.payload.get("checkpoint_id") == owner.payload["checkpoint_id"]]
    assert len(decisions) == 1, "missing unique Owner answer"
    decision = decisions[0]
    assert decision.actor == "web" and decision.payload.get("surface") == "web", "Owner answer did not come through Web"
    assert decision.payload.get("direct_adoption") is False, "Owner answer directly adopted the candidate"
    resolutions = [e for e in events if e.type == "plan.synth.owner_decision.resolved" and e.payload.get("decision_event_id") == decision.id]
    assert len(resolutions) == 1, "Owner answer was not consumed by runtime"
    resolution = resolutions[0]
    plans = [e for e in events if e.type == "plan.approval.requested"]
    assert len(plans) == 1, "missing unique regenerated Plan"
    plan = plans[0]
    invokes = [e for e in events if e.type == "workflow.invoke.requested"]
    assert len(invokes) == 1 and invokes[0].task_id == owner.task_id, "Owner checkpoint belongs to another Task"
    for event in (owner, decision, resolution, plan):
        assert {event.payload.get("workflow_run_id"), event.correlation_id} - {None, ""} == {run_id}, "Owner recovery changed Run"
        # Web decisions and Plan approvals may be Run-scoped. The checkpoint
        # joins them to the original Task; reject an explicit contradictory ID.
        assert event.task_id in {None, "", owner.task_id}, "Owner recovery changed Task"
    assert resolution.task_id == owner.task_id, "runtime resolved another Task"
    assert events.index(owner) < events.index(decision) < events.index(resolution) < events.index(plan)
    assert plan.payload["plan_id"] != owner.payload["plan_id"], "unreviewed candidate reused as final Plan"
    return {"checkpoint_id": owner.payload["checkpoint_id"], "candidate_plan_id": owner.payload["plan_id"],
            "decision_event_id": decision.id, "resolution_event_id": resolution.id, "final_plan_id": plan.payload["plan_id"]}


def read_generated_plan(approval: Any, preview: dict, events: list, *, state: Path, project: Path) -> tuple[str, str, str]:
    """Follow an explicitly reviewed evidence ref, never search for a plausible file."""
    from zf.runtime.plan_preview_artifacts import read_preview_artifact
    payload = approval.payload
    ref = str(preview.get("refs", {}).get("markdown_ref") or "")
    assert ref, "preview has no generated artifact ref"
    digest = str(payload.get("plan_artifact_digest") or "")
    if ref != payload.get("plan_artifact_ref") or not digest:
        # Pure critics can retain a non-existent local output path. The actual
        # planner document must be pinned in that Run/fanout's reviewed contract.
        source = next((e for e in events if e.id == payload["plan_id"]), None)
        assert source is not None, "missing bound Plan source event"
        run = payload.get("workflow_run_id") or payload.get("trace_id")
        assert run and source.payload.get("workflow_run_id") == run, "Plan source Run mismatch"
        refs = [*source.payload.get("evidence_refs", []), *source.payload.get("artifact_refs", [])]
        assert ref in refs, "resolved Plan is not explicit source evidence"
        fanout = source.payload.get("fanout_id")
        reviews = [e for e in events[:events.index(source)] if e.type == "fanout.synth.completed"
                   and e.payload.get("workflow_run_id") == run and e.payload.get("fanout_id") == fanout]
        assert fanout and reviews, "no same-Run fanout review"
        reviewed = reviews[-1].payload
        contract_digest = reviewed.get("reviewed_plan_candidate_digest")
        assert contract_digest, "review has no candidate digest"
        contract = json.loads(read_preview_artifact(
            reviewed["plan_synth_contract_ref"], state_dir=state, project_root=project, digest=contract_digest,
        ))
        assert contract.get("workflow_run_id") == run and contract.get("fanout_id") == fanout, "review contract binding mismatch"
        bindings = [b for b in contract.get("source_bindings", []) if b.get("ref") == ref]
        assert len(bindings) == 1 and bindings[0].get("sha256"), "Plan absent from reviewed digest bindings"
        digest = bindings[0]["sha256"]
        declared = payload.get("plan_artifact_digest")
        assert not declared or declared.removeprefix("sha256:") == digest.removeprefix("sha256:"), "approval digest disagrees with reviewed Plan"
    markdown = read_preview_artifact(ref, state_dir=state, project_root=project, digest=digest)
    return ref, digest.removeprefix("sha256:"), markdown


def audit(project: Path, *, journey: str, owner_checkpoint: bool = False) -> dict[str, Any]:
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
    ref, digest, markdown = read_generated_plan(approval, preview, events, state=state, project=project)
    check_preview(preview, expected_plan_id=plan_id, expected_markdown=markdown)
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
    owner_evidence = check_owner_checkpoint(events, run_id) if owner_checkpoint else None
    return {"audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "owner_checkpoint": owner_evidence,
            "workflow_run_id": run_id, "plan_id": plan_id, "plan_sha256": digest,
            "plan_artifact_ref": ref, "parent_task_id": task.id, "provider_roles_dispatched": sorted(dispatched),
            "channel_confirmation_event_id": confirmations[0].id if confirmations else None}
