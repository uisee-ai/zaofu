"""Read the authorized stage obligations at one Request/Run ledger cursor."""

from collections import Counter, defaultdict

from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.task_pipeline_contexts import latest_task_pipeline_generation_context
from zf.runtime.stage_team_mailbox import mailbox_index, member_mailbox
from zf.runtime.stage_team_observations import receipt_observations


def _proposal_receipt(scope, events):
    """Approval follows the exact proposed digest in the same request revision."""
    authority, receipt = "unknown", None
    for event in events:
        p = event.payload
        if (event.origin != "kernel" or p.get("request_id") != scope["request_id"]
                or p.get("revision") != scope["revision"]):
            continue
        if event.type == "workflow.request.proposed":
            receipt = event if p.get("proposal_digest") == scope.get("proposal_digest") else None
            authority = "proposed" if receipt else "unknown"
        elif event.type == "workflow.request.approved" and receipt and authority == "proposed":
            authority, receipt = "approved", event
        elif event.type == "workflow.request.rejected":
            authority, receipt = "unknown", None
    return authority, receipt


def _member(declaration, authority, receipt):
    return {**declaration, "status": "ready" if authority == "approved" else "proposed",
            "owner": "kernel" if authority == "approved" else "owner", "instance": "",
            "operation_ids": [], "attempt_ids": [], "task_ids": declaration.get("task_ids", []),
            "status_since": receipt.ts, "last_event_at": receipt.ts, "source_event_id": receipt.id,
            "evidence_ref": {}, "verdict": "", "history": [], "attempt_history": [], "messages": []}


def team_summary(team):
    """First paint carries the roster. Member evidence and handoffs stay in node detail."""
    fields = {"mailbox", "question", "expected_output", "attempt_count", "instances", "source_seq", "last_activity", "wait", "waiting_for", "obligation_id", "role", "status", "instance", "task_ids", "stage", "owner", "recipient",
              "status_since", "last_event_at", "source_event_id", "verdict", "pipeline_stage", "task_map_generation", "required", "coverage_owner", "reused"}
    members = [{k: v for k, v in member.items() if k in fields} for member in team["members"]]
    return {**{k: v for k, v in team.items() if k != "communications"}, "partial": True, "members": members}


def _apply_operation(member, op, admissions, by_id, *, detailed):
    member["operation_ids"].append(op["operation_id"])
    for event_id in (op.get("source_event_ids", []) if detailed else ()):
        fact = by_id.get(event_id)
        if not fact or fact.origin != "kernel" or fact.type not in {
            "workflow.operation.started", "workflow.operation.failed", "workflow.operation.redrive_admitted",
            "workflow.operation.retry_started", "workflow.operation.interrupted", "workflow.operation.blocked", "workflow.operation.cancelled",
        }:
            continue
        p = fact.payload
        member["attempt_history"].append({"event_id": fact.id, "event_type": fact.type, "time": fact.ts,
            "attempt_id": p.get("active_attempt_id") or p.get("source_attempt_id") or "", "instance": p.get("role_instance", ""),
            "reason": p.get("reason", ""), "operation_id": op["operation_id"]})
    attempt = op.get("active_attempt_id")
    activity = []
    for entry in op.get("recent_activity", []):
        payload = entry.get("payload") or {}
        observed_attempt = payload.get("attempt_id") or payload.get("active_attempt_id")
        if observed_attempt and observed_attempt != attempt:
            continue
        if observed_attempt or not (op.get("retry_count") or op.get("redrive_count")):
            activity.append(entry)
    if activity:
        last = activity[-1]
        member["last_activity"] = {"event_id": last["id"], "time": last.get("ts"), "event_type": last["type"]}
    else:
        member.pop("last_activity", None)
    attempts = [*op.get("redrive_source_attempt_ids", []), attempt]
    member["attempt_ids"] = list(dict.fromkeys([*member["attempt_ids"], *(a for a in attempts if a)]))
    member["instance"] = op.get("role_instance", "") if op.get("started_event_id") else ""
    # A transport ending or a worker declaring success does not admit a result.
    admission = admissions.get((op["operation_id"], attempt, op["request_hash"]))
    result = admission.payload if admission else {}
    evidence = result.get("control_result_ref") or {}
    status = op["status"]
    if (status not in {"cancelled", "superseded"} and evidence.get("ref") and evidence.get("sha256")
            and (not op.get("admitted_call_result_ref") or op["admitted_call_result_ref"] == result.get("envelope_ref"))):
        status = "admitted"
    elif status == "settled":
        status = "awaiting_admission"
    event = admission if status == "admitted" else by_id.get(op.get("last_event_id"))
    if status == "running":
        retrying = op.get("redrive_count", 0) or op.get("retry_count", 0)
        if retrying:
            status = "retrying"
        entry = next((by_id.get(i) for i in reversed(op.get("source_event_ids", []))
                      if i in by_id and by_id[i].origin == "kernel" and by_id[i].type in {
                          "workflow.operation.started", "workflow.operation.retry_started"}
                      and by_id[i].payload.get("active_attempt_id") == attempt), None) if retrying else by_id.get(op.get("started_event_id"))
        event = entry or event
    member["history"].append({"operation_id": op["operation_id"], "attempt_id": attempt or "",
                              "instance": member["instance"], "status": status,
                              "reason": op.get("reason", ""), "source_event_id": event.id if event else "",
                              "last_event_at": event.ts if event else op.get("last_event_at", "")})
    member.update(status=status, verdict=result.get("semantic_verdict", "") if status == "admitted" else "",
                  owner=(member.get("recipient") or "kernel") if status == "admitted" else
                  member["instance"] or "kernel", evidence_ref={k: evidence[k] for k in ("ref", "sha256")} if status == "admitted" else {},
                  last_event_at=max(filter(None, [op.get("last_event_at", ""), event.ts if event else "",
                      *(a.get("ts", "") for a in activity)]), default=""),
                  status_since=(op.get("started_at", "") if status == "running" else event.ts if event else ""),
                  source_event_id=event.id if event else op.get("last_event_id", ""))


def _message_relations(events, run_id, positions):
    from zf.runtime.workflow_board import ACTOR

    messages, by_post = defaultdict(list), {}
    for event in events:
        if event.origin != "kernel" or event.actor != ACTOR or not event.type.startswith("workflow.board."):
            continue
        p = event.payload
        if event.type == "workflow.board.posted" and p.get("workflow_run_id") == run_id:
            sender = p.get("sender") or {}
            if sender.get("workflow_run_id") != run_id or not sender.get("operation_id"):
                continue
            links = []
            for recipient, binding in (p.get("audience") or {}).items():
                if binding is not None and (not isinstance(binding, dict) or binding.get("workflow_run_id") != run_id
                                            or binding.get("instance_id") != recipient):
                    continue
                row = {"posted_seq": positions[event.id], "sender": sender.get("instance_id", ""), "recipient": recipient,
                       "posted": event.ts, "source_event_id": event.id, "message_key": p.get("message_key", ""),
                       "seen": "", "consumed": ""}
                messages[(sender["operation_id"], sender.get("attempt_id"))].append(row)
                if isinstance(binding, dict) and binding.get("workflow_run_id") == run_id:
                    messages[(binding.get("operation_id"), binding.get("attempt_id"))].append(row)
                    links.append((binding, row))
            by_post[event.id] = links
        elif event.type in {"workflow.board.seen", "workflow.board.consumed"}:
            for binding, row in by_post.get(event.causation_id, []):
                if p.get("receiver") == binding:
                    kind = event.type.rsplit(".", 1)[1]
                    row[kind], row[kind + "_seq"] = event.ts, positions[event.id]
    return messages


def _specialist_invocation(member, binding, invocations):
    key = (binding["task_map_generation"], member["work_unit_id"], member["obligation_id"], member["task_ids"][0])
    for request in reversed(invocations.get(key, [])):
        p = request.payload
        obligation = p["verify_specialist_obligation"]
        if (obligation.get("execution_plan_ref") == binding["execution_plan_ref"]
                and obligation.get("role") == member["role"] and obligation.get("target_commit")
                and obligation.get("contract_revision") == member.get("contract_revision")
                and p.get("target_ref") == obligation["target_commit"]):
            return request
    return None


def _reused_opinion(member, binding, reused, old_invocations, operations, by_id, positions):
    source = member.get("reuse_operation_id")
    if not source or not member.get("specialist"):
        return None
    parent = operations.get(source) or {}
    original = old_invocations.get(source)
    if (not original or parent.get("operation_type") != "task_verify_specialist" or parent.get("status") != "settled"
            or not parent.get("admitted_call_result_ref")
            or original.payload.get("workflow_operation_request_hash") != parent.get("request_hash")):
        return None
    old = original.payload["verify_specialist_obligation"]
    task = member["task_ids"][0]
    identity = {"task_id": task, "task_map_generation": binding["task_map_generation"],
                "contract_revision": member["contract_revision"], "obligation_id": member["obligation_id"],
                "role": member["role"]}
    if (parent.get("task_id") != task or original.task_id != task or original.actor != "orchestrator"
            or not old.get("execution_plan_ref") or not old.get("work_unit_id")
            or not old.get("pattern_id") or old["pattern_id"] != original.payload.get("pattern_id")
            or any(not value or old.get(key) != value for key, value in identity.items())
            or any(member.get(key) != old.get(key) for key in ("question", "input_refs", "expected_output"))
            or (member.get("pattern_id") and member["pattern_id"] != old.get("pattern_id"))):
        return None
    for receipt in reversed(reused.get(source, [])):
        p = receipt.payload
        result = by_id.get(p.get("source_result_event_id"))
        if (receipt.actor != "orchestrator" or receipt.task_id != task or any(p.get(key) != value for key, value in identity.items())
                or p.get("work_unit_id") != member["work_unit_id"] or p.get("execution_plan_ref") != binding["execution_plan_ref"]
                or p.get("workflow_run_id") != parent.get("workflow_run_id")
                or not p.get("target_commit") or p["target_commit"] != old.get("target_commit")
                or original.payload.get("target_ref") != p["target_commit"]
                or not result or result.type != "workflow.call.result.admitted" or result.origin != "kernel"
                or receipt.causation_id != result.id or not positions[original.id] < positions[result.id] < positions[receipt.id]
                or positions[binding["event_id"]] >= positions[receipt.id]):
            continue
        rp = result.payload
        child = operations.get(rp.get("operation_id")) or {}
        evidence = rp.get("control_result_ref") or {}
        given = p.get("evidence_ref") or {}
        if (rp.get("semantic_verdict") not in {"passed", "approved", "rejected"}
                or child.get("parent_operation_id") != source or child.get("status") != "settled"
                or child.get("task_id") != task or child.get("role_instance") != member["role"]
                or child.get("workflow_run_id") != p["workflow_run_id"] or rp.get("workflow_run_id") != p["workflow_run_id"]
                or not rp.get("attempt_id") or rp["attempt_id"] != child.get("active_attempt_id")
                or not rp.get("request_hash") or rp["request_hash"] != child.get("request_hash")
                or not rp.get("envelope_ref") or rp["envelope_ref"] != child.get("admitted_call_result_ref")
                or not isinstance(given, dict) or any(not evidence.get(k) or evidence[k] != given.get(k) for k in ("ref", "sha256"))):
            continue
        return receipt, result, child, old
    return None


def _belongs_to_child(delivery, start, member, binding, latest, by_id, events, run, positions):
    if not start or start.payload.get("stage_id") != member["stage"]:
        return False
    p = delivery.payload
    if binding.get("added_child_id"):
        return (positions[delivery.id] > binding["added_source_seq"] and binding["added_fanout_id"] == p.get("fanout_id")
                and latest.get(member["stage"]) == start.id
                and binding["added_child_id"] == p.get("child_id") and p.get("role_instance") == member["role"])
    child = next((c for c in start.payload.get("expected_children", []) if c.get("child_id") == p.get("child_id")), {})
    if not (p.get("role_instance") == child.get("role_instance") == member["role"]):
        return False
    if binding.get("freeze_id"):
        trigger = by_id.get(start.payload.get("trigger_event_id"))
        if not trigger or trigger.type != "candidate.ready":
            return False
        try:
            native = frozen_candidate_for_payload(events, run, payload={"candidate_event_id": trigger.id})
        except (ValueError, KeyError):
            return False
        return bool(native and native.id == binding["event_id"])
    if latest.get(member["stage"]) != start.id:
        return False
    obligation = (child.get("payload") or {}).get("stage_obligation", {})
    return member["obligation_id"] == "judge-author" or obligation.get("obligation_id") == member["obligation_id"]


def _judge_target_events(events, run, inputs, by_id):
    """Bind Judge/Discovery activity to this freeze, including same-HEAD re-freezes."""
    freeze = next((by_id[item["event_id"]] for item in inputs.values()
                   if item.get("freeze_id") and item.get("event_id") in by_id), None)
    if not freeze:
        try:
            freeze = frozen_candidate_for_payload(events, run, payload={})
        except (KeyError, ValueError):
            return set()
    if not freeze:
        return None  # Historical Runs may have no Candidate freeze contract.
    latest = latest_task_pipeline_generation_context((e for e in events if e.origin == "kernel"), workflow_run_id=run)
    if latest and freeze.payload.get("task_map_generation") != latest.get("task_map_generation"):
        return set()  # A new Run generation has yet to freeze its Candidate.
    bound, candidate_ids, fanouts = set(), {freeze.id}, set()
    head = freeze.payload.get("candidate_head_commit") or freeze.payload.get("target_commit")
    for e in events:
        p = e.payload
        if e.id == freeze.id:
            bound.add(e.id)
            continue
        if not bound or e.origin != "kernel" or (p.get("workflow_run_id") or e.correlation_id) != run:
            continue
        batch = p.get("judge_batch_admissions")
        pin = ((batch.get("candidate_event_id") if isinstance(batch, dict) else "")
               or p.get("candidate_snapshot_event_id") or p.get("candidate_event_id"))
        if p.get("freeze_id") and p["freeze_id"] != freeze.payload.get("freeze_id"):
            continue
        if pin and pin not in candidate_ids:
            continue
        target = p.get("candidate_head_commit") or p.get("target_commit")
        if target and head and target != head:
            continue
        if p.get("task_map_generation") and p["task_map_generation"] != freeze.payload.get("task_map_generation"):
            continue
        if e.type == "candidate.ready":
            if pin:
                candidate_ids.add(e.id)
                bound.add(e.id)
            continue
        if (pin or any(link in bound for link in (e.causation_id, p.get("trigger_event_id"), p.get("source_event_id"),
                                                  p.get("verification_event_id"))) or p.get("fanout_id") in fanouts):
            bound.add(e.id)
            if e.type == "fanout.started" and p.get("fanout_id"):
                fanouts.add(p["fanout_id"])
    return bound


def project_stage_teams(nodes, source, operations, *, detail_node="*"):
    """Attach the same read model to first paint, execution summary, and full detail."""
    scope = source["scope"]
    run = scope["run_id"]
    events = [e for _, e in source["events"]]
    by_id = {e.id: e for e in events}
    from zf.runtime.workflow_operation import OPERATION_EVENT_TYPES, reduce_workflow_operations
    if any(e.origin != "kernel" and (e.type in OPERATION_EVENT_TYPES or e.type == "workflow.call.result.admitted") for e in events):
        from zf.runtime.sdlc_workflow_states import bind_pipeline_containers
        canonical = [(seq, e) for seq, e in source["events"] if e.origin == "kernel"]
        operations = reduce_workflow_operations([e for _, e in canonical], workflow_run_id=run)
        bind_pipeline_containers(nodes, operations, canonical)
    authority, proposal = _proposal_receipt(scope, events)
    teams = source["compiled"].get("stage_teams", {})
    inputs = source.get("stage_team_inputs", {})
    judge_events = _judge_target_events(events, run, inputs, by_id) if run and teams.get("judge") else None
    fanouts, latest_fanout, dispatches, admissions = {}, {}, defaultdict(list), {}
    positions = {event.id: seq for seq, event in source["events"]}
    invocations, old_invocations, reused = defaultdict(list), {}, defaultdict(list)
    for event in events:
        p = event.payload
        if event.origin != "kernel" or not run or (p.get("workflow_run_id") or event.correlation_id) != run:
            continue
        if event.type == "fanout.started":
            if (judge_events is not None and (p.get("stage_id") or "").endswith(
                    ("-lanes-final", "-post-verify-discovery", "-judge-advisory")) and event.id not in judge_events):
                continue
            fanouts[p.get("fanout_id")] = event
            latest_fanout[p.get("stage_id")] = event.id
        elif event.type == "fanout.child.dispatched":
            dispatches[p.get("operation_id") or ""].append(event)
        elif event.type == "workflow.invoke.requested" and p.get("workflow_operation_id"):
            o = p.get("verify_specialist_obligation") or {}
            if o and p.get("task_id") == o.get("task_id") and p.get("task_map_generation") == o.get("task_map_generation"):
                old_invocations[p["workflow_operation_id"]] = event
                invocations[o.get("task_map_generation"), o.get("work_unit_id"),
                            o.get("obligation_id"), o.get("task_id")].append(event)
        elif event.type == "task.verify.specialist.reused" and p.get("source_operation_id"):
            reused[p["source_operation_id"]].append(event)
        elif event.type == "workflow.call.result.admitted":
            admissions[(p.get("operation_id"), p.get("attempt_id"), p.get("request_hash"))] = event
    messages = _message_relations(events, run, positions) if detail_node else {}
    mailboxes = mailbox_index(events, run)
    observations = receipt_observations(events, operations, positions)
    stage_rows = {n["data"]["stage_id"]: n for n in nodes.values() if n["id"].startswith("workflow:stage:")}
    operations_by_role = defaultdict(dict)
    operations_by_unit = defaultdict(dict)
    for oid, operation in operations.items():
        operations_by_role[operation.get("role_instance")][oid] = operation
        assignment = operation.get("orchestration_assignment") or {}
        if assignment.get("work_unit_id"):
            operations_by_unit[(assignment["work_unit_id"], operation.get("task_id"))][oid] = operation
    additions = []
    if authority == "approved" and teams.get("plan"):
        for e in events:
            p = e.payload
            if (e.type != "fanout.plan.member.admitted" or e.origin != "kernel"
                    or not run or p.get("workflow_run_id") != run or p.get("stage_id") != "prd-scan"
                    or not p.get("workflow_proposal_digest") or p["workflow_proposal_digest"] != scope.get("proposal_digest")):
                continue
            start = fanouts.get(p.get("fanout_id"))
            source_fact = by_id.get(p.get("evidence_event_id"))
            member = p.get("member") or {}
            if (not start or start.id != latest_fanout.get("prd-scan") or positions[start.id] >= positions[e.id]
                    or not source_fact or source_fact.type != "fanout.child.completed" or source_fact.origin != "kernel"
                    or (source_fact.payload.get("workflow_run_id") or source_fact.correlation_id) != run
                    or source_fact.payload.get("fanout_id") != p["fanout_id"]
                    or not positions[start.id] < positions[source_fact.id] < positions[e.id]
                    or not isinstance(member, dict) or not member.get("obligation_id") or not p.get("child_id")
                    or not member.get("role") or member["role"] != p.get("role_instance")):
                continue
            additions.append((e, member))
    for stage, node in stage_rows.items():
        detailed = detail_node in {"*", node["id"]}
        declared = []
        # A Plan snapshot exposes the whole team from first paint, including its unique author.
        roster_stages = [stage]
        if stage == "prd-plan" and "plan" in teams and "prd-scan" in stage_rows:
            roster_stages.insert(0, "prd-scan")
        if stage.endswith("-lanes-final") and "judge" in teams:
            prefix = stage.removesuffix("-lanes-final")
            roster_stages = [prefix + "-post-verify-discovery", prefix + "-judge-advisory", stage]
        for roster_stage in roster_stages:
            stage_node = stage_rows.get(roster_stage)
            if proposal and stage_node:
                selected = stage_node["data"]["definition"].get("metadata", {}).get("team_obligations", [])
                declared.extend((_member({**m, "stage": roster_stage}, authority, proposal), {}) for m in selected)
        if "prd-scan" in roster_stages and additions:
            declared_ids = {m["obligation_id"] for m, _ in declared}
            for e, item in additions:
                if item["obligation_id"] in declared_ids:
                    continue
                declared_ids.add(item["obligation_id"])
                added = _member({**item, "stage": "prd-scan", "admission_evidence_id": e.payload["evidence_event_id"]},
                                "approved", e)
                declared.append((added, {"added_child_id": e.payload["child_id"], "added_fanout_id": e.payload["fanout_id"],
                                         "added_source_seq": positions[e.id]}))
                if item.get("required", True):
                    for m, _ in declared:
                        if m["stage"] == "prd-plan" and m["obligation_id"] == "plan-author":
                            m["input_refs"] = [*m.get("input_refs", []), item["obligation_id"]]
                            m.update(status_since=e.ts, last_event_at=e.ts, source_event_id=e.id)
        if proposal and (stage == "prd-plan" or stage.endswith(("-lanes-final", "-judge-advisory"))):
            category = "plan" if stage == "prd-plan" else "judge"
            advisory_stage = "prd-scan" if category == "plan" else "prd-judge-advisory"
            for specialist in teams.get(category, {}).get("members", []):
                disposition = specialist.get("disposition") or {}
                if specialist.get("required") is False and disposition.get("status") == "reassigned":
                    member = _member({**specialist, "stage": advisory_stage, "recipient": specialist["coverage_owner"]},
                                     authority, proposal)
                    if authority == "approved":
                        member.update(status="reassigned", owner=specialist["coverage_owner"])
                    declared.append((member, {}))
        if proposal and stage.endswith("-lanes-final") and teams.get("judge"):
            author = teams["judge"]["author"]
            if author in node["data"]["definition"].get("roles", []):
                declared.append((_member({"obligation_id": "judge-author", "role": author,
                    "stage": stage, "expected_output": "正式 Judge 判词",
                    "judge_questions": teams["judge"].get("questions", []),
                    "judge_objectives": teams["judge"].get("objectives", []),
                    "input_refs": [m["obligation_id"] for m, _ in declared if m.get("required", True)]},
                    authority, proposal), {}))
        dynamic = inputs.get(stage) or {}
        dynamic_event = by_id.get(dynamic.get("event_id"))
        if dynamic_event and dynamic_event.origin == "kernel" and (dynamic_event.payload.get("workflow_run_id") or dynamic_event.correlation_id) == run:
            for m in dynamic.get("members", []):
                binding = m.get("binding", dynamic)
                receipt = by_id.get(binding.get("event_id"), dynamic_event)
                declared.append((_member({**m, "stage": stage}, "approved", receipt), binding))
        seen = set()
        observed = []
        for member, binding in declared:
            member_detail = detailed or any(detail_node == "task:" + t for t in member["task_ids"])
            if (authority == "approved" or binding) and member["status"] != "reassigned":
                invocation = _specialist_invocation(member, binding, invocations) if member.get("specialist") else None
                if invocation:
                    member.update(status="requested", source_event_id=invocation.id,
                                  status_since=invocation.ts, last_event_at=invocation.ts,
                                  target_commit=invocation.payload["target_ref"])
                candidates = (operations_by_unit[(member["obligation_id"], member["task_ids"][0])]
                              if binding.get("execution_plan_ref") and not member.get("specialist")
                              else operations_by_role[member["role"]])
                for oid, op in candidates.items():
                    if op.get("workflow_run_id") != run:
                        continue
                    if member.get("specialist"):
                        parent = operations.get(op.get("parent_operation_id")) or {}
                        match = (invocation and parent.get("operation_id") == invocation.payload["workflow_operation_id"]
                                 and parent.get("workflow_run_id") == run and parent.get("task_id") in member["task_ids"]
                                 and parent.get("operation_type") == "task_verify_specialist"
                                 and parent.get("request_hash") == invocation.payload.get("workflow_operation_request_hash")
                                 and op.get("role_instance") == member["role"] and op.get("task_id") in member["task_ids"])
                    elif binding.get("execution_plan_ref"):
                        assignment = op.get("orchestration_assignment") or {}
                        match = (op.get("task_map_generation") == binding["task_map_generation"]
                                 and op.get("task_id") in member["task_ids"]
                                 and op.get("task_pipeline_stage") == member["pipeline_stage"]
                                 and assignment.get("execution_plan_ref") == binding["execution_plan_ref"]
                                 and assignment.get("work_unit_id") == member["obligation_id"]
                                 and assignment.get("selected_role") == op.get("role_instance")
                                 and (not member.get("contract_revision") or member["contract_revision"]
                                      == op.get("contract_revision")))
                    else:
                        match = False
                        for delivery in dispatches.get(oid, []):
                            p = delivery.payload
                            start = fanouts.get(p.get("fanout_id"))
                            if op.get("role_instance") != member["role"] or not _belongs_to_child(
                                    delivery, start, member, binding, latest_fanout, by_id, events, run, positions):
                                continue
                            match = True
                            break
                    if match:
                        _apply_operation(member, op, admissions, by_id, detailed=member_detail)
                        if member_detail:
                            for attempt in member["attempt_ids"]:
                                member["messages"].extend(messages.get((oid, attempt), []))
                        seen.add(oid)
                if not member["operation_ids"]:
                    carried = _reused_opinion(member, binding, reused, old_invocations, operations, by_id, positions)
                    if carried:
                        receipt, admission, child, old = carried
                        key = (child["operation_id"], child["active_attempt_id"], child["request_hash"])
                        _apply_operation(member, child, {key: admission}, by_id, detailed=member_detail)
                        member.update(source_event_id=receipt.id, source_result_event_id=admission.id,
                                      source_operation_id=child["parent_operation_id"], reused=True,
                                      original_execution_plan_ref=old["execution_plan_ref"],
                                      original_work_unit_id=old["work_unit_id"], target_commit=receipt.payload["target_commit"],
                                      status_since=receipt.ts, last_event_at=receipt.ts)
                        if member_detail:
                            member["messages"].extend(messages.get(key[:2], []))
                        seen.add(child["operation_id"])
                # Legacy/observer-mode fanout may dispatch a real child without creating an operation.
                if not member["operation_ids"] and not binding.get("execution_plan_ref"):
                    for delivery in dispatches.get("", []):
                        p = delivery.payload
                        if not _belongs_to_child(delivery, fanouts.get(p.get("fanout_id")), member, binding,
                                                 latest_fanout, by_id, events, run, positions):
                            continue
                        member.update(status="dispatched", instance=p["role_instance"], owner=p["role_instance"],
                                      last_event_at=delivery.ts, status_since=delivery.ts, source_event_id=delivery.id)
                        if p.get("attempt_id"):
                            member["attempt_ids"].append(p["attempt_id"])
            if member["operation_ids"]:
                facts = observations.get(member["operation_ids"][-1], [])
                pulse = next((f for f in reversed(facts) if f["kind"] == "heartbeat"), None)
                if pulse and pulse["time"] >= (member.get("last_activity") or {}).get("time", ""):
                    member["last_activity"] = pulse
                    member["last_event_at"] = max(member["last_event_at"], pulse["time"])
                wait = next((f for f in reversed(facts) if f["kind"] == "wait" and f["event_id"] == member["source_event_id"]), None)
                if wait and member["status"] in {"suspended", "blocked"}:
                    member["wait"] = wait
                    member["owner"] = wait["owner"] or "kernel"
            member["attempt_count"] = len(member["attempt_ids"])
            member["instances"] = list(dict.fromkeys(h["instance"] for h in member["history"] if h["instance"]))
            member["source_seq"] = positions.get(member["source_event_id"])
            for fact in member["attempt_history"]:
                fact["seq"] = positions.get(fact["event_id"])
            observed.append(member)
        relevant_ops = [o for o in operations.values() if run and o.get("workflow_run_id") == run
                        and (o.get("compiled_stage_id") or o.get("parent_stage_id")) in roster_stages]
        by_obligation = {m["obligation_id"]: m for m in observed}
        for m in observed:
            if m["status"] == "ready":
                waiting = [ref for ref in m.get("input_refs", []) if ref in by_obligation
                           and by_obligation[ref]["status"] != "admitted"]
                if waiting:
                    m["wait"] = {"reason": "awaiting_required_result", "wake_event": "workflow.call.result.admitted",
                                 "seq": m["source_seq"], "event_id": m["source_event_id"]}
                    m.update(status="waiting_inputs", waiting_for=waiting,
                             owner=" / ".join(by_obligation[ref]["role"] for ref in waiting))
                else:
                    completed = [by_obligation[r] for r in m.get("input_refs", []) if r in by_obligation]
                    latest_input = max(completed, key=lambda x: x.get("source_seq") or 0, default=None)
                    if latest_input and (latest_input.get("source_seq") or 0) > (m["source_seq"] or 0):
                        m.update(status_since=latest_input["status_since"], last_event_at=latest_input["status_since"],
                                 source_seq=latest_input["source_seq"], source_event_id=latest_input["source_event_id"])
        for member in observed:
            mailbox = member_mailbox(member, mailboxes, scope.get("proposal_digest", ""), dynamic.get("freeze_id", ""))
            if mailbox:
                member["mailbox"] = mailbox
        bound = bool(declared) and (authority == "approved" or all(binding for _, binding in declared))
        required = [m for m in observed if m.get("required", True)]
        optional = [m for m in observed if not m.get("required", True)]
        team = {"authority": "approved" if bound else authority if declared else "unknown", "members": observed,
                "required_total": len(required) if bound else None, "optional_count": len(optional),
                "optional_admitted_count": sum(m["status"] == "admitted" for m in optional),
                "admitted_count": sum(m["status"] == "admitted" for m in required),
                "verdicts": dict(Counter(m["verdict"] for m in required if m["verdict"])),
                "optional_verdicts": dict(Counter(m["verdict"] for m in optional if m["verdict"])),
                "unbound_operation_ids": [o["operation_id"] for o in relevant_ops if o["operation_id"] not in seen]}
        if events:
            team["snapshot_at"] = events[-1].ts
        definition = node["data"]["definition"]
        if authority == "approved" and not declared and not scope.get("historical"):
            if teams.get("candidate_verify") and definition.get("trigger") == "candidate.ready":
                team["authority"] = "waiting_freeze"
            elif teams.get("task_verify") and definition.get("metadata", {}).get("task_map"):
                team["authority"] = "waiting_oa"
        if dynamic.get("task_map_generation"):
            progress = {}
            for mode in ("impl", "verify"):
                task_ids = {task for m in observed if m.get("pipeline_stage") == mode for task in m["task_ids"]}
                if task_ids:
                    accepted = set()
                    for m in observed:
                        if m.get("pipeline_stage") != mode or m.get("specialist") or m["status"] != "admitted" or m["verdict"] != "passed":
                            continue
                        for oid in m["operation_ids"][-1:]:
                            op = operations[oid]
                            admitted = by_id.get(m["source_event_id"])
                            result = by_id.get(admitted.payload.get("source_event_id")) if admitted else None
                            expected = "dev.build.done" if mode == "impl" else "task.pipeline.verify.completed"
                            if (result and result.type == expected and result.task_id == op.get("task_id")
                                    and op.get("status") == "settled" and op.get("contract_revision")
                                    and result.payload.get("operation_id") == oid
                                    and result.payload.get("task_map_generation") == op.get("task_map_generation")
                                    and result.payload.get("target_commit" if mode == "verify" else "source_commit")):
                                accepted.update(m["task_ids"])
                    progress[mode] = {"total": len(task_ids), "admitted": len(accepted)}
            team["task_progress"] = progress
        communication = {}
        for op in relevant_ops:
            for msg in messages.get((op["operation_id"], op.get("active_attempt_id")), []):
                communication[(msg["source_event_id"], msg["recipient"])] = msg
        for m in observed:
            for msg in m["messages"]:
                communication[(msg["source_event_id"], msg["recipient"])] = msg
        team["communications"] = list(communication.values())
        if dynamic.get("freeze_id"):
            declared_ac = {(ac["task_id"], ac["acceptance_id"]) for m in required for ac in m.get("acceptance", [])}
            reported_ac = {(ac["task_id"], ac["acceptance_id"]) for m in required if m["status"] == "admitted"
                           for ac in m.get("acceptance", [])}
            team["unreported_acceptance"] = [{"task_id": t, "acceptance_id": a} for t, a in sorted(declared_ac - reported_ac)]
        if stage == "prd-plan":
            if bound:
                inputs_required = [m for m in required if m["obligation_id"] != "plan-author"]
                team["plan_inputs"] = {"total": len(inputs_required),
                                       "admitted": sum(m["status"] == "admitted" for m in inputs_required)}
            from zf.runtime.goal_closure_lifecycle_evidence import plan_lifecycle_evidence
            current = latest_task_pipeline_generation_context(events, workflow_run_id=run) or {}
            plan_events = plan_lifecycle_evidence(events, workflow_run_id=run,
                task_map_generation=current.get("task_map_generation", ""), package_identity=current)
            team["decisions"] = [{k: row[k] for k in ("event_id", "event_type", "ts", "actor")}
                                 for row in plan_events if row["origin"] == "kernel"
                                 and row["event_type"] in {"plan.approved", "plan.rejected"}]
        if stage.endswith("-lanes-final"):
            deliveries = {"ship.completed", "run.delivery.settled"}
            decisions = {"judge.passed", "judge.failed"} | deliveries
            team["decisions"], team["unbound_deliveries"] = [], []
            for e in events:
                if (e.origin != "kernel" or not run or (e.payload.get("workflow_run_id") or e.correlation_id) != run
                        or e.type not in decisions):
                    continue
                row = {"event_id": e.id, "event_type": e.type, "ts": e.ts, "actor": e.actor}
                if judge_events is None or e.id in judge_events:
                    team["decisions"].append(row)
                elif e.type in deliveries:
                    team["unbound_deliveries"].append(row)
        if observed:
            team["uncovered_obligations"] = [m["obligation_id"] for m in required if m["status"] != "admitted"]
        if dynamic:
            team.update({k: dynamic[k] for k in ("freeze_id", "task_map_generation", "diagnostic") if k in dynamic})
        node["data"]["team"] = team
        for m in observed:
            for task_id in m["task_ids"]:
                task = nodes.get("task:" + task_id)
                if task and task["kind"] == "task":
                    summary = {"stage": m.get("pipeline_stage") or m["stage"], "obligation_id": m["obligation_id"],
                               "instance": m["instance"], "status": m["status"], "owner": m["owner"],
                               "attempt_count": m["attempt_count"], "status_since": m["status_since"]}
                    rows = task["data"].setdefault("team_members", [])
                    if summary not in rows:
                        rows.append(summary)
    # The full node inspector exposes definition metadata. Keep later proposal
    # assignments out of historical prefixes preceding that exact proposal.
    if proposal is None:
        for n in stage_rows.values():
            definition = n["data"]["definition"]
            if "team_obligations" in definition.get("metadata", {}):
                n["data"]["definition"] = {**definition, "metadata": {
                    k: v for k, v in definition["metadata"].items() if k != "team_obligations"}}
