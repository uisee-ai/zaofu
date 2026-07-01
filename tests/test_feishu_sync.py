from __future__ import annotations

import json
from pathlib import Path

import yaml

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.integrations.feishu.clients import (
    MockFeishuBitableClient,
    MockFeishuDocumentClient,
)
from zf.integrations.feishu.sync import (
    FeishuSyncLedger,
    sync_automation_bitable,
    sync_automation_document,
    sync_kanban_bitable,
)
from zf.integrations.feishu.targets import (
    parse_feishu_bitable_ref,
    parse_feishu_document_id,
)
from zf.integrations.feishu.transport import FeishuTransportError
from zf.cli import feishu as cli_feishu


def _project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        yaml.safe_dump({
            "version": "1.0",
            "project": {"name": "feishu-sync-test", "state_dir": "runtime-state"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }),
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    return tmp_path, tmp_path / "runtime-state"


def test_sync_automation_document_renders_and_emits_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-A", title="active"))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    client = MockFeishuDocumentClient()

    result = sync_automation_document(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        document_id="doc-a",
        client=client,
        ledger=FeishuSyncLedger.for_state_dir(state_dir),
        writer=writer,
    )

    assert result["ok"] is True
    assert client.appended[0][0] == "doc-a"
    markdown = client.appended[0][1]
    assert "# ZaoFu Project Overview" in markdown
    assert "## Project Status" in markdown
    assert "- Board: Todo=1, In Progress=0, Verify=0, Blocked=0, Done=0" in markdown
    assert "## Action Required" in markdown
    assert "## Delivery Health" in markdown
    assert "## Runtime Health" in markdown
    assert "## Open In ZaoFu" in markdown
    assert "### Progress Snapshot" not in markdown
    assert "## Evidence Refs" not in markdown
    assert "Terminal success rate is low" not in markdown
    assert "| Metric |" not in markdown
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "feishu.document.synced" for event in events)
    ledger = json.loads(
        (state_dir / "integrations" / "feishu" / "sync-ledger.json").read_text(
            encoding="utf-8",
        ),
    )
    assert ledger["documents"]["automations:proj-a"]["document_id"] == "doc-a"


def test_sync_automation_bitable_upserts_summary_and_insight_rows(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-A", title="active"))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    client = MockFeishuBitableClient()
    ledger = FeishuSyncLedger.for_state_dir(state_dir)

    first = sync_automation_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl-auto",
        client=client,
        ledger=ledger,
        writer=writer,
    )
    second = sync_automation_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl-auto",
        client=client,
        ledger=ledger,
        writer=writer,
    )

    assert first["created"] >= 3
    assert first["updated"] == 0
    assert second["created"] <= 1
    assert second["updated"] >= first["created"] - 1
    assert any(row[2]["Record Type"] == "summary" for row in client.created)
    assert any(row[2]["Record Type"] == "insight" for row in client.created)
    assert any(row[2]["Automation"] == "daily-brief" for row in client.created)
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "feishu.automation_bitable.synced" for event in events)


def test_sync_kanban_bitable_upserts_by_ledger(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-A", title="active"))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    client = MockFeishuBitableClient()
    ledger = FeishuSyncLedger.for_state_dir(state_dir)

    first = sync_kanban_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl",
        client=client,
        ledger=ledger,
        writer=writer,
    )
    second = sync_kanban_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl",
        client=client,
        ledger=ledger,
        writer=writer,
    )

    assert first["created"] == 1
    assert first["updated"] == 0
    assert second["created"] == 0
    assert second["updated"] == 1
    assert client.created[0][2]["Task ID"] == "TASK-A"
    assert client.created[0][2]["Priority"] == "3"
    assert client.created[0][2]["Board Column"] == "Todo"
    assert client.updated[0][2] == "rec-1"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(event.type == "feishu.bitable.synced" for event in events) == 2


def test_sync_kanban_bitable_uses_workflow_projection(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-VERIFY",
        title="handoff to review",
        status="in_progress",
        assigned_to="dev",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-VERIFY"))
    client = MockFeishuBitableClient()

    result = sync_kanban_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl",
        client=client,
        ledger=FeishuSyncLedger.for_state_dir(state_dir),
    )

    assert result["created"] == 1
    assert client.created[0][2]["Board Column"] == "Verify"


def test_sync_kanban_bitable_recreates_deleted_remote_record(tmp_path: Path) -> None:
    class DeletedRecordClient(MockFeishuBitableClient):
        def update_record(
            self,
            app_token: str,
            table_id: str,
            record_id: str,
            fields: dict,
        ) -> str:
            self.updated.append((app_token, table_id, record_id, dict(fields)))
            raise FeishuTransportError("Feishu API error 1002: note has been deleted")

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-A", title="active"))
    ledger = FeishuSyncLedger.for_state_dir(state_dir)
    ledger.set_bitable_record_id(
        app_token="app",
        table_id="tbl",
        task_id="TASK-A",
        record_id="rec-deleted",
    )
    client = DeletedRecordClient()

    result = sync_kanban_bitable(
        state_dir=state_dir,
        project_id="proj-a",
        project_name="Proj A",
        app_token="app",
        table_id="tbl",
        client=client,
        ledger=ledger,
    )

    assert result["created"] == 1
    assert result["recreated"] == 1
    assert result["updated"] == 0
    assert client.updated[0][2] == "rec-deleted"
    assert client.created[0][2]["Task ID"] == "TASK-A"
    assert ledger.bitable_record_id(
        app_token="app",
        table_id="tbl",
        task_id="TASK-A",
    ) == "rec-1"


def test_cli_feishu_sync_dry_runs_use_project_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, state_dir = _project(tmp_path, monkeypatch)
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-A", title="active"))

    assert main(["feishu", "sync-automations", "--dry-run"]) == 0
    automations = capsys.readouterr().out
    assert "# ZaoFu Project Overview" in automations
    assert "## Project Status" in automations
    assert "- Board: Todo=1, In Progress=0, Verify=0, Blocked=0, Done=0" in automations
    assert "feishu-sync-test" in automations

    assert main(["feishu", "sync-kanban-table", "--dry-run"]) == 0
    kanban = capsys.readouterr().out
    assert "# ZaoFu Kanban Sync" in kanban
    assert "TASK-A" in kanban
    assert not (project / ".zf").exists()


def test_feishu_cli_loads_project_dotenv_without_overriding_shell_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.setenv("FEISHU_APP_SECRET", "shell-secret")
    (tmp_path / ".env").write_text(
        "\n".join([
            "FEISHU_APP_ID=env-app",
            "FEISHU_APP_SECRET=env-secret",
            "FEISHU_AUTOMATION_DOCUMENT_ID=doc-env",
            "FEISHU_BITABLE_APP_TOKEN=app-env",
            "FEISHU_BITABLE_TABLE_ID=tbl-env",
        ]) + "\n",
        encoding="utf-8",
    )

    loaded = cli_feishu._load_project_env(tmp_path)

    assert loaded["FEISHU_APP_ID"] == "env-app"
    assert loaded["FEISHU_AUTOMATION_DOCUMENT_ID"] == "doc-env"
    assert loaded["FEISHU_BITABLE_APP_TOKEN"] == "app-env"
    assert "FEISHU_APP_SECRET" not in loaded
    assert cli_feishu.os.environ["FEISHU_APP_SECRET"] == "shell-secret"


def test_feishu_target_url_parsing() -> None:
    assert parse_feishu_document_id("https://example.feishu.cn/docx/docabc123?from=space") == (
        "docabc123"
    )
    assert parse_feishu_document_id("doc-bare") == "doc-bare"

    ref = parse_feishu_bitable_ref(
        "https://example.feishu.cn/base/appabc123?table=tblabc123&view=vewabc123",
    )
    assert ref.app_token == "appabc123"
    assert ref.table_id == "tblabc123"
    assert ref.view_id == "vewabc123"


def test_cli_feishu_init_targets_writes_project_dotenv(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _project(tmp_path, monkeypatch)

    assert main([
        "feishu",
        "init-targets",
        "--transport",
        "mock",
        "--write-env",
    ]) == 0

    out = capsys.readouterr().out
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "Created Feishu sync targets" in out
    assert "FEISHU_AUTOMATION_DOCUMENT_ID=doc-mock-1" in env_text
    assert "FEISHU_AUTOMATION_BITABLE_TABLE_ID=tbl-mock-2" in env_text
    assert "FEISHU_BITABLE_APP_TOKEN=app-mock-1" in env_text
    assert "FEISHU_BITABLE_TABLE_ID=tbl-mock-1" in env_text
    assert not (tmp_path / ".zf").exists()


def test_cli_feishu_sync_uses_project_dotenv_targets(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _project(tmp_path, monkeypatch)
    for name in (
        "FEISHU_AUTOMATION_DOCUMENT_ID",
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
        "FEISHU_AUTOMATION_DOCUMENT_URL",
        "FEISHU_BITABLE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join([
            "FEISHU_AUTOMATION_DOCUMENT_ID=doc-env",
            "FEISHU_BITABLE_APP_TOKEN=app-env",
            "FEISHU_BITABLE_TABLE_ID=tbl-env",
        ]) + "\n",
        encoding="utf-8",
    )

    assert main(["feishu", "sync-automations"]) == 0
    assert "doc-env" in capsys.readouterr().out

    assert main(["feishu", "sync-kanban-table"]) == 0
    assert "tbl-env" in capsys.readouterr().out


def test_cli_feishu_sync_kanban_ensures_base_views(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _project(tmp_path, monkeypatch)
    TaskStore(tmp_path / "runtime-state" / "kanban.json").add(
        Task(id="TASK-A", title="active"),
    )
    for name in (
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
        "FEISHU_BITABLE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join([
            "FEISHU_BITABLE_APP_TOKEN=app-board",
            "FEISHU_BITABLE_TABLE_ID=tbl-board",
        ]) + "\n",
        encoding="utf-8",
    )
    client = MockFeishuBitableClient()
    monkeypatch.setattr(cli_feishu, "_build_bitable_client", lambda kind: client)

    assert main(["feishu", "sync-kanban-table", "--transport", "mock"]) == 0

    out = capsys.readouterr().out
    ensured_fields = client.ensured_fields[0][2]
    assert "Kanban Base layout" in out
    assert any(
        spec["field_name"] == "Board Column" and spec["type"] == 3
        for spec in ensured_fields
    )
    assert [view["view_name"] for view in client.created_views[-2:]] == [
        "ZaoFu Grid",
        "ZaoFu Kanban",
    ]
    assert client.configured_layouts[-1]["group_config"][0]["field"] == "Board Column"
    assert client.created[0][2]["Board Column"] == "Todo"


def test_cli_feishu_sync_automation_insights_creates_missing_table(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _project(tmp_path, monkeypatch)
    for name in (
        "FEISHU_AUTOMATION_BITABLE_APP_TOKEN",
        "FEISHU_AUTOMATION_BITABLE_TABLE_ID",
        "FEISHU_AUTOMATION_BITABLE_URL",
        "FEISHU_BITABLE_APP_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "FEISHU_BITABLE_APP_TOKEN=app-board\n",
        encoding="utf-8",
    )
    client = MockFeishuBitableClient()
    monkeypatch.setattr(cli_feishu, "_build_bitable_client", lambda kind: client)

    assert main([
        "feishu",
        "sync-automation-insights-table",
        "--transport",
        "mock",
    ]) == 0

    out = capsys.readouterr().out
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "Created Feishu Automation Insights table" in out
    assert "Synced Automation insights" in out
    assert "FEISHU_AUTOMATION_BITABLE_APP_TOKEN=app-board" in env_text
    assert "FEISHU_AUTOMATION_BITABLE_TABLE_ID=tbl-mock-1" in env_text
    assert client.created_tables[0]["app_token"] == "app-board"
    assert client.created[0][0] == "app-board"
    assert client.created[0][1] == "tbl-mock-1"


def test_cli_feishu_sync_recreates_deleted_kanban_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class DeletedTargetClient(MockFeishuBitableClient):
        def create_record(self, app_token: str, table_id: str, fields: dict) -> str:
            if app_token == "app-deleted":
                raise FeishuTransportError(
                    "Feishu API error 1002: note has been deleted",
                )
            return super().create_record(app_token, table_id, fields)

    _project(tmp_path, monkeypatch)
    TaskStore(tmp_path / "runtime-state" / "kanban.json").add(
        Task(id="TASK-A", title="active"),
    )
    for name in (
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
        "FEISHU_BITABLE_URL",
        "FEISHU_FOLDER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join([
            "FEISHU_BITABLE_APP_TOKEN=app-deleted",
            "FEISHU_BITABLE_TABLE_ID=tbl-deleted",
            "FEISHU_FOLDER_TOKEN=fld-a",
        ]) + "\n",
        encoding="utf-8",
    )
    client = DeletedTargetClient()
    monkeypatch.setattr(cli_feishu, "_build_bitable_client", lambda kind: client)

    assert main(["feishu", "sync-kanban-table", "--transport", "mock"]) == 0

    out = capsys.readouterr().out
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "Recreated Feishu Kanban target" in out
    assert "FEISHU_BITABLE_APP_TOKEN=app-mock-1" in env_text
    assert "FEISHU_BITABLE_TABLE_ID=tbl-mock-1" in env_text
    assert "FEISHU_BITABLE_URL=https://example.feishu.cn/base/app-mock-1?table=tbl-mock-1" in env_text
    assert client.created_bases[0]["folder_token"] == "fld-a"
    assert [view["view_name"] for view in client.created_views[-2:]] == [
        "ZaoFu Grid",
        "ZaoFu Kanban",
    ]
    assert client.created[0][0] == "app-mock-1"
    assert client.created[0][1] == "tbl-mock-1"


def test_cli_feishu_sync_accepts_feishu_urls_from_dotenv(tmp_path: Path, monkeypatch, capsys) -> None:
    _project(tmp_path, monkeypatch)
    for name in (
        "FEISHU_AUTOMATION_DOCUMENT_ID",
        "FEISHU_AUTOMATION_DOC_ID",
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join([
            "FEISHU_AUTOMATION_DOCUMENT_URL=https://example.feishu.cn/docx/doc-url",
            "FEISHU_BITABLE_URL=https://example.feishu.cn/base/app-url?table=tbl-url",
        ]) + "\n",
        encoding="utf-8",
    )

    assert main(["feishu", "sync-automations"]) == 0
    assert "doc-url" in capsys.readouterr().out

    assert main(["feishu", "sync-kanban-table"]) == 0
    assert "tbl-url" in capsys.readouterr().out


def test_cli_feishu_cron_template(tmp_path: Path, monkeypatch, capsys) -> None:
    _project(tmp_path, monkeypatch)

    assert main([
        "feishu",
        "cron-template",
        "--daily-time",
        "08:30",
        "--hourly-minute",
        "12",
    ]) == 0

    out = capsys.readouterr().out
    assert "30 8 * * *" in out
    assert "12 * * * *" in out
    assert "sync-automations" in out
    assert "sync-kanban-table" in out
