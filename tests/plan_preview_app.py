"""Isolated real API fixture for Docker Playwright Plan review tests.

Run: uvicorn tests.plan_preview_app:create_test_app --factory --host 0.0.0.0 --port 8003
The data is explicitly synthetic; no provider, product run, or approval occurs.
"""
import atexit
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.web.server import create_app
from tests.test_plan_artifact_package import _fixture_port, _run_contract


def create_test_app():
    directory = TemporaryDirectory(prefix=f"zf-plan-preview-e2e-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-")
    atexit.register(directory.cleanup)
    root = Path(directory.name)
    state = root / "review-state"
    config_path = root / "zf.yaml"
    config_path.write_text(
        "apiVersion: zaofu.dev/v1\nkind: ZfConfig\nmetadata: {name: preview-fixture}\n"
        "spec:\n  version: '1.0'\n"
        "  project: {name: preview-fixture, state_dir: review-state}\n"
    )
    (state / "artifacts").mkdir(parents=True)
    (state / "kanban.json").write_text("[]")
    (state / "feature_list.json").write_text("[]")
    full = "# 完整规划示例\n\n## 架构\nAPI、业务、数据库的验证责任。\n\n"
    full += "\n".join(f"业务条款 {i}：输入、拒绝及状态观察。\n" for i in range(250))
    full += "\n## 未决事项\nQ15 保持待确认。\n\nEND-OF-FULL-PLAN\n"
    (state / "artifacts/full-plan.md").write_text(full)
    (state / "artifacts/digest.md").write_text("# 计划摘要\n机械检查摘要。\n")
    (state / "artifacts/task-map.json").write_text(json.dumps({"tasks": [{"task_id": "T1", "title": "读取闭环"}]}))
    port = _fixture_port(state, "acceptance_matrix", "END-OF-MATRIX")
    package = build_plan_artifact_package(
        workflow_run_id="preview-fixture", flow_kind="prd", producer_stage_id="plan",
        run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1",
        produced=[port], required_ports=["acceptance_matrix"],
    )
    descriptor = write_plan_artifact_package(state, package)
    writer = EventWriter(EventLog(state / "events.jsonl"))
    atexit.register(writer.append, ZfEvent(
        type="simulation.done", actor="fixture", payload={"purpose": "plan-preview-e2e"},
    ))
    for plan_id, plan_ref in [("evt-preview-complete", "artifacts/full-plan.md"), ("evt-preview-missing", "artifacts/missing.md"), ("evt-preview-digest", "")]:
        writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
            "plan_id": plan_id, "task_count": 1, "task_map_ref": "artifacts/task-map.json",
            "plan_artifact_ref": plan_ref, "plan_artifact_digest": hashlib.sha256(full.encode()).hexdigest(),
            "digest_ref": "artifacts/digest.md", "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"],
        }))
    return create_app(state, config=load_config(config_path), project_root=root)
