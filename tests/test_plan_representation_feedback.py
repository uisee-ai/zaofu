"""Author diagnostics must preserve distinct outcomes and source files."""
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).parents[1] / "skills/zf-plan-task-map-contract/scripts/inspect_representation.py"


def test_feedback_distinguishes_exact_copy_from_unique_semantics(tmp_path):
    matrix = tmp_path / "test_matrix.json"
    matrix.write_text(json.dumps({"tests": [
        {"id": "T1", "then": "拒绝，原件不变", "expected_result": "拒绝，原件不变",
         "parameter_coverage": ["missing role", "reader"]},
        {"id": "T2", "then": "200", "expected_result": "200 and no temporary file"},
        {"id": "T3", "then": "400", "parameter_coverage": "400"},
        {"id": "T4", "then": "", "expected_result": ""},
    ]}, ensure_ascii=False))
    source = tmp_path / "source_index.json"
    source.write_text(json.dumps({"traceability": [{"source_id": "R1", "stage_projections": [
        {"mandatory_statement": "unique final integration obligation"},
    ]}]}))
    before = {p.name: p.read_bytes() for p in (matrix, source)}
    result = subprocess.run([sys.executable, SCRIPT, matrix, "--source-index", source],
                            check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert [row["path"] for row in report["duplicates"]] == [
        "$.tests[0].expected_result", "$.tests[2].parameter_coverage",
    ]
    assert report["duplicate_value_utf8_bytes"] == len("拒绝，原件不变400".encode())
    assert len(report["stage_projection_locations"]) == 1
    assert {p.name: p.read_bytes() for p in (matrix, source)} == before
