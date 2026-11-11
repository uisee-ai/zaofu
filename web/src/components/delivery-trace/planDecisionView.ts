import { type Row, strings, text, unique } from "./planReviewModel.js";

/** Presentation aliases only; preserve the author's status and obligations. */
export function planDecisionView(row: Row) {
  return {
    id: text(row.decision_id) || text(row.id),
    question: text(row.question) || text(row.title) || text(row.decision),
    resolution: text(row.resolution) || text(row.recommendation) || text(row.proposed_resolution),
    deadline: text(row.enter_before) || text(row.latest_gate) || text(row.latest_decision_stage) || text(row.latest_use_point),
    closure: text(row.done_when) || text(row.closure_evidence),
    sources: unique([...strings(row.evidence_refs), ...strings(row.source_refs), text(row.source_ref)]),
    options: Array.isArray(row.options) ? row.options.map(option => typeof option === "string" ? option : text(option?.label)).filter(Boolean) : [],
  };
}
