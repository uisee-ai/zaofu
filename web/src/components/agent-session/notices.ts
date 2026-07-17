// Completed-run notice filtering (frontend-stress OBS-1, 2026-07-15).
//
// A COMPLETED run keeps only REAL notices — a kind:"status" part with a
// MEANINGFUL title, e.g. the blocked-route "未自动扇出(防风暴)" / "Not routed"
// line. Progress placeholders are working-state trail: they must collapse
// into the final bubble, otherwise every round's row accumulates — visual
// noise plus unbounded DOM growth on long threads.

import type { AgentSessionPart } from "./types";

// Working-state placeholder titles (never notices on a completed run):
// "Status" is the generic delta placeholder; "Started"/"Queued" are the
// kanban turn placeholders (operator report 2026-07-16: a finished run kept
// showing "Started claude-headless"); "Working"/"Waiting"/"Sending"/"Done"
// are channel/pending progress lines.
const PROGRESS_PLACEHOLDER_TITLES = new Set([
  "Status",
  "Started",
  "Queued",
  "Working",
  "Waiting",
  "Sending",
  "Done",
]);

export function isCompletedRunNotice(part: AgentSessionPart): boolean {
  return (
    part.kind === "status"
    && Boolean(part.title)
    && !PROGRESS_PLACEHOLDER_TITLES.has(part.title)
  );
}

export function completedRunNotices(
  parts: AgentSessionPart[],
  runStatus: string,
): AgentSessionPart[] {
  if (runStatus !== "completed") return [];
  return parts.filter(isCompletedRunNotice);
}
