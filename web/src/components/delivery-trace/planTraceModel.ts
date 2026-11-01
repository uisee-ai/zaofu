import { object, portRows, rows, strings, text, unique } from "./planReviewModel.js";
import type { ReviewArtifacts, Row } from "./planReviewModel";

export interface TraceRow {
  id: string; statement: string; sourceRefs: string[]; disposition: string;
  taskIds: string[]; acceptanceIds: string[]; commandIds: string[];
  evidence: string[]; evidenceStatus: string; issues: string[]; reason: string;
  tasks: Row[]; criteria: Row[]; commands: Row[];
}
function index(items: Row[], field: string) {
  const result = new Map<string, Row[]>();
  items.forEach(item => { const id = text(item[field]); if (id) result.set(id, [...(result.get(id) || []), item]); });
  return result;
}

export function buildPlanTrace(artifacts: ReviewArtifacts): TraceRow[] {
  const source = index(portRows(artifacts.source_index, "traceability"), "source_id");
  const coverage = index(portRows(artifacts.coverage_ledger, "entries"), "source_id");
  const tasks = index(rows(object(artifacts.task_map).tasks).map(t => ({ ...t, task_id: t.task_id || t.id })), "task_id");
  const criteria = index(rows(object(artifacts.acceptance_matrix).acceptance), "id");
  const commands = index(rows(object(artifacts.test_matrix).commands), "id");
  return unique([...source.keys(), ...coverage.keys()]).map(id => {
    const origins = source.get(id) || [];
    const entries = coverage.get(id) || [];
    const issues: string[] = [];
    if (!origins.length) issues.push("来源索引缺少此条款");
    if (!entries.length) issues.push("尚未分配");
    if (origins.length > 1 || entries.length > 1) issues.push("来源或覆盖记录重复，需核对");
    const taskIds = unique(entries.flatMap(e => strings(e.task_ids)));
    const acceptanceIds = unique(entries.flatMap(e => strings(e.acceptance_ids)));
    const commandIds = unique(entries.flatMap(e => strings(e.verification_command_ids)));
    const disposition = entries.length === 1 ? text(entries[0].disposition) : entries.length ? "conflict" : "unmapped";
    if (disposition === "covered" && (!taskIds.length || !acceptanceIds.length || !commandIds.length)) issues.push("已分配记录缺少 Task / AC / 命令");
    const check = (ids: string[], registry: Map<string, Row[]>, label: string, available: boolean) => ids.forEach(ref => {
      const matches = registry.get(ref) || [];
      if (!available) issues.push(`${label} 工件未读取，无法核对：${ref}`);
      else if (!matches.length) issues.push(`${label} 不存在：${ref}`);
      else if (matches.length > 1) issues.push(`${label} 重复：${ref}`);
    });
    check(taskIds, tasks, "Task", Array.isArray(object(artifacts.task_map).tasks));
    check(acceptanceIds, criteria, "AC", Array.isArray(object(artifacts.acceptance_matrix).acceptance));
    check(commandIds, commands, "命令", Array.isArray(object(artifacts.test_matrix).commands));
    const boundCriteria = acceptanceIds.flatMap(id => criteria.get(id) || []);
    const boundCommands = commandIds.flatMap(id => commands.get(id) || []);
    return {
      id, statement: origins.map(s => text(s.statement || s.title)).join("\n"),
      sourceRefs: unique(origins.map(s => text(s.source_ref))), disposition,
      taskIds, acceptanceIds, commandIds, issues,
      reason: entries.map(e => text(e.reason)).filter(Boolean).join("；"),
      evidenceStatus: unique(entries.map(e => text(e.evidence_status))).join(" / ") || unique(boundCriteria.map(ac => text(ac.evidence_state))).join(" / ") || "未提供",
      evidence: unique([...entries.flatMap(e => [...strings(e.evidence_refs), ...strings(e.evidence_paths), text(e.evidence_path)]), ...boundCriteria.flatMap(ac => strings(ac.evidence_paths)), ...boundCommands.flatMap(command => strings(command.producer_paths))]),
      tasks: taskIds.flatMap(id => tasks.get(id) || []),
      criteria: boundCriteria,
      commands: boundCommands,
    };
  });
}
export const dispositionLabel = (value: string) => ({ covered: "已分配", pending_decision: "待决定", explicit_non_goal: "明确非目标", out_of_scope: "范围外", unmapped: "未分配", conflict: "记录冲突" }[value] || value || "未声明");
export function traceSearch(row: TraceRow) {
  return [row.id, row.statement, row.disposition, dispositionLabel(row.disposition), ...row.sourceRefs, ...row.taskIds, ...row.acceptanceIds, ...row.commandIds, ...row.tasks.map(t => text(t.title))].join(" ").toLowerCase();
}
export function comparePlanTrace(before: TraceRow[], after: TraceRow[]) {
  const old = new Map(before.map(row => [row.id, row]));
  const current = new Map(after.map(row => [row.id, row]));
  const signature = (row: TraceRow) => JSON.stringify([row.statement, [...row.sourceRefs].sort(), row.disposition, [...row.taskIds].sort(), [...row.acceptanceIds].sort(), [...row.commandIds].sort(), row.evidenceStatus]);
  return unique([...old.keys(), ...current.keys()]).flatMap(id => {
    const a = old.get(id), b = current.get(id);
    if (!a || !b || signature(a) !== signature(b)) return [{ id, kind: !a ? "新增" : !b ? "移除" : "修改", before: a, after: b }];
    return [];
  });
}
