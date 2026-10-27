import { useCallback, useEffect, useRef, useState } from "react";
import type { SdlcNode, SdlcStructure } from "./sdlcGraphModel";

/** The selected compiled node is bound to the exact structure generation. */
export function useWorkflowStructureNode(projectId: string, requestId: string, asOf: number,
  structure: SdlcStructure | null, nodeId: string, onStale: () => Promise<void>) {
  const generation = structure?.generation || "";
  const key = JSON.stringify([projectId, requestId, asOf, generation, nodeId]);
  const selection = JSON.stringify([projectId, requestId, asOf, nodeId]);
  const [state, setState] = useState<{ key: string; node: SdlcNode | null; error: string }>({ key: "", node: null, error: "" });
  const [attempt, setAttempt] = useState(0);
  const retries = useRef({ selection: "", count: 0 });
  const retry = useCallback(() => {
    retries.current = { selection, count: 0 };
    setState({ key, node: null, error: "" });
    setAttempt(value => value + 1);
  }, [key, selection]);
  useEffect(() => {
    if (!structure || !nodeId || !structure.nodes.some(node => node.id === nodeId)) return;
    if (retries.current.selection !== selection) retries.current = { selection, count: 0 };
    const controller = new AbortController();
    let cancelled = false;
    const params = new URLSearchParams({ node_id: nodeId, as_of: String(asOf) });
    void fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow-requests/${encodeURIComponent(requestId)}/graph/node?${params}`, {
      signal: controller.signal, cache: "no-store", headers: { "X-ZF-Structure-Generation": generation },
    }).then(async response => {
      if (response.status === 409) {
        if (!cancelled && retries.current.count < 1) {
          retries.current.count += 1;
          setState({ key, node: null, error: "流程已更新，正在重新读取。" });
          void onStale();
        } else if (!cancelled) setState({ key, node: null, error: "节点持续更新，请重试。" });
        return;
      }
      if (!response.ok) throw new Error(`节点详情读取失败 HTTP ${response.status}`);
      const value = await response.json() as { scope: SdlcStructure["scope"]; node: SdlcNode };
      if (value.scope?.request_id !== requestId || value.node?.id !== nodeId
        || value.scope.revision !== structure.scope.revision || value.scope.run_id !== structure.scope.run_id
        || value.scope.proposal_digest !== structure.scope.proposal_digest
        || value.scope.head_seq !== structure.scope.head_seq || value.scope.source_seq !== structure.scope.source_seq) {
        throw new Error("节点详情版本与流程结构不一致");
      }
      if (!cancelled) {
        retries.current.count = 0;
        setState({ key, node: value.node, error: "" });
      }
    }).catch(reason => { if (!cancelled) setState({ key, node: null, error: String(reason) }); });
    return () => { cancelled = true; controller.abort(); };
  }, [projectId, requestId, asOf, generation, nodeId, onStale, selection, attempt]);
  return { ...(state.key === key ? state : { key, node: null, error: "" }), retry };
}
