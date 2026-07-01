import type { AgentSummary, CostSummary } from "../api/types";

export type CockpitTone = "ok" | "warn" | "err" | "info" | "muted";

export interface AttentionRow {
  severity: "err" | "warn" | "info";
  domain: "agent" | "delivery" | "runtime" | "task" | "recovery";
  target: string;
  reason: string;
  recommended_action: string;
  evidence: string;
  source_projection: string;
}

export interface FleetMetrics {
  backendWorkers: number;
  controlAgents: number;
  contextWarn: number;
  drift: number;
  healthy: number;
  maxContext: number | null;
  operatorAgents: number;
  providerSummary: string;
  silent: number;
  stuck: number;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  workerAgents: number;
}

export interface RoleFleetRow {
  role: string;
  workers: number;
  backend: string;
  active_tasks: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  usd: number;
  max_context: number | null;
  attention: number;
}

export function isOperatorAgent(agent: AgentSummary): boolean {
  return agent.agent_kind === "web_surface" || agent.parent_role === "kanban-agent";
}

export function isControlAgent(agent: AgentSummary): boolean {
  return !isOperatorAgent(agent) && (
    agent.agent_kind === "control"
    || agent.parent_role === "orchestrator"
    || agent.role_type === "orchestrator"
    || agent.parent_role === "supervisor"
  );
}

export function isBackendWorker(agent: AgentSummary): boolean {
  return !isOperatorAgent(agent);
}

export function agentClassLabel(agent: AgentSummary): "operator" | "control" | "worker" {
  if (isOperatorAgent(agent)) return "operator";
  if (isControlAgent(agent)) return "control";
  return "worker";
}

export function needsAttention(value?: string | null): boolean {
  return !["", "idle", "working", "completed_verified"].includes(value ?? "");
}

export function buildFleetMetrics(
  agents: AgentSummary[],
  agentCockpit: Record<string, unknown> | null,
  cost: CostSummary | null,
): FleetMetrics {
  const backendAgents = agents.filter(isBackendWorker);
  const summary = asRecord(agentCockpit?.summary);
  const providers = new Map<string, number>();
  let maxContext: number | null = null;
  let healthy = 0;
  for (const agent of backendAgents) {
    if (agent.backend) providers.set(agent.backend, (providers.get(agent.backend) ?? 0) + 1);
    if (typeof agent.context_usage_ratio === "number") {
      maxContext = Math.max(maxContext ?? 0, agent.context_usage_ratio);
    }
    if (["healthy", "running", "running_task", "working"].includes(agent.lifecycle_state || agent.attention_state || "")) {
      healthy += 1;
    }
  }
  const providerSummary = [...providers.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([provider, count]) => `${provider} x${count}`)
    .join(", ") || "-";
  const totalInputTokens = Object.values(cost?.per_role ?? {}).reduce((total, row) => total + (row.input_tokens ?? 0), 0);
  const totalOutputTokens = Object.values(cost?.per_role ?? {}).reduce((total, row) => total + (row.output_tokens ?? 0), 0);
  return {
    backendWorkers: backendAgents.length,
    controlAgents: agents.filter(isControlAgent).length,
    contextWarn: numberValue(summary.context_warn) ?? backendAgents.filter((agent) => (agent.context_usage_ratio ?? 0) >= 0.75).length,
    drift: numberValue(summary.drift) ?? backendAgents.filter((agent) => String(agent.attention_state || "").includes("drift")).length,
    healthy,
    maxContext,
    operatorAgents: agents.filter(isOperatorAgent).length,
    providerSummary,
    silent: numberValue(summary.silent) ?? 0,
    stuck: numberValue(summary.stuck) ?? backendAgents.filter((agent) => String(agent.lifecycle_state || agent.attention_state || "").includes("stuck")).length,
    totalCostUsd: cost?.total_usd ?? Object.values(cost?.per_role ?? {}).reduce((total, row) => total + (row.usd ?? 0), 0),
    totalInputTokens,
    totalOutputTokens,
    workerAgents: agents.filter((agent) => agentClassLabel(agent) === "worker").length,
  };
}

export function buildAgentAttentionRows(
  agents: AgentSummary[],
  agentCockpit: Record<string, unknown> | null,
  recovery: Record<string, unknown> | null,
): AttentionRow[] {
  const rows: AttentionRow[] = [];
  const seen = new Set<string>();
  for (const item of asRecordArray(agentCockpit?.workers)) {
    const target = textValue(item.instance_id) || textValue(item.agent_id) || "-";
    const status = textValue(item.status) || textValue(item.attention) || "attention";
    const reason = asStringArray(item.reasons)[0] || textValue(item.reason) || status;
    if (!target || target === "-") continue;
    if (!isAttentionStatus(status) && !reason) continue;
    const key = `cockpit:${target}:${status}:${reason}`;
    seen.add(key);
    rows.push({
      severity: severityFor(status),
      domain: "agent",
      target,
      reason,
      recommended_action: asStringArray(item.next_actions)[0] || recommendedAction(status),
      evidence: textValue(item.last_event_id) || textValue(item.task_id) || "-",
      source_projection: "agent_cockpit",
    });
  }
  for (const agent of agents.filter(isBackendWorker)) {
    const status = agent.attention_state || agent.lifecycle_state || "";
    if (!isAttentionStatus(status)) continue;
    const reason = agent.needs_input_reason || agent.provider_stop_reason || status;
    const key = `agent:${agent.instance_id}:${status}:${reason}`;
    if (seen.has(key)) continue;
    rows.push({
      severity: severityFor(status),
      domain: "agent",
      target: agent.instance_id,
      reason,
      recommended_action: recommendedAction(status),
      evidence: agent.last_event_type || agent.task_id || "-",
      source_projection: "agents",
    });
  }
  for (const item of asRecordArray(recovery?.suggestions)) {
    const target = textValue(item.instance_id) || textValue(item.task_id) || "-";
    if (!target || target === "-") continue;
    rows.push({
      severity: "warn",
      domain: "recovery",
      target,
      reason: textValue(item.reason) || textValue(item.suggestion_type) || "recovery suggested",
      recommended_action: textValue(item.recommended_recovery) || "review recovery",
      evidence: textValue(item.trigger_event_id) || "-",
      source_projection: "recovery",
    });
  }
  return rows.sort((left, right) => severityRank(right.severity) - severityRank(left.severity)).slice(0, 24);
}

export function buildRoleFleetRows(agents: AgentSummary[], cost: CostSummary | null): RoleFleetRow[] {
  const grouped = new Map<string, AgentSummary[]>();
  for (const agent of agents.filter(isBackendWorker)) {
    const role = agent.parent_role || agent.role_type || "unknown";
    grouped.set(role, [...(grouped.get(role) ?? []), agent]);
  }
  const roles = [...new Set([...grouped.keys(), ...Object.keys(cost?.per_role ?? {})])].sort();
  return roles.map((role) => {
    const workers = grouped.get(role) ?? [];
    const usage = cost?.per_role[role] ?? {
      entries: 0,
      input_tokens: workers.reduce((total, worker) => total + (worker.cost?.input_tokens ?? 0), 0),
      output_tokens: workers.reduce((total, worker) => total + (worker.cost?.output_tokens ?? 0), 0),
      usd: workers.reduce((total, worker) => total + (worker.cost?.usd ?? 0), 0),
    };
    const backends = [...new Set(workers.map((worker) => worker.backend).filter(Boolean))].sort();
    const activeTasks = new Set(workers.map((worker) => worker.task_id || worker.active_task).filter(Boolean));
    const maxContext = workers.reduce<number | null>((max, worker) => {
      if (typeof worker.context_usage_ratio !== "number") return max;
      return Math.max(max ?? 0, worker.context_usage_ratio);
    }, null);
    return {
      role,
      workers: workers.length,
      backend: backends.join(", ") || "-",
      active_tasks: activeTasks.size,
      total_tokens: (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0),
      input_tokens: usage.input_tokens ?? 0,
      output_tokens: usage.output_tokens ?? 0,
      usd: usage.usd ?? 0,
      max_context: maxContext,
      attention: workers.filter((worker) => needsAttention(worker.attention_state)).length,
    };
  });
}

export function contextPercent(value: number | null): string {
  return value == null ? "unknown" : `${Math.round(value * 100)}%`;
}

export function attentionTone(rows: AttentionRow[]): CockpitTone {
  if (rows.some((row) => row.severity === "err")) return "err";
  if (rows.some((row) => row.severity === "warn")) return "warn";
  return rows.length ? "info" : "ok";
}

function isAttentionStatus(status: string): boolean {
  const value = status.toLowerCase();
  return Boolean(value) && !["idle", "working", "healthy", "running", "running_task", "completed_verified"].includes(value);
}

function severityFor(status: string): "err" | "warn" | "info" {
  const value = status.toLowerCase();
  if (value.includes("stuck") || value.includes("silent") || value.includes("failed")) return "err";
  if (value.includes("context") || value.includes("drift") || value.includes("warn")) return "warn";
  return "info";
}

function recommendedAction(status: string): string {
  const value = status.toLowerCase();
  if (value.includes("stuck") || value.includes("silent")) return "open recovery / respawn";
  if (value.includes("context")) return "checkpoint / compact";
  if (value.includes("drift")) return "review drift";
  return "review worker";
}

function severityRank(severity: AttentionRow["severity"]): number {
  if (severity === "err") return 3;
  if (severity === "warn") return 2;
  return 1;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => textValue(item)).filter(Boolean) : [];
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
