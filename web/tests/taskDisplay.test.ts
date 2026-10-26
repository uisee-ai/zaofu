import type { Task } from "../src/api/types.js";
import {
  taskAttentionBadge,
  taskIsEffectivelyTerminal,
  taskRiskBadge,
  taskTerminalOutcome,
  taskTerminalTone,
} from "../src/lib/task-display.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "TASK-1",
    title: "Task",
    status: "in_progress",
    assigned_to: "dev-1",
    retry_count: 0,
    blocked_reason: "",
    phase: "impl",
    created_at: "2026-08-13T00:00:00Z",
    ...overrides,
  };
}

const reconciled = task({
  blocked_reason: "historical verify failure",
  retry_count: 3,
  display_status: "done",
  effective_terminal: true,
  canonical_drift: true,
  terminal_outcome: "success",
});
assert(taskAttentionBadge(task({ retry_count: 4 })).label === "", "retries alone do not become board blockers");
assert(taskAttentionBadge(task({ blocked_reason: "Waiting for access" })).label === "Blocked", "real blockers remain visible");
assert(taskAttentionBadge(reconciled).label === "", "terminal cards do not display historical blockers");
const terminalRisk = taskRiskBadge(reconciled, {
  attention: ["context"],
  contextRatio: 0.99,
  inputTokens: 100,
  outputTokens: 10,
  usd: 1,
  workerIds: ["dev-1"],
});
assert(taskIsEffectivelyTerminal(reconciled), "reconciled done should be effectively terminal");
assert(terminalRisk.label === "", "terminal task must not inherit historical risk");
assert(taskTerminalOutcome(reconciled) === "success", "terminal outcome should remain explicit");
assert(taskTerminalTone(reconciled) === "ok", "successful terminal work should use the success tone");

const cancelled = task({
  status: "cancelled",
  display_status: "cancelled",
  effective_terminal: true,
  terminal_outcome: "cancelled",
});
for (const status of ["cancelled", "superseded", "archived"]) {
  assert(taskTerminalOutcome(task({ status })) === status, "terminal status remains explicit without an outcome projection");
}
assert(taskTerminalTone(cancelled) === "muted", "cancelled work must not look successful or actionable");

const blocked = task({
  status: "blocked",
  blocked_reason: "missing dependency",
  attention: {
    required: true,
    severity: "error",
    code: "task_blocked",
    label: "missing dependency",
    since: "",
    source_ref: "",
  },
});
const blockedRisk = taskRiskBadge(blocked, undefined);
assert(blockedRisk.tone === "err", "active blocker should remain an error");
assert(blockedRisk.label === "missing dependency", "structured attention should supply the label");

const normalRisk = taskRiskBadge(task(), undefined);
assert(normalRisk.label === "", "absence of attention should not render risk normal");

const queued = task({
  status: "blocked",
  blocked_reason: "fanout_queue:fo-1:queued-TASK-1",
  attention: {
    required: false,
    severity: "none",
    code: "",
    label: "",
    since: "",
    source_ref: "",
  },
});
assert(taskRiskBadge(queued, undefined).label === "", "structured no-attention must suppress queue history");
assert(taskAttentionBadge(queued).label === "", "board attention must respect the authoritative no-attention projection");
