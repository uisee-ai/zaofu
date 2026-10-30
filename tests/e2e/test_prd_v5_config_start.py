"""Versioned PRD v5 -> approved config baseline -> real runtime dispatch."""
from pathlib import Path
import hashlib
import json
from types import SimpleNamespace
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.core.state.session import SessionStore
from zf.runtime import workflow_runtime_start
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_preview_review import WorkflowPreviewReview
from zf.runtime.workflow_proposal import load_workflow_proposal
from zf.runtime.workflow_requests import load_workflow_request
from zf.web.server import create_app

pytestmark = [pytest.mark.host, pytest.mark.mock_e2e]
ROOT = Path(__file__).resolve().parents[2]


def test_v5_approved_config_starts_from_clean_delivery_baseline(monkeypatch, workflow_runtime_launcher):
    if not shutil.which("tmux"):
        pytest.skip("tmux required for actual runtime startup")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(tempfile.mkdtemp(prefix=f"zf-v5-start-{stamp}-"))
    state = root / ".test-state"
    try:
        source = ROOT / "zf-prd-v5.yaml"
        documents = list(yaml.safe_load_all(source.read_text()))
        flow, controller = documents
        assert flow["metadata"]["name"] == "zaofu-prd/v5-production"
        # The Codex executable is a local mock; preserve v5 on-demand and blocking policies.
        flow["spec"].update(prdRef="requirement.md", targetRoot=str(root))
        spec = controller["spec"]
        spec["profile_sources"] = [str(ROOT / path) for path in spec["profile_sources"]]
        spec["skill_sources"][0]["path"] = str(ROOT / "skills")
        spec["project"] = {"name": "v5-start-test", "state_dir": ".test-state"}
        spec["session"] = {"tmux_session": f"zf-v5-{root.name[-8:]}"}
        spec["runtime"]["run_manager"] = {"backend": "codex", "resident_agent": {"enabled": False}}
        spec["runtime"]["autoresearch_resident"] = {"enabled": False}
        spec["runtime"]["workdirs"] = {"enabled": False}
        config_path = root / "zf.yaml"
        config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
        config = load_config(config_path)
        assert config.version == "5.0"
        assert config.workflow.flow_metadata["task_pipeline"]["mode"] == "blocking"
        assert {role.backend for role in config.roles} <= {"codex", "claude-code"}
        (root / "requirement.md").write_text("实现 CSV 导出；验证表头和空列表输出；python -m unittest -q。\n")
        (root / ".gitignore").write_text(".test-state/\nartifacts/\ndocs/intake/\n")
        (root / "tmux").mkdir()
        state.mkdir()
        SessionStore(state / "session.yaml").create(project_root=str(root))
        git = lambda *args: subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
        git("init", "-q", "-b", "dev")
        git("add", "--", "zf.yaml", "requirement.md", ".gitignore")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test: isolated v5 baseline")
        binary_dir = root / "bin"
        binary_dir.mkdir()
        binary = binary_dir / "codex"
        binary.write_text("#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
                          f"Path({str(root / 'mock-provider-called')!r}).write_text('mock codex invoked')\n"
                          "if sys.argv[1:] == ['login', 'status']:\n print('Logged in using mock test credentials')\n"
                          "elif sys.argv[1:] == ['auth', 'status']:\n print('{\"loggedIn\":true}')\n"
                          "elif '--version' in sys.argv:\n print('codex-cli 0.0.0-test')\n"
                          "else:\n print('Mock Codex ready ›', flush=True)\n for line in sys.stdin: print('›', flush=True)\n")
        binary.chmod(0o755)
        claude = binary_dir / "claude"
        claude.write_bytes(binary.read_bytes())
        claude.chmod(0o755)
        monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
        monkeypatch.chdir(root)
        for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_TMUX_SESSION", "ZF_CLI_CMD"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TMUX_TMPDIR", str(root / "tmux"))
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "isolated-test-token")
        monkeypatch.setattr(workflow_runtime_start, "ensure_project_runtime", workflow_runtime_launcher.real)
        with TestClient(create_app(state, config=config, project_root=root)) as client:
            def action(name, payload):
                response = client.post(f"/api/actions/{name}", json=payload, headers={"X-ZF-Web-Token": "isolated-test-token", "X-Idempotency-Key": f"v5-start:{name}"})
                result = response.json()
                assert response.status_code < 300 and result.get("ok"), result
                return result
            prepared = action("workflow-request", {
                "kind": "prd", "objective": "实现已确认的 CSV 导出", "from": "requirement.md",
                "target_root": str(root), "backend": "codex", "allow_missing_env": True,
            })
            assert prepared["status"] == "proposal_ready", prepared
            request = load_workflow_request(state, prepared["request_id"])
            review = WorkflowPreviewReview(state, root, EventWriter(EventLog(state / "events.jsonl")))
            draft = review.discuss({
                "request_id": request["request_id"], "revision": request["revision"],
                "proposal_digest": request["proposal_digest"], "field": "config.0.roleDefaults.model",
                "mode": "revise", "message": "Use the test model for this approved workflow",
            }, agent=SimpleNamespace(run_turn=lambda **kwargs: SimpleNamespace(
                ok=True, incomplete=False, reply=json.dumps({"answer": "Use test model", "replacement": "v5-test-model"}),
            )))
            revised = review.apply(draft["draft_ref"])
            assert revised["ok"] and not revised["blockers"], revised
            prepared.update({key: revised[key] for key in ("request_id", "proposal_ref", "proposal_digest")})
            proposal = load_workflow_proposal(state, prepared["proposal_ref"])
            assert proposal["change_mode"] == "config_change", proposal
            snapshot = hydrate_sidecar_ref(state, proposal["effective_config_ref"]).payload
            assert snapshot["config"]["version"] == "5.0"
            assert snapshot["config"]["workflow"]["_flow_metadata"]["task_pipeline"]["mode"] == "blocking"
            binding = {key: prepared[key] for key in ("request_id", "proposal_ref", "proposal_digest")}
            applied = action("workflow-config-apply", {
                **binding, "proposal_id": proposal["proposal_id"], "validation_result_ref": proposal["validation_result_ref"],
                "approval_ref": "operator:v5-start-test", "establish_delivery_baseline": True,
            })
            assert applied["delivery_baseline_ref"]
            assert git("status", "--porcelain", "--untracked-files=no") == ""
            submitted = action("workflow-submit", {**binding, "intake_ref": prepared["intake_ref"], "kind": "prd", "allow_missing_env": True})
            assert submitted["runtime_start"]["status"] in {"running", "starting"}
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                events = EventLog(state / "events.jsonl").read_all()
                if any(e.type == "task.attempt.sent" for e in events):
                    break
                time.sleep(0.2)
            assert any(e.type == "workflow.invoke.accepted" for e in events)
            assert any(e.type == "task.attempt.sent" for e in events), [e.type for e in events[-30:]]
            assert sum(e.type == "workflow.invoke.requested" for e in events) == 1
            assert (root / "mock-provider-called").exists()
            assert hashlib.sha256(config_path.read_bytes()).hexdigest() == proposal["target_config"]["sha256"]
    finally:
        if state.exists():
            EventWriter(EventLog(state / "events.jsonl")).emit("simulation.done", actor="test", payload={"source": "prd-v5-config-start"})
            subprocess.run([sys.executable, "-m", "zf.cli.main", "stop", "--fast", "--include-run-manager"], cwd=root,
                           env=os.environ.copy(), capture_output=True, timeout=30)
            assert not workflow_runtime_start._runtime_owner(state), f"runtime cleanup failed: {root}"
        shutil.rmtree(root)
