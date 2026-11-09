import { useEffect, useRef, useState } from 'react';
import type { SdlcStructure } from './sdlcGraphModel';

/** The execution projection is bound to the exact structure generation. */
export function useWorkflowExecutionSummary(projectId: string, requestId: string, asOf: number, structure: SdlcStructure | null, enabled: boolean) {
  const identity = JSON.stringify([projectId, requestId, asOf, structure?.scope.revision, structure?.scope.run_id, structure?.scope.proposal_digest]);
  const latest = useRef(structure);
  latest.current = structure;
  const refresh = useRef<() => void>(() => {});
  const [state, setState] = useState<{ identity: string; summary: SdlcStructure | null; error: string; unsupported: boolean; loading: boolean }>({ identity: '', summary: null, error: '', unsupported: false, loading: false });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let controller: AbortController | null = null;
    let disposed = false;
    let inFlight = false;
    let completedGeneration = '';
    async function read() {
      const requested = latest.current;
      if (disposed || inFlight || !requested || requested.generation === completedGeneration) return;
      inFlight = true;
      const active = new AbortController();
      controller = active;
      const timer = setTimeout(() => active.abort(), 60_000);
      setState(current => ({ identity, summary: current.identity === identity ? current.summary : null, error: '', unsupported: false, loading: true }));
      try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow-requests/${encodeURIComponent(requestId)}/graph/execution?as_of=${asOf}`, {
          signal: active.signal, cache: 'no-store', headers: { 'X-ZF-Structure-Generation': requested.generation },
        });
        if (response.status === 404 || response.status === 405) {
          if (!disposed) setState({ identity, summary: null, error: '', unsupported: true, loading: false });
          return;
        }
        if (!response.ok) throw new Error(`执行摘要读取失败 HTTP ${response.status}`);
        const summary = await response.json() as SdlcStructure;
        if (summary.schema_version !== 'sdlc-workbench-execution.v1') {
          // Older servers may answer a generic route with another schema.
          if (!disposed) setState({ identity, summary: null, error: '', unsupported: true, loading: false });
          return;
        }
        if (!summary.execution_summary || summary.generation !== requested.generation ||
            summary.scope?.request_id !== requestId || summary.scope.revision !== requested.scope.revision || summary.scope.run_id !== requested.scope.run_id ||
            summary.scope.proposal_digest !== requested.scope.proposal_digest || summary.scope.head_seq !== requested.scope.head_seq ||
            !Array.isArray(summary.nodes) || !Array.isArray(summary.edges)) {
          throw new Error('执行摘要来源不一致；请重试');
        }
        if (!disposed) setState({ identity, summary, error: '', unsupported: false, loading: false });
      } catch (reason) {
        if (!disposed) setState(current => ({ ...current, identity, error: String(reason), unsupported: false, loading: false }));
      } finally {
        clearTimeout(timer);
        inFlight = false;
        completedGeneration = requested.generation;
        // Keep the completed, internally consistent snapshot visible. New
        // generations coalesce instead of cancelling every slow live read.
        if (!disposed && latest.current?.generation !== requested.generation) void read();
      }
    }
    refresh.current = () => { void read(); };
    void read();
    return () => { disposed = true; controller?.abort(); refresh.current = () => {}; };
  }, [identity, enabled, projectId, requestId, asOf, retry]);
  useEffect(() => { refresh.current(); }, [structure?.generation]);
  const visible = enabled && state.identity === identity ? state : { identity, summary: null, error: '', unsupported: false, loading: false };
  return { ...visible, retry: () => setRetry(value => value + 1) };
}
