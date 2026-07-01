"""W5-E2E baseline — post-run phase observation report.

Usage:
    python -m tests.e2e.w5_phase_report [--state-dir /tmp/zaofu-w5e2e/.zf]

Reads events.jsonl after a real run and prints a per-Phase pass/fail
assessment + key metrics. Safe to run multiple times; reads only, never
writes.

Current mode (default): 10-phase check against the runtime event truth
used by the robustness runners:
  P0 Preflight — (assumed pass if harness started)
  P1 Intent — user.message seen
  P2 PDD / Design — arch.proposal.done
  P3 Critic Design Gate — design.critique.done
  P4 TDD / Contract — task.contract.update
  P5 Build — task.assigned + dev.build.done
  P6 Review — review.approved
  P7 Verify — test.passed; discriminator.passed is required only in w5/full
  P8 Judge — judge.passed
  P9 Ship — task.status_changed payload.to=done, plus feature done projection
            when the scenario uses features

Full mode (W5E2E-T1 shipped): adds Test Spec and GAN strictness.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PhaseResult:
    phase: str
    status: str            # pass | partial | fail | not-reached
    evidence: list[str]
    fail_reasons: list[str]


def _load_events(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    out = []
    for line in events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _count_by_type(events: list[dict]) -> Counter:
    return Counter(e.get("type", "") for e in events)


def _task_done_count(events: list[dict]) -> int:
    """Count terminal task projection using current and legacy protocols."""
    done = 0
    for event in events:
        if event.get("type") == "task.status_changed":
            if (event.get("payload") or {}).get("to") == "done":
                done += 1
        elif event.get("type") == "task.done":
            done += 1
    return done


def _done_features_from_archive(state_dir: Path) -> set[str]:
    done: set[str] = set()
    archive_dir = state_dir / "feature_list"
    if not archive_dir.exists():
        return done
    for path in sorted(archive_dir.glob("*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("status") == "done":
                done.add(str(row.get("id") or f"archive:{path.name}:{len(done)}"))
    return done


def _feature_done_count(events: list[dict], state_dir: Path) -> int:
    """Count feature done via event projection or terminal archive."""
    done: set[str] = set()
    for event in events:
        if event.get("type") != "feature.status_changed":
            continue
        payload = event.get("payload") or {}
        if payload.get("to") == "done":
            done.add(str(event.get("feature_id") or event.get("task_id") or len(done)))
    done.update(_done_features_from_archive(state_dir))
    return len(done)


def _assess_phase(
    name: str,
    required: list[str],
    counts: Counter,
    optional_warn: list[str] | None = None,
) -> PhaseResult:
    """Assess one phase. `required` events must each appear ≥1 time."""
    evidence, fails = [], []
    for event in required:
        if counts.get(event, 0) > 0:
            evidence.append(f"{event} × {counts[event]}")
        else:
            fails.append(f"missing: {event}")
    status = (
        "pass" if not fails
        else ("partial" if evidence else "not-reached")
    )
    return PhaseResult(phase=name, status=status, evidence=evidence, fail_reasons=fails)


def generate_report(events_path: Path, mode: str = "current") -> list[PhaseResult]:
    events = _load_events(events_path)
    counts = _count_by_type(events)
    state_dir = events_path.parent
    strict_w5 = mode in {"w5", "full"}

    phases: list[PhaseResult] = []

    # P0 — assume pass if session.started appeared
    phases.append(_assess_phase(
        "P0 Preflight", ["session.started"], counts,
    ))

    # P1 — user.message
    phases.append(_assess_phase("P1 Intent", ["user.message"], counts))

    # P2 — PDD / Design. In the current dev-codex topology this is no
    # longer optional: dev should not start from raw user intent.
    p2 = _assess_phase(
        "P2 PDD / Design",
        ["arch.proposal.done"],
        counts,
    )
    # W5/full mode still expects an explicit GAN lifecycle marker.
    if strict_w5 and counts.get("gan.round.started", 0) == 0:
        p2.fail_reasons.append("no gan.round.started — GAN loop skipped?")
        if p2.status == "pass":
            p2.status = "partial"
    phases.append(p2)

    p_critic = _assess_phase(
        "P3 Critic Design Gate",
        ["design.critique.done"],
        counts,
    )
    if counts.get("gate.failed", 0) > 0:
        p_critic.evidence.append(f"gate.failed × {counts['gate.failed']}")
    phases.append(p_critic)

    phases.append(_assess_phase(
        "P4 TDD / Contract",
        ["task.contract.update"],
        counts,
    ))

    if mode == "full":
        # P3 full-mode (requires W5E2E-T1 shipped)
        phases.append(_assess_phase(
            "P4.5 Test Spec",
            ["test.spec.done"],
            counts,
        ))

    # Phase: multi-dev build
    p_build = _assess_phase(
        "Build (dev)",
        ["task.assigned", "dev.build.done"],
        counts,
    )
    phases.append(p_build)

    # Phase: Review
    phases.append(_assess_phase("Review", ["review.approved"], counts))

    # Phase: Verify (gate + test + discriminator)
    verify_required = ["test.passed"]
    if strict_w5:
        verify_required.append("discriminator.passed")
    p_verify = _assess_phase(
        "Verify (gate + test + discriminator)",
        verify_required,
        counts,
    )
    if not strict_w5 and counts.get("discriminator.passed", 0) > 0:
        p_verify.evidence.append(
            f"discriminator.passed × {counts['discriminator.passed']}"
        )
    if counts.get("scope.violation", 0) > 0:
        p_verify.fail_reasons.append(
            f"scope.violation × {counts['scope.violation']} (expected 0 for clean run)"
        )
        if p_verify.status == "pass":
            p_verify.status = "partial"
    phases.append(p_verify)

    phases.append(_assess_phase("Judge", ["judge.passed"], counts))

    # Phase: Ship. Current runtime truth is task.status_changed -> done
    # plus terminal kanban archive; legacy task.done remains accepted.
    task_done = _task_done_count(events)
    feature_done = _feature_done_count(events, state_dir)
    evidence: list[str] = []
    fails: list[str] = []
    if task_done > 0:
        evidence.append(f"task done × {task_done}")
    else:
        fails.append("missing: task.status_changed payload.to=done")
    if feature_done > 0:
        evidence.append(f"feature done × {feature_done}")
    else:
        fails.append("no feature done projection (event or archive)")
    ship_status = (
        "pass" if not fails
        else ("partial" if evidence else "not-reached")
    )
    p_ship = PhaseResult(
        phase="Ship",
        status=ship_status,
        evidence=evidence,
        fail_reasons=fails,
    )
    phases.append(p_ship)

    return phases


def print_report(events_path: Path, mode: str) -> int:
    events = _load_events(events_path)
    counts = _count_by_type(events)

    print(f"=== W5-E2E Phase Report ({events_path}) ===")
    print(f"Total events: {len(events)}")
    print()

    phases = generate_report(events_path, mode=mode)

    # Pretty-print
    status_glyph = {
        "pass": "✓", "partial": "~",
        "fail": "✗", "not-reached": "–",
    }

    exit_code = 0
    for p in phases:
        glyph = status_glyph.get(p.status, "?")
        print(f"  [{glyph}] {p.phase} — {p.status}")
        for e in p.evidence:
            print(f"        · {e}")
        for r in p.fail_reasons:
            print(f"        ! {r}")
        if p.status in ("fail", "not-reached"):
            exit_code = 1

    print()
    print("--- Key metrics ---")
    # Rework ratio
    rework = counts.get("review.rejected", 0) + counts.get("test.failed", 0) + counts.get("judge.failed", 0)
    done = _task_done_count(events)
    if done > 0:
        print(f"  Rework ratio: {rework / done:.2f} (= {rework} fail / {done} done)")
    else:
        print("  Rework ratio: N/A (0 tasks done)")

    # Critical flags
    flags = {
        "scope.violation": "scope violations",
        "discriminator.failed": "discriminator fails",
        "worker.stuck": "stuck events",
        "human.escalate": "escalations",
        "review.suspended": "review SUSPEND",
        "test.suspended": "test SUSPEND",
        "hook.write_failed": "hook write failures",
        "task.rework.capped": "rework cap hits",
    }
    for event_type, label in flags.items():
        n = counts.get(event_type, 0)
        if n > 0:
            print(f"  ⚠ {label}: {n}")

    # Cost markers (agent.usage if any)
    cost_events = [e for e in events if e.get("type") == "agent.usage"]
    if cost_events:
        print(f"  agent.usage events: {len(cost_events)}")

    # Workers seen
    actors = Counter(e.get("actor", "") for e in events if e.get("actor"))
    print(f"  Distinct actors: {len(actors)} ({', '.join(sorted(actors)[:10])})")

    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser(description="W5-E2E post-run phase report")
    ap.add_argument(
        "--state-dir", type=Path,
        default=Path("/tmp/zaofu-w5e2e/.zf"),
        help="path to .zf/ directory (default /tmp/zaofu-w5e2e/.zf)",
    )
    ap.add_argument(
        "--mode", choices=["current", "codex", "mixed", "w5", "full"],
        default="current",
        help=(
            "'current' = runtime truth used by robustness runners; "
            "'codex'/'mixed' are aliases with backend-specific reports; "
            "'w5'/'full' keep legacy W5 strict discriminator/GAN checks"
        ),
    )
    args = ap.parse_args()

    events_path = args.state_dir / "events.jsonl"
    if not events_path.exists():
        print(f"ERROR: {events_path} not found. Did the run happen?", file=sys.stderr)
        return 2

    return print_report(events_path, mode=args.mode)


if __name__ == "__main__":
    sys.exit(main())
