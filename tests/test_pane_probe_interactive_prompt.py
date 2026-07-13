"""交互确认假死态探测(avbs-r5 撞限死态,r6 硬前置)。"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from zf.core.config.schema import RoleConfig, SessionConfig, ZfConfig
from zf.runtime.pane_probe import (
    build_runtime_pane_probe,
    pane_probe_attention_items,
)

# avbs-r5 真实 pane 文本(2026-07-04 capture-pane 实录,9/15 pane 同款)
_R5_USAGE_LIMIT_PANE = (
    "⚠ `--dangerously-bypass-hook-trust` is enabled.\n"
    "  Enabled hooks may run without review for this\n"
    "  invocation.\n"
    "• You have 4 usage limit resets available. Run /\n"
    "usage to use one.\n"
    "› Find and fix a bug in @filename\n"
)
_WORKING_PANE = "Codex is still applying the implementation\n"


def _completed(args, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _state_dir(tmp_path: Path, instances: list[str]) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "role_sessions.yaml").write_text(
        yaml.safe_dump({
            "instance_meta": {
                name: {
                    "backend": "codex",
                    "last_heartbeat_at": "2026-06-01T00:00:00+00:00",
                    "last_heartbeat_payload": {"state": "idle", "current_task_id": ""},
                }
                for name in instances
            },
        }),
        encoding="utf-8",
    )
    return state_dir


def _config(instances: list[str]) -> ZfConfig:
    return ZfConfig(
        session=SessionConfig(tmux_session="zf-test", tmux_layout="window_per_role"),
        roles=[
            RoleConfig(name=name, backend="codex", instance_id=name)
            for name in instances
        ],
    )


def _probe(tmp_path: Path, pane_texts: dict[str, str]):
    instances = list(pane_texts)

    def fake_tmux(args, **kwargs):
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _completed(args, "%9\tcodex\t/tmp/project\t0\n")
        if args[:2] == ["tmux", "capture-pane"]:
            target = args[args.index("-t") + 1]
            name = target.split(":", 1)[1]
            return _completed(args, pane_texts[name])
        return _completed(args, "", returncode=1, stderr="unexpected")

    return build_runtime_pane_probe(
        _state_dir(tmp_path, instances),
        config=_config(instances),
        project_root=tmp_path,
        now=_dt("2026-06-01T00:05:00+00:00"),
        runner=fake_tmux,
    )


def test_usage_limit_prompt_detected_single_pane(tmp_path: Path) -> None:
    probe = _probe(tmp_path, {
        "dev-1": _R5_USAGE_LIMIT_PANE,
        "dev-2": _WORKING_PANE,
    })
    by_id = {p["instance_id"]: p for p in probe["panes"]}
    assert by_id["dev-1"]["activity_status"] == "interactive_prompt"
    assert by_id["dev-1"]["interactive_prompt_marker"] == "usage_limit_reset_confirm"
    assert by_id["dev-2"]["activity_status"] != "interactive_prompt"
    assert probe["summary"]["interactive_prompt"] == 1
    assert probe["summary"]["correlated_interactive_prompts"] == {}

    items = pane_probe_attention_items(probe)
    prompt_items = [i for i in items if "interactive" in i["fingerprint"]]
    assert len(prompt_items) == 1
    assert prompt_items[0]["human_action_required"] is True
    assert prompt_items[0]["severity"] == "high"


def test_fleet_correlated_prompt_emits_critical_item(tmp_path: Path) -> None:
    """r5 实案:9 worker 共享配额同时撞限 → 舰队级相关性失效。"""
    probe = _probe(tmp_path, {
        f"dev-{i}": _R5_USAGE_LIMIT_PANE for i in range(1, 4)
    })
    assert probe["summary"]["interactive_prompt"] == 3
    assert probe["summary"]["correlated_interactive_prompts"] == {
        "usage_limit_reset_confirm": 3,
    }
    items = pane_probe_attention_items(probe)
    fleet = [i for i in items if i["fingerprint"].startswith("pane_probe_fleet:")]
    assert len(fleet) == 1
    assert fleet[0]["severity"] == "critical"
    assert fleet[0]["human_action_required"] is True
    # 单 pane 项照发(3 个)
    single = [i for i in items if i["fingerprint"].startswith("pane_probe_interactive:")]
    assert len(single) == 3
