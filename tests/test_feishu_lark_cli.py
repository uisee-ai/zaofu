from __future__ import annotations

import json
import subprocess

import pytest

from zf.integrations.feishu.lark_cli import (
    LarkCliBitableClient,
    LarkCliChatAdminClient,
    LarkCliDocumentClient,
    LarkCliResult,
    LarkCliRunner,
)
from zf.integrations.feishu.transport import FeishuTransportError


class StubRunner:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[list[str], str | None]] = []

    def run(self, command, *, input_text=None):
        self.calls.append((list(command), input_text))
        payload = self.responses.pop(0) if self.responses else {}
        return LarkCliResult(payload=payload, argv=("lark-cli", *command))


def test_runner_uses_argv_maps_credentials_and_redacts_failure(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            2,
            stdout="",
            stderr="request rejected secret-value",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(
        executable="/bin/true",
        check_version=False,
        environ={
            "FEISHU_APP_ID": "cli_test",
            "FEISHU_APP_SECRET": "secret-value",
        },
        tenant_token_provider=lambda _app_id, _app_secret: "tenant-value",
    )

    with pytest.raises(FeishuTransportError) as exc:
        runner.run(["base", "+record-upsert", "--json", "{}"])

    assert "secret-value" not in str(exc.value)
    argv, kwargs = calls[0]
    assert argv[:3] == ["/bin/true", "base", "+record-upsert"]
    assert kwargs["env"]["LARKSUITE_CLI_APP_ID"] == "cli_test"
    assert kwargs["env"]["LARKSUITE_CLI_APP_SECRET"] == "secret-value"
    assert kwargs["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "tenant-value"
    assert kwargs["env"]["LARKSUITE_CLI_BRAND"] == "feishu"
    assert "shell" not in kwargs


def test_runner_mints_and_reuses_tenant_token_for_app_credentials(monkeypatch):
    calls = []
    token_calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    def mint(app_id, app_secret):
        token_calls.append((app_id, app_secret))
        return "tenant-value"

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(
        executable="/bin/true",
        check_version=False,
        environ={
            "FEISHU_APP_ID": "cli_test",
            "FEISHU_APP_SECRET": "secret-value",
        },
        tenant_token_provider=mint,
    )

    runner.run(["base", "+record-search", "--json", "{}"])
    runner.run(["base", "+record-search", "--json", "{}"])

    assert token_calls == [("cli_test", "secret-value")]
    assert calls[0][1]["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "tenant-value"
    assert calls[1][1]["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "tenant-value"


def test_runner_prefers_explicit_tenant_token(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(
        executable="/bin/true",
        check_version=False,
        environ={
            "FEISHU_APP_ID": "cli_test",
            "FEISHU_APP_SECRET": "secret-value",
            "FEISHU_TENANT_ACCESS_TOKEN": "explicit-token",
        },
        tenant_token_provider=lambda _app_id, _app_secret: pytest.fail(
            "explicit token must avoid minting"
        ),
    )

    runner.run(["base", "+record-search", "--json", "{}"])

    assert calls[0][1]["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "explicit-token"


def test_runner_can_use_profile_without_project_bot_credentials(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(
        executable="/bin/true",
        check_version=False,
        prefer_profile=True,
        environ={
            "FEISHU_APP_ID": "different-app",
            "FEISHU_APP_SECRET": "different-secret",
            "FEISHU_TENANT_ACCESS_TOKEN": "different-token",
            "LARKSUITE_CLI_APP_ID": "different-app",
            "LARKSUITE_CLI_APP_SECRET": "different-secret",
            "LARKSUITE_CLI_TENANT_ACCESS_TOKEN": "different-token",
        },
    )

    runner.run(["base", "+record-search", "--json", "{}"])

    env = calls[0][1]["env"]
    assert "LARKSUITE_CLI_APP_ID" not in env
    assert "LARKSUITE_CLI_APP_SECRET" not in env
    assert "LARKSUITE_CLI_TENANT_ACCESS_TOKEN" not in env
    assert env["LARKSUITE_CLI_DEFAULT_AS"] == "bot"


def test_runner_rejects_commands_outside_allowlist():
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    with pytest.raises(FeishuTransportError, match="not allowed"):
        runner.run(["im", "+send"])


def test_runner_accepts_restricted_project_group_im_commands(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    runner.run(["im", "+chat-create", "--name", "ZaoFu"])
    runner.run(["im", "chat.members", "create", "--params", "{}", "--data", "{}"])

    assert calls[0][1:3] == ["im", "+chat-create"]
    assert calls[1][1:4] == ["im", "chat.members", "create"]


def test_chat_admin_client_creates_and_verifies_required_members():
    runner = StubRunner([
        {"chat_id": "oc_project", "name": "ZaoFu · project"},
        {"users": [{"member_id": "ou_owner"}], "bots": [{"app_id": "cli_runm"}]},
        {},
        {"users": [{"member_id": "ou_owner"}], "bots": [
            {"app_id": "cli_runm"}, {"app_id": "cli_kanban"},
        ]},
    ])
    client = LarkCliChatAdminClient(runner)

    created = client.create_group(
        name="ZaoFu · project",
        owner_open_id="ou_owner",
        bot_app_ids=["cli_runm", "cli_kanban"],
        provisioner_app_id="cli_runm",
    )
    verified = client.ensure_members(
        created["chat_id"],
        owner_open_id="ou_owner",
        bot_app_ids=["cli_runm", "cli_kanban"],
    )

    assert created["chat_id"] == "oc_project"
    create_command = runner.calls[0][0]
    assert create_command[:2] == ["im", "+chat-create"]
    assert create_command[create_command.index("--bots") + 1] == "cli_kanban"
    assert verified["verified"] is True
    member_create = runner.calls[2][0]
    assert member_create[:3] == ["im", "chat.members", "create"]
    assert '"member_id_type":"app_id"' in member_create[member_create.index("--params") + 1]


def test_chat_admin_client_uses_bot_app_id_not_member_id_for_verification():
    client = LarkCliChatAdminClient(
        StubRunner([
            {
                "users": [{"member_id": "ou_owner"}],
                "bots": [{"member_id": "ou_bot_identity", "app_id": "cli_kanban"}],
            }
        ])
    )

    members = client.list_members("oc_project")

    assert members == {"users": {"ou_owner"}, "bots": {"cli_kanban"}}


def test_chat_admin_client_requires_members_list_capability(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="lark-cli version 1.0.56\n", stderr=""
        ),
    )

    with pytest.raises(FeishuTransportError, match="require >= 1.0.64"):
        LarkCliChatAdminClient(LarkCliRunner(executable="/bin/true"))


def test_runner_parses_success_json(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout='{"ok":true,"data":{"record":{"record_id":"rec_1"}}}',
            stderr="",
        ),
    )
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    result = runner.run(["base", "+record-upsert", "--json", "{}"])

    assert result.payload["ok"] is True
    assert result.payload["data"]["record"]["record_id"] == "rec_1"
    assert result.argv[:3] == ("/bin/true", "base", "+record-upsert")


def test_runner_accepts_idempotent_view_layout_no_op(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=(
                "[lark-cli] warning\n"
                '{"ok":false,"error":{"code":800070003,'
                '"message":"no operation produced"}}'
            ),
        ),
    )
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    result = runner.run(["base", "+view-set-sort", "--json", "{}"])

    assert result.payload == {"ok": True, "no_op": True}


def test_runner_does_not_accept_no_op_for_non_idempotent_command(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr='{"ok":false,"error":{"code":800070003}}',
        ),
    )
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    with pytest.raises(FeishuTransportError, match="exited 1"):
        runner.run(["base", "+base-create", "--name", "Example"])


def test_runner_rejects_old_version(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="lark-cli version 1.0.46\n",
            stderr="",
        ),
    )

    with pytest.raises(FeishuTransportError, match="require >= 1.0.47"):
        LarkCliRunner(executable="/bin/true")


def test_runner_reports_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = LarkCliRunner(
        executable="/bin/true",
        check_version=False,
        timeout_seconds=3,
    )

    with pytest.raises(FeishuTransportError, match="timed out after 3s"):
        runner.run(["base", "+record-upsert", "--json", "{}"])


def test_runner_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="not-json",
            stderr="",
        ),
    )
    runner = LarkCliRunner(executable="/bin/true", check_version=False)

    with pytest.raises(FeishuTransportError, match="invalid JSON"):
        runner.run(["base", "+record-upsert", "--json", "{}"])


def test_document_client_uses_v2_markdown_stdin():
    runner = StubRunner(
        [
            {"document": {"document_id": "doc_1", "url": "https://x/docx/doc_1"}},
            {"document_id": "doc_1"},
        ]
    )
    client = LarkCliDocumentClient(runner)

    created = client.create_document(title="Weekly & Review", content="# Body\n")
    appended = client.append_markdown("doc_1", "## Next\n")

    assert created["document_id"] == "doc_1"
    assert appended["blocks"] == 1
    create_command, create_stdin = runner.calls[0]
    assert create_command[:2] == ["docs", "+create"]
    assert create_command[create_command.index("--api-version") + 1] == "v2"
    assert "--title" not in create_command
    assert create_stdin == "<title>Weekly &amp; Review</title>\n## Body\n"
    update_command, update_stdin = runner.calls[1]
    assert update_command[:2] == ["docs", "+update"]
    assert update_command[update_command.index("--command") + 1] == "append"
    assert update_stdin == "## Next\n"


def test_document_client_demotes_titles_before_append():
    runner = StubRunner([{"document_id": "doc_1"}])
    client = LarkCliDocumentClient(runner)

    client.append_markdown("doc_1", "# Report\n\nBody\n# Another\n")

    _, update_stdin = runner.calls[0]
    assert update_stdin == "## Report\n\nBody\n## Another\n"


def test_bitable_client_exact_search_and_duplicate_conflict():
    runner = StubRunner(
        [
            {
                "data": {
                    "fields": ["Task ID"],
                    "record_id_list": ["rec_1", "rec_noise"],
                    "data": [["TASK-1"], ["TASK-10"]],
                }
            },
            {
                "data": {
                    "fields": ["Task ID"],
                    "record_id_list": ["rec_1", "rec_2"],
                    "data": [["TASK-1"], ["TASK-1"]],
                }
            },
        ]
    )
    client = LarkCliBitableClient(runner)

    assert (
        client.find_record_id(
            "app",
            "tbl",
            key_field="Task ID",
            key_value="TASK-1",
        )
        == "rec_1"
    )
    with pytest.raises(FeishuTransportError, match="multiple"):
        client.find_record_id(
            "app",
            "tbl",
            key_field="Task ID",
            key_value="TASK-1",
        )


def test_bitable_client_exact_search_returns_empty_without_match():
    runner = StubRunner(
        [
            {
                "data": {
                    "fields": ["Task ID"],
                    "record_id_list": ["rec_noise"],
                    "data": [["TASK-10"]],
                }
            },
        ]
    )
    client = LarkCliBitableClient(runner)

    assert (
        client.find_record_id(
            "app",
            "tbl",
            key_field="Task ID",
            key_value="TASK-1",
        )
        == ""
    )


def test_bitable_client_maps_fields_views_and_upsert_commands():
    runner = StubRunner(
        [
            {"items": [{"name": "Task ID"}]},
            {"field": {"id": "fld_status"}},
            {"items": [{"name": "ZaoFu Grid"}]},
            {"view": {"id": "vew_kanban"}},
            {"record": {"record_id": "rec_1"}, "created": True},
        ]
    )
    client = LarkCliBitableClient(runner)

    fields = client.ensure_fields(
        "app",
        "tbl",
        [
            {"field_name": "Task ID", "type": 1},
            {
                "field_name": "Status",
                "type": 3,
                "property": {"options": [{"name": "Todo", "color": 0}]},
            },
        ],
    )
    views = client.ensure_views(
        "app",
        "tbl",
        [
            {"view_name": "ZaoFu Grid", "view_type": "grid"},
            {"view_name": "ZaoFu Kanban", "view_type": "kanban"},
        ],
    )
    record_id = client.create_record("app", "tbl", {"Task ID": "TASK-1"})

    assert fields["created"] == ["Status"]
    assert views["created"] == ["ZaoFu Kanban"]
    assert record_id == "rec_1"
    field_json = json.loads(runner.calls[1][0][runner.calls[1][0].index("--json") + 1])
    assert field_json == {
        "name": "Status",
        "type": "select",
        "multiple": False,
        "options": [{"name": "Todo"}],
    }
    assert runner.calls[-1][0][:2] == ["base", "+record-upsert"]


def test_bitable_client_accepts_lark_cli_record_id_list_response():
    runner = StubRunner(
        [
            {
                "data": {
                    "created": True,
                    "record": {"record_id_list": ["rec_1"]},
                }
            }
        ]
    )
    client = LarkCliBitableClient(runner)

    record_id = client.create_record("app", "tbl", {"Task ID": "TASK-1"})

    assert record_id == "rec_1"


def test_bitable_client_detects_deleted_record_before_update():
    runner = StubRunner(
        [
            {
                "data": {
                    "record_id_list": ["rec_1"],
                    "record_not_found": ["rec_1"],
                }
            }
        ]
    )
    client = LarkCliBitableClient(runner)

    with pytest.raises(FeishuTransportError, match="record has been deleted"):
        client.update_record(
            "app",
            "tbl",
            "rec_1",
            {"Task ID": "TASK-1", "Status": "review"},
        )

    assert runner.calls[0][0][:2] == ["base", "+record-get"]


def test_bitable_client_updates_existing_record_after_preflight():
    runner = StubRunner(
        [
            {
                "data": {
                    "record_id_list": ["rec_1"],
                    "record_not_found": [],
                }
            },
            {"data": {"record": {"update": {"Status": "review"}}}},
        ]
    )
    client = LarkCliBitableClient(runner)

    record_id = client.update_record(
        "app",
        "tbl",
        "rec_1",
        {"Task ID": "TASK-1", "Status": "review"},
    )

    assert record_id == "rec_1"
    assert [call[0][:2] for call in runner.calls] == [
        ["base", "+record-get"],
        ["base", "+record-upsert"],
    ]
