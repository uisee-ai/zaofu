import { type Row, strings, text, unique } from "./planReviewModel.js";

/** Presentation aliases only; preserve the author's status and obligations. */
export function planDecisionView(row: Row) {
  return {
    id: text(row.decision_id) || text(row.id),
    question: text(row.question) || text(row.title) || text(row.decision) || text(row.statement),
    resolution: text(row.resolution) || text(row.recommendation) || text(row.proposed_resolution),
    deadline: text(row.enter_before) || text(row.latest_gate) || text(row.latest_decision_stage) || text(row.latest_use_point),
    closure: text(row.done_when) || text(row.closure_evidence),
    sources: unique([...strings(row.evidence_refs), ...strings(row.source_refs), text(row.source_ref)]),
    options: Array.isArray(row.options) ? row.options.map(option => typeof option === "string" ? option : text(option?.label)).filter(Boolean) : [],
  };
}

const statusLabels: Record<string, string> = {
  proposed: "待确认提议", pending: "待决定", pending_decision: "待决定", open: "待决定",
  pending_approval: "待审批", pending_validation: "待验证", not_run: "尚未执行",
  authorized_with_task_obligations: "已授权待落实", startup_authorized_measurement_pending: "启动已授权，测量待完成",
  resolved: "已关闭", closed: "已关闭", approved: "已批准", confirmed: "已确认", done: "已完成",
  decided_source: "来源已确定",
};
export function decisionStatusLabel(row: Row): string {
  const raw = text(row.status);
  return statusLabels[raw.toLowerCase()] || (raw ? `未识别状态：${raw}` : "状态未声明");
}
export function decisionReadingSummary(decisions: Row[]): string {
  const counts = new Map<string, number>();
  for (const row of decisions) {
    const label = decisionStatusLabel(row);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts].map(([label, count]) => `${label} ${count}`).concat(
    `明确阻塞 ${decisions.filter(row => row.blocking === true).length}`,
  ).join(" · ");
}
