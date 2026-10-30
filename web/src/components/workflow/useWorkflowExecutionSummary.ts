import { useEffect, useState } from 'react';
import type { SdlcStructure } from './sdlcGraphModel';

/** The execution projection is bound to the exact structure generation. */
export function useWorkflowExecutionSummary(projectId: string, requestId: string, asOf: number, structure: SdlcStructure | null, enabled: boolean) {
  const identity = JSON.stringify([projectId, requestId, asOf, structure?.generation || '']);
  const [state, setState] = useState<{ identity: string; summary: SdlcStructure | null; error: string; unsupported: boolean; loading: boolean }>({ identity: '', summary: null, error: '', unsupported: false, loading: false });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!enabled || !structure) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 60_000);
    let disposed = false;
    setState({ identity, summary: null, error: '', unsupported: false, loading: true });
    void (async () => {
      try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow-requests/${encodeURIComponent(requestId)}/graph/execution?as_of=${asOf}`, {
          signal: controller.signal, cache: 'no-store', headers: { 'X-ZF-Structure-Generation': structure.generation },
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
        if (!summary.execution_summary || summary.generation !== structure.generation ||
            summary.scope?.request_id !== requestId || summary.scope.revision !== structure.scope.revision || summary.scope.run_id !== structure.scope.run_id ||
            summary.scope.proposal_digest !== structure.scope.proposal_digest || summary.scope.head_seq !== structure.scope.head_seq ||
            !Array.isArray(summary.nodes) || !Array.isArray(summary.edges)) {
          throw new Error('执行摘要来源不一致；请重试');
        }
        if (!disposed) setState({ identity, summary, error: '', unsupported: false, loading: false });
      } catch (reason) {
        if (!disposed) setState({ identity, summary: null, error: String(reason), unsupported: false, loading: false });
      } finally { clearTimeout(timer); }
    })();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, [identity, enabled, structure, projectId, requestId, asOf, retry]);
  const visible = enabled && state.identity === identity ? state : { identity, summary: null, error: '', unsupported: false, loading: false };
  return { ...visible, retry: () => setRetry(value => value + 1) };
}
