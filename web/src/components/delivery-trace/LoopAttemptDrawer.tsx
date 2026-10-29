import { useState } from "react";

import type { LoopViewTask } from "../../api/types";
import { attemptRange, durationLabel } from "./loopTimelineModel";

const ATTEMPT_BATCH_SIZE = 100;

const TONE = {
  accent: "var(--brand, #4477dd)",
  err: "var(--err)",
  faint: "var(--text-tertiary, #889)",
  line: "var(--line)",
  muted: "var(--muted-foreground, #667)",
};

const hhmm = (iso: string) => (iso ? iso.slice(11, 16) : "");

export function LoopAttemptDrawer({ task, indices, observed, onShowAll }: {
  task: LoopViewTask; indices?: number[]; observed: number; onShowAll: () => void;
}) {
  const [visibleCount, setVisibleCount] = useState(ATTEMPT_BATCH_SIZE);
  const entries = (indices ?? task.attempts.map((_, index) => index)).map((index) => ({ attempt: task.attempts[index], index }));
  const attempts = entries.slice(0, visibleCount);
  const remaining = entries.length - attempts.length;
  const nextBatch = Math.min(ATTEMPT_BATCH_SIZE, remaining);

  return (
    <div
      className="loop-v2-attempt-drawer"
      data-testid="loop-attempt-drawer"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="loop-v2-attempt-heading">
        <strong>{indices ? indices.length === 0 ? "Selected records are no longer available" : indices.length === 1 ? task.attempts[indices[0]]?.record_kind === "signal" ? "Unpaired event" : `Attempt #${indices[0] + 1}` : `${indices.length} grouped records` : "Execution and event history"}</strong>
        {indices ? <button type="button" className="loop-timeline-show-all" onClick={onShowAll}>View all {task.attempts.length} records</button> : null}
      </div>
      <div className="loop-v2-attempt-count" data-testid="loop-attempt-count">
        Showing {attempts.length} of {entries.length} records
      </div>
      {attempts.map(({ attempt, index }) => {
        const range = attemptRange(attempt, observed);
        const fail = attempt.terminal?.type.endsWith(".failed");
        return (
          <div
            className="loop-v2-attempt-row"
            data-testid="loop-attempt-row"
            key={`${attempt.started_ts}:${index}`}
            style={{
              borderTop: index ? `1px solid ${TONE.line}` : undefined,
              fontSize: "var(--text-12)",
              opacity: attempt.record_kind === "signal" || attempt.counted ? 1 : 0.65,
            }}
          >
            <span style={{ fontFamily: "var(--font-mono, monospace)", color: TONE.faint }}>
              {attempt.record_kind === "signal" ? "Event" : `#${index + 1}${attempt.counted ? "" : " · superseded"}${attempt.orphan ? " · unknown start" : ""}`}
            </span>
            <span style={{ color: TONE.muted }}>{attempt.role || "—"}</span>
            <span style={{ color: fail ? TONE.err : attempt.open ? TONE.accent : "var(--text)" }}>
              {attempt.record_kind === "signal" ? `${hhmm(attempt.terminal?.ts || attempt.started_ts)} UTC · ${attempt.terminal?.type || "Event"}` : <>
                {attempt.orphan ? "Start unknown" : hhmm(attempt.started_ts) || "Start unknown"}{attempt.terminal ? `–${hhmm(attempt.terminal.ts)} UTC · ${attempt.terminal.type}` : " UTC · OPEN at last observation"}
                <span className="loop-attempt-duration">{range.known ? durationLabel(range.end - range.start) : "Duration unknown"}</span>
              </>}
              {attempt.terminal?.reason ? <span style={{ color: TONE.muted }}> · {attempt.terminal.reason}</span> : null}
            </span>
          </div>
        );
      })}
      {remaining > 0 ? (
        <button
          className="loop-v2-attempt-load-more"
          data-testid="loop-attempt-load-more"
          onClick={() => setVisibleCount((current) => Math.min(current + ATTEMPT_BATCH_SIZE, entries.length))}
          type="button"
        >
          Load more ({nextBatch})
        </button>
      ) : null}
    </div>
  );
}
