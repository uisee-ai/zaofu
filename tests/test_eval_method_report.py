"""Review exports preserve evidence and never turn missing grades into passes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/eval-method-report.py"


def module():
    spec = importlib.util.spec_from_file_location("method_report", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def experiment(tmp_path):
    run = tmp_path / "run"
    rows = []
    for arm in ("baseline", "candidate"):
        folder = run / f"1-{arm}"
        folder.mkdir(parents=True)
        trace = '{"type":"turn.completed","usage":{"input_tokens":10}}\n'
        (folder / "trace.jsonl").write_text(trace)
        (folder / "prompt.txt").write_text("Review source.md")
        (folder / "project").mkdir()
        (folder / "project/source.md").write_text("The actual specification")
        digest = hashlib.sha256(trace.encode()).hexdigest()
        result = {"returncode": 0, "turn_completed": True, "timed_out": False,
                  "changed_paths": [], "errors": [], "answers": ["progress", "A specific finding"],
                  "trace_sha256": digest, "seconds": 2,
                  "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4},
                  "commands": [{"command": "cat source.md"}, {"command": "cat source.md"}]}
        write(folder / "result.json", result)
        rows.append({"id": folder.name, "arm": arm, "trace_sha256": digest})
    write(run / "manifest.json", {"schema_version": "plan-execution-eval.v1", "runs": rows})
    rubric = tmp_path / "rubric.json"
    write(rubric, [{"id": "atomic", "expectation": "No partial writes", "mandatory": True}])
    return run, rubric, tmp_path / "export"


def grade_all(output):
    for packet in (output / "review").glob("p-*"):
        grade = json.loads((packet / "grading.json").read_text())
        grade["expectations"][0].update(passed=False, evidence="source.md: failure leaves partial state")
        grade["rubric_feedback"]["reviewed"] = True
        write(packet / "grading.json", grade)


def test_cli_exports_only_final_answer_and_keeps_labels_private(experiment):
    run, rubric, output = experiment
    result = subprocess.run([sys.executable, str(SCRIPT), "export", "--run", str(run),
                             "--rubric", str(rubric), "--output", str(output)],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["verdict"] == "human_review_required"
    assert report["arms"]["baseline"]["pending_or_invalid_grades"] == 1
    for packet in (output / "review").glob("p-*"):
        assert (packet / "answer.txt").read_text() == "A specific finding"
        assert (packet / "inputs/source.md").read_text() == "The actual specification"
        assert "baseline" not in (packet / "grading.json").read_text()
    grade_all(output)
    scored = subprocess.run([sys.executable, str(SCRIPT), "summarize", str(output)],
                            capture_output=True, text=True, check=True)
    row = json.loads(scored.stdout)["arms"]["candidate"]
    assert row["reviewed_valid_runs"] == 1 and row["mandatory_failures"] == 1
    assert row["reported_passes"] == 0 and row["repeated_command_count"] == 1
    assert row["seconds_sample_sd"] is None


@pytest.mark.parametrize("failure", ["prepared", "provider_error", "changed_input", "stale_trace", "missing_result"])
def test_failed_or_missing_execution_cannot_be_graded_as_success(experiment, failure):
    run, rubric, output = experiment
    folder = run / "1-candidate"
    result = json.loads((folder / "result.json").read_text())
    if failure == "prepared":
        path = run / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["prepared_only"] = True
        write(path, manifest)
    elif failure == "provider_error":
        result["errors"] = ["turn.failed"]
    elif failure == "changed_input":
        result["changed_paths"] = ["source.md"]
    elif failure == "stale_trace":
        (folder / "trace.jsonl").write_text("different trace")
    write(folder / "result.json", result)
    if failure == "missing_result":
        (folder / "result.json").unlink()
    helper = module()
    helper.export(run, rubric, output)
    grade_all(output)
    row = helper.summarize(output)["arms"]["candidate"]
    assert row["reviewed_valid_runs"] == 0 and row["valid_runs"] == 0
    assert row["usage_totals"]["input_tokens"] is None


@pytest.mark.parametrize("failure", ["duplicate", "no_evidence", "not_boolean", "rubric_unreviewed", "changed_answer", "changed_input"])
def test_invalid_grades_stay_pending(experiment, failure):
    run, rubric, output = experiment
    helper = module()
    helper.export(run, rubric, output)
    grade_all(output)
    for packet in (output / "review").glob("p-*"):
        path = packet / "grading.json"
        grade = json.loads(path.read_text())
        if failure == "duplicate":
            grade["expectations"] *= 2
        elif failure == "no_evidence":
            grade["expectations"][0]["evidence"] = ""
        elif failure == "not_boolean":
            grade["expectations"][0]["passed"] = "true"
        elif failure == "rubric_unreviewed":
            grade["rubric_feedback"]["reviewed"] = False
        elif failure == "changed_input":
            (packet / "inputs/source.md").write_text("Weakened specification")
        else:
            (packet / "answer.txt").write_text("Rewritten answer")
        write(path, grade)
    report = helper.summarize(output)
    assert all(r["pending_or_invalid_grades"] == 1 for r in report["arms"].values())
    assert all(r["reported_passes"] == 0 for r in report["arms"].values())


def test_report_refuses_reused_output_and_changed_rubric(experiment):
    run, rubric, output = experiment
    helper = module()
    helper.export(run, rubric, output)
    with pytest.raises(FileExistsError):
        helper.export(run, rubric, output)
    (output / "review/rubric.json").write_text("[]")
    with pytest.raises(ValueError, match="rubric changed"):
        helper.summarize(output)
