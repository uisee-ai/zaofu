"""Bounded Markdown change for a targeted review of an Owner-rejected preview."""
from __future__ import annotations

import difflib
import hashlib
import json
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


def _attachments(rows, document):
    children = [s for s in rows if s["source_id"].startswith("child-artifact-") and s != document]
    by_name = {s["artifact_id"]: s for s in children}
    if len(children) != len(by_name) or not children:
        raise ValueError("ambiguous structural artifacts")
    return by_name


def _author_note(state, source, report, document):
    ref = source["ref"]
    if (ref not in report.get("artifact_refs", []) or ref not in report.get("evidence_refs", [])
            or any(value == ref for key, value in report.items() if key.endswith("_ref"))
            or any(isinstance(p, dict) and p.get("ref") == ref for p in report.get("plan_ports", []))
            or "$" not in source.get("allowed_paths", ["$"])):
        return None
    with sidecar_path(state, ref).open("rb") as stream:
        raw = stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024 or hashlib.sha256(raw).hexdigest() != source["sha256"]:
        return None
    body = json.loads(raw)
    if (not isinstance(body, dict) or body.get("review_kind") != "author_self_review"
            or body.get("independent_critic") is not False or body.get("plan_sha256") != document["sha256"]):
        return None
    return body


def _changed_hunks(before, after):
    # Keep the original compact single span byte-for-byte for frozen requests.
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    old_end, new_end = len(before), len(after)
    while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
        old_end -= 1
        new_end -= 1
    span = {"start_line": start + 1, "prior_start_line": start + 1,
            "before": "".join(before[start:old_end]), "after": "".join(after[start:new_end]),
            "preceding_context": "".join(after[max(0, start - 2):start]),
            "following_context": "".join(after[new_end:new_end + 2])}
    size = sum(len(value.encode()) for value in span.values() if isinstance(value, str))
    if size <= 48 * 1024 and size < sum(len(line.encode()) for line in after):
        return [span]
    # Bound the line matcher as well as the encoded result. Group all edits.
    if len(before) * len(after) > 2_000_000:
        return []
    groups = difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(2)
    hunks = []
    for group in groups:
        edits = [op for op in group if op[0] != "equal"]
        _, old_start, _, new_start, _ = edits[0]
        _, _, old_end, _, new_end = edits[-1]
        hunks.append({"start_line": new_start + 1, "prior_start_line": old_start + 1,
                      "before": "".join(before[old_start:old_end]), "after": "".join(after[new_start:new_end]),
                      "preceding_context": "".join(after[max(0, new_start-2):new_start]),
                      "following_context": "".join(after[new_end:new_end+2])})
        if len(hunks) > 12:
            return []
    return hunks


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
    old_files, new_files = _attachments(prior, old), _attachments(sources, new)
    if (old_files.keys() != new_files.keys() or old_report.get("plan_ports") != report.get("plan_ports")
            or before == after):
        return None
    notes = []
    for name, current in new_files.items():
        prior_file = old_files[name]
        if prior_file["sha256"] == current["sha256"]:
            continue
        # Only a document-bound author self-review may change alongside the
        # Markdown. Supply its old body and require the current one in full.
        prior_body = _author_note(state, prior_file, old_report, old)
        current_body = _author_note(state, current, report, new)
        if (not prior_body or not current_body or prior_file.get("access_scope") != current.get("access_scope")
                or not _has_full_read(required, prior_file) or not _has_observed_read(observed, prior_file)):
            return None
        notes.append({"source_id": current["source_id"], "current_sha256": current["sha256"],
                      "prior_sha256": prior_file["sha256"], "prior_ref": prior_file["ref"], "before": prior_body})
    hunks = _changed_hunks(before, after)
    if not hunks:
        return None
    size = sum(len(h[key].encode()) for h in hunks
               for key in ("before", "after", "preceding_context", "following_context"))
    size += sum(len(json.dumps(note, ensure_ascii=False).encode()) for note in notes)
    if size > 48 * 1024 or size >= sum(len(line.encode()) for line in after):
        return None
    change = {"prior_sha256": old["sha256"], "current_sha256": new["sha256"], "source_id": new["source_id"],
              "prior_ref": old["ref"], "current_ref": new["ref"], "rejection_event": rejected[0].id}
    if len(hunks) == 1:
        # Preserve existing single-hunk certificates and replay identity.
        hunks[0].pop("prior_start_line")
        change.update(hunks[0])
    else:
        change["hunks"] = hunks
    if notes:
        change["changed_author_notes"] = notes
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
        for note in change.get("changed_author_notes", []):
            original = _unique(sources, lambda s: s["source_id"] == note["source_id"])
            if not _has_full_read(rows, original):
                return None
        rows.append({**root, "source_id": EVIDENCE_ID, "artifact_id": proof["artifact_id"],
                     "artifact_sha256": proof["sha256"], "json_path": "$"})
        from zf.runtime.plan_review_selection_cost import choose_review_reads

        chosen, _ = choose_review_reads(state, manifest, before=ordinary, candidate=rows, explicit=False)
        return (ordinary, strategy) if chosen == ordinary else (rows, "targeted_markdown_change")
    except (ValueError, KeyError, OSError, TypeError, StopIteration):
        return None
