// Test-only authorization policy for the frozen PRD preview scenario.
export type JourneyEvent = { id: string; seq: number; type: string; task_id?: string; correlation_id?: string; payload?: Record<string, any> };
export function sameInvocation(row: JourneyEvent, invoked: JourneyEvent): boolean {
  if (row.seq < invoked.seq) return false;
  const keys = ["workflow_run_id", "trace_id", "request_id", "goal_id", "run_id"];
  const ids = new Set(keys.map(key => invoked.payload?.[key]).filter(Boolean));
  if (invoked.correlation_id) ids.add(invoked.correlation_id);
  const supplied = keys.map(key => row.payload?.[key]).filter(Boolean);
  if (row.correlation_id) supplied.push(row.correlation_id);
  return supplied.some(id => ids.has(id));
}
export function terminalPlanningEvent(rows: JourneyEvent[], invoked: JourneyEvent): JourneyEvent | undefined {
  const scoped = rows.filter(row => sameInvocation(row, invoked));
  const terminal = scoped.filter(row => ["human.escalate", "workflow.invoke.rejected", "run.goal.blocked", "runtime.start.failed"].includes(row.type)).at(-1);
  if (!terminal) return undefined;
  // Rework failure events are not terminal. An explicitly restarted planning
  // attempt supersedes an earlier escalation; unrelated children do not.
  if (scoped.some(row => row.seq > terminal.seq &&
      (row.type === "plan.approval.requested" || (row.type === "runtime.action.completed" &&
        row.payload?.action === "stage-replan-new-generation" && row.payload?.status === "applied" &&
        row.payload?.escalation_event_id === terminal.id)))) return undefined;
  return terminal;
}
export function prdOnlyOption(options: { id: string; label: string; action?: string }[]): string | undefined {
  const matches = options.filter(option => !option.action && /PRD/i.test(option.label) &&
    /仅|只|写入|保存|落盘|save|only/i.test(option.label) &&
    !/workflow|kanban|task|实施|执行|启动|创建任务|生成计划|生成\s*plan/i.test(option.label));
  return matches.length === 1 ? matches[0].id : undefined;
}
