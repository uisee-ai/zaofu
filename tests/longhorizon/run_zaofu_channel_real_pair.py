"""l4-pair scenario — two sequential rounds in the SAME channel.

Sibling module to ``run_zaofu_channel_real.py``; keeps the main runner
under the project's 1000-line ceiling. Reuses pure helpers (build seed
steps, wait_for_target_reply, etc.) from the parent module.

Round 1: operator @dev-cc-1 (claude-code) → wait for reply chain.
Round 2: operator @review-cdx-1 (codex), same channel/thread → wait
again. Round 2 only starts after Round 1's reply.completed lands. A
fence timestamp (``after_ts``) prevents Round 1's assistant reply from
satisfying Round 2's wait.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from tests.longhorizon.run_zaofu_channel_real import (
    DEFAULT_WEB_URL,
    RESULTS_TSV,
    SCENARIO_L4_PAIR_DOC,
    STATE_DIR_NAME,
    ChannelIds,
    RunReport,
    SeedStep,
    _print_preflight,
    _print_seed_plan,
    build_seed_steps,
    discover_project_id,
    make_channel_ids,
    post_channel_message,
    preflight,
    wait_for_target_reply,
    _event_ts_epoch,
    zf_emit,
)


L4_PAIR_DEV_TEXT = (
    "请用 Python 写 def add(a,b) 返回 a+b。简短,只要代码不要解释。"
)
L4_PAIR_REVIEW_TEXT = (
    "请用 1-2 句中文审查上面 dev 写的 add(a,b),简短."
)


def build_round2_step(
    ids: ChannelIds,
    *,
    text: str = L4_PAIR_REVIEW_TEXT,
) -> SeedStep:
    """Round-2 message posted in same channel/thread, mentioning reviewer."""
    target = ids.review_member
    return SeedStep(
        type="channel.message.posted",
        actor=ids.op_member,
        payload={
            "channel_id": ids.channel_id,
            "thread_id": ids.thread_id,
            "message_id": f"msg-{ids.channel_id}-002",
            "role": "user",
            "text": f"@{target} {text}",
            "source": ids.source,
            "mentions": [target],
        },
    )


def append_pair_results_row(
    ids: ChannelIds,
    *,
    mode: str,
    round1_status: str,
    round2_status: str,
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
    rounds_completed = int(round1_status == "pass") + int(round2_status == "pass")
    vcr = "1.0000" if rounds_completed == 2 else "0.0000"
    note = (
        f"l4-pair-channel-real: {ids.channel_id} "
        f"round1={round1_status} round2={round2_status} "
        f"rounds={rounds_completed}/2"
    )
    if fail_reason:
        note += f" ({fail_reason})"
    line = (
        f"{iteration}\tlive-{mode}\t{vcr}\t{wall_seconds:.2f}\t-\t0.00"
        f"\tpass\t{note}\n"
    )
    with RESULTS_TSV.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _print_round2_plan(state_dir: Path, round2: SeedStep) -> None:
    payload_short = json.dumps(round2.payload, ensure_ascii=False)
    if len(payload_short) > 160:
        payload_short = payload_short[:157] + "..."
    print("== Round 2 seed (would emit after round 1 reply.completed) ==")
    print(f"  [r2] zf emit {round2.type}")
    print(f"        --actor {round2.actor}")
    print(f"        --payload '{payload_short}'")
    print(f"        --state-dir {state_dir}")
    print()


def _print_pair_criteria(ids: ChannelIds, budget_seconds: int) -> None:
    print("== Success criteria (per round) ==")
    print(f"  round 1 target = {ids.dev_member}")
    print(f"  round 2 target = {ids.review_member}")
    print(f"  per-round budget = {budget_seconds}s")
    print("  events per round:")
    print("    1. channel.agent.reply.started  (target=member)")
    print("    2. channel.message.posted       (role=assistant)")
    print("    3. channel.agent.reply.completed")
    print()


def run_l4_pair(
    mode: str,
    budget_seconds: int,
    workspace: Path,
    *,
    via_web: bool = False,
    web_url: str = DEFAULT_WEB_URL,
    action_token: str | None = None,
) -> int:
    """Drive two sequential reply rounds in the SAME channel."""
    now = datetime.now(timezone.utc)
    ids = make_channel_ids(now)
    state_dir = workspace / STATE_DIR_NAME
    token = action_token or os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    pre = preflight(
        workspace, via_web=via_web, web_url=web_url, action_token=token,
    )
    seed_round1 = build_seed_steps(
        ids,
        via_web=via_web,
        target_member=ids.dev_member,
        user_text=L4_PAIR_DEV_TEXT,
    )
    project_id = discover_project_id(web_url) if via_web else "default"

    print(f"== run_zaofu_channel_real (l4-pair / {mode}) ==")
    print(f"  workspace   = {workspace}")
    print(f"  state_dir   = {state_dir}")
    print(f"  scenario    = {SCENARIO_L4_PAIR_DOC}")
    print(f"  channel_id  = {ids.channel_id}")
    print(f"  budget      = {budget_seconds}s per round")
    if via_web:
        print(f"  via-web     = {web_url}  project_id={project_id}")
    print()

    report_for_pre = RunReport(
        mode=mode, started_at=now.isoformat(),
        budget_seconds=budget_seconds, channel_ids=ids,
        preflight=pre, seed_steps=seed_round1,
    )
    _print_preflight(report_for_pre)

    if mode == "dry-run":
        round2 = build_round2_step(ids)
        _print_seed_plan(
            state_dir, report_for_pre,
            web_url=web_url, project_id=project_id,
        )
        _print_round2_plan(state_dir, round2)
        _print_pair_criteria(ids, budget_seconds)
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
        append_pair_results_row(
            ids, mode=mode, round1_status="fail", round2_status="fail",
            wall_seconds=0.0, fail_reason=reason,
        )
        return 2

    events_file = state_dir / "events.jsonl"
    t0 = time.time()
    print("[live] round 1: emitting seed events …")
    for step in seed_round1:
        if step.kind == "web_action":
            ok, last = post_channel_message(
                web_url, project_id, step.payload, token,
            )
            print(f"  web {step.type}: ok={ok}  {last}")
        else:
            ok, last = zf_emit(state_dir, step, cwd=workspace)
            print(f"  emit {step.type}: ok={ok}  {last}")
        if not ok:
            reason = f"round1 {step.type} failed: {last}"
            wall = time.time() - t0
            append_pair_results_row(
                ids, mode=mode, round1_status="fail", round2_status="skip",
                wall_seconds=wall, fail_reason=reason,
            )
            return 3

    print(f"[live] round 1: waiting up to {budget_seconds}s for {ids.dev_member} …")
    deadline_r1 = time.time() + budget_seconds
    r1_signals, r1_status = wait_for_target_reply(
        events_file, ids.channel_id, ids.dev_member, deadline_r1,
    )
    print(f"[live] round 1 status={r1_status}")
    if r1_status != "pass":
        missing = [k for k, v in r1_signals.items() if v is None]
        reason = f"round1 {r1_status}: " + ", ".join(missing)
        wall = time.time() - t0
        append_pair_results_row(
            ids, mode=mode, round1_status=r1_status, round2_status="skip",
            wall_seconds=wall, fail_reason=reason,
        )
        return 4

    # Fence so round-1's assistant reply doesn't satisfy round-2's wait.
    # events.jsonl uses ISO-8601 `ts`, not `ts_epoch` — parse via
    # _event_ts_epoch so this fence actually moves past round 1.
    r1_done_ts = _event_ts_epoch(r1_signals["channel.agent.reply.completed"] or {})
    after_ts = r1_done_ts + 0.001 if r1_done_ts else time.time()

    round2 = build_round2_step(ids)
    print("[live] round 2: emitting reviewer mention …")
    ok, last = zf_emit(state_dir, round2, cwd=workspace)
    print(f"  emit {round2.type}: ok={ok}  {last}")
    if not ok:
        reason = f"round2 {round2.type} failed: {last}"
        wall = time.time() - t0
        append_pair_results_row(
            ids, mode=mode, round1_status="pass", round2_status="fail",
            wall_seconds=wall, fail_reason=reason,
        )
        return 3

    print(
        f"[live] round 2: waiting up to {budget_seconds}s for {ids.review_member} …"
    )
    deadline_r2 = time.time() + budget_seconds
    r2_signals, r2_status = wait_for_target_reply(
        events_file, ids.channel_id, ids.review_member, deadline_r2,
        after_ts=after_ts,
    )
    print(f"[live] round 2 status={r2_status}")
    wall = time.time() - t0
    reason = ""
    if r2_status != "pass":
        missing = [k for k, v in r2_signals.items() if v is None]
        reason = f"round2 {r2_status}: " + ", ".join(missing)
    append_pair_results_row(
        ids, mode=mode, round1_status="pass", round2_status=r2_status,
        wall_seconds=wall, fail_reason=reason,
    )
    return 0 if r2_status == "pass" else 4
