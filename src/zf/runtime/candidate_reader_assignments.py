"""Reader responsibilities declared in the approved product acceptance spec."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import canonical_json_sha256


def candidate_acceptance_id(task_id: str, acceptance_id: str) -> str:
    # Preserve the aggregate contract's historical task-qualified AC identity.
    encoded = json.dumps({"task_id": task_id, "acceptance_id": acceptance_id},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "candidate-ac-" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def validate_reader_assignments(spec: Mapping[str, Any]) -> None:
    if "candidate_reader_assignments" not in spec:
        return
    rows = spec["candidate_reader_assignments"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("candidate_reader_assignments requires at least one Reader")
    roles: set[str] = set()
    journeys: set[str] = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {"role_instance", "acceptance", "journey_ids", "plan_reads"}
                or not _identifier(row.get("role_instance")) or row["role_instance"] in roles):
            raise ValueError("candidate_reader_assignments requires unique role instances")
        roles.add(row["role_instance"])
        acceptance = row.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            raise ValueError("each Candidate Reader needs explicit task-qualified acceptance")
        keys = set()
        for item in acceptance:
            if (not isinstance(item, dict) or set(item) != {"task_id", "acceptance_id"}
                    or not all(_identifier(v) for v in item.values())):
                raise ValueError("Candidate Reader acceptance requires task_id and acceptance_id")
            key = (item["task_id"], item["acceptance_id"])
            if key in keys:
                raise ValueError("duplicate Candidate Reader acceptance within one assignment")
            keys.add(key)
        ids = row.get("journey_ids")
        if (not isinstance(ids, list) or not all(_identifier(v) for v in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Candidate Reader journey_ids must be a list of distinct IDs")
        journeys.update(ids)
        reads = row.get("plan_reads", {})
        if not isinstance(reads, dict):
            raise ValueError("Candidate Reader plan_reads must map Plan port names to JSON paths")
        for port, paths in reads.items():
            if (not _identifier(port) or not isinstance(paths, list) or
                    not all(isinstance(p, str) and re.fullmatch(r"\$(?:\.[A-Za-z0-9_-]+|\[\d+\])*", p)
                            for p in paths) or len(set(paths)) != len(paths)):
                raise ValueError("Candidate Reader plan_reads contains invalid paths")
    if not isinstance(spec.get("user_journeys"), list) or any(
        not isinstance(j, dict) or not _identifier(j.get("journey_id")) for j in spec["user_journeys"]
    ):
        raise ValueError("Candidate Reader assignments require stable journey IDs")
    known = {j["journey_id"] for j in spec["user_journeys"]}
    required = {j["journey_id"] for j in spec["user_journeys"] if j.get("mandatory", True)}
    if journeys - known or required - journeys:
        raise ValueError("Candidate Reader journey assignments do not cover the frozen product spec")


def assigned_spec(state: Path, freeze: Mapping[str, Any]) -> dict:
    from zf.runtime.product_acceptance import product_acceptance_binding_from_package

    ref = freeze.get("plan_artifact_package_ref")
    if not ref:
        return {}
    binding = product_acceptance_binding_from_package(state, {
        "ref": ref, "sha256": freeze.get("plan_artifact_package_digest")}, validate_ports=False)
    spec = binding.get("spec", {})
    if "candidate_reader_assignments" not in spec:
        return {}
    if any(spec.get(k) != freeze.get(k) for k in ("workflow_run_id", "task_map_generation")):
        raise ValueError("Candidate Reader assignments belong to another Run or task map")
    return spec


def approved_roster_ref(state_dir: Path, events, freeze: Mapping[str, Any], spec: Mapping[str, Any]) -> dict:
    from zf.runtime.orchestrator_agent_run_plan import current_run_plan_admission

    admitted = current_run_plan_admission(events, require_kernel=True, **{k: str(freeze.get(k) or "") for k in
        ("workflow_run_id", "task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest")})
    digest = canonical_json_sha256(spec.get("candidate_reader_assignments", []))
    ref = admitted.get("execution_plan_ref") or {}
    if not ref.get("sha256") or not ref.get("ref") or admitted.get("candidate_reader_assignments_digest") != digest:
        raise ValueError("frozen Candidate Reader roster lacks the current OA decision")
    from zf.runtime.orchestrator_agent_run_plan import EXECUTION_PLAN_SCHEMA
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    frozen_plan = hydrate_sidecar_ref(state_dir, ref).payload
    identity = frozen_plan.get("identity", {}) if isinstance(frozen_plan, dict) else {}
    if (not identity or frozen_plan.get("schema_version") != EXECUTION_PLAN_SCHEMA
            or frozen_plan.get("candidate_reader_assignments_digest") != digest
            or any(identity.get(k) != freeze.get(k) for k in ("workflow_run_id", "task_map_generation",
                                                               "plan_artifact_package_ref", "plan_artifact_package_digest"))):
        raise ValueError("immutable OA execution plan differs from its Candidate Reader approval")
    return {"candidate_reader_assignments_digest": digest, "execution_plan_ref": ref}


def validate_acceptance_coverage(rows: list[dict], criteria: list[dict]) -> None:
    assigned = {(ac["task_id"], ac["acceptance_id"]) for row in rows for ac in row["acceptance"]}
    known = {(r["source_task_id"], r["source_acceptance_id"]) for r in criteria}
    if len(known) != len(criteria):
        raise ValueError("Candidate Reader cannot bind duplicate source AC identities")
    required = {(r["source_task_id"], r["source_acceptance_id"]) for r in criteria if r.get("mandatory", True)}
    if assigned - known or required - assigned:
        raise ValueError(f"Candidate Reader AC coverage mismatch: missing={sorted(required-assigned)}, "
                         f"unknown={sorted(assigned-known)}")


def validate_plan_assignment_coverage(spec: Mapping[str, Any], task_map: Mapping[str, Any]) -> None:
    """The Plan must name stable AC IDs before assigning independent reviewers."""
    if "candidate_reader_assignments" not in spec:
        return
    criteria = []
    tasks = next((task_map[k] for k in ("tasks", "children", "task_order", "order")
                  if isinstance(task_map.get(k), list)), [])
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("scoped Candidate plan needs structured tasks and stable acceptance IDs")
        task_id = task.get("task_id") or task.get("id") or task.get("task")
        if not _identifier(task_id):
            raise ValueError("scoped Candidate plan needs stable task IDs")
        acs = task.get("acceptance_criteria") or task.get("acceptance")
        if not isinstance(acs, list) or not acs:
            raise ValueError(f"scoped Candidate plan needs structured acceptance for {task_id}")
        for ac in acs:
            ac_id = (ac.get("id") or ac.get("acceptance_id")) if isinstance(ac, dict) else None
            if not _identifier(ac_id):
                raise ValueError(f"scoped Candidate plan needs stable acceptance IDs for {task_id}")
            criteria.append({"source_task_id": task_id, "source_acceptance_id": ac_id,
                             "mandatory": ac.get("mandatory", True)})
    validate_acceptance_coverage(spec["candidate_reader_assignments"], criteria)


def scope_reader_contract(aggregate: dict, spec: dict, *, role: str, target: str) -> dict:
    """Narrow the authenticated snapshot before the attempt's target and input manifest are pinned."""
    rows = spec["candidate_reader_assignments"]
    criteria = aggregate["acceptance_criteria"]
    validate_acceptance_coverage(rows, criteria)
    row = next((r for r in rows if r["role_instance"] == role), None)
    if row is None:
        raise ValueError(f"Candidate Reader {role!r} lacks an approved assignment")
    ids = {candidate_acceptance_id(a["task_id"], a["acceptance_id"]) for a in row["acceptance"]}
    selected = [a for a in criteria if a["acceptance_id"] in ids]
    if {a["acceptance_id"] for a in selected} != ids:
        raise ValueError("Candidate Reader AC identity differs from the frozen contract")
    commands = []
    for command in aggregate["verification_commands"]:
        linked = set(command["acceptance_ids"])
        if not linked:
            raise ValueError("scoped Candidate commands require an explicit AC binding: " + command["command_id"])
        if linked & ids:
            commands.append({**command, "acceptance_ids": sorted(linked & ids)})
    scope = {
        "role_instance": role, "task_ids": sorted({a["task_id"] for a in row["acceptance"]}),
        "acceptance_ids": sorted(ids), "journey_ids": list(row["journey_ids"]),
        "candidate_event_id": aggregate["candidate_event_id"], "target_commit": target,
        "plan_artifact_package_digest": aggregate["plan_artifact_package_digest"],
        "assignments_digest": canonical_json_sha256(rows), "parent_contract_digest": canonical_json_sha256(aggregate),
        "plan_reads": row.get("plan_reads", {}),
    }
    aggregate.update(acceptance_criteria=selected, verification_commands=commands,
                     verification_command=commands[0]["command"] if commands else "",
                     verification_tiers=list(dict.fromkeys(c["tier"] for c in commands)),
                     contract_revision="candidate-contract-r" + canonical_json_sha256([
                         aggregate["contract_revision"], scope])[:20],
                     candidate_reader_scope=scope,
                     behavior="Verify the assigned acceptance criteria and journeys on the frozen candidate.")
    return scope


def scope_plan_reads(sources: list[dict], scope: Mapping[str, Any], state_dir: Path) -> dict[str, list[str]]:
    """Pin narrower mandatory reads without widening a source's on-demand grants."""
    from zf.runtime.artifact_read_ledger import _select_json_path
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    declared = scope.get("plan_reads", {})
    ports = {s["source_id"].removeprefix("plan-port-"): s for s in sources}
    if set(declared) - set(ports):
        raise ValueError("Candidate Reader plan_reads names an unbound Plan port")
    selections = {}
    for name, source in ports.items():
        if name not in declared and name != "product_acceptance_spec":
            continue
        paths = list(declared.get(name, []))
        source_id = source["source_id"]
        if not paths and name != "product_acceptance_spec":
            selections[source_id] = []
            continue
        allowed = source["allowed_paths"]
        body = hydrate_sidecar_ref(state_dir, {"ref": source["ref"], "sha256": source["sha256"]}).payload
        if name == "product_acceptance_spec":
            defaults = [f"$.{key}" for key in ("entrypoints", "assembly_owner", "provider_qualification")
                      if key in body]
            defaults += [f"$.user_journeys[{i}]" for i, j in enumerate(body["user_journeys"])
                      if j["journey_id"] in scope["journey_ids"]]
            for path in defaults:
                # A port can grant a whole collection, but no indexed reads. In
                # that case request the narrowest existing grant containing it.
                if "$" not in allowed and path not in allowed:
                    parents = [p for p in allowed if path.startswith((p + "[", p + "."))]
                    if parents:
                        path = max(parents, key=len)
                paths.append(path)
        for path in paths:
            if "$" not in allowed and path not in allowed:
                raise ValueError(f"Candidate Reader path outside frozen selection: {name} {path}")
            _select_json_path(body, path)
        selections[source_id] = list(dict.fromkeys(paths))
    return selections
