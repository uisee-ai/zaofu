"""Restricted invocation for the explicitly opted-in continuity qualification."""
from pathlib import Path
import json
from zf.runtime.provider_sandbox import probe_codex_sandbox


def provider_command(root: Path, state: Path, prompt: str) -> list[str]:
    return ["codex", "exec", "--skip-git-repo-check", "--json", "--ephemeral",
        "--model", "gpt-6.1-terra", "--config", 'model_reasoning_effort="high"',
        "--sandbox", "workspace-write", "--add-dir", str(state.resolve()),
        "-C", str(root.resolve()), prompt]


def require_provider_sandbox(root: Path, state: Path, evidence: Path) -> None:
    """Fail before a paid model turn; never retry with weaker permissions."""
    record = probe_codex_sandbox(root)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "sandbox-preflight.json").write_text(json.dumps(record, indent=2) + "\n")
    if record["returncode"] != 0:
        raise RuntimeError("Codex sandbox preflight failed; inspect sandbox-preflight.json. "
            "Repair the host bubblewrap/AppArmor prerequisites; do not bypass the sandbox.")
