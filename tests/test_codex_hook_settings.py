"""Tests for _write_codex_hook_settings — 1202-T1.

Codex has a Claude-compatible hook system under the `hooks` feature.
zaofu writes a project-local hooks.json so
the running codex process wires into zaofu's hook_recv bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_codex_hook_settings_generates_file(tmp_path: Path):
    from zf.cli.start import _write_codex_hook_settings

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    hook_file = tmp_path / ".codex" / "hooks.json"
    assert hook_file.exists(), "hooks.json should land in project .codex/"

    data = json.loads(hook_file.read_text())
    assert "hooks" in data


def test_codex_hook_settings_covers_five_events(tmp_path: Path):
    from zf.cli.start import _write_codex_hook_settings

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    expected = {
        "SessionStart", "UserPromptSubmit", "PreToolUse",
        "PostToolUse", "Stop",
    }
    assert set(data["hooks"].keys()) == expected


def test_codex_hook_settings_command_binds_hook_recv(tmp_path: Path):
    from zf.cli.start import _write_codex_hook_settings

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "zf hook-recv" in stop_cmd
    assert "--event codex.hook.stop" in stop_cmd
    assert "--backend codex" in stop_cmd
    assert str(state_dir) in stop_cmd


def test_codex_hook_settings_uses_configured_zf_cli_cmd(
    tmp_path: Path,
    monkeypatch,
):
    from zf.cli.start import _write_codex_hook_settings

    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert stop_cmd.startswith("uv --project /repo run zf hook-recv ")
    assert "--event codex.hook.stop" in stop_cmd


def test_codex_hook_settings_json_is_parseable(tmp_path: Path):
    """Codex loads hooks.json as strict JSON — no trailing commas etc."""
    from zf.cli.start import _write_codex_hook_settings

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    raw = (tmp_path / ".codex" / "hooks.json").read_text()
    json.loads(raw)  # will raise on malformed


def test_codex_hook_settings_all_five_events_have_type_command(tmp_path: Path):
    from zf.cli.start import _write_codex_hook_settings

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    for ev_name, entries in data["hooks"].items():
        assert entries, f"{ev_name} has no entries"
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command", \
                    f"{ev_name} hook.type should be 'command', got {hook['type']}"


# --- P1-CODEX-HOOK-TRUST: deterministic trust-hash computation -------------
# codex 0.133 broke the `app-server hooks/list` RPC zaofu used to fetch hook
# `currentHash` values, so workers stalled at the interactive `/hooks` review.
# These vectors are real `trusted_hash` values codex itself persisted to
# CODEX_HOME/config.toml on "Trust all" for two live projects; they lock the
# replicated hash (codex_hook_hash) against codex's own algorithm.

_EVENT_META = {
    "session_start": ("SessionStart", "codex.hook.session_start"),
    "user_prompt_submit": ("UserPromptSubmit", "codex.hook.user_prompt_submit"),
    "pre_tool_use": ("PreToolUse", "codex.hook.pre_tool_use"),
    "post_tool_use": ("PostToolUse", "codex.hook.post_tool_use"),
    "stop": ("Stop", "codex.hook.stop"),
}

_REAL_HASHES = {
    ("/path/to/example-project/.zf-mixed", "pre_tool_use"):
        "sha256:a9bdc376f3404497be9b879de763ead26782087852a2c4273b748a19029e3231",
    ("/path/to/example-project/.zf-mixed", "post_tool_use"):
        "sha256:22849323caaedf088f0e39ebd11d8dce908b6acc11c3fa74a45b8e17418e5262",
    ("/path/to/example-project/.zf-mixed", "session_start"):
        "sha256:6c2f3bf7f920f880dc0f46c75e43e0572a5202c9613630964c61667e56094756",
    ("/path/to/example-project/.zf-mixed", "user_prompt_submit"):
        "sha256:02e8a4dc851ce229812888fedcde8be970ff0df943460d8506403d0b5df976a2",
    ("/path/to/example-project/.zf-mixed", "stop"):
        "sha256:13ef7a211a9003f1f6037914aa704816b5e38dad383a0ab2f807aa124f4203fe",
    ("/path/to/example-project/.zf", "pre_tool_use"):
        "sha256:cc0367dbf0faf6d2395b7e9810ddb96ab4fccf6d82452932fdebd92c76827551",
    ("/path/to/example-project/.zf", "post_tool_use"):
        "sha256:aae2f3c9df9245f60a78180a3aceb14c88794650376abfcead35cfb3d96bcd3e",
    ("/path/to/example-project/.zf", "session_start"):
        "sha256:68125e329c1e9c5bd8fbb2c1da2090f9e3f9d4605436b8fbc7ce5f1c246c53a3",
    ("/path/to/example-project/.zf", "user_prompt_submit"):
        "sha256:1991ff9f6fb7816c398746dfe3dbfb3b7c1007345f21e53fe80f8c4fd428f014",
    ("/path/to/example-project/.zf", "stop"):
        "sha256:9f8dc49d2866898fb0f5bda3e5ddf1012bc442fed660d2e287881153fdd73310",
}


@pytest.mark.parametrize(("state_dir", "label"), list(_REAL_HASHES.keys()))
def test_codex_hook_hash_matches_real_codex_values(state_dir: str, label: str):
    from zf.runtime.codex_hooks import codex_hook_hash

    engine_name, zf_event = _EVENT_META[label]
    assert codex_hook_hash(Path(state_dir), engine_name, zf_event) == \
        _REAL_HASHES[(state_dir, label)]


def test_codex_hook_trust_states_keys_both_roots(tmp_path: Path):
    """One (key, hash) per event per candidate project root; key path matches
    codex's `<hooks.json>:<label>:0:0` and hash is path-independent."""
    from zf.runtime.codex_hooks import codex_hook_trust_states

    state_dir = tmp_path / ".zf-mixed"
    worktree = tmp_path / "workdirs" / "dev-1" / "project"
    main = tmp_path

    states = codex_hook_trust_states(state_dir, worktree, main)
    keys = {k for k, _ in states}
    # 5 events x 2 distinct roots
    assert len(states) == 10
    assert f"{worktree.resolve()}/.codex/hooks.json:session_start:0:0" in keys
    assert f"{main.resolve()}/.codex/hooks.json:stop:0:0" in keys
    # same hash for the same event regardless of which root keys it
    by_label = {}
    for key, h in states:
        label = key.rsplit(":", 3)[1]
        by_label.setdefault(label, set()).add(h)
    for label, hashes in by_label.items():
        assert len(hashes) == 1, f"{label} hash should be path-independent"


def test_codex_hook_trust_states_dedups_identical_roots(tmp_path: Path):
    from zf.runtime.codex_hooks import codex_hook_trust_states

    state_dir = tmp_path / ".zf"
    states = codex_hook_trust_states(state_dir, tmp_path, tmp_path)
    assert len(states) == 5  # deduped to one root


# --- F3 decision B: codex version-drift sensor for the deterministic hash ----
# codex_hook_hash replicates codex_rs internals (version-coupled). The static
# byte-exact vectors above only prove correctness for the verified version;
# they cannot catch codex changing its algorithm in a newer build. This sensor
# fails (does not silently pass) when a codex on PATH drifts from the verified
# baseline, prompting re-verification. Skipped when codex is absent / unparsable.
def test_codex_version_matches_hash_baseline():
    import shutil
    import subprocess

    from zf.runtime.codex_hooks import CODEX_HASH_VERIFIED_VERSION

    codex = shutil.which("codex")
    if not codex:
        pytest.skip("codex not on PATH; hash-drift sensor inactive")
    try:
        out = subprocess.run(
            [codex, "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("could not run `codex --version`")

    import re
    m = re.search(r"(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
    if not m:
        pytest.skip(f"could not parse codex version from {out.stdout!r}")
    running = f"{m.group(1)}.{m.group(2)}"
    assert running == CODEX_HASH_VERIFIED_VERSION, (
        f"codex {running} differs from the version codex_hook_hash was verified "
        f"against ({CODEX_HASH_VERIFIED_VERSION}). Re-verify codex_hook_hash "
        f"against this codex (the bypass flag still protects spawns) and bump "
        f"CODEX_HASH_VERIFIED_VERSION."
    )
