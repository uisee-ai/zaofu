import { useState } from "react";

import type { LoopViewTask } from "../../api/types";
import { formatTime } from "../../lib/format";

const ATTEMPT_BATCH_SIZE = 100;

const TONE = {
  accent: "var(--brand, #4477dd)",
  err: "var(--err)",
  faint: "var(--text-tertiary, #889)",
  line: "var(--line)",
  muted: "var(--muted-foreground, #667)",
};

const hhmm = (iso: string) => formatTime(iso, false);

export function LoopAttemptDrawer({ task }: { task: LoopViewTask }) {
  const [visibleCount, setVisibleCount] = useState(ATTEMPT_BATCH_SIZE);
  const attempts = task.attempts.slice(0, visibleCount);
  const remaining = task.attempts.length - attempts.length;
  const nextBatch = Math.min(ATTEMPT_BATCH_SIZE, remaining);

  return (
    <div
      className="loop-v2-attempt-drawer"
      data-testid="loop-attempt-drawer"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="loop-v2-attempt-heading" style={{ fontSize: 10 }}>
        attempt projection · {task.source} · verbatim, read-only
      </div>
      <div className="loop-v2-attempt-count" data-testid="loop-attempt-count" style={{ fontSize: 10.5 }}>
        Showing {attempts.length} of {task.attempts.length} attempts
      </div>
      {attempts.map((attempt, index) => {
        const fail = attempt.terminal?.type.endsWith(".failed");
        return (
          <div
            className="loop-v2-attempt-row"
            data-testid="loop-attempt-row"
            key={`${attempt.started_ts}:${index}`}
            style={{
              borderTop: index ? `1px solid ${TONE.line}` : undefined,
              fontSize: 11.5,
              opacity: attempt.counted ? 1 : 0.55,
            }}
          >
            <span style={{ fontFamily: "var(--font-mono, monospace)", color: TONE.faint }}>
              #{index + 1}{attempt.counted ? "" : " u"}{attempt.orphan ? " ?" : ""}
            </span>
            <span style={{ color: TONE.muted }}>{attempt.role || "—"}</span>
            <span style={{ color: fail ? TONE.err : attempt.open ? TONE.accent : "var(--text)" }}>
              {hhmm(attempt.started_ts)}{attempt.terminal ? `–${hhmm(attempt.terminal.ts)} · ${attempt.terminal.type}` : " · OPEN"}
              {attempt.terminal?.reason ? <span style={{ color: TONE.muted }}> · {attempt.terminal.reason}</span> : null}
            </span>
          </div>
        );
      })}
      {remaining > 0 ? (
        <button
          className="loop-v2-attempt-load-more"
          data-testid="loop-attempt-load-more"
          onClick={() => setVisibleCount((current) => Math.min(current + ATTEMPT_BATCH_SIZE, task.attempts.length))}
          type="button"
        >
          Load more ({nextBatch})
        </button>
      ) : null}
    </div>
  );
}
