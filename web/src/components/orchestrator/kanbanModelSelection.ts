import type { KanbanModelCatalog } from "../../api/client";

export function kanbanEffortLabel(value: string): string {
  return ({ minimal: "Minimal", low: "Light", medium: "Medium", high: "High", xhigh: "Extra High" } as Record<string, string>)[value] || value;
}

export function resolveKanbanModelSelection(catalog: KanbanModelCatalog | null, model: string, effort: string) {
  const selected = catalog?.models.find((item) => item.model === model);
  const defaultModel = catalog?.models.find((item) => item.model === catalog.session_defaults?.model);
  const commonEfforts = catalog?.models.length
    ? catalog.models[0].efforts.filter((value) => catalog.models.every((item) => item.efforts.includes(value))) : [];
  const availableEfforts = selected?.efforts ?? (model ? [] : defaultModel?.efforts ?? commonEfforts);
  const validEffort = availableEfforts.includes(effort) ? effort : "";
  const selectionUnavailable = Boolean((model || effort) && (
    !catalog || (model && !catalog.models.some((item) => item.model === model))
    || (effort && !validEffort)
  ));
  const request: { model?: string; thinking_level?: string } = !selectionUnavailable && (selected || validEffort) ? {
    ...(selected ? { model: selected.model } : {}),
    thinking_level: validEffort || (selected?.efforts.includes(selected.default_effort) ? selected.default_effort : ""),
  } : {};
  return {
    selected,
    defaultModel,
    availableEfforts,
    validEffort,
    selectionUnavailable,
    request,
  };
}
