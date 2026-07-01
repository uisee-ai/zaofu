"""Mock Feishu clients used by CLI tests and dry-run style sync checks."""

from __future__ import annotations

from typing import Any


class MockFeishuDocumentClient:
    def __init__(self) -> None:
        self.appended: list[tuple[str, str]] = []
        self.created_documents: list[dict[str, str]] = []

    def create_document(
        self,
        *,
        title: str,
        folder_token: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        document_id = f"doc-mock-{len(self.created_documents) + 1}"
        document = {
            "document_id": document_id,
            "title": title,
            "folder_token": folder_token,
            "url": f"https://example.feishu.cn/docx/{document_id}",
        }
        self.created_documents.append({**document, "content": content})
        return document

    def append_markdown(self, document_id: str, markdown: str) -> dict[str, Any]:
        self.appended.append((document_id, markdown))
        return {
            "document_id": document_id,
            "blocks": max(1, markdown.count("\n## ") + 1),
        }


class MockFeishuBitableClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, dict[str, Any]]] = []
        self.updated: list[tuple[str, str, str, dict[str, Any]]] = []
        self.created_bases: list[dict[str, str]] = []
        self.created_tables: list[dict[str, str]] = []
        self.created_views: list[dict[str, str]] = []
        self.configured_layouts: list[dict[str, Any]] = []
        self.ensured_fields: list[tuple[str, str, list[dict[str, object]]]] = []

    def create_base(
        self,
        *,
        name: str,
        folder_token: str = "",
        time_zone: str = "",
    ) -> dict[str, Any]:
        app_token = f"app-mock-{len(self.created_bases) + 1}"
        base = {
            "app_token": app_token,
            "base_token": app_token,
            "name": name,
            "folder_token": folder_token,
            "time_zone": time_zone,
            "url": f"https://example.feishu.cn/base/{app_token}",
        }
        self.created_bases.append(base)
        return base

    def create_table(self, app_token: str, *, name: str) -> dict[str, Any]:
        table_id = f"tbl-mock-{len(self.created_tables) + 1}"
        table = {
            "app_token": app_token,
            "table_id": table_id,
            "name": name,
        }
        self.created_tables.append(table)
        return table

    def ensure_fields(
        self,
        app_token: str,
        table_id: str,
        field_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        fields = [dict(spec) for spec in field_specs]
        self.ensured_fields.append((app_token, table_id, fields))
        return {
            "existing": [],
            "created": [str(spec.get("field_name") or "") for spec in fields],
        }

    def create_view(
        self,
        app_token: str,
        table_id: str,
        *,
        name: str,
        view_type: str,
    ) -> dict[str, Any]:
        view_id = f"vew-mock-{len(self.created_views) + 1}"
        view = {
            "app_token": app_token,
            "table_id": table_id,
            "view_id": view_id,
            "view_name": name,
            "view_type": view_type,
        }
        self.created_views.append(view)
        return view

    def ensure_views(
        self,
        app_token: str,
        table_id: str,
        view_specs: list[dict[str, str]],
    ) -> dict[str, Any]:
        existing_names = {
            str(view.get("view_name") or "")
            for view in self.created_views
            if view.get("app_token") == app_token and view.get("table_id") == table_id
        }
        created: list[str] = []
        for spec in view_specs:
            name = str(spec.get("view_name") or "").strip()
            if not name or name in existing_names:
                continue
            self.create_view(
                app_token,
                table_id,
                name=name,
                view_type=str(spec.get("view_type") or "grid"),
            )
            existing_names.add(name)
            created.append(name)
        return {"existing": sorted(existing_names - set(created)), "created": created}

    def ensure_view_layouts(
        self,
        app_token: str,
        table_id: str,
        layout_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        existing_names = {
            str(view.get("view_name") or "")
            for view in self.created_views
            if view.get("app_token") == app_token and view.get("table_id") == table_id
        }
        configured: list[str] = []
        skipped: list[str] = []
        for spec in layout_specs:
            name = str(spec.get("view_name") or "").strip()
            if not name or name not in existing_names:
                if name:
                    skipped.append(name)
                continue
            self.configured_layouts.append({
                "app_token": app_token,
                "table_id": table_id,
                **dict(spec),
            })
            configured.append(name)
        return {"configured": configured, "skipped": skipped}

    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any]) -> str:
        record_id = f"rec-{len(self.created) + 1}"
        self.created.append((app_token, table_id, dict(fields)))
        return record_id

    def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str:
        self.updated.append((app_token, table_id, record_id, dict(fields)))
        return record_id
