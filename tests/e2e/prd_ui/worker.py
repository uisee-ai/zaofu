"""Scripted planning worker: consumes dispatched operations and submits real contracts.

This process replaces model reasoning only. It never emits terminal events, creates
Tasks, or edits projections. ResultSubmit and the actual runtime own admission.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.result_submit import SemanticResultSubmitService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def candidate(project: Path, request: dict, operation: str) -> dict:
    from tests.test_plan_candidate_preflight import _task_map, _source_index, _plan_ports, _product_acceptance_spec
    task_map = _task_map()
    task = task_map["tasks"][0]
    task["title"] = "保持 README 可读并验证缺失文件错误"
    task["allowed_paths"] = ["README.md", "tests/test_readme.py"]
    task["allowed_paths_reason"] = "Own the README contract and its negative regression test."
    task_map["goal_claims"][0]["text"] = "README.md remains readable and missing files fail explicitly."
    task["acceptance_criteria"][0]["statement"] = task_map["goal_claims"][0]["text"]
    command = "python -m unittest discover -s tests -p test_readme.py"
    task["validation"]["commands"][0]["command"] = command
    task["validation"]["commands"].append({"id": "readme-smoke", "command": "test -r README.md", "rolling_smoke": True, "owner": "task_verify", "tier": "runtime", "deterministic": True, "reusable": True, "timeout_seconds": 30})
    task["verification"] = command
    source = (request.get("plan_candidate_validation") or {}).get("manifest", {}).get("trigger_payload", {})
    source_ref = source.get("prd_ref") or source.get("requirement_spec_ref") or "docs/prd/ui-requirement.md"
    task["source_ref"] = source_ref
    task["source_key"] = "requirement:claim-1"
    index = _source_index()
    index["tasks"][0].update(source_ref=source_ref, source_excerpt=task_map["goal_claims"][0]["text"])
    ports = _plan_ports()
    ports[2]["body"]["commands"][0]["command"] = command
    ports[2]["body"]["commands"].append(dict(task["validation"]["commands"][-1]))
    acceptance = _product_acceptance_spec()
    acceptance["entrypoints"][0].update(start_command="cat README.md", health_check="test -r README.md")
    acceptance["user_journeys"][0].update(title="Read README and receive an explicit error when absent")
    acceptance["user_journeys"][0]["assertions"][0]["statement"] = task_map["goal_claims"][0]["text"]
    ports.append({"logical_name": "product_acceptance_spec", "body": acceptance})
    plan = f"""# README 合同完整实施计划

## PRD
用户：项目操作者。目标：README.md 保持可读；缺失时明确报告错误。
范围：文件存在、读取与缺失分支。非目标：其他业务功能。本轮只生成计划。
来源：{source_ref}。验收 AC-1；规划后须 Owner 审批。

## SDD
文件系统为输入边界，README.md 为产品产物，测试读取文件并检查错误返回。
正常路径返回文件内容；不存在路径返回非零退出码与明确错误。无需数据库或网络依赖。
实现 owner=TASK-1；测试 owner=task_verify。测试使用临时目录，避免破坏真实文件。

## TDD
AC-1 → test-command → `{command}`。
测试场景包括文件存在、可读和缺失。预期：存在时读取成功，缺失时错误明确。
验证层级 runtime；owner task_verify；证据 artifacts/readme-tests.json；当前 NOT_RUN。
命令及产品测试在后续实施任务中创建，规划阶段不声称已经执行通过。

## Task Spec: TASK-1

### 保持 README 可读
操作者从项目根目录运行 `cat README.md`，正常返回完整文件内容；缺失文件时返回非零
退出码和明确错误。测试在临时目录创建存在/缺失前态，结束后清理临时目录，不写真实数据。
先验证缺失分支的行为 RED，再实现并以同一断言取得 GREEN；本片负责完整 README 行为，
独立 task_verify 按 AC-1/test-command 复核，规划时证据为 NOT_RUN。

### 任务与依赖
| Task | 范围 | 依赖 | Wave | 验收 | Verify |
|---|---|---|---|---|---|
| TASK-1 | README.md、tests/test_readme.py | 无 | 1 | AC-1 | task_verify/test-command |
单一有界功能，无需虚构并行任务。任务完成后再运行验证。

## Decision Register
D-1：Owner 已确认缺失时报错；D-2：生成完整 Plan 后等待审批。无未决决定。

## 需求与验收映射
读取能力 CAP-1 → AC-1 → TASK-1 → test-command。见 capability_matrix、acceptance_matrix、test_matrix。

## 风险与审批
风险：错误被忽略造成误判。用独立临时目录的缺失文件测试证明错误路径。
检查来源、范围、验收、命令和证据后由 Owner 决定；审批前 impl/verify 不得派发。
"""
    state = project / load_config(project / "zf.yaml").project.state_dir
    directory = state / "artifacts/ui-planning" / operation
    directory.mkdir(parents=True, exist_ok=True)
    refs = {}
    for filename, content in (("plan.md", plan), ("task_map.json", json.dumps(task_map)), ("source-index.json", json.dumps(index))):
        path = directory / filename
        path.write_text(content)
        refs[filename] = str(path) if filename == "source-index.json" else path.relative_to(state).as_posix()
    return {"status": "passed", "execution_status": "completed", "result_semantics": "artifact_production",
            "summary": "Scripted provider produced the README Plan; product verification is NOT_RUN.",
            "findings": [], "plan_md": plan, "plan_artifact_ref": refs["plan.md"],
            "plan_artifact_digest": hashlib.sha256(plan.encode()).hexdigest(),
            "plan_content_profile": "derived_plan_full", "task_map": task_map, "task_map_ref": refs["task_map.json"],
            "source_index": index, "source_index_ref": refs["source-index.json"], "plan_ports": ports}


def serve(project: Path) -> None:
    state = project / load_config(project / "zf.yaml").project.state_dir
    log = EventLog(state / "events.jsonl")
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    attempted = set()
    while True:
        events = log.read_all()
        if any(e.type == "simulation.done" for e in events):
            return
        sent = {e.payload.get("operation_id") for e in events if e.type == "task.attempt.sent"}
        for operation_id, op in reduce_workflow_operations(events).items():
            if op["status"] != "running" or operation_id not in sent or operation_id in attempted:
                continue
            request = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
            profile = request.get("output_profile_id")
            if profile not in {"workflow-read", "plan-synth"}:
                continue
            attempted.add(operation_id)
            try:
                role = op["role_instance"]
                token = (state / "private/result-submit/roles" / f"{role}.token").read_text().strip()
                manifest_paths = list((state / "artifacts/attempts" / op["active_attempt_id"] / "source-manifests").glob("*.json"))
                manifests = [json.loads(path.read_text()) for path in manifest_paths]
                manifest = next(m for m in manifests if m.get("schema_version") == "attempt-source-manifest.v1")
                for required in request.get("required_reads", []):
                    read_attempt_artifact(state, manifest=manifest, source_id=required["source_id"],
                                          artifact_id=required["artifact_id"], json_path=required["json_path"],
                                          max_items=0, max_chars=0)
                if profile == "plan-synth":
                    semantic = {"status": "completed", "execution_status": "completed", "recommendation": "approve",
                                "plan_content_profile": "derived_plan",
                                "summary": "Scripted critic reviewed the bound candidate and requirement.",
                                "report": {"status": "passed", "recommendation": "approve", "findings": [],
                                           "summary": "Source, README behavior, negative test, ownership and approval boundary are covered."}}
                elif request.get("stage_id") == "prd-plan":
                    semantic = candidate(project, request, operation_id)
                else:
                    semantic = {"status": "passed", "execution_status": "completed", "result_semantics": "artifact_production",
                                "summary": "README.md is the current repository entry; no product implementation exists.",
                                "findings": [], "artifact_refs": []}
                result = service.submit(operation_id=operation_id, role_instance=role, credential=token, semantic_result=semantic)
                print(json.dumps({"operation_id": operation_id, "role": role, "event": result.canonical_event_type}), flush=True)
            except Exception as exc:
                print(json.dumps({"operation_id": operation_id, "error": f"{type(exc).__name__}: {exc}", "details": getattr(exc, "details", {})}), flush=True)
        time.sleep(0.3)

if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve())
