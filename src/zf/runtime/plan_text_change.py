"""Bounded Markdown change for a targeted review of an Owner-rejected preview."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from zf.runtime.artifact_read_ledger_validation import required_read_satisfied
from zf.runtime.plan_local_review_policy import _has_full_read, _has_observed_read
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, sidecar_path


def _unique(sources, predicate):
    matches = [s for s in sources if predicate(s)]
    if len(matches) != 1:
        raise ValueError("ambiguous Plan text change source")
    return matches[0]


def _document(state, sources, report):
    source = _unique(sources, lambda s: s["source_id"].startswith("child-artifact-")
                     and s["ref"] == report.get("plan_artifact_ref"))
    if "$" not in source.get("allowed_paths", ["$"]):
        raise ValueError("Plan text is outside the allowed selection")
    path = sidecar_path(state, source["ref"])
    with path.open("rb") as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024 or hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Plan Markdown exceeds the comparison budget or changed")
    text = raw.decode("utf-8")
    if report.get("plan_md") != text:
        raise ValueError("inline and file Plan Markdown differ")
    return source, text.splitlines(keepends=True)


def text_change(state: Path, *, sources, previous, report, trigger, events, run, package_digest, project_root=None):
    if trigger.origin != "kernel":
        return None
    context_source = _unique(sources, lambda s: s["source_id"] == "plan-rework-context")
    context = hydrate_sidecar_ref(state, context_source).payload
    if (context.get("schema_version") != "plan-rework-context.v1"
            or context.get("rework_of") != trigger.payload.get("rework_of")
            or any(context.get(key) for key in ("requirement_amendment_ref", "human_resolution",
                                                "owner_decision_resolution", "operator_override"))):
        return None
    rejected = [e for e in events if e.id == trigger.payload.get("rework_of") and e.type == "plan.rejected"
                and e.actor == "operator" and e.payload.get("workflow_run_id") == run]
    if len(rejected) != 1 or not rejected[0].payload.get("plan_id"):
        return None
    request = [e for e in events if e.type == "plan.approval.requested" and e.origin == "kernel"
               and e.payload.get("workflow_run_id") == run and e.payload.get("plan_id") == rejected[0].payload["plan_id"]
               and e.payload.get("plan_artifact_package_digest") == package_digest]
    if len(request) != 1 or rejected[0].payload.get("request_event_id") != request[0].id:
        return None
    prior = previous["sources"]
    prior_report = _unique(prior, lambda s: s["source_id"].startswith("child-result-"))
    old_report = hydrate_sidecar_ref(state, prior_report).payload
    old, before = _document(state, prior, old_report)
    visible_digest = request[0].payload.get("plan_artifact_digest")
    if not visible_digest:
        from zf.runtime.operator_plan_preview import build_plan_preview

        # Older approval requests can point at a logical synthesis path. Use
        # the Web resolver and accept only the prior immutable artifact.
        preview = build_plan_preview(state, events, plan_id=request[0].payload["plan_id"], project_root=project_root)
        refs = preview.get("refs", {})
        if (not preview.get("ok") or preview.get("content_kind") != "full_plan"
                or refs.get("markdown_ref") != old["ref"]):
            return None
        visible_digest = refs.get("plan_artifact_digest")
    if visible_digest != old["sha256"]:
        return None
    new, after = _document(state, sources, report)
    required, observed = previous["_critic_required_reads"], previous["_critic_observed_reads"]
    if not _has_full_read(required, old) or not _has_observed_read(observed, old):
        return None
    def other_artifacts(rows, doc):
        items = [(s["artifact_id"], s["sha256"]) for s in rows
                 if s["source_id"].startswith("child-artifact-") and s != doc]
        if len({name for name, _ in items}) != len(items) or not items:
            raise ValueError("ambiguous structural artifacts")
        return sorted(items)
    if (other_artifacts(prior, old) != other_artifacts(sources, new)
            or old_report.get("plan_ports") != report.get("plan_ports") or before == after):
        return None
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    end_old, end_new = len(before), len(after)
    while end_old > start and end_new > start and before[end_old-1] == after[end_new-1]:
        end_old -= 1
        end_new -= 1
    left, right = max(0, start-2), end_new+2
    change = {"prior_sha256": old["sha256"], "current_sha256": new["sha256"], "source_id": new["source_id"],
              "prior_ref": old["ref"], "current_ref": new["ref"], "rejection_event": rejected[0].id,
              "start_line": start+1, "before": "".join(before[start:end_old]), "after": "".join(after[start:end_new]),
              "preceding_context": "".join(after[left:start]), "following_context": "".join(after[end_new:right])}
    size = sum(len(change[k].encode()) for k in ("before", "after", "preceding_context", "following_context"))
    if size > 48 * 1024 or size >= sum(len(s.encode()) for s in after):
        return None
    change["previously_read_inline_ports"] = any(
        r.get("source_id") == prior_report["source_id"] and r.get("artifact_sha256") == prior_report["sha256"]
        and r.get("artifact_id") == prior_report["artifact_id"] and r.get("json_path") in ("$", "$.plan_ports")
        and not any(r.get(k) for k in ("max_chars", "max_items", "allow_truncated"))
        and required_read_satisfied(observed, r) for r in required)
    return change


def select_text_change(state, manifest, required, *, events, fanout, stage, role, policy, explicit, project_root=None):
    from zf.runtime.plan_unchanged_review import EVIDENCE_ID, build_evidence

    if explicit or policy.get("mode") != "targeted" or not policy.get("focus"):
        return None
    try:
        sources = manifest["sources"]
        proof = _unique(sources, lambda s: s["source_id"] == EVIDENCE_ID)
        certified = hydrate_sidecar_ref(state, proof).payload
        change = certified.get("markdown_change")
        if not change or "$" not in proof.get("allowed_paths", ["$"]):
            return None
        # Never select partial reads on the strength of a caller-supplied sidecar.
        if certified != build_evidence(state, events=events, sources=sources, run=manifest["workflow_run_id"],
                fanout=fanout, stage=stage, trigger_id=manifest["source_event_id"], critic_role=role, compare_text=True,
                project_root=project_root):
            return None
        report = _unique(sources, lambda s: s["source_id"].startswith("child-result-"))
        body = hydrate_sidecar_ref(state, report).payload
        document = _unique(sources, lambda s: s["source_id"] == change["source_id"])
        if (not _has_full_read(required, report) or not _has_full_read(required, document)
                or any(not re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in body)):
            return None
        omit = {"plan_md"}
        if change["previously_read_inline_ports"]:
            omit.add("plan_ports")
        from zf.runtime.plan_input_read_selection import select_plan_input_reads

        # Retain the same current-candidate navigation that an ordinary
        # targeted review received. Only a verified proof reaches this branch.
        ordinary, strategy = select_plan_input_reads(state, {**manifest, "sources": [
            s for s in sources if s is not proof]}, required, policy=policy)
        rows = []
        root = next(r for r in required if r["source_id"] == report["source_id"])
        for row in ordinary:
            if row["source_id"] == document["source_id"]:
                continue
            if row["source_id"] != report["source_id"]:
                rows.append(row)
                continue
            path = row["json_path"]
            if path == "$":
                rows.extend({**root, "json_path": "$."+key, "min_returned_bytes": 0 if value == "" else 1}
                            for key, value in body.items() if key not in omit)
            elif not any(path == "$."+key or path.startswith(("$."+key+"[", "$."+key+".")) for key in omit):
                rows.append(row)
        rows.append({**root, "source_id": EVIDENCE_ID, "artifact_id": proof["artifact_id"],
                     "artifact_sha256": proof["sha256"], "json_path": "$"})
        from zf.runtime.plan_review_selection_cost import choose_review_reads

        chosen, _ = choose_review_reads(state, manifest, before=ordinary, candidate=rows, explicit=False)
        return (ordinary, strategy) if chosen == ordinary else (rows, "targeted_markdown_change")
    except (ValueError, KeyError, OSError, TypeError, StopIteration):
        return None
