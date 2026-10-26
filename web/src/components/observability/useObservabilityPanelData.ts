import { useEffect, useState } from "react";
import { getIntegrationQueue, getRepairActions, getTaskPipeline } from "../../api/client";
import type { IntegrationQueueProjection, RepairActionProjection, TaskPipelineProjection } from "../../api/types";

interface PanelData {
  projectId: string;
  integrationQueue: IntegrationQueueProjection | null;
  repairActions: RepairActionProjection | null;
  taskPipeline: TaskPipelineProjection | null;
  error: string | null;
}

const empty: PanelData = { projectId: "", integrationQueue: null, repairActions: null, taskPipeline: null, error: null };

export function useObservabilityPanelData(projectId: string, tab: string, seq?: number): PanelData {
  const [data, setData] = useState<PanelData>(empty);
  useEffect(() => {
    if (!projectId || !["integration", "repair", "pipeline"].includes(tab)) return;
    let cancelled = false;
    const request: Promise<Partial<PanelData>> = tab === "integration"
      ? getIntegrationQueue(projectId).then(integrationQueue => ({ integrationQueue }))
      : tab === "repair"
        ? getRepairActions(projectId).then(repairActions => ({ repairActions }))
        : getTaskPipeline(projectId).then(taskPipeline => ({ taskPipeline }));
    void request.then(next => {
      if (!cancelled) setData(current => ({ ...(current.projectId === projectId ? current : empty), ...next, projectId, error: null }));
    }).catch(error => {
      if (!cancelled) setData(current => ({ ...(current.projectId === projectId ? current : empty), projectId, error: error instanceof Error ? error.message : String(error) }));
    });
    return () => { cancelled = true; };
  }, [projectId, tab, seq]);
  if (data.projectId !== projectId) return empty;
  return ["integration", "repair", "pipeline"].includes(tab) ? data : { ...data, error: null };
}
