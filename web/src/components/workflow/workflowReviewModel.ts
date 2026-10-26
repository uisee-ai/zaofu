import type { WorkflowRequestDetail } from "../../api/types";

export function reviewBinding(detail: WorkflowRequestDetail): Record<string, unknown> {
  const request = detail.result || {};
  return { request_id: request.request_id, revision: request.revision, proposal_digest: request.proposal_digest };
}
export function reviewText(value: unknown): string {
  return typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value, null, 2);
}
export function previewQuote(selection: Selection | null, root: HTMLElement | null): { field: string; text: string; start: number; end: number } | null {
  if (!selection || selection.isCollapsed || !root || selection.rangeCount !== 1) return null;
  const range = selection.getRangeAt(0);
  const element = (node: Node) => node.nodeType === 1 ? node as Element : node.parentElement;
  const start = element(range.startContainer)?.closest<HTMLElement>("[data-review-field]");
  const end = element(range.endContainer)?.closest<HTMLElement>("[data-review-field]");
  const text = selection.toString();
  if (!start || start !== end || !root.contains(start) || !text.trim()) return null;
  const prefix = range.cloneRange();
  prefix.selectNodeContents(start); prefix.setEnd(range.startContainer, range.startOffset);
  const offset = Array.from(prefix.toString()).length;
  return { field: start.dataset.reviewField || "", text, start: offset, end: offset + Array.from(text).length };
}
export function reviewFieldLabel(field: string): string {
  if (field.includes(".workflow.plan_approval")) return "计划生成后等待审批";
  if (field === "execution_plan") return "执行计划";
  const parts = field.split(".");
  if (["roleDefaults", "role_defaults"].includes(parts[2])) return "执行配置 · 模型（model）";
  if (["roleSkillBundles", "role_skill_bundles"].includes(parts[2])) return `执行配置 · ${parts[3]} skills`;
  if (parts[0] === "requirement") {
    const label = ({ objective: "目标", acceptance: "验收条件", constraints: "约束", open_questions: "待澄清问题" } as Record<string, string>)[parts[1]] || "需求";
    return parts.length > 2 ? `${label} ${Number(parts[2]) + 1}` : label;
  }
  return ({ lanes: "执行配置 · 并行任务数", strictness: "执行配置 · 验证严格度", backend: "执行配置 · 执行 Provider", verifyBackend: "执行配置 · 验证 Provider", topology: "执行配置 · 流程结构" } as Record<string, string>)[parts[2]] || "执行配置";
}
