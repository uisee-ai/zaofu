import type { PageId, ProjectionKind } from "./sharedTypes";
import { isObservabilitySnapshotPage } from "./pageLoadPolicy.js";

export interface CompatiblePageQuery {
  changed: boolean;
  page: string;
  params: URLSearchParams;
}

export interface ProjectionPageSelection {
  id: string;
  kind: ProjectionKind;
}

export function canBootstrapScopedPageBeforeWorkspace(
  page: PageId,
  projectId: string,
): boolean {
  return (page === "traces" || page === "project" || page === "board" || isObservabilitySnapshotPage(page)) && Boolean(projectId.trim());
}

const OBSERVABILITY_COMPATIBILITY_TABS = new Set([
  "logs",
  "operations",
  "pipeline",
  "integration",
  "repair",
]);

const OBSERVABILITY_PAGE_ALIASES: Partial<Record<string, PageId>> = {
  traces: "traces",
  events: "events",
  runs: "runs",
  fanouts: "fanouts",
  candidates: "candidates",
};

const PROJECTION_PAGE: Record<ProjectionKind, PageId> = {
  trace: "traces",
  run: "runs",
  fanout: "fanouts",
  candidate: "candidates",
};

const PROJECTION_QUERY_KEY: Record<ProjectionKind, string> = {
  trace: "trace_id",
  run: "run_id",
  fanout: "fanout_id",
  candidate: "pdd_id",
};

const PROJECTION_QUERY_KEYS = new Set(Object.values(PROJECTION_QUERY_KEY));

function moveLegacyParam(
  params: URLSearchParams,
  legacyKey: string,
  canonicalKey: string,
): void {
  const legacyValue = params.get(legacyKey);
  if (!params.has(canonicalKey) && legacyValue) {
    params.set(canonicalKey, legacyValue);
  }
  params.delete(legacyKey);
}

/**
 * Resolve historical Web routes before page resource planning starts.
 *
 * The returned params are a clone. Callers may safely persist them without
 * mutating the URLSearchParams instance supplied by the browser or a test.
 */
export function resolvePageCompatibility(source: URLSearchParams): CompatiblePageQuery {
  const before = source.toString();
  const params = new URLSearchParams(source);
  let page = params.get("page") ?? "";

  if (page === "roles") page = "agents";
  else if (page === "workflow") page = "workflows";
  else if (page === "goal-coverage") page = "delivery-graph";
  else if (page === "process" || page === "runtime" || page === "control-room") {
    page = "observability";
    params.set("obs_tab", "operations");
  } else if (page === "diagnostics") {
    page = "events";
  } else if (page === "workdirs" || page === "skills" || page === "archives") {
    page = "traces";
  } else if (page === "observability") {
    const tab = params.get("obs_tab") ?? "";
    const alias = OBSERVABILITY_PAGE_ALIASES[tab];
    if (alias) {
      page = alias;
    } else if (tab === "runtime_logs") {
      params.set("obs_tab", "operations");
    } else if (tab === "raw" || tab === "diagnostics") {
      page = "events";
    } else if (!OBSERVABILITY_COMPATIBILITY_TABS.has(tab)) {
      page = "traces";
    }
  }

  if (page === "traces") moveLegacyParam(params, "obs_trace", "trace_id");
  if (page === "events") moveLegacyParam(params, "obs_task", "task");
  if (page === "runs") moveLegacyParam(params, "obs_run_id", "run_id");
  if (page !== "observability") params.delete("obs_tab");
  if (page) params.set("page", page);
  else params.delete("page");

  return {
    changed: params.toString() !== before,
    page,
    params,
  };
}

export function projectionPageQuery(
  source: URLSearchParams,
  kind: ProjectionKind,
  id: string,
): CompatiblePageQuery {
  const before = source.toString();
  const params = new URLSearchParams(source);
  const page = PROJECTION_PAGE[kind];
  for (const key of PROJECTION_QUERY_KEYS) params.delete(key);
  params.delete("obs_trace");
  params.delete("obs_task");
  params.delete("obs_run_id");
  params.delete("obs_tab");
  params.set("page", page);
  params.set(PROJECTION_QUERY_KEY[kind], id);
  return {
    changed: params.toString() !== before,
    page,
    params,
  };
}

export function projectionSelectionForPage(
  page: PageId,
  params: URLSearchParams,
): ProjectionPageSelection | null {
  const entry = (Object.entries(PROJECTION_PAGE) as Array<[ProjectionKind, PageId]>)
    .find(([, candidatePage]) => candidatePage === page);
  if (!entry) return null;
  const [kind] = entry;
  const id = params.get(PROJECTION_QUERY_KEY[kind]) ?? "";
  return id ? { id, kind } : null;
}
