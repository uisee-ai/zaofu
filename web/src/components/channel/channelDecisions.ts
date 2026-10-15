import type { ChannelDetail } from "../../api/types";
import type { AgentConversation, AgentSessionRun, AgentSessionTurn } from "../agent-session/types";
import { flowText } from "./channelFlow.js";
import { channelThreadId } from "../agent-session/channelProjection.js";

interface QuestionSource { sourceRunId: string; sourceMessageId: string; askedBy: string }
interface RunRow { run: AgentSessionRun; turn: AgentSessionTurn }

export const DECISION_CATEGORIES = {
  evidence: "事实核证", test: "测试假设", clarification: "阶段澄清",
  answer: "已答复", deferred: "本轮暂缓", assumption: "假设", out_of_scope: "范围排除", other: "其他处置",
} as const;
export type DecisionCategory = keyof typeof DECISION_CATEGORIES;
export interface DecisionReceipt extends QuestionSource {
  id: string; question: string; answer: string; resolution: string; resolvedBy: string;
  kind: string; threadId: string; riskNote: string; category: DecisionCategory;
  original: Record<string, unknown>;
}
export interface DecisionGroup { id: string; category: DecisionCategory; receipts: DecisionReceipt[] }

function decisionCategory(resolution: string, answer: string): DecisionCategory {
  if (resolution === "deferred") return "deferred";
  if (resolution === "evidence") return "evidence";
  if (resolution === "assumption") return "assumption";
  if (resolution === "out_of_scope") return "out_of_scope";
  if (resolution !== "answered") return "other";
  // Legacy answers can explicitly label their scope. These are display labels,
  // never inferred decisions, approval, or changes to the persisted resolution.
  if (/^(?:本(?:次|轮)[^：:\n]{0,24})?测试(?:决定|假设)[：:]/u.test(answer.trim())) return "test";
  if (/^(?:本轮)?阶段澄清[（(：:]/u.test(answer.trim())) return "clarification";
  return "answer";
}

function groupDecisionReceipts(receipts: DecisionReceipt[]): DecisionGroup[] {
  const groups = new Map<string, DecisionGroup>();
  for (const receipt of receipts) {
    // Only explicitly scoped answers may group different questions. A generic
    // "agreed" on two unrelated questions is not evidence of a shared decision.
    const sharedScope = receipt.category === "test" || receipt.category === "clarification";
    const key = JSON.stringify([receipt.threadId, receipt.kind, receipt.resolution, receipt.resolvedBy,
      receipt.category, receipt.riskNote, receipt.answer,
      sharedScope ? "" : receipt.question, receipt.answer && receipt.resolvedBy ? "" : receipt.id]);
    const group = groups.get(key);
    if (group) group.receipts.push(receipt);
    else groups.set(key, { id: receipt.id, category: receipt.category, receipts: [receipt] });
  }
  return [...groups.values()];
}

export function questionTargetRun(question: QuestionSource, rows: RunRow[]): RunRow | undefined {
  // Explicit provenance is authoritative. Never reattach an unloaded run's
  // question/decision to a newer, unrelated reply from the same member.
  if (question.sourceRunId) return rows.find(({ run }) => run.id === question.sourceRunId);
  const candidates = rows.filter(({ run, turn }) => Boolean(question.askedBy)
    && run.memberId === question.askedBy
    && (!question.sourceMessageId || turn.user?.id === question.sourceMessageId));
  if (!question.sourceMessageId && candidates.length !== 1) return undefined;
  return [...candidates].sort((a, b) => (a.run.updatedAt || "").localeCompare(b.run.updatedAt || "")).at(-1);
}

export function channelDecisionReceipts(detail: ChannelDetail | null, conversation: AgentConversation, threadId: string) {
  const receipts: DecisionReceipt[] = Object.values(detail?.open_questions ?? {}).filter((row) =>
    row.status === "resolved" && channelThreadId(flowText(row.thread_id)) === channelThreadId(threadId))
    .map((row) => ({
      id: flowText(row.question_id), question: flowText(row.question), answer: flowText(row.answer),
      resolution: flowText(row.resolution), resolvedBy: flowText(row.resolved_by),
      askedBy: flowText(row.asked_by), sourceRunId: flowText(row.source_run_id || row.run_id),
      sourceMessageId: flowText(row.source_message_id || row.message_id),
      kind: flowText(row.kind), threadId: flowText(row.thread_id), riskNote: flowText(row.risk_note),
      category: decisionCategory(flowText(row.resolution), flowText(row.answer)), original: row,
    }));
  const rows = conversation.threads.find((thread) => thread.id === threadId)?.turns
    .flatMap((turn) => turn.runs.map((run) => ({ run, turn }))) ?? [];
  const byRun = new Map<string, typeof receipts>();
  const unassigned: typeof receipts = [];
  for (const receipt of receipts) {
    const target = questionTargetRun(receipt, rows);
    if (!target) { unassigned.push(receipt); continue; }
    byRun.set(target.run.id, [...(byRun.get(target.run.id) ?? []), receipt]);
  }
  return { byRun, unassigned, groups: groupDecisionReceipts(receipts), recordCount: receipts.length };
}
