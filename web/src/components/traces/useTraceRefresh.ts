import { useEffect, useRef, useState } from "react";
import type { RecentEvent } from "../../api/types";
import { LoopViewRefreshScheduler } from "../delivery-trace/loopViewRefreshScheduler";

// A new generation resets bounded cursors; never merge pages from two reads.
export function useTraceRefresh(projectId: string, events: RecentEvent[], liveState: string,
  busy: boolean, sourceSeq: number) {
  const [revision, setRevision] = useState(0);
  const current = useRef({ busy, sourceSeq });
  current.current = { busy, sourceSeq };
  const dirty = useRef({ seq: 0, force: false, pending: false });
  const schedulerRef = useRef<LoopViewRefreshScheduler | null>(null);
  const seen = useRef({ projectId: "", eventKey: "", liveState });
  const event = events[0];
  const eventKey = `${event?.id ?? ""}:${event?.seq ?? ""}:${event?.type ?? ""}`;

  useEffect(() => {
    dirty.current = { seq: 0, force: false, pending: false };
    const scheduler = new LoopViewRefreshScheduler({
      debounceMs: 250, maxWaitMs: 1500,
      isVisible: () => document.visibilityState === "visible",
      refresh: () => {
        const next = dirty.current;
        if (!next.force && next.seq <= current.current.sourceSeq) { next.pending = false; return; }
        if (current.current.busy) { next.pending = true; return; }
        current.current.busy = true;
        dirty.current = { seq: 0, force: false, pending: false };
        setRevision(value => value + 1);
      },
    });
    schedulerRef.current = scheduler;
    const visible = () => scheduler.visibilityChanged();
    const timer = setInterval(() => { dirty.current.force = true; scheduler.invalidate(); }, 30_000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      scheduler.cancel(); clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      if (schedulerRef.current === scheduler) schedulerRef.current = null;
    };
  }, [projectId]);

  useEffect(() => {
    const previous = seen.current;
    const reconnect = liveState === "live" && ["reconnecting", "degraded"].includes(previous.liveState);
    const changed = previous.eventKey !== eventKey && event
      && (!event.seq || event.seq > sourceSeq || event.type === "stream.gap");
    if (previous.projectId === projectId && (changed || reconnect)) {
      dirty.current.seq = Math.max(dirty.current.seq, event?.seq ?? 0);
      dirty.current.force ||= reconnect || !event?.seq || event.type === "stream.gap";
      schedulerRef.current?.invalidate();
    }
    seen.current = { projectId, eventKey, liveState };
  }, [projectId, eventKey, liveState, sourceSeq]);

  useEffect(() => {
    if (!busy && dirty.current.pending) schedulerRef.current?.invalidate();
  }, [busy]);
  return revision;
}
