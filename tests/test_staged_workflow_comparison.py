import json
import sys
from pathlib import Path

from tests.e2e.staged_workflow_comparison import ARMS, IDENTITY, METRICS, digest, run_comparison, run_process


def test_all_arms_keep_failure_and_identity_drift(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("import json,sys; j=json.load(open(sys.argv[1])); print(json.dumps({'identity':j['identity'] if j['arm']=='direct' else {},'metrics':" + repr({k: False if k in {'accepted', 'integrated'} else 0 for k in METRICS}) + "}))")
    commands = {arm: [sys.executable, str(worker), "{job}"] for arm in ARMS}
    commands["dynamic"] = [sys.executable, "-c", "raise SystemExit(7)"]
    manifest = {"schema_version": "staged-workflow-comparison.v1", "identity": {key: key for key in IDENTITY},
                "commands": commands, "repeats": 1, "timeout_seconds": 5,
                "cases": [{"id": "case-1", "route": "general", "scenario": "restart"}]}
    import hashlib
    manifest["source_files"] = {str(worker): hashlib.sha256(worker.read_bytes()).hexdigest()}
    for key, value in (("source_digest", manifest["source_files"]), ("input_digest", manifest["cases"]), ("budget_digest", {})):
        manifest["identity"][key] = digest(value)
    result = run_comparison(manifest, tmp_path / "result.json")
    assert [row["status"] for row in result["rows"]] == ["completed", "identity_mismatch", "failed"]
    assert result["decision"] == "HOLD"
    assert len(json.loads((tmp_path / "result.json").read_text())["rows"]) == 3


def test_timeout_kills_child_process_group(tmp_path):
    result = run_process([sys.executable, "-c", "import time; time.sleep(20)"], cwd=tmp_path, timeout=.05)
    assert result["status"] == "timeout"
    assert result["returncode"] < 0


def test_timeout_kills_provider_descendants_in_shared_group(tmp_path):
    import os
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
    result = run_process([sys.executable, "-u", "-c", code], cwd=tmp_path, timeout=.2)
    pid = int(result["stdout"].strip())
    state = Path(f"/proc/{pid}/stat")
    assert not state.exists() or state.read_text().split()[2] == "Z"


def test_unknown_and_nonfinite_metrics_never_promote():
    from tests.e2e.staged_workflow_comparison import valid_metrics
    row = {k: 0 for k in METRICS}
    row.update(accepted=True, integrated=True, cost_usd=None)
    assert valid_metrics(row)
    row["tokens"] = float("nan")
    assert not valid_metrics(row)
