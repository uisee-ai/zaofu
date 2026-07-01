"""Feishu Docx and Bitable clients used by scheduled projection sync."""

from __future__ import annotations

import urllib.parse
from typing import Any

from zf.integrations.feishu.mock_clients import (
    MockFeishuBitableClient,
    MockFeishuDocumentClient,
)
from zf.integrations.feishu.transport import FeishuHttpTransport
from zf.integrations.feishu.view_layouts import (
    field_config_body,
    filter_body,
    timebar_body,
    visible_fields_body,
)

_DIRECT_CHILD_BLOCK_TYPES = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}


class FeishuHttpDocumentClient:
    """Append markdown to a Feishu Docx document through OpenAPI."""

    def __init__(self, transport: FeishuHttpTransport | None = None) -> None:
        self.transport = transport or FeishuHttpTransport()

    def create_document(
        self,
        *,
        title: str,
        folder_token: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("document title is required")
        body: dict[str, Any] = {
            "format": "markdown",
            "content": content or f"# {title}\n",
        }
        if folder_token:
            body["parent_token"] = folder_token
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            "/docs_ai/v1/documents",
            body,
        )
        document = _document_result(response)
        document.setdefault("title", title)
        return document

    def append_markdown(self, document_id: str, markdown: str) -> dict[str, Any]:
        document_id = document_id.strip()
        if not document_id:
            raise ValueError("document_id is required")
        blocks = _extract_blocks(self._convert_markdown(markdown))
        if not blocks:
            blocks = [_plain_text_block(markdown)]
        children = _direct_child_blocks(blocks)
        if not children:
            children = [_plain_text_block(markdown)]
        quoted_document = urllib.parse.quote(document_id, safe="")
        inserted = 0
        for chunk in _chunks(children, 50):
            self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
                "POST",
                (
                    f"/docx/v1/documents/{quoted_document}/blocks/"
                    f"{quoted_document}/children?document_revision_id=-1"
                ),
                {"children": chunk, "index": -1},
            )
            inserted += len(chunk)
        return {"document_id": document_id, "blocks": inserted}

    def _convert_markdown(self, markdown: str) -> dict[str, Any]:
        return self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            "/docx/v1/documents/blocks/convert",
            {
                "content_type": "markdown",
                "content": markdown,
            },
        )


class FeishuHttpBitableClient:
    """Create/update Bitable records through Feishu OpenAPI."""

    def __init__(self, transport: FeishuHttpTransport | None = None) -> None:
        self.transport = transport or FeishuHttpTransport()

    def create_base(
        self,
        *,
        name: str,
        folder_token: str = "",
        time_zone: str = "",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("base name is required")
        body: dict[str, Any] = {"name": name}
        if folder_token:
            body["folder_token"] = folder_token
        if time_zone:
            body["time_zone"] = time_zone
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            "/base/v3/bases",
            body,
        )
        return _base_result(response)

    def create_table(self, app_token: str, *, name: str) -> dict[str, Any]:
        app = _quoted_required(app_token, "app_token")
        name = name.strip()
        if not name:
            raise ValueError("table name is required")
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            f"/base/v3/bases/{app}/tables",
            {"name": name},
        )
        return _table_result(response)

    def ensure_fields(
        self,
        app_token: str,
        table_id: str,
        field_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        app = _quoted_required(app_token, "app_token")
        table = _quoted_required(table_id, "table_id")
        existing = self._list_field_names(app, table)
        created: list[str] = []
        for spec in field_specs:
            field_name = str(spec.get("field_name") or "").strip()
            if not field_name or field_name in existing:
                continue
            body = {
                "field_name": field_name,
                "type": int(spec.get("type") or 1),
            }
            if isinstance(spec.get("property"), dict):
                body["property"] = spec["property"]
            if spec.get("ui_type"):
                body["ui_type"] = str(spec.get("ui_type") or "")
            self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
                "POST",
                _field_path(app, table),
                body,
            )
            existing.add(field_name)
            created.append(field_name)
        return {"existing": sorted(existing - set(created)), "created": created}

    def _list_field_names(self, app: str, table: str) -> set[str]:
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "GET",
            _field_path(app, table),
            None,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return set()
        names: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("field_name") or item.get("name") or "").strip()
            if name:
                names.add(name)
        return names

    def create_view(
        self,
        app_token: str,
        table_id: str,
        *,
        name: str,
        view_type: str,
    ) -> dict[str, Any]:
        app = _quoted_required(app_token, "app_token")
        table = _quoted_required(table_id, "table_id")
        name = name.strip()
        view_type = view_type.strip()
        if not name:
            raise ValueError("view name is required")
        if not view_type:
            raise ValueError("view_type is required")
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            _view_path(app, table),
            {"view_name": name, "view_type": view_type},
        )
        return _view_result(response)

    def ensure_views(
        self,
        app_token: str,
        table_id: str,
        view_specs: list[dict[str, str]],
    ) -> dict[str, Any]:
        app = _quoted_required(app_token, "app_token")
        table = _quoted_required(table_id, "table_id")
        existing = self._list_view_names(app, table)
        created: list[str] = []
        for spec in view_specs:
            name = str(spec.get("view_name") or "").strip()
            if not name or name in existing:
                continue
            self.create_view(
                app_token,
                table_id,
                name=name,
                view_type=str(spec.get("view_type") or "grid"),
            )
            existing.add(name)
            created.append(name)
        return {"existing": sorted(existing - set(created)), "created": created}

    def ensure_view_layouts(
        self,
        app_token: str,
        table_id: str,
        layout_specs: list[dict[str, object]],
    ) -> dict[str, Any]:
        app = _quoted_required(app_token, "app_token")
        table = _quoted_required(table_id, "table_id")
        view_refs = self._list_view_refs(app, table)
        field_refs = self._list_field_refs(app, table)
        configured: list[str] = []
        skipped: list[str] = []
        for spec in layout_specs:
            name = str(spec.get("view_name") or "").strip()
            view_ref = view_refs.get(name, "")
            if not name or not view_ref:
                if name:
                    skipped.append(name)
                continue
            applied = 0
            visible_fields = visible_fields_body(spec, field_refs)
            if visible_fields:
                self._set_view_property(app, table, view_ref, "visible_fields", visible_fields)
                applied += 1
            filter_config = filter_body(spec, field_refs)
            if filter_config:
                self._set_view_property(app, table, view_ref, "filter", filter_config)
                applied += 1
            group = field_config_body(spec, field_refs, "group_config")
            if group:
                self._set_view_property(app, table, view_ref, "group", group)
                applied += 1
            sort = field_config_body(spec, field_refs, "sort_config")
            if sort:
                self._set_view_property(app, table, view_ref, "sort", sort)
                applied += 1
            timebar = timebar_body(spec, field_refs)
            if timebar:
                self._set_view_property(app, table, view_ref, "timebar", timebar)
                applied += 1
            if applied:
                configured.append(name)
        return {"configured": configured, "skipped": skipped}

    def _set_view_property(
        self,
        app: str,
        table: str,
        view_ref: str,
        segment: str,
        body: dict[str, Any],
    ) -> None:
        self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "PUT",
            _view_property_path(app, table, view_ref, segment),
            body,
        )

    def _list_view_names(self, app: str, table: str) -> set[str]:
        return set(self._list_view_refs(app, table))

    def _list_view_refs(self, app: str, table: str) -> dict[str, str]:
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "GET",
            _view_path(app, table),
            None,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return {}
        refs: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("view_name") or item.get("name") or "").strip()
            view_ref = str(item.get("view_id") or item.get("id") or name).strip()
            if name:
                refs[name] = view_ref
        return refs

    def _list_field_refs(self, app: str, table: str) -> dict[str, str]:
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "GET",
            _field_path(app, table),
            None,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return {}
        refs: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("field_name") or item.get("name") or "").strip()
            field_ref = str(item.get("field_id") or item.get("id") or name).strip()
            if name:
                refs[name] = field_ref
        return refs

    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any]) -> str:
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "POST",
            _record_path(app_token, table_id),
            {"fields": fields},
        )
        return _record_id(response)

    def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str:
        response = self.transport._request_json(  # noqa: SLF001 - package-level OpenAPI reuse
            "PUT",
            f"{_record_path(app_token, table_id)}/{urllib.parse.quote(record_id, safe='')}",
            {"fields": fields},
        )
        return _record_id(response) or record_id


def _record_path(app_token: str, table_id: str) -> str:
    app = _quoted_required(app_token, "app_token")
    table = _quoted_required(table_id, "table_id")
    return f"/bitable/v1/apps/{app}/tables/{table}/records"


def _field_path(app_token: str, table_id: str) -> str:
    app = _quoted_required(app_token, "app_token")
    table = _quoted_required(table_id, "table_id")
    return f"/bitable/v1/apps/{app}/tables/{table}/fields"


def _view_path(app_token: str, table_id: str) -> str:
    app = _quoted_required(app_token, "app_token")
    table = _quoted_required(table_id, "table_id")
    return f"/bitable/v1/apps/{app}/tables/{table}/views"


def _view_property_path(app_token: str, table_id: str, view_ref: str, segment: str) -> str:
    app = _quoted_required(app_token, "app_token")
    table = _quoted_required(table_id, "table_id")
    view = _quoted_required(view_ref, "view_id")
    prop = urllib.parse.quote(segment.strip(), safe="")
    if not prop:
        raise ValueError("view property segment is required")
    return f"/base/v3/bases/{app}/tables/{table}/views/{view}/{prop}"


def _quoted_required(value: str, label: str) -> str:
    quoted = urllib.parse.quote(value.strip(), safe="")
    if not quoted:
        raise ValueError(f"{label} is required")
    return quoted


def _document_result(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    document_id = str(
        document.get("document_id")
        or document.get("token")
        or data.get("document_id")
        or "",
    )
    if not document_id:
        raise ValueError("Feishu document create response did not include document_id")
    result = dict(document)
    result["document_id"] = document_id
    return result


def _base_result(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    base = data.get("base") if isinstance(data.get("base"), dict) else data
    app_token = str(base.get("app_token") or base.get("base_token") or base.get("token") or "")
    if not app_token:
        raise ValueError("Feishu base create response did not include app_token")
    result = dict(base)
    result["app_token"] = app_token
    result.setdefault("base_token", app_token)
    return result


def _table_result(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    table = data.get("table") if isinstance(data.get("table"), dict) else data
    table_id = str(table.get("table_id") or table.get("id") or "")
    if not table_id:
        raise ValueError("Feishu table create response did not include table_id")
    result = dict(table)
    result["table_id"] = table_id
    return result


def _view_result(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    view = data.get("view") if isinstance(data.get("view"), dict) else data
    view_id = str(view.get("view_id") or view.get("id") or "")
    if not view_id:
        raise ValueError("Feishu view create response did not include view_id")
    result = dict(view)
    result["view_id"] = view_id
    return result


def _record_id(response: dict[str, Any]) -> str:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    record = data.get("record") if isinstance(data.get("record"), dict) else data
    return str(record.get("record_id") or "")


def _extract_blocks(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def _plain_text_block(markdown: str) -> dict[str, Any]:
    return {
        "block_type": 2,
        "text": {
            "elements": [{
                "text_run": {
                    "content": markdown,
                    "text_element_style": {},
                },
            }],
            "style": {},
        },
    }


def _direct_child_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for block in blocks:
        try:
            block_type = int(block.get("block_type") or 0)
        except (TypeError, ValueError):
            continue
        if block_type not in _DIRECT_CHILD_BLOCK_TYPES:
            continue
        children.append(_strip_child_readonly(block))
    return children


def _strip_child_readonly(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_child_readonly(item) for item in value]
    if not isinstance(value, dict):
        return value
    blocked = {
        "block_id",
        "parent_id",
        "children",
        "page_token",
        "document_revision_id",
    }
    return {
        key: _strip_child_readonly(item)
        for key, item in value.items()
        if key not in blocked
    }


def _chunks(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]
