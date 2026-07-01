from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.config.loader import ConfigError, load_config
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_briefing import build_orchestrator_briefing


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "zf.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _valid_reader_yaml() -> str:
    return """
version: "1.0"
project:
  name: test
roles:
  - name: review
    backend: mock
    role_kind: reader
    publishes: [review.approved, review.rejected]
  - name: security-review
    backend: mock
    role_kind: reader
    publishes: [review.approved, review.rejected]
workflow:
  stages:
    - id: review-candidate
      trigger: candidate.ready
      topology: fanout_reader
      roles: [review, security-review]
      target_ref: candidate/${pdd_id}
      aggregate:
        mode: wait_for_all
        success_event: review.approved
        failure_event: review.rejected
      timeout_seconds: 900
"""


def test_valid_fanout_reader_stage_loads(tmp_path: Path):
    config = load_config(_write_config(tmp_path, _valid_reader_yaml()))

    assert len(config.workflow.stages) == 1
    stage = config.workflow.stages[0]
    assert stage.id == "review-candidate"
    assert stage.topology == "fanout_reader"
    assert stage.aggregate.mode == "wait_for_all"


def test_invalid_topology_name_fails(tmp_path: Path):
    path = _write_config(
        tmp_path,
        _valid_reader_yaml().replace("fanout_reader", "fanout_any"),
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_fanout_role_fails(tmp_path: Path):
    path = _write_config(
        tmp_path,
        _valid_reader_yaml().replace("[review, security-review]", "[review, missing]"),
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_synth_role_must_be_reader_role(tmp_path: Path):
    path = _write_config(
        tmp_path,
        _valid_reader_yaml().replace(
            "  - name: security-review\n"
            "    backend: mock\n"
            "    role_kind: reader\n"
            "    publishes: [review.approved, review.rejected]\n",
            "  - name: security-review\n"
            "    backend: mock\n"
            "    role_kind: reader\n"
            "    publishes: [review.approved, review.rejected]\n"
            "  - name: review-synth\n"
            "    backend: mock\n"
            "    role_kind: writer\n",
        ).replace(
            "        failure_event: review.rejected\n",
            "        failure_event: review.rejected\n"
            "        synth_role: review-synth\n",
        ),
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_reader_fanout_with_writer_role_fails(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
version: "1.0"
project:
  name: test
roles:
  - name: dev
    backend: mock
    role_kind: writer
workflow:
  stages:
    - id: bad-reader
      trigger: candidate.ready
      topology: fanout_reader
      roles: [dev]
      aggregate:
        mode: wait_for_all
        success_event: review.approved
        failure_event: review.rejected
""",
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_writer_fanout_without_task_map_fails(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
version: "1.0"
project:
  name: test
roles:
  - name: dev
    backend: mock
    role_kind: writer
workflow:
  stages:
    - id: dev-wave
      trigger: tasks.ready
      topology: fanout_writer_scoped
      roles: [dev]
      aggregate:
        mode: wait_for_all
        success_event: candidate.updated
        failure_event: candidate.conflict
""",
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_writer_fanout_accepts_source_task_map_and_role_pool(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
version: "1.0"
project:
  name: test
roles:
  - name: dev
    instance_id: dev-1
    backend: mock
    role_kind: writer
  - name: dev
    instance_id: dev-2
    backend: mock
    role_kind: writer
workflow:
  stages:
    - id: dev-wave
      trigger: task_map.ready
      topology: fanout_writer_scoped
      source:
        task_map: ".zf/artifacts/${pdd_id}/task_map.json"
      fanout:
        assignment:
          role_pool: [dev-1, dev-2]
      aggregate:
        mode: candidate_integration
        success_event: candidate.ready
        failure_event: integration.failed
""",
    )

    config = load_config(path)

    stage = config.workflow.stages[0]
    assert stage.roles == ["dev-1", "dev-2"]
    assert stage.task_map == ".zf/artifacts/${pdd_id}/task_map.json"


def test_workflow_render_output_is_stable(tmp_path: Path, monkeypatch, capsys):
    _write_config(tmp_path, _valid_reader_yaml())
    monkeypatch.chdir(tmp_path)

    result = main(["workflow", "render"])

    assert result == 0
    assert capsys.readouterr().out == (
        "Linear topology:\n"
        "(empty topology)\n"
        "\n"
        "Star stages:\n"
        "  candidate.ready --[fanout_reader:review-candidate]--> "
        "[review, security-review] --[wait_for_all]--> "
        "review.approved / review.rejected (target=candidate/${pdd_id})\n"
    )


def test_orchestrator_briefing_mentions_declared_star_stage(tmp_path: Path):
    config = load_config(_write_config(tmp_path, _valid_reader_yaml()))
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")

    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config,
        trigger_event=ZfEvent(type="candidate.ready", actor="zf-cli"),
    )

    assert "review-candidate" in briefing
    assert "fanout_reader" in briefing
    assert "Layer 2 不能在 YAML 之外发明 authoritative fanout" in briefing
