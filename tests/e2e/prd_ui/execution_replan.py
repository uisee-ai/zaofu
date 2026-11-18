"""External fixture change and independent audit; never writes runtime state."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

from zf.core.config.loader import load_config
from zf.core.events import EventLog


def _events(project: Path):
    state = project / load_config(project / "zf.yaml").project.state_dir
    log = EventLog(state / "events.jsonl")
    try:
        return log.read_all()
    finally:
        log.close()


def initialize_protocol(root: Path) -> None:
    (root / "protocol.json").write_text(json.dumps({
        "version": 1, "input_key": "message", "output_prefix": "v1:",
    }))


def change_protocol_after_implementation(root: Path, project: Path) -> None:
    evidence = root / "evidence"
    receipt = evidence / "protocol-change.json"
    if receipt.exists():
        return
    verification = next((e for e in _events(project)
                         if e.type == "task.pipeline.stage.dispatched"
                         and e.payload.get("task_pipeline_stage") == "verify"), None)
    if verification is None:
        return
    target = str(verification.payload.get("target_commit") or "")
    if not target:
        raise AssertionError("verification lacks implemented commit")
    protocol = root / "protocol.json"
    before = protocol.read_bytes()
    revised = {"version": 2, "input_key": "text", "output_prefix": "v2:"}
    temporary = protocol.with_suffix(".next")
    temporary.write_text(json.dumps(revised))
    temporary.replace(protocol)
    receipt.write_text(json.dumps({
        "trigger_event_id": verification.id, "task_id": verification.task_id,
        "workflow_run_id": verification.payload.get("workflow_run_id"),
        "target_commit": target, "before": json.loads(before), "after": revised,
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "boundary": "Owner-authorized external input change after implementation; no Plan or state edits",
    }, indent=2))


def check_execution_order(events, change: dict) -> dict:
    run_id = change.get("workflow_run_id")
    assert run_id, "fixture change lacks Run binding"
    events = [e for e in events if (e.payload.get("workflow_run_id") or
                                  e.payload.get("trace_id") or e.correlation_id) == run_id]
    by_id = {event.id: index for index, event in enumerate(events)}
    assert change["trigger_event_id"] in by_id, "missing fixture-change trigger"
    changed = by_id[change["trigger_event_id"]]
    initial = [e for e in events[:changed] if e.type == "plan.approved"]
    assert initial, "implementation not bound to approved Plan"
    replans = [e for e in events[changed:] if e.type in {
        "orchestrator.replan_requested", "prd.plan.requested",
    }]
    assert replans, "no execution-time replan"
    approvals = [e for e in events[by_id[replans[0].id]:] if e.type == "plan.approved"]
    assert approvals, "regenerated Plan not approved"
    assert approvals[-1].payload["plan_id"] != initial[-1].payload["plan_id"], "old approval reused"
    dispatches = [e for e in events[by_id[approvals[-1].id]:]
                  if e.type == "task.pipeline.stage.dispatched"]
    assert dispatches, "no execution resumed after new approval"
    old_generation = events[changed].payload.get("task_map_generation")
    assert old_generation, "initial execution lacks Task Map generation"
    assert any(e.payload.get("task_map_generation") and
               e.payload["task_map_generation"] != old_generation for e in dispatches), "old generation reused"
    completed = [e for e in events[by_id[dispatches[0].id]:] if e.type == "run.goal.completed"]
    assert len(completed) == 1, "missing unique final goal completion"
    return {"initial_plan": initial[-1].payload["plan_id"],
            "revised_plan": approvals[-1].payload["plan_id"],
            "replan_event": replans[0].id, "resumed_dispatches": [e.id for e in dispatches],
            "completed_event": completed[0].id}


def verify_protocol_product(project: Path, commit: str) -> dict:
    # Read the exact final Git object, not a worker's mutable checkout or tests.
    source = subprocess.check_output(["git", "show", f"{commit}:render_protocol.py"], cwd=project)
    cases = []
    with TemporaryDirectory(prefix="zf-protocol-oracle-") as directory:
        archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=project)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(directory, filter="data")
        script = Path(directory) / "render_protocol.py"
        for value, output, success in [
            ('{"text":"hello"}', b"v2:hello\n", True),
            ('{"text":"中文"}', "v2:中文\n".encode(), True),
            ('{"text":""}', b"v2:\n", True),
            ('{"message":"old"}', b"", False),
            ('{"text":"ok","extra":1}', b"", False),
            ('[]', b"", False),
            ('{"text":3}', b"", False), ('not json', b"", False),
        ]:
            result = subprocess.run([sys.executable, script], input=value.encode(),
                                    capture_output=True, timeout=10, cwd=directory)
            passed = ((result.returncode == 0) == success and result.stdout == output
                      and (not result.stderr if success else bool(result.stderr)))
            cases.append({"input": value, "passed": passed, "exit_code": result.returncode,
                          "stdout": result.stdout.decode(errors="replace")})
    return {"target_commit": commit, "passed": all(c["passed"] for c in cases), "cases": cases,
            "source_sha256": hashlib.sha256(source).hexdigest()}


def audit_execution_replan(root: Path, project: Path) -> dict:
    events = _events(project)
    change = json.loads((root / "evidence/protocol-change.json").read_text())
    result = check_execution_order(events, change)
    completion = next(e for e in events if e.id == result["completed_event"])
    candidate_id = completion.payload.get("candidate_event_id")
    assert candidate_id, "completion lacks exact candidate binding"
    candidates = [e for e in events if e.type == "candidate.ready"
                  and e.id == candidate_id
                  and (e.payload.get("workflow_run_id") or e.correlation_id) == change["workflow_run_id"]]
    assert len(candidates) == 1, "missing unique completed candidate"
    candidate = candidates[0].payload
    commit = str(candidate.get("candidate_commit") or candidate.get("target_commit") or candidate.get("target_ref") or "")
    assert commit, "candidate lacks immutable target"
    result["oracle"] = verify_protocol_product(project, commit)
    assert result["oracle"]["passed"], "independent protocol oracle failed"
    result["boundary"] = "real execution replan and frozen-product oracle; restart/cap matrix reported separately"
    return result
