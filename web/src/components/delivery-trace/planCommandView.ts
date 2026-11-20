import { rows, object, strings, text, unique, type Row } from "./planReviewModel.js";

/** Read-only responsibility labels. Execution remains the runtime selector's job. */
export function commandResponsibility(command: Row, taskId: string): string {
  const producer = text(command.producer_task_id);
  const owner = text(command.owner);
  const scope = producer && producer !== taskId ? `其他任务 ${producer}` : "本任务";
  if (owner === "human") return `${scope} · 人工验收`;
  if (owner === "candidate_verify") return `${scope} · 候选集成验证`;
  if (owner === "task_verify" || owner === "impl_self_check") return `${scope} · 本片验证`;
  return `${scope} · 阶段待核对（${owner || "未声明"}）`;
}
export function taskCommandReferences(tasks: Row[], task: Row, ids?: string[]) {
  const requested = ids || unique([
    ...rows(object(task.validation).commands).map(c => text(c.id)),
    ...rows(task.acceptance_criteria).flatMap(ac => strings(ac.verification_command_ids)),
  ]);
  return requested.map(id => {
    const local = rows(object(task.validation).commands).filter(c => c.id === id);
    const definitions = local.length ? local : tasks.flatMap(t => rows(object(t.validation).commands).filter(c => c.id === id));
    return { id, definitions, label: definitions.length === 1
      ? commandResponsibility(definitions[0], text(task.task_id || task.id))
      : definitions.length ? "定义不唯一，请核对原文" : "定义缺失，请核对工程附件" };
  });
}
