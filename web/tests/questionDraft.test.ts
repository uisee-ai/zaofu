import { readQuestionDraft, writeQuestionDraft } from "../src/components/common/questionDraft.js";

const data = new Map<string, string>();
const storage = { getItem: (key: string) => data.get(key) || null,
  setItem: (key: string, value: string) => { data.set(key, value); }, removeItem: (key: string) => { data.delete(key); } };
const draft = { activeIndex: 1, selected: { first: "csv", second: "other" }, skipped: {}, textAnswers: { second: "custom" } };
writeQuestionDraft(storage, "project/thread/request:1", "questions", draft);
if (JSON.stringify(readQuestionDraft(storage, "project/thread/request:1", "questions")?.selected) !== JSON.stringify(draft.selected)) throw new Error("Lost choices");
if (readQuestionDraft(storage, "project/thread/request:1", "questions")?.activeIndex !== 1) throw new Error("Lost question index");
for (const [key, signature] of [["project/thread/request:2", "questions"], ["other/thread/request:1", "questions"], ["project/thread/request:1", "new questions"]]) {
  if (readQuestionDraft(storage, key, signature)) throw new Error("Draft crossed request or question boundary");
}
for (const value of ["broken JSON", "[]", JSON.stringify({ ...draft, signature: "questions", selected: [] }), JSON.stringify({ ...draft, signature: "questions", activeIndex: 99 })]) {
  data.set("invalid", value);
  if (readQuestionDraft(storage, "invalid", "questions")) throw new Error("Invalid draft accepted");
}
const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
writeQuestionDraft(blocked, "key", "questions", draft);
if (readQuestionDraft(blocked, "key", "questions")) throw new Error("Blocked storage must fall back");
