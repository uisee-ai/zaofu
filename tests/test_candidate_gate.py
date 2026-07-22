"""合并候选树质量门 fail-closed(2026-07-08,controller review ⑤c)。

r4 F10:多 lane 写入型 workflow 没配 quality_gates,candidate 合成树不经
验证即进 judge。validate 的 WARN 连打三轮无人理(LB-3 教训同型)→ 多 lane
升 fail-closed;单 lane(light)豁免;显式 waiver 是观测型运行的合法出口。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from zf.core.config.candidate_gate import combined_candidate_gate_gap
from zf.core.config.loader import load_config
from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)


def _config(*, lanes: int, gates: dict | None = None, waiver: bool = False):
    roles = [f"dev-lane-{i}" for i in range(lanes)]
    return ZfConfig(
        project=ProjectConfig(name="t"),
        workflow=WorkflowConfig(
            stages=[WorkflowStageConfig(
                id="impl", trigger="task_map.ready",
                topology="fanout_writer_scoped", roles=roles,
            )],
            allow_unverified_candidate=waiver,
        ),
        quality_gates=gates or {},
    )


def test_multi_lane_without_gates_is_a_gap():
    gap = combined_candidate_gate_gap(_config(lanes=2))
    assert "quality_gates" in gap and "multi-lane" in gap


def test_single_lane_light_is_exempt():
    assert combined_candidate_gate_gap(_config(lanes=1)) == ""


def test_real_gates_close_the_gap():
    cfg = _config(lanes=2, gates={
        "static": QualityGateConfig(required_checks=["python -m pytest -q"]),
    })
    assert combined_candidate_gate_gap(cfg) == ""


def test_todo_placeholder_gates_still_gap():
    cfg = _config(lanes=2, gates={
        "static": QualityGateConfig(
            required_checks=["TODO: typecheck 命令"],
        ),
    })
    gap = combined_candidate_gate_gap(cfg)
    assert "TODO" in gap


def test_explicit_waiver_closes_the_gap():
    assert combined_candidate_gate_gap(_config(lanes=2, waiver=True)) == ""


def test_task_contract_required_closes_cold_start_gap():
    cfg = _config(lanes=2)
    cfg.workflow.candidate_quality_source = "task_contract_required"

    assert combined_candidate_gate_gap(cfg) == ""


def test_disabled_or_empty_gates_do_not_count():
    cfg = _config(lanes=2, gates={
        "off": QualityGateConfig(enabled=False, required_checks=["x"]),
        "empty": QualityGateConfig(required_checks=[]),
    })
    assert combined_candidate_gate_gap(cfg) != ""


def test_multi_kind_container_defers_gate_until_selected_flow():
    cfg = _config(lanes=2)
    cfg.workflow.flow_metadata_by_kind = {
        "issue": {"flow_kind": "issue"},
        "prd": {"flow_kind": "prd"},
    }
    cfg.workflow.stages[0].flow_kind = "prd"

    assert combined_candidate_gate_gap(cfg) == ""
    assert combined_candidate_gate_gap(cfg, flow_kind="issue") == ""
    assert "quality_gates" in combined_candidate_gate_gap(
        cfg, flow_kind="prd",
    )


def test_loader_parses_waiver(tmp_path: Path):
    data = {
        "project": {"name": "t", "state_dir": str(tmp_path / ".zf")},
        "roles": [{"name": "dev", "backend": "mock"}],
        "workflow": {"allow_unverified_candidate": True},
    }
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.workflow.allow_unverified_candidate is True


def test_controller_multi_lane_examples_use_task_contract_quality_source(
    tmp_path: Path,
):
    """Controller cold start accepts run-scoped contracts without static commands."""
    import subprocess
    import sys as _sys

    repo = Path(__file__).resolve().parents[1]
    for name in ("prd-fanout-v3", "refactor-lane-v3"):
        proc = subprocess.run(
            [_sys.executable, "-c",
             "import sys; sys.argv=['zf','validate','--path',sys.argv[1]]; "
             "from zf.cli import main; sys.exit(main())",
             str(repo / "examples" / "prod" / "controller" / f"{name}.yaml")],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, (name, proc.stderr[-400:])
    # Issue defaults to one lane, so it has no combined-candidate skew surface.
    issue = subprocess.run(
        [_sys.executable, "-c",
         "import sys; sys.argv=['zf','validate','--path',sys.argv[1]]; "
         "from zf.cli import main; sys.exit(main())",
         str(repo / "examples" / "prod" / "controller" / "issue-fanout-v3.yaml")],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert issue.returncode == 0, issue.stderr[-400:]
    # light(单 lane)不受影响
    proc = subprocess.run(
        [_sys.executable, "-c",
         "import sys; sys.argv=['zf','validate','--path',sys.argv[1]]; "
         "from zf.cli import main; sys.exit(main())",
         str(repo / "examples" / "prod" / "controller" / "prd-light-v3.yaml")],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr[-400:]
