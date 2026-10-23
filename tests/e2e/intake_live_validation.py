"""Explicit real-provider intake validation; evidence is never a mock verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import yaml

from fastapi.testclient import TestClient
from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.workspace.project_initializer import ProjectInitializer
from zf.web.server import create_app


FIXTURES = Path(__file__).parent / "fixtures/intake-live"


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root: Path, harness: Path, evidence: Path) -> None:
    """Freeze isolated products before calling any model for these tasks."""
    for kind in ("issue", "prd"):
        project = root / kind
        project.mkdir()
        for source in (FIXTURES / kind).iterdir():
            if source.is_file():
                shutil.copy2(source, project / source.name)
        (project / "README.md").write_text(f"# 184 {kind} validation\n\n需求见 requirement.md。\n")
        (project / ".gitignore").write_text(".zf-validation/\n__pycache__/\n.zf/\nzf.yaml\nartifacts/\ndocs/intake/\n")
        (project / "AGENTS.md").write_text(
            "# 产品边界\n按 requirement.md 完整交付。不得修改 requirement.md、zf.yaml、"
            "AGENTS.md 或仓库外评估器；不要读取外部 oracle。运行状态只走当前 zf CLI，"
            "不得直接写 events/kanban。不要使用 8001 或操作其他会话。验证结论必须有实际证据。\n"
        )
        docs = list(yaml.safe_load_all((harness / f"examples/prod/controller/{kind}-fanout-v3.yaml").read_text()))
        flow, config = docs[0]["spec"], docs[1]["spec"]
        flow.update(lanes=1, backend="codex")
        flow["roleDefaults"] = {"model": "gpt-6-astra", "model_reasoning_effort": "high", "transport": "tmux"}
        if kind == "issue":
            flow.update(issueRef="requirement.md")
        else:
            flow.update(prdRef="requirement.md", targetRoot=".")
        config["profile_sources"] = [str(harness / "examples/prod/controller/common/profiles.yaml")]
        config["project"] = {"name": f"184-live-{kind}", "state_dir": ".zf-validation"}
        config["session"] = {"tmux_session": f"zf-184-live-{root.name[-7:]}-{kind}"}
        config["skill_sources"] = [{"name": "zaofu-skills", "path": str(harness / "skills"), "mode": "readonly"}]
        config["runtime"] = {
            "run_manager": {"resident_agent": {"enabled": False}},
            "autoresearch_resident": {"enabled": False},
            "feishu_inbound": {"enabled": False},
            "git": {"candidate_base_ref": "main", "ship_target_branch": "main"},
        }
        for role in config.get("roles", []):
            role.update(model="gpt-6-astra", model_reasoning_effort="high", transport="tmux")
        workflow = config.setdefault("workflow", {})
        workflow["plan_approval"] = kind == "prd"
        workflow["orchestration"] = {"mode": "exception_advisor"}
        workflow["run_limits"] = {"timeout_seconds": 1800, "token_budget": 3000000, "cost_budget_usd": 30}
        workflow["execution_profiles"] = {"bounded-direct-v1": {
            "strategy": "direct", "limits": {"timeout_seconds": 600, "token_budget": 1000000,
                                                "max_usage_samples": 60, "cost_budget_usd": 10}}}
        config["channel"] = {"agent_profiles": {"product-pm": {
            "revision": 1, "persona": "Product requirement facilitator", "display_name": "Product PM",
            "channel_role": "product_pm", "provider": "codex", "backend": "codex",
            "visibility_ceiling": "planner", "permission_ceiling": "read_only", "lifecycle": "persistent"}}}
        (project / "zf.yaml").write_text(yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True))
        loaded = load_config(project / "zf.yaml")
        for args in (["init", "-q", "-b", "main"], ["config", "user.name", "184 Live Validation"],
                     ["config", "user.email", "184-live@localhost"]):
            subprocess.run(["git", *args], cwd=project, check=True)
        paths = ["requirement.md", "README.md", "AGENTS.md", ".gitignore"]
        if kind == "issue":
            paths += ["counter.py", "test_counter.py"]
        subprocess.run(["git", "add", "--", *paths], cwd=project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "test: freeze isolated product requirements", "--", *paths], cwd=project, check=True)
        ProjectInitializer().initialize(cwd=project, with_instruction_docs=False, with_git_hooks=False, workspace_register=False)
        from zf.runtime.workflow_route_catalog import workflow_route_catalog

        catalog = workflow_route_catalog(loaded)
        if not any(row["kind"] == kind for row in catalog["routes"]):
            raise RuntimeError(f"missing executable {kind} route before provider call")
        from zf.core.config.render import build_config_inspection_report

        preflight = build_config_inspection_report(
            loaded, config_path=project / "zf.yaml", project_root=project,
            state_dir=project / loaded.project.state_dir,
        )
        save(evidence / kind / "preflight.json", preflight)
        if preflight["status"] == "STOP":
            raise RuntimeError(f"{kind} preflight blocked before provider call")
        save(evidence / kind / "freeze.json", {
            "config_sha256": digest(project / "zf.yaml"), "files": {p: digest(project / p) for p in paths},
            "seed_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip(),
            "catalog": catalog, "model": "gpt-6-astra", "effort": "high",
        })


class EntryRun:
    def __init__(self, project: Path, evidence: Path):
        self.project, self.evidence = project, evidence
        self.config = load_config(project / "zf.yaml")
        self.state = project / self.config.project.state_dir
        self.log = event_log_from_project(self.state, config=self.config)
        self.sequence = 0
        os.environ.setdefault("ZF_WEB_ACTION_TOKEN", "184-isolated-validation-token")
        self.client = TestClient(create_app(self.state, config=self.config, project_root=project))

    def post(self, action: str, payload: dict, *, expect_ok: bool = True) -> dict:
        self.sequence += 1
        response = self.client.post(f"/api/actions/{action}", json=payload,
                                    headers={"x-zf-web-token": os.environ["ZF_WEB_ACTION_TOKEN"]})
        data = response.json()
        save(self.evidence / f"{self.sequence:02d}-{action}.json", {
            "request": payload, "http_status": response.status_code, "response": data})
        if expect_ok and (response.status_code >= 400 or data.get("ok") is False):
            raise RuntimeError(f"{action}: {response.status_code}: {data}")
        return data

    def chat(self, message: str, **context) -> dict:
        return self.post("chat-orchestrator", {
            "backend": "codex-headless", "permission_profile": "read_only", "sync": True,
            "project_id": self.project.name, "conversation_id": f"kanban:{self.project.name}",
            "thread_key": "validation", "message": message, **context,
        })["reply"]

    def propose_selected(self, plan: dict, action: str) -> dict:
        if not plan.get("valid"):
            raise RuntimeError(f"invalid Plan: {plan.get('validation_error')}")
        options = [o for o in plan["options"] if o.get("submit_action") == action]
        option = next((o for o in options if o.get("recommended")), options[0] if options else None)
        if option is None:
            raise RuntimeError(f"Plan has no {action} option")
        result = self.post("kanban-plan-apply", {"plan_response": {
            "request_event_id": plan["request_event_id"], "request_id": plan["request_id"],
            "revision": plan["revision"], "question_id": plan["question_id"], "option_id": option["id"],
        }})
        event_id = result["proposal_event_id"]
        return next(e.payload["proposal"] for e in self.log.read_all() if e.id == event_id)

    def approve(self, proposal: dict, action: str) -> dict:
        if proposal.get("action") != action or not proposal.get("valid"):
            raise RuntimeError("unexpected or invalid action proposal")
        return self.post(action, {**proposal["payload"], "proposal_event_id": proposal["proposal_event_id"]})

    def start_task(self, reply: dict) -> dict:
        proposal = reply.get("action_proposal")
        if not proposal:
            proposal = self.propose_selected(reply["plan_request"], "create-task")
        assert not any(e.type == "task.created" for e in self.log.read_all())
        created = self.approve(proposal, "create-task")
        assert not any(e.type == "workflow.invoke.requested" for e in self.log.read_all())
        task_id = created["task_id"]
        plan_event = next((e for e in self.log.read_all() if e.id == created.get("workflow_plan_event_id")), None)
        if plan_event:
            plan = plan_event.payload.get("plan_request") or plan_event.payload["request"]
        else:
            plan = self.chat("为这个已创建 Task 推荐已注册 Workflow 路线，提供路线 Plan；不要创建新 Task 或启动。", task_id=task_id)["plan_request"]
        return self.start_existing(task_id, plan)

    def start_existing(self, task_id: str, plan: dict | None = None) -> dict:
        if plan is None:
            reply = self.chat("为当前 Task 选择现有可用路线并生成 task_workflow Plan，目标与验收保持 Task 合同完整。路线目录已更新，请读取本轮实际上下文；不创建新 Task、不直接启动。", task_id=task_id)
            plan = reply.get("plan_request")
        if not plan:
            raise RuntimeError("Provider did not return a task_workflow Plan")
        workflow = self.propose_selected(plan, "workflow-start")
        assert not any(e.type == "workflow.invoke.requested" for e in self.log.read_all())
        save(self.evidence / "workflow-preview.json", workflow)
        started = self.approve(workflow, "workflow-start")
        replay = self.approve(workflow, "workflow-start")
        assert sum(e.type == "workflow.invoke.requested" for e in self.log.read_all()) == 1
        save(self.evidence / "entry-result.json", {"task_id": task_id, "start": started, "replay": replay})
        return started

    def channel(self, channel_id: str = "ch-184-ledger") -> dict:
        from zf.runtime.channel_projection import project_channel

        channel = project_channel(self.state, channel_id)
        if not channel:
            self.post("channel-create", {"channel_id": channel_id, "name": "Ledger delivery"})
            self.post("channel-invite-member", {
                "channel_id": channel_id, "member_id": "product-pm", "profile_id": "product-pm",
                "member_type": "provider_agent", "permission_profile": "read_only",
                "permissions": ["read", "message", "summarize", "propose_workflow"],
            })
        channel = project_channel(self.state, channel_id) or {}
        if channel.get("leader_member_id") != "product-pm":
            self.post("channel-set-leader", {"channel_id": channel_id, "leader_member_id": "product-pm",
                      "expected_revision": channel.get("leader_revision", 0),
                      "idempotency_key": f"{channel_id}-leader-r1"})
        self.post("channel-post-message", {
            "channel_id": channel_id, "thread_id": "main", "member_id": "operator", "role": "user",
            "client_message_id": "184-ledger-source", "text": "交付需求已明确，请阅读[完整需求](requirement.md)。稍后将基于该文件生成正式 PRD，不要实现或启动工作流。",
        })
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            channel = project_channel(self.state, channel_id) or {}
            active = [r for r in channel.get("reply_requests", []) if r.get("status") in {"requested", "pending", "queued", "running", "started"}]
            if not active:
                break
            time.sleep(1)
        requested = self.post("channel-synthesis-request", {
            "channel_id": channel_id, "thread_id": "main", "target_member_id": "product-pm",
            "source_file": {"ref": "requirement.md", "sha256": digest(self.project / "requirement.md")},
            "reason": "将选中的完整需求整理为可执行 PRD，保留全部边界、返回值和错误行为。范围已明确，没有待定业务决定。满足注入的质量契约并给出可执行验收；不要实现、建单或启动。",
        })
        save(self.evidence / "synthesis-request.json", requested)
        reply_event_ids = set(requested.get("route", {}).get("reply_requests", []))
        reply_request_ids = {e.payload.get("request_id") for e in self.log.read_all()
                             if e.id in reply_event_ids}
        wait_seconds = max(360, self.config.channel.provider_timeout_seconds + 60)
        deadline = time.monotonic() + wait_seconds
        consensus = None
        while time.monotonic() < deadline:
            channel = project_channel(self.state, channel_id) or {}
            consensus = (channel.get("consensus") or {}).get("main")
            if consensus and consensus.get("artifact_ref"):
                break
            failed = [e for e in self.log.read_all() if e.type == "channel.agent.reply.failed"
                      and e.payload.get("request_id") in reply_request_ids]
            if failed:
                raise RuntimeError(f"Channel synthesis failed: {failed[-1].payload.get('reason')}")
            time.sleep(1)
        if not consensus:
            raise RuntimeError(f"Channel did not produce a canonical PRD within {wait_seconds} seconds")
        save(self.evidence / "prd-before-owner-review.json", consensus)
        # Stop for the validation operator to inspect the actual PRD; do not auto-confirm model content.
        return consensus

    def evaluate_prd(self, channel_id: str, *, timeout: float = 1900, retry: bool = False) -> dict:
        """Require a version-bound independent review before operator handoff."""
        from zf.runtime.channel_projection import project_channel

        channel = project_channel(self.state, channel_id) or {}
        consensus = (channel.get("consensus") or {}).get("main") or {}
        requested = self.post("channel-prd-evaluate", {
            "channel_id": channel_id, "thread_id": "main", "role": "full",
            **{key: consensus[key] for key in
               ("prd_revision", "artifact_ref", "artifact_digest")},
            **({"retry": True} if retry else {}),
        })
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            terminal = next((event for event in reversed(self.log.read_all())
                             if event.type in {"output.evaluation.completed", "output.evaluation.failed"}
                             and event.payload.get("request_id") == requested["request_id"]), None)
            if terminal:
                result = {"event_id": terminal.id, "event_type": terminal.type,
                          **terminal.payload}
                save(self.evidence / "independent-prd-evaluation.json", result)
                if terminal.type != "output.evaluation.completed" or result.get("verdict") != "PASS":
                    raise RuntimeError("independent PRD evaluation did not pass; inspect retained report")
                report = self.state / result["report_ref"]
                if digest(report) != result["report_digest"].removeprefix("sha256:"):
                    raise RuntimeError("independent PRD report digest mismatch")
                current = project_channel(self.state, channel_id) or {}
                current = (current.get("consensus") or {}).get("main") or {}
                if any(current.get(key) != consensus.get(key) for key in
                       ("prd_revision", "artifact_ref", "artifact_digest")):
                    raise RuntimeError("PRD changed during evaluation; review current version")
                # A passing advisory review is not Owner confirmation or execution authorization.
                return result
            time.sleep(1)
        raise RuntimeError("independent PRD evaluation timed out; ensure zf start is running")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "kanban", "channel", "evaluate-prd", "resume-route"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--task")
    parser.add_argument("--channel-id", default="ch-184-ledger")
    parser.add_argument("--retry-evaluation", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.root, args.harness, args.evidence)
        return 0
    if not args.confirm_real:
        parser.error("--confirm-real is required")
    kind = "issue" if args.phase in {"kanban", "resume-route"} else "prd"
    run = EntryRun(args.root / kind, args.evidence / kind / f"entry-round{args.attempt}")
    try:
        with run.client:
            if args.phase == "channel":
                run.channel(args.channel_id)
            elif args.phase == "evaluate-prd":
                run.evaluate_prd(args.channel_id, retry=args.retry_evaluation)
            elif args.phase == "resume-route":
                if not args.task:
                    parser.error("--task is required")
                run.start_existing(args.task)
            else:
                reply = run.chat("创建一个任务修复 requirement.md 所述计数器步长缺陷，按全文验收并补回归测试。不要重构。先给出 Task 提案和可用 Workflow 路线，等待批准。")
                run.start_task(reply)
        return 0
    except Exception as exc:
        save(run.evidence / "failure.json", {"error": str(exc), "time": time.time()})
        raise


if __name__ == "__main__":
    sys.exit(main())
