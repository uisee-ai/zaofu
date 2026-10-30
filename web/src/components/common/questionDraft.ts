/** Browser-local drafts are not submitted answers or runtime state. */
export interface QuestionDraft {
  activeIndex: number;
  selected: Record<string, string>;
  skipped: Record<string, boolean>;
  textAnswers: Record<string, string>;
}
type StoragePort = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function readQuestionDraft(storage: StoragePort, key: string, signature: string): QuestionDraft | undefined {
  try {
    const value = JSON.parse(storage.getItem(key) || "null");
    if (!value || value.signature !== signature || !Number.isInteger(value.activeIndex)
      || value.activeIndex < 0 || value.activeIndex > 2) return;
    for (const [field, type] of [["selected", "string"], ["textAnswers", "string"], ["skipped", "boolean"]]) {
      const entries = value[field];
      if (!entries || typeof entries !== "object" || Array.isArray(entries)
        || Object.values(entries).some(item => typeof item !== type)) return;
    }
    return value as QuestionDraft;
  } catch { return; }
}

export function writeQuestionDraft(storage: StoragePort, key: string, signature: string, draft: QuestionDraft): void {
  try { storage.setItem(key, JSON.stringify({ ...draft, signature })); } catch { /* Storage may be disabled or full. */ }
}

export function clearQuestionDraft(key: string): void {
  try { window.localStorage.removeItem(key); } catch { /* Keep answering available without storage. */ }
}

export function planQuestionDraftKey(scope: string, request: {
  requestEventId: string; requestId: string; revision: number; requestDigest?: string;
}): string {
  return `zf.plan-question-draft:${JSON.stringify([
    scope, request.requestEventId, request.requestId, request.revision, request.requestDigest || "",
  ])}`;
}
