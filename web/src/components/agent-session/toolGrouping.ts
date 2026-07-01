// Tool-run collapsing with a streaming tail. Within a contiguous run of tool
// parts, older tools fold
// into a "See N steps" summary; only the trailing STREAMING_TAIL tools of
// the live run stay expanded so the user can watch current activity. Once
// the agent emits non-tool output after the run, or the run goes idle, the
// whole run folds except still-in-progress tools.
//
// Pure functions over AgentSessionPart — unit tested in
// tests/agentToolGrouping.test.ts. The timeline applies these per run.

import type { AgentSessionPart, AgentSessionStatus } from "./types";

export const STREAMING_TAIL = 3;

const TOOL_KINDS = new Set(["tool", "tool_call", "tool_result", "command", "test_result", "file_read", "file_change"]);

export function isToolPart(part: AgentSessionPart): boolean {
  return TOOL_KINDS.has(part.kind);
}

/** A tool part still running (no output yet) — never folds even when idle. */
export function isInProgressTool(part: AgentSessionPart): boolean {
  return isToolPart(part) && (part.state === "streaming" || part.state === "submitted");
}

/**
 * If the parts end in a contiguous tool run, return its start index — that
 * run is the live activity. Otherwise -1: the agent has spoken/reasoned
 * after the most recent tools, so they're no longer "current".
 */
export function findStreamingRunStart(parts: AgentSessionPart[]): number {
  if (parts.length === 0) return -1;
  if (!isToolPart(parts[parts.length - 1]!)) return -1;
  let i = parts.length - 1;
  while (i > 0 && isToolPart(parts[i - 1]!)) i -= 1;
  return i;
}

/**
 * Split a contiguous tool run into folded ("See N steps") vs standalone.
 * For the live-streaming run, the trailing STREAMING_TAIL tools stay out;
 * for any other run, only still-in-progress tools stay out.
 */
export function partitionToolRun(
  run: AgentSessionPart[],
  isStreamingRun: boolean,
): { grouped: AgentSessionPart[]; standalone: AgentSessionPart[] } {
  if (isStreamingRun) {
    const tailStart = Math.max(0, run.length - STREAMING_TAIL);
    return { grouped: run.slice(0, tailStart), standalone: run.slice(tailStart) };
  }
  return {
    grouped: run.filter((part) => !isInProgressTool(part)),
    standalone: run.filter(isInProgressTool),
  };
}

export interface ToolRunSegment {
  kind: "tools";
  grouped: AgentSessionPart[];
  standalone: AgentSessionPart[];
  total: number;
}
export interface PartSegment {
  kind: "part";
  part: AgentSessionPart;
}
export type TimelineSegment = ToolRunSegment | PartSegment;

/**
 * Walk a run's parts, grouping contiguous tool runs and leaving everything
 * else as individual segments. `runStatus` decides whether the trailing
 * tool run keeps its streaming tail.
 */
export function segmentRunParts(
  parts: AgentSessionPart[],
  runStatus: AgentSessionStatus,
): TimelineSegment[] {
  const isAgentActive = runStatus === "streaming" || runStatus === "submitted";
  const streamingRunStart = isAgentActive ? findStreamingRunStart(parts) : -1;
  const segments: TimelineSegment[] = [];
  for (let i = 0; i < parts.length; i += 1) {
    if (isToolPart(parts[i]!)) {
      const runStart = i;
      while (i < parts.length && isToolPart(parts[i]!)) i += 1;
      const run = parts.slice(runStart, i);
      i -= 1;
      const { grouped, standalone } = partitionToolRun(run, runStart === streamingRunStart);
      segments.push({ kind: "tools", grouped, standalone, total: run.length });
      continue;
    }
    segments.push({ kind: "part", part: parts[i]! });
  }
  return segments;
}
