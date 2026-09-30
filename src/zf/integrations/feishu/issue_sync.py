"""Feishu Bitable projection for the Issue/Triage/Delivery lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from issue_core import JsonIssueSessionStore

from zf.core.events import EventLog, EventWriter
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.feishu.client_ports import BitableClient
from zf.integrations.feishu.renderers import _bitable_text
from zf.integrations.feishu.sync import (
    FeishuSyncLedger,
    _upsert_bitable_record,
    _redact_token,
)
from zf.runtime.issue_lifecycle_projection import _workflow_projection
from zf.core.self_issue.store import IssueDraftStore, PublicationIntentStore


DEFAULT_ISSUE_FIELD_MAP = {
    "issue_key": "Issue Key", "source_type": "Source Type", "session_id": "Session ID",
    "issue_number": "Issue Number", "issue_url": "Issue URL", "repository": "Repository", "title": "Title",
    "description": "Description", "state": "Current State", "stage": "Current Stage",
    "triage_id": "Triage ID", "task_id": "Task ID", "workflow_run_id": "Workflow Run ID",
    "candidate_sha": "Candidate SHA", "pr_url": "PR URL", "pr_lifecycle": "PR Lifecycle",
    "merge_sha": "Merge SHA", "report_ref": "Report Reference", "attachment_count": "Attachment Count",
    "publication_state": "Publication State", "published_issue_number": "Published Issue Number",
    "published_issue_url": "Published Issue URL",
    "start_time": "Start Time", "end_time": "End Time", "created_at": "Created At",
    "updated_at": "Updated At", "synced_at": "Synced At",
}


def issue_field_specs(field_map: dict[str, str] | None = None) -> list[dict[str, object]]:
    fields = _field_map(field_map)
    date_keys = {"start_time", "end_time", "created_at", "updated_at", "synced_at"}
    seen: set[str] = set()
    specs: list[dict[str, object]] = []
    for key, value in fields.items():
        name = str(value or "").strip()
        if name and name not in seen:
            seen.add(name)
            specs.append({"field_name": name, "type": 5 if key in date_keys else 1})
    return specs


def issue_view_specs() -> list[dict[str, str]]:
    return [
        {"view_name": "ZaoFu Issues", "view_type": "grid"},
        {"view_name": "ZaoFu Issue Gantt", "view_type": "gantt"},
    ]


def issue_view_layout_specs(field_map: dict[str, str] | None = None) -> list[dict[str, object]]:
    fields = _field_map(field_map)
    return [
        {"view_name": "ZaoFu Issues", "visible_fields": list(fields.values())},
        {"view_name": "ZaoFu Issue Gantt", "visible_fields": [fields["issue_key"], fields["title"], fields["state"], fields["start_time"], fields["end_time"]], "timebar": {"start_time": fields["start_time"], "end_time": fields["end_time"], "title": fields["title"]}},
    ]


def _date_value(value: str) -> str | None:
    """Return the lark-cli datetime CellValue format."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _text(value: object, limit: int = 2000) -> str:
    if value is None:
        return ""
    return _bitable_text(str(value)[:limit])


def _field_map(field_map: dict[str, str] | None = None) -> dict[str, str]:
    fields = {**DEFAULT_ISSUE_FIELD_MAP, **(field_map or {})}
    unknown = sorted(set(fields) - set(DEFAULT_ISSUE_FIELD_MAP))
    if unknown:
        raise ValueError(f"unknown Issue Bitable field keys: {', '.join(unknown)}")
    names = [str(value or "").strip() for value in fields.values()]
    if any(not name for name in names):
        raise ValueError("Issue Bitable field names must not be empty")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate Issue Bitable field names: {', '.join(duplicates)}")
    return {key: str(value).strip() for key, value in fields.items()}


def _event_matches(event: Any, *, issue_key: str, task_id: str, run_id: str) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    source = str(payload.get("source_key") or "")
    if source and source != issue_key:
        return False
    task_values = {
        value for value in (
            str(event.task_id or ""), str(payload.get("task_id") or ""),
            str(payload.get("owner_task_id") or ""),
        ) if value
    }
    event_run = str(
        payload.get("workflow_run_id") or payload.get("run_id")
        or payload.get("trace_id") or event.correlation_id or ""
    )
    if task_id and task_values and task_id not in task_values:
        return False
    if run_id and not event_run:
        return False
    if run_id and event_run and event_run != run_id:
        return False
    return (not task_id or task_id in task_values or not task_values) and (
        not run_id or event_run == run_id or not event_run
    )


def _external_records(state_dir: Path, fields: dict[str, str], synced_at: str) -> list[dict[str, Any]]:
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    records: list[dict[str, Any]] = []
    for item in IssueMirrorStore(state_dir).list():
        workflow = _workflow_projection(events, item)
        if not workflow:
            workflow = {"state": "mirrored"}
        task_id = str(workflow.get("task_id") or "")
        run_id = str(workflow.get("run_id") or "")
        candidate_sha = ""
        pr_url = ""
        merge_sha = ""
        handoff = _read_handoff(state_dir, item.number)
        handoff_matches = (
            str(handoff.get("source_key") or item.issue_key) == item.issue_key
            and (
                not str(handoff.get("source_revision") or "")
                or not str(workflow.get("source_revision") or "")
                or str(handoff.get("source_revision")) == str(workflow.get("source_revision"))
            )
        )
        if handoff_matches:
            candidate_sha = str(handoff.get("candidate_head_sha") or "")
            pull_request = handoff.get("pull_request") if isinstance(handoff.get("pull_request"), dict) else {}
            pr_url = str(pull_request.get("url") or "")
            merge_sha = str(handoff.get("merge_sha") or pull_request.get("merge_commit_sha") or "")
        else:
            pull_request = {}
        for event in events:
            if event.type not in {"candidate.publication.prepared", "forge.pull_request.recorded", "forge.pull_request.synced", "delivery.merged"}:
                continue
            if not _event_matches(event, issue_key=item.issue_key, task_id=task_id, run_id=run_id):
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            candidate_sha = str(payload.get("candidate_head_sha") or payload.get("candidate_sha") or candidate_sha)
            pr_url = str(payload.get("pull_request_url") or payload.get("html_url") or payload.get("pr_url") or payload.get("url") or pr_url)
            merge_sha = str(payload.get("merge_commit_sha") or payload.get("merge_sha") or merge_sha)
        created = str(item.created_at)
        updated = str(item.updated_at)
        values = {
            "issue_key": item.issue_key, "source_type": "external_issue", "session_id": "",
            "issue_number": item.number, "issue_url": item.html_url, "repository": item.repository, "title": item.title,
            "description": item.html_url, "state": workflow.get("state"),
            "stage": workflow.get("lifecycle", {}).get("current") if isinstance(workflow.get("lifecycle"), dict) else workflow.get("state"),
            "triage_id": workflow.get("triage_id"), "task_id": task_id, "workflow_run_id": run_id,
            "candidate_sha": candidate_sha, "pr_url": pr_url, "pr_lifecycle": str(handoff.get("status") or workflow.get("state")),
            "merge_sha": merge_sha, "report_ref": str(handoff.get("handoff_ref") or ""), "attachment_count": 0,
            "publication_state": "", "published_issue_number": "", "published_issue_url": "",
            "start_time": workflow.get("admitted_at") or created,
            "end_time": (
                workflow.get("state_event_at")
                or str(pull_request.get("merged_at") or "")
                if workflow.get("state") in {"merged", "closed", "failed", "blocked", "fix_blocked", "triage_blocked"}
                else ""
            ),
            "created_at": created, "updated_at": updated, "synced_at": synced_at,
        }
        records.append(_record(fields, values))
    return records


def _read_handoff(state_dir: Path, issue_number: int) -> dict[str, Any]:
    path = Path(state_dir) / "artifacts" / "issue-triage" / "delivery" / "github" / str(issue_number) / "handoff.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _session_records(state_dir: Path, fields: dict[str, str], synced_at: str) -> list[dict[str, Any]]:
    root = Path(state_dir) / "self-issues" / "portable-sessions"
    if not root.exists():
        return []
    store = JsonIssueSessionStore(root)
    drafts = IssueDraftStore(Path(state_dir) / "self-issues" / "drafts.json")
    intents = PublicationIntentStore(Path(state_dir) / "self-issues" / "publication-intents.json")
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.session.json")):
        try:
            session = store.load_session(path.name.removesuffix(".session.json"))
            working = store.load_working_set(session.session_id)
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        workflow = dict(working.workflow or {})
        state = session.status.value
        published: dict[str, str] = {}
        publication_state = ""
        if session.publication_draft_id:
            draft = drafts.get(session.publication_draft_id)
            if draft is not None:
                publication_state = draft.publication_state
                published = dict(draft.published_issue_ref)
            if not published:
                for intent in reversed(intents.for_draft(session.publication_draft_id)):
                    if intent.published_issue_ref:
                        published = dict(intent.published_issue_ref)
                        publication_state = intent.status
                        break
        values = {
            "issue_key": f"session:{session.session_id}", "source_type": "portable_session",
            "session_id": session.session_id, "issue_number": "", "issue_url": published.get("url", ""), "repository": str(session.target_binding.get("project") or ""),
            "title": str((working.report or {}).get("title") or session.raw_report[:300]),
            "description": session.raw_report, "state": state,
            "stage": str(workflow.get("label") or workflow.get("phase") or state),
            "triage_id": "", "task_id": "", "workflow_run_id": session.active_run_id,
            "candidate_sha": "", "pr_url": "", "pr_lifecycle": state,
            "merge_sha": "", "report_ref": session.final_report_ref, "attachment_count": len(session.attachments),
            "publication_state": publication_state, "published_issue_number": published.get("number", ""),
            "published_issue_url": published.get("url", ""),
            "start_time": session.created_at, "end_time": session.updated_at if state in {"completed", "cancelled", "failed"} else "",
            "created_at": session.created_at, "updated_at": session.updated_at, "synced_at": synced_at,
        }
        records.append(_record(fields, values))
    return records


def _record(fields: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        name = fields[key]
        result[name] = _date_value(str(value)) if key in {"start_time", "end_time", "created_at", "updated_at", "synced_at"} else _text(value)
    return result


def build_issue_records(state_dir: Path, *, synced_at: str, field_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    fields = _field_map(field_map)
    return _external_records(Path(state_dir), fields, synced_at) + _session_records(Path(state_dir), fields, synced_at)


def render_issue_records_markdown(records: list[dict[str, Any]]) -> str:
    if not records:
        return "# ZaoFu Issue Sync\n\nNo Issue sessions or mirrors to sync.\n"
    columns = list(records[0])
    lines = ["# ZaoFu Issue Sync", "", "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in records:
        lines.append("| " + " | ".join(str(row.get(column, "")).replace("|", "\\|").replace("\n", " ") for column in columns) + " |")
    return "\n".join(lines) + "\n"


def sync_issue_bitable(*, state_dir: Path, project_id: str, project_name: str, app_token: str, table_id: str, client: BitableClient, ledger: FeishuSyncLedger, writer: EventWriter | None = None, field_map: dict[str, str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    synced_at = datetime.now(timezone.utc).isoformat()
    records = build_issue_records(state_dir, synced_at=synced_at, field_map=field_map)
    if dry_run:
        return {"ok": True, "dry_run": True, "records": records, "markdown": render_issue_records_markdown(records)}
    key_field = _field_map(field_map)["issue_key"]
    created = updated = recreated = 0
    batch_update = getattr(client, "batch_update_records", None)
    batch_create = getattr(client, "batch_create_records", None)
    if callable(batch_update) and callable(batch_create):
        update_records: dict[str, dict[str, Any]] = {}
        create_rows: list[tuple[str, dict[str, Any]]] = []
        for values in records:
            key = str(values.get(key_field) or "").strip()
            if not key:
                continue
            record_id = ledger.bitable_record_id(
                app_token=app_token, table_id=table_id, task_id=key,
            )
            if record_id:
                update_records[record_id] = values
            else:
                create_rows.append((key, values))
        for start in range(0, len(update_records), 200):
            batch_update(app_token, table_id, dict(list(update_records.items())[start:start + 200]))
            updated += min(200, len(update_records) - start)
        for start in range(0, len(create_rows), 200):
            chunk = create_rows[start:start + 200]
            ids = batch_create(app_token, table_id, [values for _, values in chunk])
            if len(ids) != len(chunk):
                raise ValueError("Feishu batch create returned an unexpected record count")
            for (key, _), record_id in zip(chunk, ids, strict=True):
                ledger.set_bitable_record_id(
                    app_token=app_token, table_id=table_id,
                    task_id=key, record_id=record_id,
                )
            created += len(chunk)
    else:
        for values in records:
            key = str(values.get(key_field) or "").strip()
            if not key:
                continue
            action, was_recreated = _upsert_bitable_record(client=client, ledger=ledger, app_token=app_token, table_id=table_id, stable_key=key, key_field=key_field, fields=values)
            created += int(action == "created")
            updated += int(action == "updated")
            recreated += int(was_recreated)
    if writer is not None:
        writer.emit("feishu.issue_bitable.synced", actor="zf-feishu-projector", payload={"schema_version": "feishu-issue-bitable.v1", "project_id": project_id, "project_name": project_name, "app_token": _redact_token(app_token), "table_id": table_id, "rows": len(records), "created": created, "updated": updated, "recreated": recreated})
    return {"ok": True, "dry_run": False, "rows": len(records), "created": created, "updated": updated, "recreated": recreated}


__all__ = ["DEFAULT_ISSUE_FIELD_MAP", "build_issue_records", "issue_field_specs", "issue_view_layout_specs", "issue_view_specs", "render_issue_records_markdown", "sync_issue_bitable"]
