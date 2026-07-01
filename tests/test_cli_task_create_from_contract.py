from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.task.store import TaskStore


def test_task_create_from_contract_creates_feature_task_and_events(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    contract_file = tmp_path / "contract.json"
    contract_file.write_text(json.dumps({
        "contract": {
            "behavior": "Implement word_stats",
            "verification": "python -m unittest",
            "verification_tiers": ["runtime"],
            "scope": ["src/textkit/stats.py"],
            "acceptance": ["counts words", "tests pass"],
            "spec_skip_reason": "fixture task",
            "task_id": "ignored-extra-field",
        },
    }), encoding="utf-8")

    result = main([
        "task",
        "create-from-contract",
        "--state-dir",
        str(state_dir),
        "--title",
        "Implement word_stats",
        "--feature-title",
        "Textkit",
        "--contract-file",
        str(contract_file),
        "--assign",
        "dev",
        "--json",
    ])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    task = TaskStore(state_dir / "kanban.json").get(payload["task_id"])
    assert task is not None
    assert task.assigned_to == "dev"
    assert task.contract.feature_id == payload["feature_id"]
    assert task.contract.behavior == "Implement word_stats"
    assert task.contract.acceptance == "counts words\ntests pass"

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert [event.type for event in events] == [
        "feature.created",
        "task.created",
        "task.contract.update",
        "task.assigned",
    ]
    assert events[-1].payload["assignee"] == "dev"
