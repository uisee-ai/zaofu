"""Isolated Channel relay through the real dispatcher, stores and Web projection.

Default is deterministic. --real explicitly invokes the installed Codex provider.
The caller owns the temporary root and any --serve process; never use port 8001.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import ChannelConfig, ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_prompt import fake_channel_reply_text
from zf.runtime.channel_sidecar import (
    channel_message_event_payload, hydrate_channel_context_pack_payload, hydrate_channel_message_text,
)
from zf.web.headless_agent import HeadlessTurnResult


ROLES = ("product_pm", "architect", "critic", "ui_ux", "backend")
REQUIRED = ("REQ-181-A", "REQ-181-B", "DISSENT-181")
CHANNEL = "ch-relay-181"


class EvidenceBackend:
    def available(self):
        return True

    def run_turn(self, **kwargs):
        for token in REQUIRED:
            assert token in kwargs["prompt"], f"source lost before provider: {token}"
        assert "30" in kwargs["prompt"] and "24" in kwargs["prompt"]
        sid = kwargs["provider_session_id"] or "mock-" + kwargs["thread_id"]
        kwargs["on_session_id"](sid)
        reply = "## 检查结果\n\nREQ-181-A：保留30天。\n\nREQ-181-B：24小时内撤销。\n\nDISSENT-181：离线时保留未决状态。\n"
        reply += fake_channel_reply_text({"member_id": "reviewer"}, {"text": "完整检查"})
        return HeadlessTurnResult(ok=True, status="completed", backend="codex-headless",
                                  thread_id=kwargs["thread_id"], provider_session_id=sid,
                                  reply=reply, messages=[], usage={})


def run(root: Path, *, real: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    config = ZfConfig(project=ProjectConfig(name="channel-181-e2e", state_dir="state"),
                      channel=ChannelConfig(provider_timeout_seconds=300))
    (root / "zf.yaml").write_text("version: '1.0'\nproject:\n  name: channel-181-e2e\n  state_dir: state\n")
    state = root / config.project.state_dir
    TaskStore(state / "kanban.json").ensure("E2E-181", "Channel relay verification")
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="test", payload={
        "channel_id": CHANNEL, "name": "181 多角色完整接力验收", "owner_actor_ref": "operator"})
    for role in ROLES:
        writer.emit("channel.member.invited", actor="test", payload={
            "channel_id": CHANNEL, "member_id": role, "member_type": "provider_agent",
            "backend": "codex", "provider": "codex", "channel_role": role,
            "permission_profile": "read_only",
            "permissions": ["read", "message"], "display_name": role,
        })
    writer.emit("channel.discussion.started", actor="test", payload={
        "channel_id": CHANNEL, "thread_id": "main", "product_mode": "multi_lens",
        "roster": list(ROLES), "synthesizer": "product_pm", "owner_actor_ref": "operator"})
    writer.emit("channel.discussion.phase.changed", actor="test", payload={
        "channel_id": CHANNEL, "thread_id": "main", "phase": "phase2_relay"})
    body = (
        "这是隔离的多角色接力验收。按当前角色分析以下要求，不调用工具、不提问、不写文件。\n"
        "请在 Markdown 正文逐项引用三个稳定 ID 并准确保留数量、时限与未决异议；不要只列 ID。"
        "每个角色都必须独立读原始来源，不只依赖上一人的摘要。合同 questions 为空、freeze=true。\n"
        "REQ-181-A：审计记录保留30天，之后按保留策略清理。\n"
        + "背景：不同专业角色应检查边界、失败场景与验收方式。\n" * 100
        + "REQ-181-B：用户撤销授权后，24小时内停止后续同步。\n"
        + "DISSENT-181：离线设备是否满足24小时要求尚未决定，必须保留为未决异议，不能擅自签收。\n"
    )
    writer.emit("channel.message.posted", actor="operator", payload=channel_message_event_payload(
        state, {"channel_id": CHANNEL, "thread_id": "main", "message_id": "requirement-181",
                "member_id": "operator", "role": "user", "text": body}, created_by="e2e"))
    source_message, sender = "requirement-181", "operator"
    evidence = []
    backend = None if real else {"codex-headless": EvidenceBackend()}
    for ordinal, role in enumerate(ROLES):
        result = request_channel_handoff(
            state_dir=state, writer=writer, channel_id=CHANNEL, thread_id="main",
            message_id=source_message, member_id=sender, target_member_id=role,
            reason="按角色完整核验来源并保留所有未决异议", actor="operator", source="e2e",
            project_root=root, config=config, headless_backends=backend, round_no=ordinal,
        )
        assert result.reply_requests, result
        channel = project_channel(state, CHANNEL)
        request = next(r for r in channel["reply_requests"]
                       if r["target_member_id"] == role and r["message_id"] == source_message)
        assert request["status"] == "completed", request
        source_message = f"msg-{request['request_id']}-reply"
        reply = next(m for m in channel["messages"] if m["message_id"] == source_message)
        text = hydrate_channel_message_text(state, reply, strict=True)
        human = text.split("<!-- zf:channel-contract -->", 1)[0]
        for token in (*REQUIRED, "30", "24"):
            assert token in human, f"{role} omitted {token} from visible answer"
        packs = [e.payload for e in writer.event_log.read_all() if e.type == "channel.context_pack.built"
                 and e.payload["context_pack_id"] == request["context_pack_id"]]
        pack = hydrate_channel_context_pack_payload(state, packs[-1], strict=True)
        documents = pack["semantic_source_documents"]
        assert all(token in str(documents) for token in REQUIRED)
        assert len([d for d in documents if d.get("message_id")]) == ordinal + 1
        evidence.append({"role": role, "request_id": request["request_id"],
                         "provider_session_id": reply["refs"].get("provider_session_id"),
                         "body_sha256": reply["body_sha256"], "visible_chars": len(human),
                         "source_count": len(documents), "covered": list(REQUIRED)})
        sender = role
        print(json.dumps(evidence[-1], ensure_ascii=False), flush=True)
    from fastapi.testclient import TestClient
    from zf.web.server import create_app
    from zf.web.projections.common import _default_project_id

    project = _default_project_id(config=config, project_root=root)
    app = create_app(state, config=config, project_root=root)
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project}/channels/{CHANNEL}/conversation?limit=100")
        assert response.status_code == 200, response.text
        api_text = response.text
        assert all(token in api_text for token in REQUIRED)
        assert "zf:channel-contract" not in api_text
    report = {"mode": "real_provider" if real else "mock", "project_id": project,
              "channel_id": CHANNEL, "roles": evidence}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return SimpleNamespace(app=app, writer=writer, report=report)


def compare_cli(root: Path) -> None:
    """Compare one fresh CLI turn using the same frozen PM input and policy."""
    import subprocess
    from zf.runtime.channel_provider_prompt import build_channel_provider_prompt, build_channel_system_prompt
    from zf.runtime.provider_permissions import codex_security_config_for_profile

    state = root / "state"
    channel = project_channel(state, CHANNEL)
    member = next(m for m in channel["members"] if m["member_id"] == ROLES[0])
    request = next(r for r in channel["reply_requests"] if r["target_member_id"] == ROLES[0])
    message = next(m for m in channel["messages"] if m["message_id"] == request["message_id"])
    message = {**message, "text": hydrate_channel_message_text(state, message, strict=True)}
    prompt = build_channel_provider_prompt(channel=channel, member=member, message=message,
                                           request=request, state_dir=state)
    system = build_channel_system_prompt(member, channel=channel)
    policy = codex_security_config_for_profile("read_only")
    destination = root / "cli-pm-output.md"
    command = ["codex", "exec", "--skip-git-repo-check", "--json", "-C", str(root),
               "--sandbox", policy["sandbox"], "-c", "approval_policy=" + json.dumps(policy["approvalPolicy"]),
               "-c", "developer_instructions=" + json.dumps(system, ensure_ascii=False),
               "--output-last-message", str(destination), "-"]
    with (root / "cli-execution.jsonl").open("w") as log:
        subprocess.run(command, input=prompt, text=True, stdout=log, stderr=subprocess.STDOUT,
                       timeout=300, check=True)
    body = destination.read_text().split("<!-- zf:channel-contract -->", 1)[0]
    for token in (*REQUIRED, "30", "24"):
        assert token in body, f"CLI baseline omitted {token}"
    baseline = {"cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
                "model_and_effort": "inherited from the same local Codex configuration",
                "sandbox": policy["sandbox"], "visible_chars": len(body),
                "covered": list(REQUIRED), "input": "same frozen PM source pack and response contract"}
    (root / "cli-comparison.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
    print(json.dumps(baseline, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--serve", type=int)
    parser.add_argument("--serve-only", action="store_true", help="Replay an existing report without invoking providers")
    parser.add_argument("--cli-baseline", action="store_true", help="Explicit real Codex CLI comparison")
    args = parser.parse_args()
    assert args.root.resolve().is_relative_to(Path("/tmp")) and args.root.name.startswith("zf-"), "isolated /tmp/zf-* root required"
    assert args.serve != 8001, "8001 belongs to the real dev session"
    if args.serve_only:
        assert args.serve and (args.root / "report.json").is_file()
        from zf.web.server import create_app
        state = args.root / "state"
        host = SimpleNamespace(app=create_app(state, project_root=args.root),
                               writer=EventWriter(EventLog(state / "events.jsonl")))
    else:
        host = run(args.root, real=args.real)
    finished = False
    def finish():
        global finished
        if not finished:
            host.writer.emit("simulation.done", actor="e2e", payload={"purpose": "channel-181-relay"})
            finished = True
    try:
        if args.cli_baseline:
            assert args.real or args.serve_only
            compare_cli(args.root)
        if args.serve:
            import uvicorn
            class EvidenceServer(uvicorn.Server):
                def handle_exit(self, sig, frame):
                    # Uvicorn re-raises termination signals before outer finally blocks.
                    finish()
                    super().handle_exit(sig, frame)
            EvidenceServer(uvicorn.Config(host.app, host="0.0.0.0", port=args.serve,
                log_level="warning", timeout_graceful_shutdown=3)).run()
    finally:
        finish()
