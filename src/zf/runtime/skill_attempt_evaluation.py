"""Replay Task Verify evaluations without using current role locks or mutating a run."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.skill_attempt_evidence import (
    EvidenceReader,
    entries,
    file_version,
    pinned,
    probe_agreement,
    skill_action,
    skill_rows,
)
from zf.runtime.skill_review_model import blinded_input
from zf.runtime.skill_semantic_review import (
    REVIEW_EVENT,
    REVIEWER,
    RULE,
    review_packet,
    validate_decision,
)

IDENTITY = ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id")
DISPATCH = "task.pipeline.stage.dispatched"
ADMITTED = "workflow.call.result.admitted"
TERMINALS = {
    "workflow.operation.cancelled": "cancelled",
    "workflow.operation.blocked": "blocked",
    "workflow.operation.failed": "failed",
    "task.attempt.failed": "failed",
    "task.attempt.superseded": "superseded",
    "task.attempt.expired": "expired",
    "task.attempt.deadlettered": "failed",
    "task.pipeline.verify.failed": "failed",
}
FIELDS = (
    *IDENTITY,
    "request_hash",
    "task_id",
    "role_instance",
    "skills",
    "workdir",
    "target_commit",
    "target_snapshot_digest",
    "contract_snapshot_digest",
    "contract_snapshot_ref",
    "output_profile_id",
    "output_profile_revision",
    "model",
    "configured_model",
    "skill_materialization_event_id",
    "briefing_digest",
    "contract_revision",
    "backend",
    "harness_version",
)


class EvaluationHistory:
    def __init__(self, state_dir: Path, project_root: Path):
        self.state = state_dir
        self.root = project_root
        self.dispatches = {}
        self.current = {}
        self.terminal = {}
        self.terminal_ids = set()
        self.review_events = defaultdict(list)
        self.host_events = defaultdict(list)
        self.host_completions = {}
        self.materializations = {}
        self.latest_materialization = {}
        self.actions = defaultdict(list)
        self.role_keys = defaultdict(set)
        self.dirty = set()
        self.missing = {}

    def ready_sidecars(self) -> set[tuple]:
        return {
            key
            for key, files in self.missing.items()
            if any(file_version(path) != version for path, version in files.items())
        }

    def consume(self, event: ZfEvent) -> None:
        p = event.payload
        if not isinstance(p, dict) or not isinstance(event.type, str) or not isinstance(event.ts, str):
            return
        if event.type in {
            "agent.tool.use",
            "codex.hook.pre_tool_use",
            "codex.hook.post_tool_use",
            "claude.hook.pre_tool_use",
            "claude.hook.post_tool_use",
        }:
            if not isinstance(event.actor, str):
                return
            if event.origin not in {"", "kernel", "external"} or not artifact_access_allowed(
                p.get("access_scope")
            ):
                return
            action = skill_action(event)
            if action:
                # Retain identifiers, never a transcript body.
                fields = {k: p[k] for k in (*IDENTITY, "session_id", "task_id", "cwd") if k in p}
                compact = ZfEvent(
                    type=event.type, id=event.id, ts=event.ts, causation_id=event.causation_id, payload=fields
                )
                self.actions[event.actor].append((compact, *action))
                self.dirty.update(
                    key
                    for key in self.role_keys[event.actor]
                    if (not p.get("dispatch_id") or key[3] == p["dispatch_id"])
                    and (
                        key not in self.terminal
                        or p.get("attempt_id")
                        or p.get("dispatch_id")
                        or event.causation_id == self.dispatches[key][0]
                        or self.dispatches[key][1]["_ts"] <= event.ts <= self.terminal[key].ts
                    )
                )
            return
        if event.origin != "kernel" or not artifact_access_allowed(p.get("access_scope")):
            return
        if event.type == REVIEW_EVENT:
            key = tuple(p.get(k) for k in IDENTITY)
            if event.actor == REVIEWER and all(isinstance(v, str) and v for v in key):
                self.review_events[key].append(event)
                self.dirty.add(key)
            return
        if event.type in {
            "workflow.operation.settled",
            "runtime.action.attempt.started",
            "runtime.action.attempt.completed",
        }:
            if event.type != "workflow.operation.settled" and p.get("action") != "task-host-assist":
                return
            op = (p.get("workflow_run_id"), p.get("operation_id"))
            if all(isinstance(v, str) for v in op):
                self.host_events[op].append(event)
                self.dirty.update(
                    k
                    for k in self.review_events
                    if k[0] == op[0]
                    and k in self.dispatches
                    and self.dispatches[k][1].get("task_id") == (event.task_id or p.get("task_id"))
                )
                if event.type == "runtime.action.attempt.completed":
                    self.host_completions[event.id] = event
            return
        if event.type == "skills.materialized":
            if event.id in self.materializations:
                return
            if not all(isinstance(p.get(k), str) for k in ("instance_id", "manifest_path")):
                return
            slot = (p["instance_id"], str(self.root / p["manifest_path"]))
            self.latest_materialization[slot] = event.id
            # A frozen digest and loading policy are sufficient for historical replay.
            self.materializations[event.id] = {
                k: p.get(k)
                for k in (
                    "task_id",
                    "instance_id",
                    "backend",
                    "skill_dispatch_treatment_ref",
                    "skill_dispatch_treatment_digest",
                )
            }
            self.materializations[event.id]["skills"] = [
                {k: row.get(k) for k in ("name", "auto_inject", "load_on_demand")}
                for row in entries(p.get("skills"))
                if isinstance(row, dict)
            ]
            return
        key = tuple(p.get(k) or "" for k in IDENTITY)
        if not all(isinstance(value, str) for value in key):
            return
        op = key[:2]
        if (
            event.type == DISPATCH
            and p.get("output_profile_id") == "task-verify"
            and all(key)
            and p.get("role_instance")
            and p.get("task_id")
        ):
            if key in self.dispatches:
                return
            if not all(isinstance(p.get(k), str) for k in ("role_instance", "task_id")):
                return
            dispatch = {k: p[k] for k in FIELDS if k in p}
            dispatch["_ts"] = event.ts
            dispatch["_event_id"] = event.id
            workdir = p.get("workdir")
            manifest = (
                Path(workdir if isinstance(workdir, str) else "").parent / "runtime/skills-manifest.json"
            )
            preceding = self.latest_materialization.get((p["role_instance"], str(manifest)))
            material_id = p.get("skill_materialization_event_id", preceding)
            material = self.materializations.get(material_id, {}) if isinstance(material_id, str) else {}
            if material.get("task_id") != p["task_id"] or material.get("instance_id") != p["role_instance"]:
                material_id, material = None, {}
            self.dispatches[key] = (event.id, dispatch, (material_id, material))
            self.current[op] = key
            self.role_keys[p["role_instance"]].add(key)
            self.dirty.add(key)
        elif event.type in TERMINALS or event.type == ADMITTED:
            if event.id in self.terminal_ids:
                return
            if not all(key) and event.type.startswith("workflow.operation."):
                # Some operation failures refer to an earlier leased attempt after a retry.
                source = p.get("source_attempt_id")
                if source:
                    candidates = [k for k in self.dispatches if k[:2] == op and source in k[2:]]
                    current = candidates[0] if len(candidates) == 1 else None
                else:
                    current = self.current.get(op)
                if not current or any(value and current[i] != value for i, value in enumerate(key)):
                    return
                if not p.get("request_hash") or p["request_hash"] != self.dispatches[current][1].get(
                    "request_hash"
                ):
                    return
                key = current
            if key not in self.dispatches:
                return
            if event.type == ADMITTED and (
                p.get("admission_status") != "admitted" or not p.get("request_hash")
            ):
                return
            if (p.get("task_id") or event.task_id) and (p.get("task_id") or event.task_id) != self.dispatches[
                key
            ][1]["task_id"]:
                return
            if p.get("request_hash") and p["request_hash"] != self.dispatches[key][1].get("request_hash"):
                return
            self.terminal_ids.add(event.id)
            if key not in self.terminal or (event.type == ADMITTED and self.terminal[key].type != ADMITTED):
                self.terminal[key] = event
                self.dirty.add(key)

    def cards(self, keys=None):
        for key in self.dispatches if keys is None else sorted(keys):
            if key in self.terminal and key in self.dispatches:
                card = self.mechanical_card(key)
                if self.review_events.get(key):
                    packet = review_packet(self, key, card)
                    match = self.matching_review(key, card, packet)
                    if match:
                        event, receipt = match
                        apply_review(card, receipt, packet)
                        card["review_event_id"] = event.id
                        card["review_ref"] = event.payload["review_ref"]["ref"]
                        card["review_input_ref"] = event.payload["input_ref"]["ref"]
                yield card

    def matching_review(self, key, card, packet):
        reader = EvidenceReader(self.state)
        current = canonical_json_sha256(packet)
        for event in reversed(self.review_events.get(key, [])):
            if event.causation_id != card["terminal_event_id"] or event.task_id != card["task_id"]:
                continue
            receipt = reader.object(event.payload.get("review_ref"))
            input_ref = event.payload.get("input_ref")
            if (
                receipt.get("schema_version") != "skill-independent-review.v1"
                or receipt.get("rule") != RULE
                or receipt.get("reviewer_role") != REVIEWER
                or receipt.get("identity") != list(key)
                or receipt.get("input_digest") != current
                or receipt.get("base_digest") != packet["base_digest"]
                or reader.object(input_ref) != packet
            ):
                continue
            if receipt.get("status") == "pending":
                if reader.pending:
                    self.missing.setdefault(key, {}).update(reader.pending)
                return event, receipt
            try:
                validated = validate_decision(packet, receipt["decision"])
            except (ValueError, KeyError, TypeError):
                continue
            reviewer = receipt.get("reviewer")
            if (
                card["role"] != REVIEWER
                and receipt.get("model_input_digest") == canonical_json_sha256(blinded_input(packet))
                and validated == receipt["decision"]
                and receipt.get("status") == validated["status"]
                and receipt.get("session_id")
                and isinstance(reviewer, dict)
                and reviewer.get("model") == receipt.get("requested_model")
                and reviewer.get("backend") == "ollama"
                and reviewer.get("backend_version")
                and re.fullmatch("[0-9a-f]{64}", str(reviewer.get("model_digest")))
                and re.fullmatch("[0-9a-f]{64}", str(receipt.get("prompt_digest")))
                and isinstance(receipt.get("inference_options"), dict)
            ):
                if reader.pending:
                    self.missing.setdefault(key, {}).update(reader.pending)
                return event, receipt
        if reader.pending:
            self.missing.setdefault(key, {}).update(reader.pending)
        return None

    def mechanical_card(self, key):
        dispatch_id, d, (material_id, m) = self.dispatches[key]
        terminal = self.terminal[key]
        p = terminal.payload
        admitted = (
            terminal.type == ADMITTED
            and bool(d.get("request_hash"))
            and p.get("request_hash") == d["request_hash"]
        )
        result_ref = p.get("control_result_ref") if admitted else None
        reader = EvidenceReader(self.state)
        result = reader.object(result_ref)
        identity_conflict = result.get("schema_version") == "verification-result.v1" and any(
            result.get(k) and result[k] != d.get(k) for k in (*IDENTITY, "request_hash", "task_id")
        )
        if result.get("schema_version") != "verification-result.v1" or any(
            result.get(k) != d.get(k) for k in (*IDENTITY, "request_hash", "task_id")
        ):
            result = {}
        outcome = (
            p.get("semantic_verdict", "unknown") if admitted else TERMINALS.get(terminal.type, "unknown")
        )
        obs = reader.object(p.get("context_delivery_session_observation_ref")) if admitted else {}
        obs_identity = obs.get("identity") or {}
        obs_identity = obs_identity if isinstance(obs_identity, dict) else {}
        sid = (
            obs.get("provider_session_id")
            if all(obs_identity.get(k) == d[k] for k in ("attempt_id", "dispatch_id", "operation_id"))
            and all(
                not obs_identity.get(k) or obs_identity[k] == d[k] for k in ("workflow_run_id", "task_id")
            )
            else None
        )
        role = d["role_instance"]
        skills, pack = skill_rows(reader, self.root, d, m, self.actions[role], sid, terminal.ts)
        contract = reader.object(pinned(d.get("contract_snapshot_ref"), d.get("contract_snapshot_digest")))
        if any(contract.get(k) != d.get(k) for k in ("workflow_run_id", "task_id")):
            contract = {}
        required = {
            r["acceptance_id"]
            for r in entries(contract.get("acceptance_criteria"))
            if isinstance(r, dict)
            and isinstance(r.get("acceptance_id"), str)
            and r["acceptance_id"]
            and r.get("mandatory", True)
        }
        rows = [r for r in entries(result.get("requirement_results")) if isinstance(r, dict)]
        reported = {r["acceptance_id"] for r in rows if isinstance(r.get("acceptance_id"), str)}
        missing_ids = sorted(required - reported)
        criteria = []

        def criterion(name, ok, refs, explanation, *, critical=False, applies=True, source="mechanical"):
            status = (
                "not_applicable" if not applies else "unknown" if ok is None else "pass" if ok else "fail"
            )
            criteria.append(
                {
                    "id": name,
                    "result": status,
                    "critical": critical,
                    "source": source,
                    "reason": explanation,
                    "evidence_refs": [ref for ref in refs if ref],
                }
            )

        evidence = [dispatch_id, terminal.id]
        if result or identity_conflict:
            evidence.append(result_ref["ref"])
        contract_evidence = [*evidence, d.get("contract_snapshot_ref") if contract else None]
        binding = ("target_commit", "contract_snapshot_digest", "target_snapshot_digest", "role_instance")
        conflicts = [
            field for field in binding if d.get(field) and result.get(field) and d[field] != result[field]
        ]
        if identity_conflict:
            conflicts.append("attempt_identity")
        bound = all(d.get(k) and result.get(k) for k in binding)
        criterion(
            "frozen_target_and_contract",
            False if conflicts else True if bound else None,
            contract_evidence,
            "绑定冲突 " + "、".join(conflicts)
            if conflicts
            else "目标、合同及角色与派发一致"
            if bound
            else "缺少可比对的冻结绑定",
            critical=True,
        )
        criterion(
            "mandatory_ac_has_result",
            not missing_ids if required and result else None,
            contract_evidence,
            f"{len(required) - len(missing_ids)}/{len(required)} 个必需验收项有逐项结果"
            if required and result
            else "缺少可比较的合同义务或准入结果",
        )
        reason = bool(
            result.get("summary") or result.get("rework_items") or any(r.get("findings") for r in rows)
        )
        criterion(
            "nonpass_reason_or_handoff",
            reason if result else None,
            evidence,
            "原裁决通过"
            if outcome == "passed"
            else "结果中有理由或返工事项"
            if reason
            else "未见可核对的原因记录",
            applies=outcome != "passed",
        )
        probes = probe_agreement(reader, d, result)
        criterion(
            "archived_probe_claims_match",
            all(ok for _, ok in probes) if probes else None,
            [ref for ref, _ in probes],
            f"{sum(ok for _, ok in probes)}/{len(probes)} 条归档探针与声明一致"
            if probes
            else "缺少绑定到本任务的可核对探针",
            critical=True,
        )
        criterion(
            "business_verdict_independently_confirmed",
            None,
            [],
            "缺少独立业务复核裁决",
            critical=True,
            source="independent_review",
        )
        failures, critical_status, score = summarize(criteria)
        if reader.pending:
            self.missing[key] = reader.pending
        else:
            self.missing.pop(key, None)
        try:
            elapsed = (datetime.fromisoformat(terminal.ts) - datetime.fromisoformat(d["_ts"])).total_seconds()
        except (ValueError, TypeError):
            elapsed = None
        return {
            "schema_version": "skill-attempt-evaluation.v1",
            "rubric_version": "task-verify.v1",
            **dict(zip(IDENTITY, key)),
            "task_id": d.get("task_id"),
            "role": role,
            "output_profile_id": d["output_profile_id"],
            "output_profile_revision": d.get("output_profile_revision"),
            "request_hash": d.get("request_hash"),
            "target_snapshot_digest": d.get("target_snapshot_digest"),
            "target_commit": d.get("target_commit"),
            "contract_digest": d.get("contract_snapshot_digest"),
            "model": d.get("model"),
            "configured_model": d.get("configured_model"),
            "briefing_digest": d.get("briefing_digest"),
            "contract_revision": d.get("contract_revision"),
            "provider": m.get("backend") or d.get("backend"),
            "harness_version": d.get("harness_version"),
            "stage_event_id": dispatch_id,
            "terminal_event_id": terminal.id,
            "task_outcome": outcome,
            "materialization_event_id": material_id,
            "skill_pack_digest": (pack or {}).get("sha256"),
            "materialization_binding": (
                "dispatch_event_ref"
                if d.get("skill_materialization_event_id")
                else "preceding_task_role_manifest"
            )
            if pack
            else "unknown",
            "evidence": {
                "contract": d.get("contract_snapshot_ref") if contract else None,
                "control_result": result_ref.get("ref") if result else None,
                "skill_pack": pack.get("ref") if pack else None,
                "provider_session_bound": bool(sid),
            },
            "attempt_cost": {
                "elapsed_seconds": elapsed if elapsed is not None and elapsed >= 0 else None,
                "tokens": None,
            },
            "critical_status": critical_status,
            "missing_mandatory_ids": missing_ids if result and contract else None,
            "archived_probe_comparisons": len(probes),
            "skills": skills,
            "criteria": criteria,
            "critical_failures": failures,
            "per_skill_cost": None,
            "score": score,
            "review_status": "pending",
            "recommendation": {
                "target": "evidence" if failures else "observation",
                "action": "核对关键证据的目标与来源" if failures else "按任务义务独立复核业务判词",
            },
        }


def apply_review(card, receipt, packet):
    card["review_status"] = receipt["status"]
    card["independent_review"] = receipt
    if receipt["status"] == "pending":
        return
    review = receipt["decision"]
    criterion = next(c for c in card["criteria"] if c["source"] == "independent_review")
    cited = {ref for row in review["items"] for ref in row["citations"]}
    evidence = {
        "contract": card["evidence"]["contract"],
        "result": card["evidence"]["control_result"],
        **{k: v["event_id"] for k, v in packet["host_facts"].items()},
    }
    card["review_evidence"] = {k: evidence[k] for k in sorted(cited) if evidence.get(k)}
    criterion.update(
        result=review["result"],
        reason=" / ".join(i["reason"] for i in review["items"]),
        evidence_refs=list(card["review_evidence"].values()),
    )
    card["recommendation"] = {"target": review["owner"], "action": review["action"]}
    card["critical_failures"], card["critical_status"], card["score"] = summarize(card["criteria"])


def summarize(criteria):
    counts = Counter(c["result"] for c in criteria)
    den = counts["pass"] + counts["fail"]
    applicable = den + counts["unknown"]
    failures = [c["id"] for c in criteria if c["critical"] and c["result"] == "fail"]
    status = (
        "failed"
        if failures
        else "pending"
        if any(c["critical"] and c["result"] == "unknown" for c in criteria)
        else "passed"
    )
    return (
        failures,
        status,
        {
            "passed": counts["pass"],
            "failed": counts["fail"],
            "unknown": counts["unknown"],
            "not_applicable": counts["not_applicable"],
            "percent": round(100 * counts["pass"] / den, 2) if den else None,
            "denominator": den,
            "coverage": round(den / applicable, 3) if applicable else None,
        },
    )
