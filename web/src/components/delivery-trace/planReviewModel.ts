/** Read-only views of admitted Plan ports. No inferred business rules or verdicts. */
import type { OperatorInboxItem, PlanPreview } from "../../api/types";

export function resolvePlanPreviewItem(preview: Pick<PlanPreview, "plan_id"> | null, items: OperatorInboxItem[], selected: OperatorInboxItem | null = null): OperatorInboxItem | null {
  if (!preview) return null;
  return items.find(item => item.plan_id === preview.plan_id)
    || (selected?.plan_id === preview.plan_id ? selected : null);
}

export type Row = Record<string, unknown>;
export const reviewPorts = ["task_map", "execution_spec", "source_inventory", "source_index", "coverage_ledger", "acceptance_matrix", "test_matrix"] as const;
export type ReviewPort = typeof reviewPorts[number];
export type ReviewArtifacts = Partial<Record<ReviewPort, unknown>>;
export const object = (value: unknown): Row => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(v => v !== null && typeof v === "object" && !Array.isArray(v)) as Row[] : [];
export const text = (value: unknown): string => typeof value === "string" ? value : "";
export const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter(v => typeof v === "string") : [];
export const unique = (values: string[]) => [...new Set(values.filter(Boolean))];
export const portRows = (port: unknown, field: string) => rows(object(port)[field] ?? object(port).rows);
export const relatedTasks = (row: Row) => unique([text(row.task_id), ...strings(row.task_ids)]);

export function planArtifactUrl(projectId: string, planId: string, name: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}/artifacts/${encodeURIComponent(name)}`;
}

export function buildPlanReview(artifacts: ReviewArtifacts) {
  const map = object(artifacts.task_map);
  const execution = object(artifacts.execution_spec);
  const inventory = object(artifacts.source_inventory);
  const warnings: string[] = [];
  if (!Array.isArray(map.tasks)) warnings.push("未取得同版任务明细，请查看完整原文与工程附件。");
  const tasks = rows(map.tasks);
  const partitions = rows(execution.tasks);
  const shared = object(object(execution.shared).human_review);
  const knownTasks = new Set(tasks.map(t => text(t.task_id || t.id)));
  for (const partition of partitions) {
    if (!knownTasks.has(text(partition.task_id))) warnings.push(`执行规格引用未知任务：${text(partition.task_id)}`);
  }
  const decisions = rows(map.owner_decision_items);
  const sources = portRows(artifacts.source_index, "traceability");
  const formal = rows(object(artifacts.test_matrix).formal_acceptance_scenarios);
  const modules = tasks.map((task) => {
    const id = text(task.task_id || task.id);
    const matches = partitions.filter(p => p.task_id === id);
    if (matches.length > 1) warnings.push(`${id} 存在重复执行规格，未采用其人工补充。`);
    const body = object(matches.length === 1 ? matches[0].body : undefined);
    const review = object(body.human_review);
    const criteria = rows(task.acceptance_criteria);
    const acceptanceIds = new Set(criteria.map(ac => text(ac.id)));
    const knownCommands = new Set(criteria.flatMap(ac => strings(ac.verification_command_ids)));
    const scenarios = rows(review.scenarios).map(scenario => {
      const refs = strings(scenario.acceptance_ids);
      const sourceRefs = unique([...strings(scenario.source_refs), ...strings(scenario.source_ids).flatMap(id => sources.filter(s => s.source_id === id).map(s => text(s.source_ref)))]);
      const issues: string[] = [];
      for (const sourceId of strings(scenario.source_ids)) {
        if (sources.filter(s => s.source_id === sourceId && text(s.source_ref)).length !== 1) issues.push(`来源 ID 未唯一解析：${sourceId}`);
      }
      if (!refs.length || refs.some(ref => !acceptanceIds.has(ref))) issues.push("验收项引用缺失或不属于当前任务");
      if (![scenario.given, scenario.when, scenario.then].every(v => text(v).trim())) issues.push("前置、操作或预期结果尚未补齐");
      if (!sourceRefs.length) issues.push("来源条款尚未绑定");
      if (!strings(scenario.command_ids).length) issues.push("验证命令尚未绑定");
      else if (strings(scenario.command_ids).some(id => !knownCommands.has(id))) issues.push("场景命令未绑定到本任务验收项");
      return { id: text(scenario.id), title: text(scenario.title || scenario.name), given: text(scenario.given), when: text(scenario.when), then: text(scenario.then), sourceRefs, acceptanceIds: refs, commandIds: strings(scenario.command_ids), issues };
    });
    return {
      id, title: text(task.title), description: text(task.description),
      sourceRefs: unique([text(task.source_ref), ...strings(task.source_refs)]),
      dependencies: strings(task.blocked_by || task.dependencies),
      criteria: criteria.map(ac => ({ id: text(ac.id), statement: text(ac.statement), sourceRef: text(ac.source_ref), commandIds: strings(ac.verification_command_ids), tier: text(ac.verification_tier) })),
      pages: rows(inventory.pages).filter(p => relatedTasks(p).includes(id)),
      forms: rows(inventory.forms).filter(p => relatedTasks(p).includes(id)),
      decisions: decisions.filter(d => d.producer_task_id === id || strings(d.affected_task_ids).includes(id) || unique([...strings(task.prerequisite_decision_ids), ...strings(task.decision_ids)]).includes(text(d.decision_id))),
      specification: text(review.module_spec_markdown), scenarios,
      formalScenarios: formal.filter(s => s.task_id === id || acceptanceIds.has(text(s.acceptance_id))),
    };
  });
  return { modules, decisions, goals: rows(map.goal_claims), groups: rows(map.modules), warnings,
    overview: text(shared.overview_markdown), journeys: text(shared.journeys_markdown),
    pages: rows(inventory.pages), forms: rows(inventory.forms),
    pageCount: Array.isArray(inventory.pages) ? inventory.pages.length : null,
    formCount: Array.isArray(inventory.forms) ? inventory.forms.length : null,
    criterionCount: modules.reduce((n, m) => n + m.criteria.length, 0),
  };
}
