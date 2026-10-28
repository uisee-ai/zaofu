import { kanbanEffortLabel, resolveKanbanModelSelection } from "../src/components/orchestrator/kanbanModelSelection.js";
import type { KanbanModelCatalog } from "../src/api/client.js";

const catalog: KanbanModelCatalog = { backend: "codex-headless", models: [
  { model: "default", label: "Default", default: true, default_effort: "low", efforts: ["low", "high"] },
  { model: "other", label: "Other", default: false, default_effort: "medium", efforts: ["medium"] },
] };
function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }

assert(kanbanEffortLabel("low") === "Light" && kanbanEffortLabel("xhigh") === "Extra High", "provider effort values have readable labels");
assert(kanbanEffortLabel("new-provider-level") === "new-provider-level", "unknown provider effort levels remain visible rather than being misrepresented");

const defaultChoice = resolveKanbanModelSelection(catalog, "", "");
assert(!Object.keys(defaultChoice.request).length, "an untouched session keeps its existing provider configuration");
const selected = resolveKanbanModelSelection(catalog, "other", "medium");
assert(selected.request.model === "other" && selected.request.thinking_level === "medium", "selected settings reach the request");
const commonCatalog: KanbanModelCatalog = { backend: "codex-headless", models: [
  { model: "a", label: "A", default: true, default_effort: "low", efforts: ["low", "high"] },
  { model: "b", label: "B", default: false, default_effort: "high", efforts: ["high", "xhigh"] },
] };
const effortOnly = resolveKanbanModelSelection(commonCatalog, "", "high");
assert(effortOnly.request.thinking_level === "high" && !("model" in effortOnly.request), "effort can change without replacing the session model");
assert(effortOnly.availableEfforts.join(",") === "high", "default-model effort list contains only values supported by every listed model");
assert(resolveKanbanModelSelection(commonCatalog, "", "low").selectionUnavailable, "unsupported default-model effort fails closed");
const configuredCatalog: KanbanModelCatalog = { ...commonCatalog, session_defaults: { model: "a", effort: "low", source: "provider_config" } };
const configured = resolveKanbanModelSelection(configuredCatalog, "", "low");
assert(configured.defaultModel?.model === "a" && configured.availableEfforts.join(",") === "low,high", "the actual default model supplies its own supported efforts");
assert(configured.request.thinking_level === "low" && !("model" in configured.request), "default model remains inherited when changing its effort");
assert(!Object.keys(resolveKanbanModelSelection(configuredCatalog, "", "").request).length, "displaying a configured default must not turn it into an override");
for (const stale of [
  resolveKanbanModelSelection(catalog, "removed-model", ""),
  resolveKanbanModelSelection(catalog, "other", "high"),
  resolveKanbanModelSelection(catalog, "", "high"),
  resolveKanbanModelSelection(null, "other", "medium"),
]) {
  assert(stale.selectionUnavailable && !Object.keys(stale.request).length, "stale or unavailable selection must not silently use a different model");
}
