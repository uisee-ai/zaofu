/** Read-only views of admitted Plan ports. No inferred business rules or verdicts. */
type Row = Record<string, unknown>;
export const reviewPorts = ["task_map", "execution_spec", "source_inventory"] as const;
export type ReviewPort = typeof reviewPorts[number];
export type ReviewArtifacts = Partial<Record<ReviewPort, unknown>>;
export const object = (value: unknown): Row => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(v => v !== null && typeof v === "object" && !Array.isArray(v)) as Row[] : [];
export const text = (value: unknown): string => typeof value === "string" ? value : "";
export const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter(v => typeof v === "string") : [];

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
      const issues: string[] = [];
      if (!refs.length || refs.some(ref => !acceptanceIds.has(ref))) issues.push("验收项引用缺失或不属于当前任务");
      if (![scenario.given, scenario.when, scenario.then].every(v => text(v).trim())) issues.push("前置、操作或预期结果尚未补齐");
      if (!strings(scenario.source_refs).length) issues.push("来源条款尚未绑定");
      if (!strings(scenario.command_ids).length) issues.push("验证命令尚未绑定");
      else if (strings(scenario.command_ids).some(id => !knownCommands.has(id))) issues.push("场景命令未绑定到本任务验收项");
      return { id: text(scenario.id), title: text(scenario.title), given: text(scenario.given), when: text(scenario.when), then: text(scenario.then), sourceRefs: strings(scenario.source_refs), acceptanceIds: refs, commandIds: strings(scenario.command_ids), issues };
    });
    return {
      id, title: text(task.title), description: text(task.description),
      sourceRefs: [...new Set([text(task.source_ref), ...strings(task.source_refs)].filter(Boolean))],
      dependencies: strings(task.blocked_by || task.dependencies),
      criteria: criteria.map(ac => ({ id: text(ac.id), statement: text(ac.statement), sourceRef: text(ac.source_ref), commandIds: strings(ac.verification_command_ids), tier: text(ac.verification_tier) })),
      pages: rows(inventory.pages).filter(p => p.task_id === id),
      forms: rows(inventory.forms).filter(p => p.task_id === id),
      decisions: decisions.filter(d => d.producer_task_id === id || strings(d.affected_task_ids).includes(id) || strings(task.prerequisite_decision_ids).includes(text(d.decision_id))),
      specification: text(review.module_spec_markdown), scenarios,
    };
  });
  return { modules, decisions, goals: rows(map.goal_claims), warnings,
    overview: text(shared.overview_markdown), journeys: text(shared.journeys_markdown),
    pageCount: Array.isArray(inventory.pages) ? inventory.pages.length : null,
    formCount: Array.isArray(inventory.forms) ? inventory.forms.length : null,
    criterionCount: modules.reduce((n, m) => n + m.criteria.length, 0),
  };
}
