"""Bind explicitly deferred audit criteria to the downstream frozen Candidate."""
from collections.abc import Mapping
from typing import Any

from zf.runtime.verification_commands import (
    acceptance_required_for_stage, verification_command_required_for_stage,
)

FROZEN_CANDIDATE_IDS = "frozen_candidate_acceptance_ids"


def bind_frozen_audit_scope(
    evidence: Mapping[str, Any], criteria: list[dict], commands: list[dict],
    *, task_id: str, write_paths: Any,
) -> None:
    """Annotate fresh normalized records. The Candidate aggregates the full snapshot."""
    if FROZEN_CANDIDATE_IDS not in evidence:
        return

    def invalid(reason: str) -> ValueError:
        return ValueError(f"{task_id}.evidence_contract.{FROZEN_CANDIDATE_IDS}: {reason}")

    ids = evidence[FROZEN_CANDIDATE_IDS]
    if (not isinstance(ids, list) or not ids
            or not all(isinstance(item, str) and item and item == item.strip() for item in ids)
            or len(set(ids)) != len(ids)):
        raise invalid("expected distinct nonempty acceptance IDs")
    if evidence.get("runtime_only") is not True or evidence.get("write_free") is not True or write_paths != []:
        raise invalid("requires a read-only audit with empty write scope")
    selected = set(ids)
    known = {item["acceptance_id"]: item for item in criteria}
    if not selected <= known.keys():
        raise invalid("unknown acceptance ID")
    if len(known) != len(criteria):
        raise invalid("ambiguous acceptance ID")
    local = [item for key, item in known.items() if key not in selected]
    by_id = {c["id"]: c for c in commands}
    # Explicit AC-to-command bindings keep legacy fallback matching out of this opt-in.
    if any(not item["verification_command_ids"] or not set(item["verification_command_ids"]) <= by_id.keys()
           for item in criteria):
        raise invalid("every criterion requires explicit known command IDs")
    from zf.runtime.task_contract_snapshot import snapshot_verification_route

    owner, _ = snapshot_verification_route(local, commands=commands, task_id=task_id)
    baseline = [item for item in local if item["mandatory"]
                and acceptance_required_for_stage(item, verification_owner=owner)]
    if not baseline:
        raise invalid("no mandatory criterion for the initial verification route")
    for item in baseline:
        if not any(verification_command_required_for_stage(by_id[cid], verification_owner=owner, task_id=task_id)
                   for cid in item["verification_command_ids"]):
            raise invalid("no independently executable mandatory baseline command for " + item["acceptance_id"])
    final_ids = set()
    for key in ids:
        item = known[key]
        if not item["mandatory"] or item["verification_owner"] != "candidate_verify":
            raise invalid("only mandatory candidate_verify criteria may be deferred")
        final_ids.update(item["verification_command_ids"])
    local_ids = {cid for item in local for cid in item["verification_command_ids"]}
    for cid in final_ids - local_ids:
        command = by_id[cid]
        if (command["owner"] != "candidate_verify" or command.get("rolling_smoke")
                or set(command["acceptance_ids"]) - selected):
            raise invalid(f"command {cid!r} has a local, smoke or non-Candidate obligation")
        command["frozen_candidate_only"] = True
    for key in ids:
        known[key]["frozen_candidate_only"] = True
