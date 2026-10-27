import { useEffect, useRef, useState } from "react";
import { getAgentMonitoring, type AgentMonitoring } from "../../api/client";
import type { RecentEvent } from "../../api/types";
import { LoopViewRefreshScheduler } from "../delivery-trace/loopViewRefreshScheduler";

export function useAgentMonitoring(projectId: string, events: RecentEvent[], liveState: string) {
  const [state, setState] = useState<{
    projectId: string; data: AgentMonitoring | null; loading: boolean; error: string;
  }>({ projectId: "", data: null, loading: true, error: "" });
  const lastInvalidation = useRef("");
  const latestSourceSeq = useRef(0);
  const dirtySeq = useRef(0);
  const forcePending = useRef(false);
  const schedulerRef = useRef<LoopViewRefreshScheduler | null>(null);
  const event = events[0];
  const eventKey = `${event?.id ?? ""}:${event?.seq ?? ""}:${event?.type ?? ""}`;

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let pending = false;
    let activeRequest: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    latestSourceSeq.current = 0;
    dirtySeq.current = 0;
    forcePending.current = false;
    const refresh = async () => {
      if (cancelled || !projectId) return;
      if (inFlight) { pending = true; return; }
      inFlight = true;
      forcePending.current = false;
      setState(current => ({ ...current, loading: true }));
      const controller = new AbortController();
      activeRequest = controller;
      const timeout = window.setTimeout(() => controller.abort(), 20_000);
      try {
        const data = await getAgentMonitoring(projectId, controller.signal);
        if (data.project_id !== projectId || data.status !== "ready") throw new Error("Metrics are not ready");
        if (!cancelled) {
          latestSourceSeq.current = data.source_seq;
          const refreshing = data.read_projection?.state === "refreshing";
          setState({ projectId, data, loading: refreshing, error: data.read_projection?.error || "" });
          clearTimeout(retryTimer);
          if (refreshing) retryTimer = setTimeout(() => scheduler.invalidate(), 1000);
        }
      } catch (error) {
        if (!cancelled) setState(current => ({ ...current, loading: false, error: String(error) }));
      } finally {
        window.clearTimeout(timeout);
        if (activeRequest === controller) activeRequest = null;
        inFlight = false;
        if (pending && !cancelled) {
          pending = false;
          if (forcePending.current || dirtySeq.current > latestSourceSeq.current) scheduler.invalidate();
        }
      }
    };
    const scheduler = new LoopViewRefreshScheduler({
      debounceMs: 250, maxWaitMs: 1500,
      isVisible: () => document.visibilityState === "visible", refresh: () => { void refresh(); },
    });
    schedulerRef.current = scheduler;
    setState({ projectId, data: null, loading: true, error: "" });
    const initial = setTimeout(() => { void refresh(); }, 0);
    // Also reconcile clock ages and store-only changes without a ledger event.
    const timer = setInterval(() => { if (!inFlight) scheduler.invalidate(); }, 10_000);
    const visible = () => { scheduler.visibilityChanged(); if (document.visibilityState === "visible") scheduler.invalidate(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      cancelled = true; activeRequest?.abort(); clearTimeout(initial); clearTimeout(retryTimer); clearInterval(timer); scheduler.cancel();
      document.removeEventListener("visibilitychange", visible);
      if (schedulerRef.current === scheduler) schedulerRef.current = null;
    };
  }, [projectId]);

  useEffect(() => {
    const signature = `${projectId}:${eventKey}:${liveState}`;
    if (lastInvalidation.current.startsWith(`${projectId}:`) && lastInvalidation.current !== signature) {
      const seq = Number(event?.seq ?? 0);
      dirtySeq.current = Math.max(dirtySeq.current, seq);
      if (!seq || event?.type === "stream.gap") forcePending.current = true;
      if (!seq || seq > latestSourceSeq.current || event?.type === "stream.gap") schedulerRef.current?.invalidate();
    }
    lastInvalidation.current = signature;
  }, [projectId, eventKey, liveState]);
  const visibleState = state.projectId === projectId ? state : { projectId, data: null, loading: true, error: "" };
  return { ...visibleState, retry: () => { forcePending.current = true; schedulerRef.current?.invalidate(); } };
}
