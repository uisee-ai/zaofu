import { taskCommandReferences } from "./planCommandView.js";

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

export function decisionStatusDistribution(decisions: Row[]): string {
  const counts = new Map<string, number>();
  for (const decision of decisions) {
    const status = text(decision.status) || "未声明";
    counts.set(status, (counts.get(status) || 0) + 1);
  }
  return [...counts].map(([status, count]) => `${status} ${count}`).join(" · ");
}

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
  const matrix = object(artifacts.test_matrix);
  const formal = rows(matrix.formal_acceptance_scenarios);
  const tests = rows(matrix.tests);
  const modules = tasks.map((task) => {
    const id = text(task.task_id || task.id);
    const matches = partitions.filter(p => p.task_id === id);
    if (matches.length > 1) warnings.push(`${id} 存在重复执行规格，未采用其人工补充。`);
    const body = object(matches.length === 1 ? matches[0].body : undefined);
    const review = object(body.human_review);
    const criteria = rows(task.acceptance_criteria);
    const acceptanceIds = new Set(criteria.map(ac => text(ac.id)));
    // Prefer the module's explicit human review; otherwise use the admitted
    // test-matrix contract. Task identity wins over reused AC identifiers.
    const authoredScenarios = rows(review.scenarios);
    const candidates = authoredScenarios.length ? authoredScenarios : tests.filter(s => relatedTasks(s).includes(id));
    const scenarios = candidates.map(scenario => {
      const scenarioId = text(scenario.id || scenario.case_id || scenario.test_id);
      const refs = strings(scenario.acceptance_ids);
      const commandIds = strings(scenario.command_ids);
      const participants = relatedTasks(scenario);
      const responsibilities = (participants.length ? participants : [id]).map(taskId => {
        const owners = tasks.filter(t => text(t.task_id || t.id) === taskId);
        const owner = owners.length === 1 ? owners[0] : {};
        const localCriteria = rows(owner.acceptance_criteria);
        const boundCommands = new Set(localCriteria.flatMap(ac => strings(ac.verification_command_ids)));
        const localCommands = commandIds.filter(command => boundCommands.has(command));
        return { taskId, uniqueTask: owners.length === 1,
          acceptanceIds: refs.filter(ref => localCriteria.some(ac => ac.id === ref)),
          commandIds: localCommands,
          commands: localCommands.map(commandId => {
            const matches = rows(object(owner.validation).commands).filter(c => c.id === commandId);
            return { id: commandId, definitions: matches };
          }),
        };
      });
      const sourceRefs = unique([...strings(scenario.source_refs), ...strings(scenario.source_ids).flatMap(id => sources.filter(s => s.source_id === id).map(s => text(s.source_ref))), ...sources.filter(s => scenarioId && relatedTasks(s).includes(id) && strings(s.scene_ids).includes(scenarioId)).map(s => text(s.source_ref))]);
      const issues: string[] = [];
      for (const sourceId of strings(scenario.source_ids)) {
        if (sources.filter(s => s.source_id === sourceId && text(s.source_ref)).length !== 1) issues.push(`来源 ID 未唯一解析：${sourceId}`);
      }
      if (!refs.length || refs.some(ref => !responsibilities.some(r => r.acceptanceIds.includes(ref)))) issues.push("验收项引用缺失或未绑定到场景参与任务");
      for (const responsibility of responsibilities) {
        if (!responsibility.uniqueTask) issues.push(`场景任务未唯一解析：${responsibility.taskId}`);
        for (const command of responsibility.commands) {
          if (command.definitions.length > 1) issues.push(`验证命令定义不唯一：${responsibility.taskId} / ${command.id}`);
        }
      }
      if (![scenario.given, scenario.when, scenario.then].every(v => text(v).trim())) issues.push("前置、操作或预期结果尚未补齐");
      if (!sourceRefs.length) issues.push("来源条款尚未绑定");
      if (!commandIds.length) issues.push("验证命令尚未绑定");
      else if (commandIds.some(command => !responsibilities.some(r => r.commandIds.includes(command)))) issues.push("场景命令未绑定到参与任务验收项");
      if (participants.length && !participants.includes(id)) issues.push("当前任务未列入场景参与任务");
      if (participants.length > 1 && responsibilities.some(r => r.taskId === id && (!r.acceptanceIds.length || !r.commandIds.length))) issues.push("本片责任待核对：场景未直接绑定本任务 AC 或命令，请对照 Task Spec 核对集成责任");
      return { id: scenarioId, evidenceStatus: text(scenario.evidence_status) || "未声明（规划场景，不代表测试通过）", title: text(scenario.title || scenario.name), given: text(scenario.given), when: text(scenario.when), then: text(scenario.then), sourceRefs, acceptanceIds: refs, commandIds, responsibilities, issues };
    });
    return {
      id, title: text(task.title), description: text(task.description),
      sourceRefs: unique([text(task.source_ref), ...strings(task.source_refs)]),
      dependencies: strings(task.blocked_by || task.dependencies),
      owner: text(task.owner_role || task.role),
      allowedPaths: strings(task.allowed_paths),
      commands: rows(object(task.validation).commands),
      commandReferences: taskCommandReferences(tasks, task),
      criteria: criteria.map(ac => ({ id: text(ac.id), statement: text(ac.statement), sourceRefs: unique([text(ac.source_ref), ...strings(ac.source_refs), ...sources.filter(s => relatedTasks(s).includes(id) && (strings(s.acceptance_ids).includes(text(ac.id)) || s.acceptance_id === ac.id)).map(s => text(s.source_ref))]), commandIds: strings(ac.verification_command_ids), commandReferences: taskCommandReferences(tasks, task, strings(ac.verification_command_ids)), tier: text(ac.verification_tier) })),
      pages: rows(inventory.pages).filter(p => relatedTasks(p).includes(id)),
      forms: rows(inventory.forms).filter(p => relatedTasks(p).includes(id)),
      decisions: decisions.filter(d => d.producer_task_id === id || strings(d.affected_task_ids).includes(id) || unique([...strings(task.prerequisite_decision_ids), ...strings(task.decision_ids)]).includes(text(d.decision_id || d.id))),
      specification: text(review.module_spec_markdown), scenarios,
      formalScenarios: formal.filter(s => relatedTasks(s).length ? relatedTasks(s).includes(id) : acceptanceIds.has(text(s.acceptance_id))),
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
