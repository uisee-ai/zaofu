"""Evidence lineage and bounded final-gate additions for reviewed Task reuse."""
from types import SimpleNamespace
from typing import Any

from zf.runtime.candidate_continuation import _json
from zf.runtime.plan_output_recovery import _acceptance_contract
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode


def historical_task_view(service: Any, events: list, candidate: Any, archived: list) -> Any:
    tasks = {}
    for task_id in candidate.payload["completed_task_ids"]:
        task = next((t for t in reversed(archived) if t.id == task_id and t.status == "done"
                     and current_task_contract_identity(t)["task_map_generation"]
                     == candidate.payload["task_map_generation"]), None)
        if task is None:
            raise ValueError("completion reuse lacks previous archived authority: " + task_id)
        tasks[task_id] = task
    return SimpleNamespace(state_dir=service.state_dir,
        project_root=service.project_root or service.state_dir.parent, config=service.config,
        event_log=SimpleNamespace(read_all=lambda: events), task_store=SimpleNamespace(get=tasks.get))


def completion_evidence_additions(old: Any, current: Any) -> list[dict]:
    """Only append evidence paths to an unchanged, future Candidate command.

    The exact delta still requires a separate Owner review. No Task-local gate,
    read-only audit, command, permission, or existing evidence may be waived.
    """
    before, after = _acceptance_contract(old.contract), _acceptance_contract(current.contract)
    if before == after:
        return []
    if task_pipeline_entry_mode(old) != "standard" or task_pipeline_entry_mode(current) != "standard":
        raise ValueError("completion reuse cannot change acceptance/execution contract: " + current.id)
    previous = before.get("validation", {}).get("commands", [])
    commands = after.get("validation", {}).get("commands", [])
    additions = []
    if len(previous) == len(commands):
        for prior, command in zip(previous, commands):
            if prior == command:
                continue
            if not isinstance(prior, dict) or not isinstance(command, dict):
                break
            paths = command.get("producer_paths")
            old_paths = prior.get("producer_paths", [])
            if (prior.get("owner") != "candidate_verify" or command.get("owner") != "candidate_verify"
                    or not isinstance(paths, list) or not paths or not isinstance(old_paths, list)
                    or any(not isinstance(p, str) or not p.strip() for p in paths)
                    or len(paths) != len(set(paths)) or not set(old_paths) <= set(paths)):
                break
            # Keep every other field exact, including command identity and gate ownership.
            changed = dict(command)
            if "producer_paths" in prior:
                changed["producer_paths"] = old_paths
            else:
                changed.pop("producer_paths", None)
            if changed != prior:
                break
            additions.append({"command_id": prior["id"], "producer_paths": paths})
            command.clear()
            command.update(changed)
    if before != after:
        raise ValueError("completion reuse cannot change acceptance/execution contract: " + current.id)
    return additions


def validate_predecessor(state: Any, row: dict, manifest: dict) -> None:
    """Recheck every retained immutable decision, without treating it as a pass."""
    seen = set()
    while row.get("predecessor_reuse_ref"):
        ref = row["predecessor_reuse_ref"]
        digest = ref.get("sha256")
        if not digest or digest in seen:
            raise ValueError("completion reuse predecessor lineage is invalid")
        seen.add(digest)
        receipt = _json(state, ref)
        previous = receipt.get("manifest", {})
        from zf.runtime.plan_completion_reuse import SCHEMA
        from zf.runtime.candidate_continuation import digest as body_digest

        prior = next((r for r in previous.get("tasks", []) if r["task_id"] == row["task_id"]), None)
        if (receipt.get("schema_version") != SCHEMA or not receipt.get("approval_ref")
                or not receipt.get("rationale") or not prior
                or previous.get("manifest_digest") != body_digest({
                    k: v for k, v in previous.items() if k != "manifest_digest"})
                or previous.get("workflow_run_id") != manifest.get("workflow_run_id")
                or previous.get("task_map_generation") != manifest.get("prior_task_map_generation")
                or any(previous.get(k) != manifest.get(k) for k in ("candidate_head_commit", "candidate_base_commit"))
                or prior["current_identity"] != row.get("previous_identity")
                or prior["current_contract"] != row.get("previous_contract")
                or (prior.get("candidate_evidence_additions")
                    and not (receipt.get("candidate_evidence_review") or {}).get(row["task_id"]))
                or any(prior.get(k) != row.get(k) for k in (
                    "completion_event_id", "verification_event_id", "prior_identity", "prior_contract"))):
            raise ValueError("completion reuse predecessor binding differs")
        row, manifest = prior, previous
