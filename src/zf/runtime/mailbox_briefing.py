"""Render a scoped inbox entry point without loading messages into the briefing."""

from pathlib import Path
from shlex import quote

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
