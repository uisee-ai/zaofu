"""One-way Feishu sync for ZaoFu automation reports and Kanban projection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.task.lifecycle import derive_phase
from zf.core.task.store import TaskStore
from zf.integrations.feishu.clients import (
    FeishuHttpBitableClient,
    FeishuHttpDocumentClient,
    MockFeishuBitableClient,
    MockFeishuDocumentClient,
)
from zf.integrations.feishu.renderers import (
    automation_row_key_field,
    build_automation_insight_records,
    build_kanban_records,
    build_stale_automation_insight_record,
    kanban_task_id_field,
    render_automation_markdown,
    render_automation_insight_records_markdown,
    render_kanban_records_markdown,
)
from zf.integrations.feishu.transport import FeishuTransportError
from zf.runtime.automation_projection import project_automations


DocumentClient = MockFeishuDocumentClient | FeishuHttpDocumentClient
BitableClient = MockFeishuBitableClient | FeishuHttpBitableClient


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeishuSyncLedger:
    """Durable mapping from ZaoFu projection keys to Feishu external IDs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_state_dir(cls, state_dir: Path) -> "FeishuSyncLedger":
        return cls(Path(state_dir) / "integrations" / "feishu" / "sync-ledger.json")

    def record_document_sync(
        self,
        *,
        key: str,
        document_id: str,
        payload: dict[str, Any],
    ) -> None:
        with locked_path(self.path):
            data = self._read_unlocked()
            documents = data.setdefault("documents", {})
            documents[key] = {
                "document_id": document_id,
                "last_synced_at": now_iso(),
                **payload,
            }
            self._write_unlocked(data)

    def bitable_record_id(self, *, app_token: str, table_id: str, task_id: str) -> str:
        data = self.read()
        key = _bitable_key(app_token, table_id, task_id)
        return str((data.get("bitable_records") or {}).get(key) or "")

    def set_bitable_record_id(
        self,
        *,
        app_token: str,
        table_id: str,
        task_id: str,
        record_id: str,
    ) -> None:
        with locked_path(self.path):
            data = self._read_unlocked()
            records = data.setdefault("bitable_records", {})
            records[_bitable_key(app_token, table_id, task_id)] = record_id
            self._write_unlocked(data)

    def read(self) -> dict[str, Any]:
        with locked_path(self.path):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"documents": {}, "bitable_records": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"documents": {}, "bitable_records": {}}
        if not isinstance(data, dict):
            return {"documents": {}, "bitable_records": {}}
        data.setdefault("documents", {})
        data.setdefault("bitable_records", {})
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def sync_automation_document(
    *,
    state_dir: Path,
    project_id: str,
    project_name: str,
    document_id: str,
    client: DocumentClient,
    ledger: FeishuSyncLedger,
    writer: EventWriter | None = None,
    automation_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    projection = project_automations(
        state_dir,
        project_id=project_id,
        project_name=project_name,
    )
    markdown = render_automation_markdown(projection, automation_ids=automation_ids)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "markdown": markdown,
            "automation_ids": automation_ids or [item["automation_id"] for item in projection["items"]],
        }

    result = client.append_markdown(document_id, markdown)
    automation_list = automation_ids or [
        str(item.get("automation_id") or "")
        for item in projection.get("items") or []
    ]
    ledger.record_document_sync(
        key=f"automations:{project_id}",
        document_id=document_id,
        payload={
            "automation_ids": automation_list,
            "blocks": int(result.get("blocks") or 0),
        },
    )
    _emit_sync_event(
        writer,
        "feishu.document.synced",
        payload={
            "source": "feishu-sync",
            "project_id": project_id,
            "project_name": project_name,
            "document_id": document_id,
            "automation_ids": automation_list,
            "blocks": int(result.get("blocks") or 0),
        },
    )
    return {"ok": True, "dry_run": False, **result}


def sync_kanban_bitable(
    *,
    state_dir: Path,
    project_id: str,
    project_name: str,
    app_token: str,
    table_id: str,
    client: BitableClient,
    ledger: FeishuSyncLedger,
    writer: EventWriter | None = None,
    field_map: dict[str, str] | None = None,
    include_archive_days: int | None = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    store = TaskStore(Path(state_dir) / "kanban.json")
    tasks = (
        store.list_all()
        if include_archive_days is None
        else store.list_all_with_archive(last_days=include_archive_days)
    )
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    try:
        ready_ids = {task.id for task in store.ready()}
    except Exception:
        ready_ids = set()
    phases = {task.id: derive_phase(task, events) if events else None for task in tasks}
    synced_at = now_iso()
    records = build_kanban_records(
        tasks,
        project_id=project_id,
        project_name=project_name,
        synced_at=synced_at,
        field_map=field_map,
        phases=phases,
        ready_ids=ready_ids,
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "records": records,
            "markdown": render_kanban_records_markdown(records),
        }

    task_id_field = kanban_task_id_field(field_map)
    created = 0
    recreated = 0
    updated = 0
    for fields in records:
        task_id = str(fields.get(task_id_field) or "").strip()
        if not task_id:
            continue
        record_id = ledger.bitable_record_id(
            app_token=app_token,
            table_id=table_id,
            task_id=task_id,
        )
        if record_id:
            try:
                client.update_record(app_token, table_id, record_id, fields)
                updated += 1
            except FeishuTransportError as exc:
                if not _is_deleted_bitable_record_error(exc):
                    raise
                record_id = client.create_record(app_token, table_id, fields)
                ledger.set_bitable_record_id(
                    app_token=app_token,
                    table_id=table_id,
                    task_id=task_id,
                    record_id=record_id,
                )
                created += 1
                recreated += 1
        else:
            record_id = client.create_record(app_token, table_id, fields)
            ledger.set_bitable_record_id(
                app_token=app_token,
                table_id=table_id,
                task_id=task_id,
                record_id=record_id,
            )
            created += 1

    _emit_sync_event(
        writer,
        "feishu.bitable.synced",
        payload={
            "source": "feishu-sync",
            "project_id": project_id,
            "project_name": project_name,
            "app_token": _redact_token(app_token),
            "table_id": table_id,
            "rows": len(records),
            "created": created,
            "recreated": recreated,
            "updated": updated,
            "include_archive_days": include_archive_days,
        },
    )
    return {
        "ok": True,
        "dry_run": False,
        "rows": len(records),
        "created": created,
        "recreated": recreated,
        "updated": updated,
    }


def sync_automation_bitable(
    *,
    state_dir: Path,
    project_id: str,
    project_name: str,
    app_token: str,
    table_id: str,
    client: BitableClient,
    ledger: FeishuSyncLedger,
    writer: EventWriter | None = None,
    field_map: dict[str, str] | None = None,
    automation_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    projection = project_automations(
        state_dir,
        project_id=project_id,
        project_name=project_name,
    )
    synced_at = now_iso()
    records = build_automation_insight_records(
        projection,
        synced_at=synced_at,
        field_map=field_map,
        automation_ids=automation_ids,
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "records": records,
            "markdown": render_automation_insight_records_markdown(records),
        }

    row_key_field = automation_row_key_field(field_map)
    current_row_keys: set[str] = set()
    created = 0
    recreated = 0
    updated = 0
    for fields in records:
        row_key = str(fields.get(row_key_field) or "").strip()
        if not row_key:
            continue
        current_row_keys.add(row_key)
        record_id = ledger.bitable_record_id(
            app_token=app_token,
            table_id=table_id,
            task_id=row_key,
        )
        if record_id:
            try:
                client.update_record(app_token, table_id, record_id, fields)
                updated += 1
            except FeishuTransportError as exc:
                if not _is_deleted_bitable_record_error(exc):
                    raise
                record_id = client.create_record(app_token, table_id, fields)
                ledger.set_bitable_record_id(
                    app_token=app_token,
                    table_id=table_id,
                    task_id=row_key,
                    record_id=record_id,
                )
                created += 1
                recreated += 1
        else:
            record_id = client.create_record(app_token, table_id, fields)
            ledger.set_bitable_record_id(
                app_token=app_token,
                table_id=table_id,
                task_id=row_key,
                record_id=record_id,
            )
            created += 1

    stale_updated = _mark_stale_automation_rows(
        project_id=project_id,
        project_name=project_name,
        app_token=app_token,
        table_id=table_id,
        row_keys=current_row_keys,
        synced_at=synced_at,
        client=client,
        ledger=ledger,
        field_map=field_map,
    )
    _emit_sync_event(
        writer,
        "feishu.automation_bitable.synced",
        payload={
            "source": "feishu-sync",
            "project_id": project_id,
            "project_name": project_name,
            "app_token": _redact_token(app_token),
            "table_id": table_id,
            "rows": len(records),
            "created": created,
            "recreated": recreated,
            "updated": updated,
            "stale_updated": stale_updated,
            "automation_ids": automation_ids or [
                str(item.get("automation_id") or "")
                for item in projection.get("items") or []
            ],
        },
    )
    return {
        "ok": True,
        "dry_run": False,
        "rows": len(records),
        "created": created,
        "recreated": recreated,
        "updated": updated,
        "stale_updated": stale_updated,
    }


def _mark_stale_automation_rows(
    *,
    project_id: str,
    project_name: str,
    app_token: str,
    table_id: str,
    row_keys: set[str],
    synced_at: str,
    client: BitableClient,
    ledger: FeishuSyncLedger,
    field_map: dict[str, str] | None,
) -> int:
    date_key = synced_at[:10]
    prefix = _bitable_key(app_token, table_id, f"{date_key}:{project_id}:")
    stale_updated = 0
    for ledger_key, record_id in (ledger.read().get("bitable_records") or {}).items():
        if not isinstance(ledger_key, str) or not ledger_key.startswith(prefix):
            continue
        row_key = ledger_key[len(_bitable_key(app_token, table_id, "")):]
        if row_key in row_keys or not record_id:
            continue
        fields = build_stale_automation_insight_record(
            row_key,
            project_id=project_id,
            project_name=project_name,
            synced_at=synced_at,
            field_map=field_map,
        )
        try:
            client.update_record(app_token, table_id, str(record_id), fields)
        except FeishuTransportError as exc:
            if not _is_deleted_bitable_record_error(exc):
                raise
            continue
        stale_updated += 1
    return stale_updated


def _emit_sync_event(
    writer: EventWriter | None,
    event_type: str,
    *,
    payload: dict[str, Any],
) -> None:
    if writer is None:
        return
    writer.append(ZfEvent(type=event_type, actor="zf-cli", payload=payload))


def _bitable_key(app_token: str, table_id: str, task_id: str) -> str:
    return f"{app_token.strip()}:{table_id.strip()}:{task_id.strip()}"


def _redact_token(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _is_deleted_bitable_record_error(exc: FeishuTransportError) -> bool:
    message = str(exc).lower()
    return "note has been deleted" in message or "record has been deleted" in message
