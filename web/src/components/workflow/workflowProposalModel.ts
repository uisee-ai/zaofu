export type WorkflowRequest = Record<string, unknown>;
export type WorkflowView = "decision" | "active" | "history";

export const WORKFLOW_VIEWS: { id: WorkflowView; label: string }[] = [
  { id: "decision", label: "Needs decision" },
  { id: "active", label: "Active" },
  { id: "history", label: "History" },
];

export type DiagnosticGroup = {
  count: number;
  key: string;
  kind: string;
  message: string;
  severity: "STOP" | "WARN" | "INFO";
};

export function workflowViewForRequest(request: WorkflowRequest): WorkflowView {
  const status = textValue(request.status).toLowerCase();
  const operationStatus = textValue(asRecord(request.operation).queue_status).toLowerCase();
  if (["proposed", "draft", "ready", "clarifying"].includes(status)) return "decision";
  if (
    ["queued", "running", "submitted", "approved", "paused", "in_progress"].includes(status)
    || ["queued", "running"].includes(operationStatus)
  ) {
    return "active";
  }
  return "history";
}

export function readinessPresentation({
  blockerCount,
  requestStatus,
  runStatus,
  terminal,
}: {
  blockerCount: number;
  requestStatus: string;
  runStatus: string;
  terminal: string;
}): { title: string; tone: "error" | "info" | "ok" } {
  if (blockerCount > 0) return { title: "Needs changes before approval", tone: "error" };
  if (terminal) return { title: "Run completed", tone: "ok" };
  if (runStatus === "paused") return { title: "Run paused", tone: "info" };
  if (runStatus === "running" || requestStatus === "running") {
    return { title: "Run in progress", tone: "info" };
  }
  if (runStatus === "queued" || requestStatus === "queued") {
    return { title: "Run queued", tone: "info" };
  }
  if (["submitted", "approved"].includes(requestStatus)) {
    return { title: "Run submitted", tone: "info" };
  }
  if (requestStatus === "clarifying") {
    return { title: "Needs clarification", tone: "error" };
  }
  if (requestStatus === "draft") {
    return { title: "Needs confirmation", tone: "info" };
  }
  if (requestStatus === "ready") {
    return { title: "Ready to prepare proposal", tone: "info" };
  }
  return { title: "Ready to run", tone: "ok" };
}

export function numberValue(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(textValue(value));
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function diagnosticSeverity(item: WorkflowRequest): "STOP" | "WARN" | "INFO" {
  const severity = textValue(item.severity).toUpperCase();
  if (severity === "STOP" || severity === "WARN") return severity;
  return "INFO";
}

export function groupDiagnostics(
  items: WorkflowRequest[],
  fallbackSeverity: "STOP" | "WARN" | "INFO",
): DiagnosticGroup[] {
  const groups = new Map<string, DiagnosticGroup>();
  for (const item of items) {
    const severity = textValue(item.severity)
      ? diagnosticSeverity(item)
      : fallbackSeverity;
    const kind = textValue(item.kind) || textValue(item.title) || "diagnostic";
    const message = textValue(item.message) || textValue(item.reason) || "No detail provided.";
    const key = `${severity}:${kind}:${message}`;
    const current = groups.get(key);
    if (current) {
      current.count += 1;
    } else {
      groups.set(key, { count: 1, key, kind, message, severity });
    }
  }
  return [...groups.values()];
}

export function stagesForCurrentFlow(
  stages: WorkflowRequest[],
  shortFlowSpec: WorkflowRequest,
  requestKind: string,
  flowFamily: string,
): WorkflowRequest[] {
  if (!stages.length) return [];
  const targetKind = normalizedFlowKind(requestKind && requestKind !== "auto" ? requestKind : flowFamily);
  const documents = asRecordArray(shortFlowSpec.documents);
  const targetDocument = documents.find(
    (document) => normalizedFlowKind(textValue(document.kind)) === targetKind,
  );
  const targetSpec = asRecord(asRecord(targetDocument).spec);
  const genericWorkflowSpec = asRecord(shortFlowSpec.generic_workflow_spec);
  const declaredTasks = targetKind === "workflow" && asRecordArray(genericWorkflowSpec.tasks).length
    ? asRecordArray(genericWorkflowSpec.tasks)
    : asRecordArray(targetSpec.tasks);
  const taskIds = new Set(
    declaredTasks
      .map((task) => textValue(task.name))
      .filter(Boolean),
  );
  if (taskIds.size) {
    const declared = stages.filter((stage) => taskIds.has(textValue(stage.id)));
    if (declared.length) return declared;
  }
  const prefix = targetKind === "prd"
    ? "prd-"
    : targetKind === "refactor"
      ? "refactor-"
      : targetKind === "issue"
        ? "issue-"
        : "";
  if (prefix) {
    const matching = stages.filter((stage) => textValue(stage.id).startsWith(prefix));
    if (matching.length) return matching;
  }
  return stages;
}

function normalizedFlowKind(value: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (normalized === "workflow") return normalized;
  return normalized.endsWith("flow") ? normalized.slice(0, -4) : normalized;
}

export function stageLevels(stages: WorkflowRequest[]): WorkflowRequest[][] {
  if (!stages.length) return [];
  const ids = new Set(stages.map((stage) => textValue(stage.id)).filter(Boolean));
  const levels = new Map<string, number>();
  const unresolved = new Set(stages.map((stage) => textValue(stage.id)).filter(Boolean));
  while (unresolved.size) {
    let progressed = false;
    for (const stage of stages) {
      const id = textValue(stage.id);
      if (!unresolved.has(id)) continue;
      const dependencies = asStringArray(stage.dependencies).filter((dependency) => ids.has(dependency));
      if (dependencies.some((dependency) => !levels.has(dependency))) continue;
      levels.set(
        id,
        dependencies.length
          ? Math.max(...dependencies.map((dependency) => levels.get(dependency) ?? 0)) + 1
          : 0,
      );
      unresolved.delete(id);
      progressed = true;
    }
    if (!progressed) {
      for (const id of unresolved) levels.set(id, 0);
      break;
    }
  }
  const grouped: WorkflowRequest[][] = [];
  for (const stage of stages) {
    const level = levels.get(textValue(stage.id)) ?? 0;
    (grouped[level] ??= []).push(stage);
  }
  return grouped.filter(Boolean);
}

export function stageRoleLabel(stage: WorkflowRequest): string {
  const roles = asStringArray(stage.roles);
  if (roles.length > 1) return `${roles.length} agents`;
  if (roles.length === 1) return roles[0];
  return textValue(stage.topology) || "direct";
}

export function expectedOutputLabel(output: WorkflowRequest): string {
  const kind = textValue(output.kind);
  if (kind === "report/markdown") return "Verified Markdown report";
  const name = textValue(output.name);
  return [name, kind].filter(Boolean).join(" / ") || "Verified delivery artifact";
}

export function requestTitle(request: WorkflowRequest): string {
  return textValue(request.objective)
    || textValue(request.title)
    || textValue(request.request_id)
    || "Workflow request";
}

export function shortDigest(value: string): string {
  return value ? value.slice(0, 12) : "-";
}

function asRecord(value: unknown): WorkflowRequest {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as WorkflowRequest
    : {};
}

function asRecordArray(value: unknown): WorkflowRequest[] {
  return Array.isArray(value)
    ? value.filter((item): item is WorkflowRequest => (
      Boolean(item) && typeof item === "object" && !Array.isArray(item)
    ))
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => textValue(item)).filter(Boolean)
    : [];
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}
