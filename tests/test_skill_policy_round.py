import json
from pathlib import Path
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_rubric_matches_labels_and_previous_report_answers(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    tools = runpy.run_path(str(ROOT / "scripts/publish-skill-round.py"))
    assert tools["calibration"]()["passed"] == 8
    r = json.loads((ROOT / "web/src/data/skill-loading-reference.json").read_text())
    for row in r["observations"]:
        for k, v in tools["score_submission"](row["answer"], row["reference"]).items():
            if k in row:
                assert v == row[k]


def test_planner_lookup_and_missing_artifact_need_actual_tool_evidence(tmp_path):
    p = runpy.run_path(str(ROOT / "scripts/eval-planner-manifest.py"))
    suite = json.loads(p["SUITE"].read_text())
    negative = next(c for c in suite["cases"] if c.get("missing"))
    bodies, sections, _ = p["fixture"](tmp_path, negative)
    assert len(sections["full"]) > len(sections["index"])
    assert (
        len(
            json.loads((tmp_path / "inputs/manifest.json").read_text())["artifact_refs"]
        )
        == 31
    )
    missing = next(
        x["ref"]
        for x in json.loads((tmp_path / "inputs/manifest.json").read_text())[
            "artifact_refs"
        ]
        if x["kind"] == "release-dependency"
    )
    trace = tmp_path / "trace"
    trace.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": str(bodies)},
            }
        )
    )
    assert p["observed_bodies"](trace, tmp_path, bodies, missing) == []
    trace.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": f"cat {missing}",
                    "exit_code": 1,
                    "aggregated_output": f"cat: {missing}: No such file or directory",
                },
            }
        )
    )
    assert p["observed_bodies"](trace, tmp_path, bodies, missing) == [missing]
    assert p["score"](negative, {"decision": "ready"}) == (False, True)
