from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.delivery_contract_gate import evaluate_delivery_contract


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_delivery_contract_gate_passes_complete_chain(tmp_path: Path) -> None:
    _write(tmp_path / "capability.json", {
        "capabilities": [{"id": "cap-webchat", "priority": "P0"}],
    })
    _write(tmp_path / "acceptance.json", {
        "rows": [{"id": "acc-webchat", "capability_id": "cap-webchat"}],
    })
    _write(tmp_path / "tests.json", {
        "tests": [{"id": "test-webchat", "acceptance_id": "acc-webchat"}],
    })
    _write(tmp_path / "task-map.json", {
        "tasks": [{
            "id": "TASK-WEBCHAT",
            "capability_ids": ["cap-webchat"],
            "affinity_tag": "webchat",
        }],
    })

    result = evaluate_delivery_contract(tmp_path, {
        "capability_matrix_paths": ["capability.json"],
        "acceptance_matrix_paths": ["acceptance.json"],
        "test_matrix_paths": ["tests.json"],
        "task_map_paths": ["task-map.json"],
    })

    assert result.passed is True
    assert result.blocking_capabilities == 1


def test_delivery_contract_gate_blocks_missing_task_and_test(tmp_path: Path) -> None:
    _write(tmp_path / "capability.json", {
        "capabilities": [{"id": "cap-provider", "priority": "P0"}],
    })
    _write(tmp_path / "acceptance.json", {
        "rows": [{"id": "acc-provider", "capability_id": "cap-provider"}],
    })
    _write(tmp_path / "tests.json", {"tests": []})
    _write(tmp_path / "task-map.json", {"tasks": []})

    result = evaluate_delivery_contract(tmp_path, {
        "capability_matrix_paths": ["capability.json"],
        "acceptance_matrix_paths": ["acceptance.json"],
        "test_matrix_paths": ["tests.json"],
        "task_map_paths": ["task-map.json"],
    })

    assert result.passed is False
    codes = {item.code for item in result.findings}
    assert "delivery_capability_missing_task" in codes
    assert "delivery_capability_missing_test" in codes
