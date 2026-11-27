"""Render a scoped inbox entry point without loading messages into the briefing."""

from pathlib import Path
from shlex import quote

from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.cli_command import zf_cli_cmd


def _inbox_available(config, state_dir):
    if state_dir is None:
        return False
    policy = getattr(getattr(config, "runtime", None), "durable_mailbox", None)
    # Retained bodies are a discovery hint when draining a disabled pilot.
    # The CLI checks accepted ledger facts and current authority on every read.
    return (getattr(policy, "enabled", False) is True
            or (Path(state_dir) / "artifacts" / "durable-mailbox").is_dir())


def mailbox_hint(config, state_dir, attempt_id):
    return _inbox_command(state_dir, attempt_id) if attempt_id and _inbox_available(config, state_dir) else ""


def run_board_hint(config, state_dir, payload):
    """Advertise peer handoff on opted-in PRD dispatches. The CLI authorizes each access."""
    attempt = payload.get("attempt_id") or payload.get("run_id")
    policy = flow_metadata_for(config, payload=payload).get("run_board_policy") or {}
    if not attempt or not payload.get("workflow_run_id") or not policy.get("enabled"):
        return ""
    command = f"{zf_cli_cmd()} artifact board --attempt {quote(str(attempt))} --state-dir {quote(str(state_dir))}"
    return (f"同伴需要你已取得的证据或接口结论时，用 `{command} --roster` 查本 Run 的成员，"
            "在同一命令后用 `--post MESSAGE_ID --recipient ROLE --text TEXT` 替换 `--roster` 发送摘要。"
            "发帖重试沿用同一 ID。收到提醒时用原命令查看，阅读后用 `--ack MESSAGE_KEY` 签收。")


def task_attempt_mailbox(config, state_dir, attempt):
    if not _inbox_available(config, state_dir):
        return "", False
    from zf.runtime.artifact_read_capability import dispatch_mailbox_attempt
    from zf.runtime.durable_mailbox_authority import enabled

    address = dispatch_mailbox_attempt(state_dir, attempt)
    return _inbox_command(state_dir, address), enabled(config) and address == attempt["attempt_id"]


def _inbox_command(state_dir, attempt_id):
    prefix = f"{zf_cli_cmd()} artifact mailbox"
    target = f"--attempt {quote(str(attempt_id))} --state-dir {quote(str(state_dir))}"
    return (f"交接收件箱 `{prefix} inbox {target}`。有消息时按 ID 执行 pull、ack。"
            "读取中断复用同一 request-id。问题另行接手和回答。")
