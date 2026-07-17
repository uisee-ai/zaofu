// Pure helpers behind the live run indicator ("Thinking · 12s") and the live
// tool-call counter shown while a run streams (stream-ux axes 2/3). No
// React/DOM here — unit tested in tests/liveRunIndicator.test.ts.

import type { AgentSessionPart, AgentSessionRun } from "./types.js";
import { isToolPart } from "./toolGrouping.js";

/** Elapsed label: "12s" under a minute, "1m 23s" from there on. */
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

/** Seconds since an ISO timestamp; undefined when missing or unparseable. */
export function elapsedSecondsSince(startedAt: string | undefined, nowMs: number): number | undefined {
  if (!startedAt) return undefined;
  const started = Date.parse(startedAt);
  if (!Number.isFinite(started)) return undefined;
  return Math.max(0, (nowMs - started) / 1000);
}

/**
 * Best available start timestamp for a run's elapsed timer: the run's own
 * startedAt when recorded, else the earliest part timestamp (channel runs
 * carry no run-level startedAt — their first status/tool part lands at run
 * start). ISO strings compare lexicographically.
 */
export function runStartTimestamp(run: Pick<AgentSessionRun, "startedAt" | "parts">): string | undefined {
  if (run.startedAt) return run.startedAt;
  let earliest: string | undefined;
  for (const part of run.parts) {
    const candidate = part.startedAt || part.updatedAt;
    if (candidate && (!earliest || candidate < earliest)) earliest = candidate;
  }
  return earliest;
}

/**
 * Ordinal of the current tool call: counts tool invocations, not their
 * result parts. The kanban projection emits a tool_result as kind "tool"
 * with a `tool-result-*` id, so both signals are checked.
 */
export function toolCallCount(parts: AgentSessionPart[]): number {
  return parts.filter((part) => (
    isToolPart(part) && part.kind !== "tool_result" && !part.id.startsWith("tool-result-")
  )).length;
}
