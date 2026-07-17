// Live "agent is working" indicator (stream-ux axis 2): pulsing dots + an
// elapsed timer, rendered as the run header's single status line while a
// working run has no reply text yet (dot/member/state on ONE line — the
// stacked dot-then-indicator layout read as broken). Ticks once per second;
// falls back to first-seen time when the run carries no usable start
// timestamp.

import { useEffect, useRef, useState } from "react";
import { elapsedSecondsSince, formatElapsed } from "./liveRunIndicator";

export function ThinkingIndicator({ label = "Thinking", startedAt, who }: { label?: string; startedAt?: string; who?: string }) {
  const firstSeenMs = useRef(Date.now());
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const seconds = elapsedSecondsSince(startedAt, nowMs) ?? Math.max(0, (nowMs - firstSeenMs.current) / 1000);
  const elapsed = formatElapsed(seconds);
  // Single animated element (operator review): the shimmering label carries
  // the semantics (Thinking/Working) and anchors the timer — no extra dot
  // wave competing with it.
  return (
    <div aria-label={`${label}, ${elapsed} elapsed`} className="agent-thinking-indicator" role="status">
      {who ? <span className="agent-run-who">@{who}</span> : null}
      <span className="agent-shimmer">{label}</span>
      <span className="mono muted">· {elapsed}</span>
    </div>
  );
}
