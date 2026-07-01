"""Real L4 channel runner — drives the cj-mono mixed-backend harness
through a pair-programming channel thread (see
``tests/longhorizon/zaofu-channel-real-l4-v1.md`` for the scenario
script).

This runner targets ``/path/to/example-project`` — NOT the zaofu
repo. It validates pre-conditions, emits a seed event sequence via
``zf emit --state-dir <cj-mono>/.zf-mixed``, polls events.jsonl for the
expected channel reply chain, and writes a result row to
``tests/longhorizon/results.tsv``.

Modes:

- ``--dry-run`` (default): pre-conditions reported honestly, seed events
  printed but NOT emitted, no orchestrator touched. Safe to run anytime.
- ``--live``: actually emits events into cj-mono and waits up to
  ``--budget-seconds`` (default 600s) for the channel reply chain.
  **Requires** ``zf start`` already running against cj-mono.

The runner deliberately does NOT start the orchestrator itself — that's
an operator decision because it spends real LLM budget.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


CJ_MONO = Path("/path/to/example-project")
STATE_DIR_NAME = ".zf-mixed"
TMUX_SESSION = "zf-mixed"
RESULTS_TSV = Path(__file__).parent / "results.tsv"
SCENARIO_DOC = Path(__file__).parent / "zaofu-channel-real-l4-v1.md"
SCENARIO_L4_PAIR_DOC = Path(__file__).parent / "zaofu-channel-real-l4-pair-v1.md"

DEFAULT_BUDGET_SECONDS = 600
DEFAULT_WEB_URL = "http://127.0.0.1:8002"
CHANNEL_POST_ACTION = "channel-post-message"
SCOPE = ["src/lh_demo/math.py", "tests/lh_demo/test_math.py"]
DEFAULT_USER_TEXT = (
    "写一个 add(a, b) 函数,放在 src/lh_demo/math.py;"
    "再加 pytest 用例覆盖正数 / 负数 / 0,放 tests/lh_demo/test_math.py。"
)


@dataclass
class PreflightResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ChannelIds:
    channel_id: str
    thread_id: str = "thr-main"
    source: str = "real-l4-runner"
    op_member: str = "op"
    dev_member: str = "dev-cc-1"
    review_member: str = "review-cdx-1"


@dataclass
class SeedStep:
    """One step in the seed sequence. ``kind`` chooses transport:

    - ``emit`` (default): ``zf emit <type>`` against state_dir.
    - ``web_action``: HTTP POST to a zf web action endpoint. ``type``
      stores the action name (e.g. ``channel-post-message``).
    """

    type: str
    actor: str
    payload: dict
    kind: str = "emit"


@dataclass
class RunReport:
    mode: str  # "dry-run" | "live"
    started_at: str
    budget_seconds: int
    channel_ids: ChannelIds
    preflight: list[PreflightResult] = field(default_factory=list)
    seed_steps: list[SeedStep] = field(default_factory=list)
    emitted_event_ids: list[str] = field(default_factory=list)
    success_signals: dict[str, dict | None] = field(default_factory=dict)
    status: str = "unknown"  # pass | fail | timeout | dry-run
    fail_reason: str = ""
    wall_seconds: float = 0.0

    def all_preflight_pass(self) -> bool:
        return all(p.ok for p in self.preflight)


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------


def preflight(
    cj_mono: Path,
    *,
    via_web: bool = False,
    web_url: str | None = None,
    action_token: str | None = None,
) -> list[PreflightResult]:
    checks: list[PreflightResult] = []

    yaml = cj_mono / "zf.yaml"
    checks.append(PreflightResult(
        name="cj_mono_zf_yaml_exists",
        ok=yaml.exists(),
        detail=str(yaml),
    ))

    state_dir = cj_mono / STATE_DIR_NAME
    checks.append(PreflightResult(
        name="state_dir_initialized",
        ok=state_dir.exists(),
        detail=f"{state_dir} (run 'zf init' inside cj-mono if missing)",
    ))

    events_file = state_dir / "events.jsonl"
    checks.append(PreflightResult(
        name="events_jsonl_writable",
        ok=events_file.exists() or state_dir.exists(),
        detail=str(events_file),
    ))

    # Accept either the configured TMUX_SESSION or any zf-cjp-* tmp clone
    # session (cj-mono live runs use a per-run dated session name).
    tmux_ls = subprocess.run(
        ["tmux", "ls", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    sessions = tmux_ls.stdout.split() if tmux_ls.returncode == 0 else []
    ok_tmux = TMUX_SESSION in sessions or any(s.startswith("zf-cjp-") for s in sessions)
    checks.append(PreflightResult(
        name="orchestrator_tmux_session",
        ok=ok_tmux,
        detail=f"sessions={sessions or '(none)'} (need {TMUX_SESSION} or zf-cjp-*)",
    ))

    for cli in ("zf", "tmux", "claude", "codex"):
        which = subprocess.run(
            ["which", cli], capture_output=True, text=True,
        )
        checks.append(PreflightResult(
            name=f"cli_{cli}",
            ok=which.returncode == 0,
            detail=which.stdout.strip() or "(not on PATH)",
        ))

    if via_web:
        ok_web, web_detail = _check_web_reachable(web_url or DEFAULT_WEB_URL)
        checks.append(PreflightResult(
            name="cli_web_reachable",
            ok=ok_web,
            detail=web_detail,
        ))
        token = action_token or os.environ.get("ZF_WEB_ACTION_TOKEN", "")
        checks.append(PreflightResult(
            name="zf_action_token_present",
            ok=bool(token),
            detail=("(set via $ZF_WEB_ACTION_TOKEN or --action-token)"
                    if not token else "token present"),
        ))

    return checks


def _check_web_reachable(web_url: str) -> tuple[bool, str]:
    url = web_url.rstrip("/") + "/api/projects"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            return status == 200, f"GET {url} -> {status}"
    except urllib.error.URLError as exc:
        return False, f"GET {url} -> URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"GET {url} -> {type(exc).__name__}: {exc}"


def discover_project_id(web_url: str, fallback: str = "default") -> str:
    """GET {web_url}/api/projects → projects[0]['project_id'].

    Returns ``fallback`` on any failure (network down, malformed JSON,
    empty list). Safe to call during dry-run preflight.
    """
    url = web_url.rstrip("/") + "/api/projects"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return fallback
    projects = data.get("projects") if isinstance(data, dict) else None
    if not projects:
        return fallback
    first = projects[0] or {}
    return first.get("project_id") or fallback


# ---------------------------------------------------------------------------
# Seed event sequence
# ---------------------------------------------------------------------------


def build_seed_steps(
    ids: ChannelIds,
    *,
    via_web: bool = False,
    target_member: str | None = None,
    user_text: str = DEFAULT_USER_TEXT,
) -> list[SeedStep]:
    target = target_member or ids.dev_member
    text = f"@{target} {user_text}"
    msg_id = f"msg-{ids.channel_id}-001"
    setup_steps = [
        SeedStep(
            type="channel.created",
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "name": "l4-pair-real",
                "source": ids.source,
                "scope": SCOPE,
            },
        ),
        SeedStep(
            type="channel.member.added",
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "thread_id": ids.thread_id,
                "member_id": ids.dev_member,
                "persona": "dev",
                "backend": "claude-code",
                "source": ids.source,
            },
        ),
        SeedStep(
            type="channel.member.added",
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "thread_id": ids.thread_id,
                "member_id": ids.review_member,
                "persona": "review",
                "backend": "codex",
                "source": ids.source,
            },
        ),
    ]
    if via_web:
        # The web action emits channel.message.posted AND triggers
        # route_channel_message internally — DO NOT also emit
        # channel.agent.reply.requested here.
        setup_steps.append(SeedStep(
            kind="web_action",
            type=CHANNEL_POST_ACTION,
            actor=ids.op_member,
            payload={
                "channel_id": ids.channel_id,
                "thread_id": ids.thread_id,
                "message_id": msg_id,
                "role": "user",
                "text": text,
                "source": ids.source,
                "member_id": ids.op_member,
                "mentions": [target],
            },
        ))
        return setup_steps
    # text carries @<target> and mentions=[target]; the router picks up
    # the mention and emits channel.agent.reply.requested itself. Do NOT
    # manually emit reply.requested here — there is no reactor for the
    # raw reply.requested event, so a manual emit is silently dropped.
    setup_steps.append(SeedStep(
        type="channel.message.posted",
        actor=ids.op_member,
        payload={
            "channel_id": ids.channel_id,
            "thread_id": ids.thread_id,
            "message_id": msg_id,
            "role": "user",
            "text": text,
            "source": ids.source,
            "mentions": [target],
        },
    ))
    return setup_steps


# ---------------------------------------------------------------------------
# zf emit / events.jsonl polling
# ---------------------------------------------------------------------------


def zf_emit(
    state_dir: Path,
    step: SeedStep,
    *,
    cwd: Path,
) -> tuple[bool, str]:
    """Invoke `zf emit` for one seed step. Returns (ok, last line of output)."""
    payload_json = json.dumps(step.payload, ensure_ascii=False)
    cmd = [
        "zf", "emit", step.type,
        "--actor", step.actor,
        "--payload", payload_json,
        "--state-dir", str(state_dir),
    ]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    last = out[-1] if out else ""
    return proc.returncode == 0, last


def _channel_post_url(web_url: str, project_id: str) -> str:
    return (
        web_url.rstrip("/")
        + f"/api/projects/{project_id}/actions/{CHANNEL_POST_ACTION}"
    )


def post_channel_message(
    web_url: str,
    project_id: str,
    payload: dict,
    token: str,
) -> tuple[bool, str]:
    """POST one channel-post-message action. Returns (ok, summary line).

    Mirrors zf_emit's return shape so the dispatcher in ``run`` can treat
    both transports uniformly.
    """
    url = _channel_post_url(web_url, project_id)
    body = json.dumps({"payload": payload}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Zf-Action-Token": token,
        "X-Zf-Web-Token": token,
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        return False, f"POST {url} -> HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"POST {url} -> URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"POST {url} -> {type(exc).__name__}: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"POST {url} -> HTTP {status} non-JSON body"
    ok = bool(data.get("ok"))
    summary = f"POST {url} -> HTTP {status} status={data.get('status')}"
    return ok, summary


def _iter_events(events_file: Path):
    if not events_file.exists():
        return
    with events_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _find_first(
    events_file: Path,
    type_: str,
    *,
    payload_match: dict | None = None,
) -> dict | None:
    for evt in _iter_events(events_file):
        if evt.get("type") != type_:
            continue
        if payload_match:
            payload = evt.get("payload") or {}
            if not all(payload.get(k) == v for k, v in payload_match.items()):
                continue
        return evt
    return None


def _event_ts_epoch(evt: dict) -> float:
    """Return event ts as epoch float, parsing ISO-8601 ``ts`` if present.

    Events in events.jsonl carry ``ts`` (ISO-8601 string with timezone),
    NOT ``ts_epoch``. Earlier wait_for_target_reply used
    ``evt.get('ts_epoch') or 0`` which always returned 0 — broke the
    ``after_ts`` fence and made every round-2 wait a false timeout.
    """
    ts = evt.get("ts") or ""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def wait_for_target_reply(
    events_file: Path,
    channel_id: str,
    target_member_id: str,
    deadline: float,
    *,
    after_ts: float = 0.0,
) -> tuple[dict[str, dict | None], str]:
    """Poll events.jsonl until reply chain for ONE target lands or deadline hits.

    ``after_ts`` filters out assistant messages older than the given epoch —
    needed for round-2 in l4-pair so round-1's assistant reply doesn't
    immediately satisfy round-2's wait. Returns (signals_dict, status).
    """
    needed = ["channel.agent.reply.started",
              "channel.message.posted_reply",
              "channel.agent.reply.completed"]
    signals: dict[str, dict | None] = {k: None for k in needed}
    while time.time() < deadline:
        if signals["channel.agent.reply.started"] is None:
            evt = _find_first(
                events_file,
                "channel.agent.reply.started",
                payload_match={
                    "channel_id": channel_id,
                    "target_member_id": target_member_id,
                },
            )
            if evt and _event_ts_epoch(evt) >= after_ts:
                signals["channel.agent.reply.started"] = evt
        if signals["channel.message.posted_reply"] is None:
            for evt in _iter_events(events_file):
                if evt.get("type") != "channel.message.posted":
                    continue
                payload = evt.get("payload") or {}
                if payload.get("channel_id") != channel_id:
                    continue
                if payload.get("role") != "assistant":
                    continue
                if _event_ts_epoch(evt) < after_ts:
                    continue
                signals["channel.message.posted_reply"] = evt
                break
        if signals["channel.agent.reply.completed"] is None:
            evt = _find_first(
                events_file,
                "channel.agent.reply.completed",
                payload_match={
                    "channel_id": channel_id,
                    "target_member_id": target_member_id,
                },
            )
            if evt and _event_ts_epoch(evt) >= after_ts:
                signals["channel.agent.reply.completed"] = evt
        failed = _find_first(
            events_file,
            "channel.agent.reply.failed",
            payload_match={"channel_id": channel_id},
        )
        if failed is not None and _event_ts_epoch(failed) >= after_ts:
            signals["channel.agent.reply.failed"] = failed
            return signals, "fail"
        if all(signals[k] is not None for k in needed):
            return signals, "pass"
        time.sleep(2)
    return signals, "timeout"


def wait_for_signals(
    events_file: Path,
    ids: ChannelIds,
    budget_seconds: int,
) -> tuple[dict[str, dict | None], str]:
    """Poll events.jsonl until all success signals appear or budget exhausted.

    Returns (signals_dict, status). status is one of {pass, fail, timeout}.
    """
    deadline = time.time() + budget_seconds
    return wait_for_target_reply(
        events_file, ids.channel_id, ids.dev_member, deadline,
    )


# ---------------------------------------------------------------------------
# Result row append
# ---------------------------------------------------------------------------


def append_results_row(report: RunReport) -> None:
    """Append one TSV row. Header is created if file is missing."""
    header = (
        "iteration\tcommit\tvcr\tmtts\tcost_per_task\trework_ratio"
        "\tguard_status\tnote\n"
    )
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(header)
    rows = RESULTS_TSV.read_text().splitlines()
    iteration = max(0, len(rows) - 1)
    vcr = "1.0000" if report.status == "pass" else "0.0000"
    started = report.success_signals.get("channel.agent.reply.started") or {}
    completed = report.success_signals.get("channel.agent.reply.completed") or {}
    t0 = _event_ts_epoch(started)
    t1 = _event_ts_epoch(completed)
    mtts = f"{t1 - t0:.2f}" if (t0 and t1) else "0.00"
    note = (
        f"l4-channel-real: {report.channel_ids.channel_id} {report.status}"
        + (f" ({report.fail_reason})" if report.fail_reason else "")
    )
    line = (
        f"{iteration}\tlive-{report.mode}\t{vcr}\t{mtts}\t-\t0.00"
        f"\tpass\t{note}\n"
    )
    with RESULTS_TSV.open("a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------------------
# Dry-run print
# ---------------------------------------------------------------------------


def _print_preflight(report: RunReport) -> None:
    print("== Preflight ==")
    for chk in report.preflight:
        flag = "PASS" if chk.ok else "FAIL"
        print(f"  [{flag}] {chk.name:32s} {chk.detail}")
    print()


def _print_seed_plan(
    state_dir: Path,
    report: RunReport,
    *,
    web_url: str | None = None,
    project_id: str | None = None,
) -> None:
    print("== Seed event plan (would emit) ==")
    for i, step in enumerate(report.seed_steps, 1):
        payload_short = json.dumps(step.payload, ensure_ascii=False)
        if len(payload_short) > 160:
            payload_short = payload_short[:157] + "..."
        if step.kind == "web_action":
            url = _channel_post_url(web_url or DEFAULT_WEB_URL,
                                    project_id or "default")
            print(f"  [{i}/{len(report.seed_steps)}] POST {url}")
            print(f"        action     {step.type}")
            print(f"        actor      {step.actor}")
            print(f"        payload    '{payload_short}'")
        else:
            print(f"  [{i}/{len(report.seed_steps)}] zf emit {step.type}")
            print(f"        --actor {step.actor}")
            print(f"        --payload '{payload_short}'")
            print(f"        --state-dir {state_dir}")
    print()


def _print_success_criteria(ids: ChannelIds, budget: int) -> None:
    print("== Success criteria (would wait for) ==")
    print(f"  channel_id  = {ids.channel_id}")
    print(f"  thread_id   = {ids.thread_id}")
    print(f"  dev member  = {ids.dev_member}")
    print(f"  budget      = {budget}s")
    print("  events:")
    print("    1. channel.agent.reply.started  (target=dev member, ≤60s)")
    print("    2. channel.message.posted       (role=assistant)")
    print("    3. channel.agent.reply.completed")
    print("  failure shortcut: channel.agent.reply.failed → exit fail")
    print()


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def make_channel_ids(now: datetime) -> ChannelIds:
    stamp = now.strftime("%Y%m%dt%H%M")
    return ChannelIds(channel_id=f"ch-l4-pair-{stamp}")


def run(
    mode: str,
    budget_seconds: int,
    workspace: Path = CJ_MONO,
    *,
    via_web: bool = False,
    web_url: str = DEFAULT_WEB_URL,
    action_token: str | None = None,
    target_member: str = "dev-cc-1",
    user_text: str = DEFAULT_USER_TEXT,
) -> int:
    now = datetime.now(timezone.utc)
    ids = make_channel_ids(now)
    ids.dev_member = target_member
    state_dir = workspace / STATE_DIR_NAME
    token = action_token or os.environ.get("ZF_WEB_ACTION_TOKEN", "")
    report = RunReport(
        mode=mode,
        started_at=now.isoformat(),
        budget_seconds=budget_seconds,
        channel_ids=ids,
        preflight=preflight(
            workspace,
            via_web=via_web,
            web_url=web_url,
            action_token=token,
        ),
        seed_steps=build_seed_steps(
            ids, via_web=via_web, target_member=target_member, user_text=user_text,
        ),
    )
    project_id = (
        discover_project_id(web_url) if via_web else "default"
    )

    print(f"== run_zaofu_channel_real ({mode}) ==")
    print(f"  workspace   = {workspace}")
    print(f"  state_dir   = {state_dir}")
    print(f"  scenario    = {SCENARIO_DOC}")
    print(f"  channel_id  = {ids.channel_id}")
    print(f"  budget      = {budget_seconds}s")
    print(f"  started_at  = {report.started_at}")
    if via_web:
        print(f"  via-web     = {web_url}  project_id={project_id}")
    print()

    _print_preflight(report)

    if mode == "dry-run":
        _print_seed_plan(
            state_dir, report, web_url=web_url, project_id=project_id,
        )
        _print_success_criteria(ids, budget_seconds)
        report.status = "dry-run"
        if report.all_preflight_pass():
            print("[dry-run] all preflight PASS — runner ready for --live.")
        else:
            failed = [p.name for p in report.preflight if not p.ok]
            print(
                "[dry-run] preflight FAILED for: "
                + ", ".join(failed)
                + "\n  Fix these before flipping to --live."
            )
        print()
        print(f"[dry-run] not appending to {RESULTS_TSV} (live-only)")
        return 0

    # --- LIVE path ------------------------------------------------------
    if not report.all_preflight_pass():
        failed = [p.name for p in report.preflight if not p.ok]
        report.status = "fail"
        report.fail_reason = "preflight failed: " + ", ".join(failed)
        print(f"[live] aborting — {report.fail_reason}")
        append_results_row(report)
        return 2

    print("[live] emitting seed events …")
    t0 = time.time()
    for step in report.seed_steps:
        if step.kind == "web_action":
            ok, last = post_channel_message(
                web_url, project_id, step.payload, token,
            )
            print(f"  web {step.type}: ok={ok}  {last}")
        else:
            ok, last = zf_emit(state_dir, step, cwd=workspace)
            print(f"  emit {step.type}: ok={ok}  {last}")
        if not ok:
            report.status = "fail"
            report.fail_reason = f"{step.kind} {step.type} failed: {last}"
            report.wall_seconds = time.time() - t0
            append_results_row(report)
            return 3
        if last.startswith("Emitted:") and "(" in last:
            report.emitted_event_ids.append(last.rsplit("(", 1)[-1].rstrip(")"))

    print(f"[live] waiting up to {budget_seconds}s for channel reply chain …")
    events_file = state_dir / "events.jsonl"
    signals, status = wait_for_signals(events_file, ids, budget_seconds)
    report.success_signals = signals
    report.status = status
    report.wall_seconds = time.time() - t0
    if status == "fail":
        failed = signals.get("channel.agent.reply.failed") or {}
        report.fail_reason = str(
            (failed.get("payload") or {}).get("reason") or "reply.failed"
        )
    elif status == "timeout":
        missing = [k for k, v in signals.items() if v is None]
        report.fail_reason = "missing: " + ", ".join(missing)

    print(f"[live] status={status} wall={report.wall_seconds:.1f}s")
    if report.fail_reason:
        print(f"[live] fail_reason={report.fail_reason}")
    append_results_row(report)
    return 0 if status == "pass" else 4


# ---------------------------------------------------------------------------
# argparse / main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive cj-mono channel reply chain (L4 real LLM scenario)",
    )
    parser.add_argument(
        "--scenario",
        choices=("l4", "l4-pair", "l5"),
        default="l4",
        help="Which scenario to run: l4 (single dev round), l4-pair "
        "(dev round + reviewer round in same channel), l5 (4-agent "
        "roundtable: arch/critic/dev/review across cc+codex backends)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print plan + preflight, do NOT emit events (default)",
    )
    parser.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Actually emit events into cj-mono state_dir (spends LLM budget)",
    )
    parser.add_argument(
        "--budget-seconds",
        type=int,
        default=DEFAULT_BUDGET_SECONDS,
        help="Wall-clock budget for the reply chain (live mode only)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=CJ_MONO,
        help="cj-mono workspace path (default: /path/to/example-project). "
        "Point at a live tmp clone (e.g. /tmp/zf-cj-mono-plan-e2e-...) to "
        "exercise the already-running orchestrator.",
    )
    parser.add_argument(
        "--via-web",
        action="store_true",
        default=False,
        help="Send the user message through the cj-mono zf web action "
        "/api/projects/<pid>/actions/channel-post-message instead of "
        "raw `zf emit channel.message.posted`.",
    )
    parser.add_argument(
        "--web-url",
        default=DEFAULT_WEB_URL,
        help=f"cj-mono zf web base URL (default: {DEFAULT_WEB_URL}).",
    )
    parser.add_argument(
        "--action-token",
        default=None,
        help="Web action token (falls back to $ZF_WEB_ACTION_TOKEN).",
    )
    parser.add_argument(
        "--target-member",
        default="dev-cc-1",
        help="member_id to @mention + wait for as the dev signal.",
    )
    parser.add_argument(
        "--user-text",
        default=DEFAULT_USER_TEXT,
        help="Message body; @<target-member> is auto-prepended.",
    )
    args = parser.parse_args(argv)

    mode = "dry-run" if args.dry_run else "live"
    if args.scenario == "l4-pair":
        # Imported lazily so the sibling can re-import helpers from this
        # module without a circular import at top-level.
        from tests.longhorizon.run_zaofu_channel_real_pair import run_l4_pair
        return run_l4_pair(
            mode,
            args.budget_seconds,
            workspace=args.workspace,
            via_web=args.via_web,
            web_url=args.web_url,
            action_token=args.action_token,
        )
    if args.scenario == "l5":
        from tests.longhorizon.run_zaofu_channel_real_l5 import run_l5_roundtable
        return run_l5_roundtable(
            mode,
            args.budget_seconds,
            workspace=args.workspace,
            via_web=args.via_web,
            web_url=args.web_url,
            action_token=args.action_token,
        )
    return run(
        mode,
        args.budget_seconds,
        workspace=args.workspace,
        via_web=args.via_web,
        web_url=args.web_url,
        action_token=args.action_token,
        target_member=args.target_member,
        user_text=args.user_text,
    )


if __name__ == "__main__":
    # When invoked as a script (python tests/longhorizon/run_zaofu_channel_real.py),
    # the repo root isn't on sys.path so sibling import
    # `tests.longhorizon.run_zaofu_channel_real_pair` would fail. Pytest runs
    # already have repo root on sys.path via conftest, so the guard is
    # script-entry-only.
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    sys.exit(main())
