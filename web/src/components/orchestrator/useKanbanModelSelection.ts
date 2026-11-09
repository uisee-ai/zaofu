import { useEffect, useState } from "react";
import { getKanbanModelCatalog, KanbanModelCatalogError, type KanbanModelCatalog } from "../../api/client";
import { resolveKanbanModelSelection } from "./kanbanModelSelection";

function savedChoice(key: string): { model: string; effort: string } {
  try {
    const value = JSON.parse(window.localStorage.getItem(`zf.kanbanModel.v1:${key}`) || "null");
    return { model: typeof value?.model === "string" ? value.model : "", effort: typeof value?.effort === "string" ? value.effort : "" };
  } catch { return { model: "", effort: "" }; }
}

export function useKanbanModelSelection(projectId: string, threadId: string, backend: string, enabled: boolean, taskId = "") {
  const [catalog, setCatalog] = useState<KanbanModelCatalog | null>(null);
  const [catalogIdentity, setCatalogIdentity] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("unavailable");
  const [authorizationRejected, setAuthorizationRejected] = useState(false);
  const [choices, setChoices] = useState<Record<string, { model: string; effort: string }>>({});
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([projectId, threadId, backend]);
  const identity = JSON.stringify([projectId, threadId, taskId]);
  const { model, effort } = choices[key] ?? savedChoice(key);

  useEffect(() => {
    setCatalog(null);
    setCatalogIdentity("");
    setAuthorizationRejected(false);
    if (!enabled || backend !== "codex-headless") {
      setStatus("unavailable");
      return undefined;
    }
    const controller = new AbortController();
    setStatus("loading");
    void getKanbanModelCatalog(projectId, threadId, taskId, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setCatalog(value);
      setCatalogIdentity(identity);
      setStatus("ready");
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setAuthorizationRejected(error instanceof KanbanModelCatalogError && (error.status === 401 || error.status === 403));
        setStatus("unavailable");
      }
    });
    return () => controller.abort();
  }, [projectId, threadId, taskId, backend, enabled, revision]);

  const currentCatalog = backend === "codex-headless" && enabled && catalogIdentity === identity ? catalog : null;
  const { selected, defaultModel, availableEfforts, validEffort, selectionUnavailable, request } = resolveKanbanModelSelection(currentCatalog, model, effort);
  const update = (next: { model: string; effort: string }) => {
    try { window.localStorage.setItem(`zf.kanbanModel.v1:${key}`, JSON.stringify(next)); } catch { /* keep in memory */ }
    setChoices((current) => ({ ...current, [key]: next }));
  };
  return {
    catalog: currentCatalog, status: currentCatalog ? status : status === "ready" ? "loading" : status,
    model: currentCatalog?.models.some((item) => item.model === model) ? model : "",
    effort: validEffort, selected, availableEfforts,
    defaultModel: defaultModel?.label || currentCatalog?.session_defaults?.model || "",
    defaultEffort: selected ? selected.default_effort : currentCatalog?.session_defaults?.effort || "",
    defaultSource: currentCatalog?.session_defaults?.source || "unknown", authorizationRejected,
    setModel: (value: string) => update({ model: value, effort: resolveKanbanModelSelection(currentCatalog, value, effort).validEffort }),
    setEffort: (value: string) => update({ model, effort: value }),
    clear: () => update({ model: "", effort: "" }),
    reload: () => setRevision((value) => value + 1),
    // The server preserves and restores the provider session baseline.
    request,
    customized: Boolean(model || effort),
    selectionUnavailable,
  };
}
