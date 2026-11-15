"""Restricted invocation for the explicitly opted-in continuity qualification."""
from pathlib import Path


def provider_command(root: Path, state: Path, prompt: str) -> list[str]:
    return ["codex", "exec", "--skip-git-repo-check", "--json", "--ephemeral",
        "--model", "gpt-6.1-terra", "--config", 'model_reasoning_effort="high"',
        "--sandbox", "workspace-write", "--add-dir", str(state.resolve()),
        "-C", str(root.resolve()), prompt]
