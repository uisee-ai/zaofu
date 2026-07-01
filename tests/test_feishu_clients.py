from __future__ import annotations

import json

from zf.integrations.feishu.clients import (
    FeishuHttpBitableClient,
    FeishuHttpDocumentClient,
)
from zf.integrations.feishu.targets import (
    automation_insight_field_specs,
    automation_insight_view_layout_specs,
    automation_insight_view_specs,
)
from zf.integrations.feishu.transport import FeishuHttpTransport


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_feishu_docx_client_converts_and_appends_markdown() -> None:
    requests = []

    def fake_urlopen(request, timeout=15):
        requests.append(request)
        if request.full_url.endswith("/docx/v1/documents/blocks/convert"):
            return _FakeResponse({
                "code": 0,
                "data": {
                    "blocks": [
                        {"block_type": 32, "block_id": "cell", "table_cell": {}},
                        {"block_type": 2, "block_id": "readonly", "text": {"elements": []}},
                    ],
                },
            })
        return _FakeResponse({"code": 0, "data": {}})

    client = FeishuHttpDocumentClient(FeishuHttpTransport(
        base_url="https://open.feishu.cn/open-apis",
        tenant_access_token="tenant-token",
        request_func=fake_urlopen,
    ))

    result = client.append_markdown("doc-a", "# Report\n")

    assert result == {"document_id": "doc-a", "blocks": 1}
    assert requests[0].full_url.endswith("/docx/v1/documents/blocks/convert")
    assert requests[1].full_url.endswith(
        "/docx/v1/documents/doc-a/blocks/doc-a/children?document_revision_id=-1",
    )
    body = json.loads(requests[1].data.decode("utf-8"))
    assert body["children"] == [{"block_type": 2, "text": {"elements": []}}]


def test_feishu_clients_create_targets_and_fields() -> None:
    requests = []
    fields = {"Status": "fld-status"}
    view_ids = {}

    def fake_urlopen(request, timeout=15):
        requests.append(request)
        url = request.full_url
        if request.full_url.endswith("/docs_ai/v1/documents"):
            return _FakeResponse({
                "code": 0,
                "data": {"document": {"document_id": "doc-new", "url": "https://d"}},
            })
        if request.full_url.endswith("/base/v3/bases"):
            return _FakeResponse({
                "code": 0,
                "data": {"base": {"app_token": "app-new", "url": "https://b"}},
            })
        if request.full_url.endswith("/base/v3/bases/app-new/tables"):
            return _FakeResponse({
                "code": 0,
                "data": {"table": {"table_id": "tbl-new"}},
            })
        if request.full_url.endswith("/bitable/v1/apps/app-new/tables/tbl-new/views"):
            if request.get_method() == "GET":
                return _FakeResponse({
                    "code": 0,
                    "data": {
                        "items": [
                            {"view_name": name, "view_id": view_id}
                            for name, view_id in view_ids.items()
                        ],
                    },
                })
            body = json.loads(request.data.decode("utf-8"))
            view_ids[body["view_name"]] = "vew-new"
            return _FakeResponse({
                "code": 0,
                "data": {"view": {"view_id": "vew-new", "view_name": "ZaoFu Kanban"}},
            })
        if request.full_url.endswith("/bitable/v1/apps/app-new/tables/tbl-new/fields"):
            if request.get_method() == "GET":
                return _FakeResponse({
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_name": name, "field_id": field_id}
                            for name, field_id in fields.items()
                        ],
                    },
                })
            body = json.loads(request.data.decode("utf-8"))
            fields[body["field_name"]] = f"fld-{len(fields) + 1}"
            return _FakeResponse({
                "code": 0,
                "data": {"field": {"field_name": "Task ID"}},
            })
        if url.endswith((
            "/base/v3/bases/app-new/tables/tbl-new/views/vew-new/filter",
            "/base/v3/bases/app-new/tables/tbl-new/views/vew-new/group",
            "/base/v3/bases/app-new/tables/tbl-new/views/vew-new/sort",
            "/base/v3/bases/app-new/tables/tbl-new/views/vew-new/visible_fields",
        )):
            return _FakeResponse({"code": 0, "data": {}})
        return _FakeResponse({"code": 0, "data": {}})

    transport = FeishuHttpTransport(
        base_url="https://open.feishu.cn/open-apis",
        tenant_access_token="tenant-token",
        request_func=fake_urlopen,
    )
    document_client = FeishuHttpDocumentClient(transport)
    bitable_client = FeishuHttpBitableClient(transport)

    document = document_client.create_document(
        title="Reports",
        folder_token="fld",
        content="# Reports\n",
    )
    base = bitable_client.create_base(name="Board", folder_token="fld")
    table = bitable_client.create_table(base["app_token"], name="Kanban")
    schema = bitable_client.ensure_fields(
        base["app_token"],
        table["table_id"],
        [
            {"field_name": "Task ID", "type": 1},
            {
                "field_name": "Board Column",
                "type": 3,
                "property": {"options": [{"name": "Todo", "color": 0}]},
            },
            {"field_name": "Status", "type": 1},
        ],
    )
    view_result = bitable_client.ensure_views(
        base["app_token"],
        table["table_id"],
        [{"view_name": "ZaoFu Kanban", "view_type": "kanban"}],
    )
    layouts = bitable_client.ensure_view_layouts(
        base["app_token"],
        table["table_id"],
        [{
            "view_name": "ZaoFu Kanban",
            "visible_fields": ["Task ID", "Board Column"],
            "filter_config": {"logic": "and", "conditions": [["Status", "==", "active"]]},
            "group_config": [{"field": "Board Column", "desc": False}],
            "sort_config": [{"field": "Task ID", "desc": False}],
        }],
    )

    assert document["document_id"] == "doc-new"
    assert base["app_token"] == "app-new"
    assert table["table_id"] == "tbl-new"
    assert schema["created"] == ["Task ID", "Board Column"]
    assert view_result["created"] == ["ZaoFu Kanban"]
    assert layouts["configured"] == ["ZaoFu Kanban"]
    field_bodies = [
        json.loads(request.data.decode("utf-8"))
        for request in requests
        if request.full_url.endswith("/bitable/v1/apps/app-new/tables/tbl-new/fields")
        and request.get_method() == "POST"
    ]
    assert field_bodies[1]["property"]["options"][0]["name"] == "Todo"
    assert any(
        request.full_url.endswith("/bitable/v1/apps/app-new/tables/tbl-new/fields")
        for request in requests
    )
    assert any(
        request.full_url.endswith("/bitable/v1/apps/app-new/tables/tbl-new/views")
        for request in requests
    )
    layout_bodies = [
        json.loads(request.data.decode("utf-8"))
        for request in requests
        if "/base/v3/bases/app-new/tables/tbl-new/views/vew-new/" in request.full_url
    ]
    assert layout_bodies[0]["visible_fields"] == ["fld-2", "fld-3"]
    assert layout_bodies[1]["conditions"][0][0] == "fld-status"
    assert layout_bodies[2]["group_config"][0]["field"] == "fld-3"
    assert layout_bodies[3]["sort_config"][0]["field"] == "fld-2"
    assert any(request.get_method() == "GET" and request.data is None for request in requests)


def test_feishu_bitable_client_creates_and_updates_records() -> None:
    requests = []

    def fake_urlopen(request, timeout=15):
        requests.append(request)
        return _FakeResponse({
            "code": 0,
            "data": {"record": {"record_id": "rec-a"}},
        })

    client = FeishuHttpBitableClient(FeishuHttpTransport(
        base_url="https://open.feishu.cn/open-apis",
        tenant_access_token="tenant-token",
        request_func=fake_urlopen,
    ))

    assert client.create_record("app-a", "tbl-a", {"Task ID": "TASK-A"}) == "rec-a"
    assert client.update_record("app-a", "tbl-a", "rec-a", {"Status": "done"}) == "rec-a"

    assert requests[0].full_url.endswith("/bitable/v1/apps/app-a/tables/tbl-a/records")
    assert requests[0].get_method() == "POST"
    assert requests[1].full_url.endswith(
        "/bitable/v1/apps/app-a/tables/tbl-a/records/rec-a",
    )
    assert requests[1].get_method() == "PUT"


def test_automation_insight_table_specs_define_operator_views() -> None:
    views = automation_insight_view_specs()
    layouts = automation_insight_view_layout_specs()

    assert [view["view_name"] for view in views] == [
        "ZaoFu Overview",
        "ZaoFu Highlights",
        "ZaoFu Action Required",
        "ZaoFu Delivery Health",
        "ZaoFu Runtime Health",
        "ZaoFu History",
    ]
    assert layouts[0]["filter_config"]["conditions"] == [["Record Type", "==", "summary"]]
    assert layouts[1]["view_name"] == "ZaoFu Highlights"
    assert ["Highlight", "==", "Normal"] not in layouts[1]["filter_config"]["conditions"]
    assert layouts[1]["group_config"][0]["field"] == "Highlight"
    assert layouts[2]["filter_config"]["logic"] == "or"
    assert layouts[3]["filter_config"]["conditions"] == [["Automation", "==", "weekly-review"]]
    assert layouts[4]["filter_config"]["conditions"] == [["Automation", "==", "project-monitor"]]


def test_automation_insight_field_specs_create_highlight_as_select() -> None:
    specs = automation_insight_field_specs()
    highlight = next(spec for spec in specs if spec["field_name"] == "Highlight")

    assert highlight["type"] == 3
    assert highlight["property"]["options"][0]["name"] == "P0 Action Required"
    assert highlight["property"]["options"][-1]["name"] == "Normal"
