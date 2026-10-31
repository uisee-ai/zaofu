import { useEffect, useState } from "react";
import type { PlanPreview } from "../../api/types";
import { planArtifactUrl, reviewPorts } from "./planReviewModel";
import type { ReviewArtifacts, ReviewPort } from "./planReviewModel";

/** Frozen bindings are the cache key. Detailed trace ports load on demand. */
export function usePlanArtifacts(preview: PlanPreview, projectId: string, detailed: boolean) {
  const bindings = JSON.stringify(preview.artifacts?.filter(p => reviewPorts.includes(p.name as ReviewPort)) || []);
  const [cache, setCache] = useState<{ key: string; artifacts: ReviewArtifacts }>({ key: "", artifacts: {} });
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const key = `${projectId}:${preview.plan_id}:${bindings}`;
  useEffect(() => {
    const controller = new AbortController();
    const saved = cache.key === key ? cache.artifacts : {};
    const ports = JSON.parse(bindings) as Array<{ name: ReviewPort }>;
    const names = ports.map(p => p.name).filter(name => (detailed || ["task_map", "execution_spec", "source_inventory"].includes(name)) && !(name in saved));
    setErrors([]); setLoading(names.length > 0);
    void Promise.all(names.map(async name => {
      try {
        const response = await fetch(planArtifactUrl(projectId, preview.plan_id, name), { signal: controller.signal, headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value: unknown = await response.json();
        if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("附件不是对象");
        return { name, value };
      } catch (error) { return { name, error: error instanceof Error ? error.message : "读取失败" }; }
    })).then(results => {
      if (controller.signal.aborted) return;
      const artifacts = { ...saved };
      const failures: string[] = [];
      results.forEach(result => { if ("error" in result) failures.push(`${result.name}：${result.error}`); else artifacts[result.name] = result.value; });
      setCache({ key, artifacts }); setErrors(failures); setLoading(false);
    });
    return () => controller.abort();
    // Cache updates are results, not a reason to restart in-flight reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, detailed]);
  return { artifacts: cache.key === key ? cache.artifacts : {}, errors, loading };
}
