import { useCallback, useEffect, useRef, useState } from "react";
import type { RecentEvent } from "../../api/types";
import { LoopViewRefreshScheduler } from "../delivery-trace/loopViewRefreshScheduler";
import type { SdlcStructure, WorkflowGraphResult } from "./sdlcGraphModel";

// Reuse the page's event stream. Stable reads return 304 without replacing the
// graph object (and therefore without recomputing layout or losing selection).
export function useWorkflowGraph(projectId: string, requestId: string, revision: string,
  asOf: number, events: RecentEvent[], liveState: string) {
  const identity = JSON.stringify([projectId, requestId, revision, asOf]);
  const [state, setState] = useState<{ identity: string; graph: WorkflowGraphResult | null; error: string; updatedAt: string }>({
    identity, graph: null, error: "", updatedAt: "",
  });
  const invalidate = useRef<(seq: number, force: boolean) => void>(() => {});
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const requireFullRef = useRef<() => void>(() => {});
  const refreshNow = useCallback(() => refreshRef.current(), []);
  const requireFull = useCallback(() => requireFullRef.current(), []);
  const seen = useRef({ identity: "", key: "", liveState });
  const latest = events[0];
  const eventKey = `${latest?.id ?? ""}:${latest?.seq ?? ""}:${latest?.type ?? ""}`;

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let pending = false;
    let forcePending = false;
    let dirtySeq = 0;
    let sourceSeq = 0;
    let etag = "";
    let fullRequested = false;
    let structureGeneration = "";
    let structureScope: SdlcStructure["scope"] | null = null;
    let loadFullAfterStructure = false;
    let generationConflicts = 0;
    let structureFallbackUsed = false;
    let controller: AbortController | null = null;
    setState({ identity, graph: null, error: "", updatedAt: "" });

    async function refresh() {
      if (disposed) return;
      if (inFlight) { pending = true; return; }
      inFlight = true;
      pending = false;
      forcePending = false;
      const active = new AbortController();
      controller = active;
      const timeout = setTimeout(() => active.abort(), 60_000);
      const structure = !fullRequested;
      try {
        const headers: Record<string, string> = {};
        if (etag) headers["If-None-Match"] = etag;
        if (!structure && structureGeneration) headers["X-ZF-Structure-Generation"] = structureGeneration;
        const suffix = structure ? "/graph/structure" : "/graph";
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow-requests/${encodeURIComponent(requestId)}${suffix}?as_of=${asOf}`, {
          signal: active.signal, cache: "no-store", headers,
        });
        if (response.status === 409 && !structure && structureGeneration && generationConflicts++ === 0) {
          // The old structure cannot authorize or describe a new source. Fetch
          // a new structure, then retry the user's detail request once.
          fullRequested = false; structureGeneration = ""; structureScope = null; etag = "";
          loadFullAfterStructure = true; pending = true; forcePending = true;
          return;
        }
        if (response.status === 304) {
          if (!disposed) setState(current => ({ ...current, error: "", updatedAt: new Date().toLocaleTimeString() }));
        } else {
          if (!response.ok) throw new Error(`图读取失败 HTTP ${response.status}`);
          const graph = await response.json() as WorkflowGraphResult;
          if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.scope?.request_id !== requestId) {
            if (structure) { fullRequested = true; pending = true; forcePending = true; etag = ""; return; }
            throw new Error("Workflow graph identity mismatch");
          }
          const isPartial = "partial" in graph && graph.partial === true;
          if (isPartial && (!graph.generation || graph.schema_version !== "sdlc-workbench-structure.v1")) {
            throw new Error("Workflow graph structure generation missing");
          }
          if (!structure && structureScope && (
            graph.scope.revision !== structureScope.revision || graph.scope.run_id !== structureScope.run_id
            || graph.scope.proposal_digest !== structureScope.proposal_digest
            || graph.scope.head_seq !== structureScope.head_seq || dirtySeq > graph.scope.head_seq
          )) {
            if (generationConflicts++ > 0) throw new Error("Workflow graph source changed; retry.");
            fullRequested = false; structureGeneration = ""; structureScope = null; etag = "";
            loadFullAfterStructure = true; pending = true; forcePending = true;
            return;
          }
          if (!disposed) {
            sourceSeq = graph.scope.head_seq;
            etag = response.headers.get("ETag") || "";
            if (isPartial) {
              structureGeneration = graph.generation; structureScope = graph.scope;
              if (loadFullAfterStructure || fullRequested) {
                loadFullAfterStructure = false; fullRequested = true; etag = "";
                pending = true; forcePending = true;
              }
            } else {
              // Once installed, the complete graph and all its details share
              // its own generation; future updates need no partial binding.
              fullRequested = true; structureGeneration = ""; structureScope = null;
              generationConflicts = 0;
            }
            setState({ identity, graph, error: "", updatedAt: new Date().toLocaleTimeString() });
          }
        }
      } catch (reason) {
        if (!disposed && structure && !structureFallbackUsed) {
          // Older servers and temporarily unavailable structure projections
          // retain the original complete read path. Attempt it only once.
          structureFallbackUsed = true; fullRequested = true; etag = "";
          pending = true; forcePending = true;
        } else if (!disposed) setState(current => ({ ...current, error: String(reason) }));
      } finally {
        clearTimeout(timeout);
        if (controller === active) controller = null;
        inFlight = false;
        if (!disposed && pending && (forcePending || dirtySeq > sourceSeq)) scheduler.invalidate();
      }
    }
    const scheduler = new LoopViewRefreshScheduler({
      debounceMs: 250, maxWaitMs: 1500,
      isVisible: () => document.visibilityState === "visible",
      refresh: () => { void refresh(); },
    });
    const signal = (seq: number, force: boolean) => {
      if (asOf || disposed) return;
      dirtySeq = Math.max(dirtySeq, seq);
      forcePending ||= force;
      if (forcePending || dirtySeq > sourceSeq) {
        if (inFlight) pending = true;
        else scheduler.invalidate();
      }
    };
    invalidate.current = signal;
    const requestFull = () => {
      if (fullRequested) return;
      fullRequested = true; etag = ""; forcePending = true;
      if (inFlight) pending = true;
      else void refresh();
    };
    requireFullRef.current = requestFull;
    const explicitRefresh = async () => {
      if (fullRequested && structureGeneration && generationConflicts) {
        fullRequested = false; structureGeneration = ""; structureScope = null; etag = "";
        loadFullAfterStructure = true; generationConflicts = 0;
      }
      forcePending = true;
      await refresh();
    };
    refreshRef.current = explicitRefresh;
    const initial = setTimeout(() => { void refresh(); }, 0);
    const timer = asOf ? undefined : setInterval(() => signal(0, true), 30_000);
    const visible = () => {
      scheduler.visibilityChanged();
      if (document.visibilityState === "visible") signal(0, true);
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      disposed = true; controller?.abort(); clearTimeout(initial); clearInterval(timer);
      scheduler.cancel(); document.removeEventListener("visibilitychange", visible);
      if (invalidate.current === signal) invalidate.current = () => {};
      if (refreshRef.current === explicitRefresh) refreshRef.current = async () => {};
      if (requireFullRef.current === requestFull) requireFullRef.current = () => {};
    };
  }, [identity, projectId, requestId, revision, asOf]);

  useEffect(() => {
    const previous = seen.current;
    const reconnect = liveState === "live" && ["reconnecting", "degraded"].includes(previous.liveState);
    if (previous.identity === identity && (eventKey !== previous.key || reconnect)) {
      invalidate.current(Number(latest?.seq || 0), reconnect || !latest?.seq || latest.type === "stream.gap");
    }
    seen.current = { identity, key: eventKey, liveState };
  }, [identity, eventKey, liveState]);

  const visible = state.identity === identity ? state : { identity, graph: null, error: "", updatedAt: "" };
  return { ...visible, refresh: refreshNow, requireFull };
}
