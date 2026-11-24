import json
from types import SimpleNamespace

import pytest

from tests.test_candidate_task_repair_decision import incident, submit
from zf.cli.main import main
from zf.core.events import ZfEvent
from zf.runtime.candidate_repair_retry import retry_rejected_candidate_repair


def rejected_candidate(tmp_path, monkeypatch):
    runtime, request, repair = incident(tmp_path, monkeypatch, canonical=True, peer=False)
    from zf.runtime import candidate_task_repair_decision
    with monkeypatch.context() as m:
        m.setattr(candidate_task_repair_decision, "_prior_product_failures", lambda *args: {"plan-1", "plan-2"})
        event, outcome = submit(runtime, request, repair)
    assert outcome["reason"] == "candidate product rework cap exhausted"
    return runtime, event


def test_candidate_repair_cli_preview_apply_and_replay(tmp_path, monkeypatch, capsys):
    runtime, event = rejected_candidate(tmp_path, monkeypatch)
    from zf.cli import recover_oa_review

    (runtime.project_root / "zf.yaml").write_text("project:\n  state_dir: state\n")
    context = SimpleNamespace(config=runtime.config, project_root=runtime.project_root,
                              config_path=runtime.project_root / "zf.yaml", state_dir=runtime.state_dir)
    monkeypatch.setattr(recover_oa_review, "resolve_project_context", lambda **kw: context)
    monkeypatch.setattr(recover_oa_review, "event_log_from_project", lambda *a, **kw: runtime.event_log)
    cmd = ["recover", "candidate-repair", "--decision", event.id]
    before = len(runtime.event_log.read_all())
    assert main(cmd) == 0, capsys.readouterr().out
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready" and result["tasks"] == ["TASK-2-FIX"]
    assert len(runtime.event_log.read_all()) == before
    assert main(cmd + ["--apply"]) == 1
    capsys.readouterr()
    with_ref = cmd + ["--apply", "--approval-ref", "owner:case"]
    from zf.runtime import run_contract
    def unreadable(*args, **kwargs):
        raise OSError("run contract unavailable")
    with monkeypatch.context() as m:
        m.setattr(run_contract, "evaluate_run_contract_resume_policy", unreadable)
        assert main(with_ref) == 1
        assert "run contract drift blocks recovery" in capsys.readouterr().out
    assert main(with_ref) == 0, capsys.readouterr().out
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    actions = {e.type for e in runtime.event_log.read_all() if e.id in applied["event_ids"]}
    assert {"task_map.amended", "task_map.ready"} <= actions
    assert main(with_ref) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_applied"
    ready = [e for e in runtime.event_log.read_all() if e.type == "task_map.ready" and e.payload.get("rework_of")]
    assert len(ready) == 1
    assert runtime.task_store.get("TASK-2").status == "done"


def test_candidate_repair_retry_retains_true_cap_and_terminal_guard(tmp_path, monkeypatch):
    runtime, event = rejected_candidate(tmp_path, monkeypatch)
    from zf.runtime import candidate_task_repair_decision
    with monkeypatch.context() as m:
        m.setattr(candidate_task_repair_decision, "_prior_product_failures", lambda *args: {"product-1", "product-2"})
        with pytest.raises(ValueError, match="cap exhausted"):
            retry_rejected_candidate_repair(runtime, event.id)
    runtime.event_writer.append(ZfEvent(type="run.cancelled", origin="kernel", actor="zf-cli",
        correlation_id=event.correlation_id, payload={"workflow_run_id": event.payload["workflow_run_id"]}))
    with pytest.raises(ValueError, match="terminal|superseded|current|obsolete"):
        retry_rejected_candidate_repair(runtime, event.id, apply=True, approval_ref="owner:case")
