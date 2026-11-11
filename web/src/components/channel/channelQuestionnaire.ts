import type { ChannelDetail } from "../../api/types";
import { channelThreadId } from "../agent-session/channelProjection.js";

function questionRecords(
  value: ChannelDetail["open_questions"],
): Array<Record<string, unknown>> {
  if (Array.isArray(value)) return value;
  return value ? Object.values(value) : [];
}

export function openOwnerQuestionnaire(
  detail: ChannelDetail | null,
  threadId: string,
): Array<Record<string, unknown>> {
  const logicalThreadId = channelThreadId(threadId);
  const allLedger = questionRecords(detail?.open_questions)
    .filter((item) => channelThreadId(String(item.thread_id || "main")) === logicalThreadId);
  const ledger = allLedger
    .filter((item) => String(item.status || "") === "open" && !item.presentation_pending)
    .filter((item) => String(item.question_id || "").trim());
  const ledgerIdentity = new Set(
    allLedger
      .map((item) => `${String(item.thread_id || "main")}::${String(item.question_id || "").trim()}`)
      .filter((item) => !item.endsWith("::")),
  );
  const ledgerByIdentity = new Map(
    ledger.map((item) => [
      `${String(item.thread_id || "main")}::${String(item.question_id).trim()}`,
      item,
    ]),
  );
  const ledgerById = new Map<string, Record<string, unknown>[]>();
  for (const item of ledger) {
    const id = String(item.question_id).trim();
    ledgerById.set(id, [...(ledgerById.get(id) || []), item]);
  }
  return Object.entries(detail?.owner_questionnaires ?? {})
    .filter(([key]) => channelThreadId(key) === logicalThreadId)
    .flatMap(([key, items]) => items.flatMap((item) => {
      const id = String(item.question_id || "").trim();
      if (!id) return [];
      const rawThreadId = String(item.thread_id || key || "main");
      const sameId = ledgerById.get(id) || [];
      const source = ledgerByIdentity.get(`${rawThreadId}::${id}`)
        || (ledgerIdentity.has(`${rawThreadId}::${id}`)
          ? undefined
          : sameId.length === 1 ? sameId[0] : undefined);
      if (!source) return [];
      // The ledger owns lifecycle and provenance. Questionnaire content owns
      // the answer choices, so merge rather than allowing a duplicate ID from
      // a folded thread to resurrect a resolved question.
      return [{
        ...source,
        ...item,
        thread_id: String(source.thread_id || rawThreadId),
        asked_by: String(source.asked_by || item.asked_by || ""),
        source_run_id: String(source.source_run_id || item.source_run_id || source.run_id || item.run_id || ""),
        source_message_id: String(source.source_message_id || item.source_message_id || source.message_id || item.message_id || ""),
      }];
    }));
}
