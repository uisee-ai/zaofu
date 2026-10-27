import { useEffect, useState } from "react";
import { getBoardTasks, type BoardTaskBootstrap } from "../api/client";
import type { Snapshot } from "../api/types";

// A separate type keeps incomplete task records out of metrics/actions snapshots.
export function useBoardBootstrap(projectId: string, enabled: boolean, snapshot: Snapshot | null) {
  const [value, setValue] = useState<BoardTaskBootstrap | null>(null);
  const complete = snapshot?.project.project_id === projectId;
  useEffect(() => {
    if (!projectId || !enabled || complete) return;
    const controller = new AbortController();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await getBoardTasks(projectId, controller.signal);
        if (!cancelled && next.project_id === projectId && next.source === "TaskStore"
          && Array.isArray(next.tasks) && Array.isArray(next.archive_tasks)) setValue(next);
      } catch { /* The full snapshot owns errors; never replace records with zeros. */ }
      if (!cancelled) timer = setTimeout(() => { void refresh(); }, 5000);
    };
    void refresh();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [projectId, enabled, complete]);
  return enabled && !complete && value?.project_id === projectId ? value : null;
}
