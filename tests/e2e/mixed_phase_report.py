"""1203-T3: Mixed Backend E2E post-run report.

Thin wrapper over `w5_phase_report`: runs the base phase check and
adds a `Mixed Backend Breakdown` section with:
  - agent.usage split by payload.backend
  - codex.hook.* count (total + per-kind)
  - codex_observe_timeout warnings
  - Per-backend worker.stuck counts (if backend resolvable via actor)

Usage:
    python -m tests.e2e.mixed_phase_report [--state-dir /tmp/zaofu-mixed/.zf]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tests.e2e.w5_phase_report import (
    _load_events,
    _count_by_type,
    generate_report,
)


def _agent_usage_by_backend(events: list[dict]) -> dict[str, Counter]:
    """Aggregate agent.usage events by payload.backend (or 'unknown').

    Returns {backend: Counter(input_tokens=N, output_tokens=N, calls=N)}.
    """
    out: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        if e.get("type") != "agent.usage":
            continue
        pl = e.get("payload") or {}
        backend = pl.get("backend") or "unknown"
        usage = pl.get("usage") or {}
        out[backend]["calls"] += 1
        out[backend]["input_tokens"] += int(usage.get("input_tokens", 0))
        out[backend]["output_tokens"] += int(usage.get("output_tokens", 0))
    return out


def _codex_hook_breakdown(counts: Counter) -> dict[str, int]:
    """Per-kind count of codex.hook.* events."""
    return {
        k: counts.get(k, 0)
        for k in (
            "codex.hook.session_start",
            "codex.hook.user_prompt_submit",
            "codex.hook.pre_tool_use",
            "codex.hook.post_tool_use",
            "codex.hook.stop",
        )
    }


def _codex_observe_timeouts(events: list[dict]) -> int:
    """Count worker.spawn_warning events with code=codex_observe_timeout."""
    n = 0
    for e in events:
        if e.get("type") != "worker.spawn_warning":
            continue
        pl = e.get("payload") or {}
        if pl.get("code") == "codex_observe_timeout":
            n += 1
    return n


def print_mixed_report(events_path: Path) -> int:
    """Print the base w5_phase_report + mixed-specific section.

    Return code = 0 when all base phases pass; 1 when any phase is
    fail/not-reached. Mixed-section findings are informational and do
    not change the return code.
    """
    events = _load_events(events_path)
    counts = _count_by_type(events)

    # Base W5-E2E report (re-uses the existing phase assessor)
    base_phases = generate_report(events_path, mode="mixed")
    rc = 0
    print(f"=== Mixed Backend E2E Report ({events_path}) ===")
    print(f"Total events: {len(events)}")
    print()
    glyph = {"pass": "✓", "partial": "~", "fail": "✗", "not-reached": "–"}
    for p in base_phases:
        print(f"  [{glyph.get(p.status, '?')}] {p.phase} — {p.status}")
        for e in p.evidence:
            print(f"        · {e}")
        for r in p.fail_reasons:
            print(f"        ! {r}")
        if p.status in ("fail", "not-reached"):
            rc = 1

    # --- Mixed Backend Breakdown ---
    print()
    print("--- Mixed Backend Breakdown ---")

    by_backend = _agent_usage_by_backend(events)
    if by_backend:
        print("  agent.usage by backend:")
        for backend, c in sorted(by_backend.items()):
            print(
                f"    {backend:12s} calls={c['calls']:4d} "
                f"in={c['input_tokens']:,} out={c['output_tokens']:,}"
            )
    else:
        print("  agent.usage: none recorded")

    hook_counts = _codex_hook_breakdown(counts)
    total_hook = sum(hook_counts.values())
    if total_hook > 0:
        print(f"  codex.hook events: {total_hook} total")
        for kind, n in hook_counts.items():
            if n > 0:
                print(f"    · {kind}: {n}")
    else:
        print("  codex.hook events: 0 (did the Codex hooks feature fire?)")

    timeouts = _codex_observe_timeouts(events)
    if timeouts > 0:
        print(f"  ⚠ codex_observe_timeout: {timeouts}")

    # Stuck per-actor (cheap proxy for per-backend stuck; inference
    # needs the yaml which we don't load here — leave breakdown to a
    # follow-up if mixed runs hit the case often)
    stuck_actors = Counter(
        e.get("actor", "") for e in events
        if e.get("type") == "worker.stuck"
    )
    if stuck_actors:
        print(f"  worker.stuck by actor: {dict(stuck_actors)}")

    return rc


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mixed Backend E2E post-run phase + backend report",
    )
    ap.add_argument(
        "--state-dir", type=Path,
        default=Path("/tmp/zaofu-mixed/.zf"),
        help="path to .zf/ directory (default /tmp/zaofu-mixed/.zf)",
    )
    args = ap.parse_args()

    events_path = args.state_dir / "events.jsonl"
    if not events_path.exists():
        print(f"ERROR: {events_path} not found. Did the run happen?",
              file=sys.stderr)
        return 2

    return print_mixed_report(events_path)


if __name__ == "__main__":
    sys.exit(main())
