"""zf bug-fix-cycle — operator helper for β-3 of the zero-touch roadmap.

Wraps the markdown playbook (`skills/zaofu-bug-fix-cycle/SKILL.md`)
into a deterministic CLI flow. Reads the most-recent `zaofu.bug.detected`
event from a project state `events.jsonl`, surfaces the evidence and suggested
fix area, and (in `--auto-stash` mode) actually runs the stash / restart /
resume steps.

Default mode is **interactive**: print the diagnosis + step-by-step
prompt, exit. Operator copy-pastes commands. This matches the
"≤3 explicit confirms" target in the design doc (vs the ≥5 manual
touch points seen in r-next-8/9).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.events.log import EventLog


_DEFAULT_STATE_DIR = ".zf"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "bug-fix-cycle",
        help="β-3 operator helper: drive the zaofu fix cycle after zaofu.bug.detected",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Project .zf/ directory (default: ./.zf relative to cwd)",
    )
    parser.add_argument(
        "--signature",
        type=str,
        default="",
        help="Filter by signature name (e.g. ship_block_loop). "
             "Empty → use the most recent zaofu.bug.detected.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the diagnosis as JSON instead of human prose.",
    )
    parser.set_defaults(func=_run)


def _zaofu_checkout_hint() -> str:
    """Best-effort path to the zaofu checkout for the operator playbook.

    An editable install resolves to the repo (pyproject.toml present); a
    site-packages install has no repo to patch, so fall back to a placeholder.
    """
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists():
        return str(candidate)
    return "<zaofu-checkout>"


def _resolve_state_dir(args) -> Path:
    if args.state_dir is not None:
        return args.state_dir
    return Path(_DEFAULT_STATE_DIR).resolve()


def _find_latest_bug(events_path: Path, signature_filter: str):
    """Walk events.jsonl tail backwards, return the latest
    zaofu.bug.detected event (optionally filtered by signature).

    Returns ZfEvent | None.
    """
    if not events_path.exists():
        return None
    log = EventLog(events_path)
    candidates = []
    for event in log.read_days(1):
        if event.type != "zaofu.bug.detected":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        sig = str(payload.get("signature") or "")
        if signature_filter and sig != signature_filter:
            continue
        candidates.append(event)
    if not candidates:
        return None
    return candidates[-1]


def _render_human(bug_event) -> str:
    payload = bug_event.payload if isinstance(bug_event.payload, dict) else {}
    sig = payload.get("signature", "?")
    conf = payload.get("confidence", "?")
    area = payload.get("suggested_fix_area", "?")
    evidence = payload.get("evidence_event_ids", []) or []
    snap = (
        payload.get("run_state_snapshot")
        or payload.get("cangjie_state_snapshot")
        or {}
    )

    lines = [
        f"zaofu.bug.detected: {sig} (confidence: {conf})",
        f"  event_id:       {bug_event.id}",
        f"  emitted_at:     {bug_event.ts}",
        f"  suggested_fix:  {area}",
        "",
        "Run state snapshot:",
    ]
    if not snap:
        lines.append("  (none)")
    for key, value in sorted(snap.items()):
        lines.append(f"  {key}: {value!r}")
    lines.append("")
    lines.append(f"Evidence events ({len(evidence)}):")
    for evid in evidence:
        lines.append(f"  - {evid}")
    lines.append("")
    lines.append("─" * 60)
    lines.append(
        "Operator playbook (see skills/zaofu-bug-fix-cycle/SKILL.md):"
    )
    lines.append("")
    lines.append("1. stash target project state:")
    lines.append("     cd <target-project-root>")
    lines.append(f"     git stash push -u -m 'zaofu-fix-pause-{sig}'")
    lines.append("")
    zaofu_root = _zaofu_checkout_hint()
    lines.append(f"2. fix zaofu in {zaofu_root}:")
    lines.append(f"     read the {len(evidence)} evidence events above")
    lines.append(f"     patch {area}")
    lines.append("     add a regression test that replays the evidence")
    lines.append("     pytest --no-cov -q")
    lines.append("     git commit + git push origin dev")
    lines.append("")
    lines.append("3. restart target project watcher:")
    lines.append("     cd <target-project-root>")
    lines.append(f"     {sys.executable} -m zf.cli.main stop")
    lines.append(f"     {sys.executable} -m zf.cli.main start &")
    lines.append("")
    lines.append("4. resume target task:")
    lines.append("     git stash pop")
    lines.append(
        "     zf emit zaofu.bug.fix_applied --payload '{...}'"
    )
    return "\n".join(lines)


def _render_json(bug_event) -> str:
    payload = bug_event.payload if isinstance(bug_event.payload, dict) else {}
    return json.dumps({
        "event_id": bug_event.id,
        "ts": bug_event.ts,
        "type": bug_event.type,
        "payload": payload,
    }, indent=2, ensure_ascii=False)


def _run(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args)
    events_path = state_dir / "events.jsonl"

    if not events_path.exists():
        print(
            f"Error: events.jsonl not found at {events_path}",
            file=sys.stderr,
        )
        return 2

    bug = _find_latest_bug(events_path, args.signature)
    if bug is None:
        msg = "No zaofu.bug.detected event found in the recent event tail."
        if args.signature:
            msg += f" (filter: signature={args.signature})"
        print(msg)
        return 1

    if args.json:
        print(_render_json(bug))
    else:
        print(_render_human(bug))
    return 0
