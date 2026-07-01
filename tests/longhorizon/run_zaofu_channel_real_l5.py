"""l5-roundtable scenario — four sequential rounds in the SAME channel.

Sibling module to ``run_zaofu_channel_real.py``; keeps the main runner
under the project's 1000-line ceiling. Reuses pure helpers (zf_emit,
wait_for_target_reply, etc.) from the parent module.

Members: arch-cc-1 (claude-code), critic-cdx-1 (codex),
dev-cc-1 (claude-code), review-cdx-1 (codex).

Rounds (sequential, same channel/thread):
  1. op @arch-cc-1     → arch design
  2. op @critic-cdx-1  → critique
  3. op @dev-cc-1      → 1-line add(a,b)
  4. op @review-cdx-1  → review

Each round waits for ``channel.agent.reply.started`` →
``channel.message.posted (role=assistant)`` →
``channel.agent.reply.completed`` targeted at the round's member.
A fence timestamp prevents the previous round's reply from satisfying
the next round's wait.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tests.longhorizon.run_zaofu_channel_real import (
    DEFAULT_WEB_URL,
    RESULTS_TSV,
    SCOPE,
    STATE_DIR_NAME,
    ChannelIds,
    RunReport,
    SeedStep,
    _print_preflight,
    discover_project_id,
    preflight,
    wait_for_target_reply,
    _event_ts_epoch,
    zf_emit,
)


SCENARIO_L5_ROUNDTABLE_DOC = (
    Path(__file__).parent / "zaofu-channel-real-l5-roundtable-v1.md"
)


L5_ARCH_TEXT = (
    "请用中文,2 句以内,设计一个最小的 Python add(a, b) 模块结构"
    "(模块名 + 函数签名 + 一个边界值)。"
)
L5_CRITIC_TEXT = (
    "请用中文,2 句以内,评审上面 arch 的设计,指出 1 个最严重的不足。"
)
L5_DEV_TEXT = (
    "请用 Python 写 def add(a, b) -> int(简短,1 行函数体)。"
)
L5_REVIEW_TEXT = (
    "请用中文,2 句以内,审查上面 dev 的代码。"
)


@dataclass
class RoundtableMember:
    member_id: str
    persona: str
    backend: str


@dataclass
class RoundtableRound:
    index: int
    target: str
    text: str
    status: str = "pending"
    wall_seconds: float = 0.0
    signals: dict = field(default_factory=dict)


ROUNDTABLE_MEMBERS = [
    RoundtableMember("arch-cc-1", "arch", "claude-code"),
    RoundtableMember("critic-cdx-1", "critic", "codex"),
    RoundtableMember("dev-cc-1", "dev", "claude-code"),
    RoundtableMember("review-cdx-1", "review", "codex"),
]


def build_roundtable_rounds() -> list[RoundtableRound]:
    """Return the 4 sequential round descriptors in order.

    Each round's ``target`` matches one ROUNDTABLE_MEMBERS entry — meta-test
    asserts the 4 distinct targets are exactly the 4 members.
    """
    return [
        RoundtableRound(1, "arch-cc-1", L5_ARCH_TEXT),
        RoundtableRound(2, "critic-cdx-1", L5_CRITIC_TEXT),
        RoundtableRound(3, "dev-cc-1", L5_DEV_TEXT),
        RoundtableRound(4, "review-cdx-1", L5_REVIEW_TEXT),
    ]


def make_roundtable_ids(now: datetime) -> ChannelIds:
    stamp = now.strftime("%Y%m%dt%H%M")
    ids = ChannelIds(channel_id=f"ch-l5-rt-{stamp}", source="real-l5-runner")
    return ids


def build_roundtable_setup_steps(ids: ChannelIds) -> list[SeedStep]:
    """channel.created + 4× channel.member.added (no message yet)."""
    steps: list[SeedStep] = [
        SeedStep(
            type="channel.created",
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "name": "l5-roundtable-real",
                "source": ids.source,
                "scope": SCOPE,
            },
        ),
    ]
    for m in ROUNDTABLE_MEMBERS:
        steps.append(SeedStep(
            type="channel.member.added",
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "thread_id": ids.thread_id,
                "member_id": m.member_id,
                "persona": m.persona,
                "backend": m.backend,
                "source": ids.source,
            },
        ))
    return steps


def build_round_message_step(
    ids: ChannelIds, round_: RoundtableRound,
) -> SeedStep:
    target = round_.target
    return SeedStep(
        type="channel.message.posted",
        actor=ids.op_member,
        payload={
            "channel_id": ids.channel_id,
            "thread_id": ids.thread_id,
            "message_id": f"msg-{ids.channel_id}-{round_.index:03d}",
            "role": "user",
            "text": f"@{target} {round_.text}",
            "source": ids.source,
            "mentions": [target],
        },
    )


def append_roundtable_results_row(
    ids: ChannelIds,
    *,
    mode: str,
    rounds: list[RoundtableRound],
    wall_seconds: float,
    fail_reason: str = "",
) -> None:
    header = (
        "iteration\tcommit\tvcr\tmtts\tcost_per_task\trework_ratio"
        "\tguard_status\tnote\n"
    )
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(header)
    rows = RESULTS_TSV.read_text().splitlines()
    iteration = max(0, len(rows) - 1)
    rounds_total = len(rounds)
    rounds_completed = sum(1 for r in rounds if r.status == "pass")
    vcr = "1.0000" if rounds_completed == rounds_total else "0.0000"
    detail = " ".join(
        f"round{r.index}={r.status}" for r in rounds
    )
    note = (
        f"l5-roundtable-channel-real: {ids.channel_id} "
        f"{detail} rounds={rounds_completed}/{rounds_total}"
    )
    if fail_reason:
        note += f" ({fail_reason})"
    line = (
        f"{iteration}\tlive-{mode}\t{vcr}\t{wall_seconds:.2f}\t-\t0.00"
        f"\tpass\t{note}\n"
    )
    with RESULTS_TSV.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _print_roundtable_setup(state_dir: Path, steps: list[SeedStep]) -> None:
    print("== Setup events (would emit) ==")
    for i, step in enumerate(steps, 1):
        payload_short = json.dumps(step.payload, ensure_ascii=False)
        if len(payload_short) > 160:
            payload_short = payload_short[:157] + "..."
        print(f"  [{i}/{len(steps)}] zf emit {step.type}")
        print(f"        --actor {step.actor}")
        print(f"        --payload '{payload_short}'")
        print(f"        --state-dir {state_dir}")
    print()


def _print_round_plan(
    state_dir: Path, ids: ChannelIds, rounds: list[RoundtableRound],
) -> None:
    print("== Roundtable plan (would emit one message per round) ==")
    for r in rounds:
        step = build_round_message_step(ids, r)
        payload_short = json.dumps(step.payload, ensure_ascii=False)
        if len(payload_short) > 160:
            payload_short = payload_short[:157] + "..."
        print(f"  [r{r.index}] target={r.target}")
        print(f"        zf emit {step.type}")
        print(f"        --payload '{payload_short}'")
        print(f"        --state-dir {state_dir}")
    print()


def _print_roundtable_criteria(
    rounds: list[RoundtableRound], budget_seconds: int,
) -> None:
    print("== Success criteria (per round) ==")
    for r in rounds:
        print(f"  round {r.index} target = {r.target}")
    print(f"  per-round budget = {budget_seconds}s")
    print("  events per round:")
    print("    1. channel.agent.reply.started  (target=member)")
    print("    2. channel.message.posted       (role=assistant)")
    print("    3. channel.agent.reply.completed")
    print()


def _drive_round(
    ids: ChannelIds,
    round_: RoundtableRound,
    state_dir: Path,
    workspace: Path,
    events_file: Path,
    budget_seconds: int,
    *,
    after_ts: float,
) -> str:
    """Emit one round's message and wait for its reply chain.

    Returns the round's status string; also mutates ``round_`` in place
    with status / signals / wall_seconds.
    """
    step = build_round_message_step(ids, round_)
    t0 = time.time()
    print(f"[live] round {round_.index}: emitting @{round_.target} …")
    ok, last = zf_emit(state_dir, step, cwd=workspace)
    print(f"  emit {step.type}: ok={ok}  {last}")
    if not ok:
        round_.status = "fail"
        round_.wall_seconds = time.time() - t0
        return round_.status
    print(
        f"[live] round {round_.index}: "
        f"waiting up to {budget_seconds}s for {round_.target} …"
    )
    deadline = time.time() + budget_seconds
    signals, status = wait_for_target_reply(
        events_file, ids.channel_id, round_.target, deadline,
        after_ts=after_ts,
    )
    round_.signals = signals
    round_.status = status
    round_.wall_seconds = time.time() - t0
    print(f"[live] round {round_.index} status={status}")
    return status


def run_l5_roundtable(
    mode: str,
    budget_seconds: int,
    workspace: Path,
    *,
    via_web: bool = False,
    web_url: str = DEFAULT_WEB_URL,
    action_token: str | None = None,
) -> int:
    """Drive four sequential reply rounds in the SAME channel."""
    now = datetime.now(timezone.utc)
    ids = make_roundtable_ids(now)
    state_dir = workspace / STATE_DIR_NAME
    token = action_token or os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    pre = preflight(
        workspace, via_web=via_web, web_url=web_url, action_token=token,
    )
    setup_steps = build_roundtable_setup_steps(ids)
    rounds = build_roundtable_rounds()
    project_id = discover_project_id(web_url) if via_web else "default"

    print(f"== run_zaofu_channel_real (l5-roundtable / {mode}) ==")
    print(f"  workspace   = {workspace}")
    print(f"  state_dir   = {state_dir}")
    print(f"  scenario    = {SCENARIO_L5_ROUNDTABLE_DOC}")
    print(f"  channel_id  = {ids.channel_id}")
    print(f"  budget      = {budget_seconds}s per round")
    if via_web:
        print(f"  via-web     = {web_url}  project_id={project_id}")
    print()

    report_for_pre = RunReport(
        mode=mode, started_at=now.isoformat(),
        budget_seconds=budget_seconds, channel_ids=ids,
        preflight=pre, seed_steps=setup_steps,
    )
    _print_preflight(report_for_pre)

    if mode == "dry-run":
        _print_roundtable_setup(state_dir, setup_steps)
        _print_round_plan(state_dir, ids, rounds)
        _print_roundtable_criteria(rounds, budget_seconds)
        if all(p.ok for p in pre):
            print("[dry-run] all preflight PASS — runner ready for --live.")
        else:
            failed = [p.name for p in pre if not p.ok]
            print(
                "[dry-run] preflight FAILED for: "
                + ", ".join(failed)
                + "\n  Fix these before flipping to --live."
            )
        print(f"\n[dry-run] not appending to {RESULTS_TSV} (live-only)")
        return 0

    # ---- LIVE -------------------------------------------------------------
    if not all(p.ok for p in pre):
        failed = [p.name for p in pre if not p.ok]
        reason = "preflight failed: " + ", ".join(failed)
        print(f"[live] aborting — {reason}")
        append_roundtable_results_row(
            ids, mode=mode, rounds=rounds,
            wall_seconds=0.0, fail_reason=reason,
        )
        return 2

    events_file = state_dir / "events.jsonl"
    t0_total = time.time()
    print("[live] emitting setup events …")
    for step in setup_steps:
        ok, last = zf_emit(state_dir, step, cwd=workspace)
        print(f"  emit {step.type}: ok={ok}  {last}")
        if not ok:
            reason = f"setup {step.type} failed: {last}"
            wall = time.time() - t0_total
            append_roundtable_results_row(
                ids, mode=mode, rounds=rounds,
                wall_seconds=wall, fail_reason=reason,
            )
            return 3

    after_ts = 0.0
    for r in rounds:
        status = _drive_round(
            ids, r, state_dir, workspace, events_file,
            budget_seconds, after_ts=after_ts,
        )
        if status != "pass":
            for later in rounds[r.index:]:  # r.index is 1-based; rounds[1:] skips r
                later.status = "skip"
            wall = time.time() - t0_total
            missing = [k for k, v in r.signals.items() if v is None]
            reason = f"round{r.index} {status}: " + ", ".join(missing)
            append_roundtable_results_row(
                ids, mode=mode, rounds=rounds,
                wall_seconds=wall, fail_reason=reason,
            )
            return 4
        completed = r.signals.get("channel.agent.reply.completed") or {}
        # events.jsonl uses ISO-8601 `ts`, not `ts_epoch` — parse via
        # _event_ts_epoch so the fence actually advances past this round.
        done_ts = _event_ts_epoch(completed)
        after_ts = done_ts + 0.001 if done_ts else time.time()

    wall = time.time() - t0_total
    append_roundtable_results_row(
        ids, mode=mode, rounds=rounds, wall_seconds=wall,
    )
    return 0
