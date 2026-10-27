import { useEffect, useState } from "react";
import { fetchOverviewPulse } from "../api/client";
import type { OverviewPulse } from "../api/types";

export function useOverviewPulse(projectId: string, seq?: number): OverviewPulse | null {
  const [result, setResult] = useState<{ projectId: string; value: OverviewPulse } | null>(null);
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      let delay = 30_000;
      try {
        const value = await fetchOverviewPulse(projectId);
        if (cancelled) return;
        setResult({ projectId, value });
        if (value.read_projection?.state && value.read_projection.state !== "ready") delay = 2000;
      } catch { delay = 5000; }
      if (!cancelled) timer = setTimeout(() => { void refresh(); }, delay);
    };
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [projectId, seq]);
  return result?.projectId === projectId ? result.value : null;
}
