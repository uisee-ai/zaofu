"""Render a scoped inbox entry point without loading messages into the briefing."""

from pathlib import Path
from shlex import quote

from zf.runtime.cli_command import zf_cli_cmd


def mailbox_hint(config, state_dir, attempt_id):
    if not attempt_id or state_dir is None:
        return ""
    policy = getattr(getattr(config, "runtime", None), "durable_mailbox", None)
    # Retained bodies are a discovery hint when draining a disabled pilot.
    # The CLI checks accepted ledger facts and current authority on every read.
    if not (getattr(policy, "enabled", False) is True
            or (Path(state_dir) / "artifacts" / "durable-mailbox").is_dir()):
        return ""
    prefix = f"{zf_cli_cmd()} artifact mailbox"
    target = f"--attempt {quote(str(attempt_id))} --state-dir {quote(str(state_dir))}"
    return (f"交接收件箱 `{prefix} inbox {target}`。有消息时按 ID 执行 pull、ack。"
            "读取中断复用同一 request-id。问题另行接手和回答。")
