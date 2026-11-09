import { composerDraftKey, readComposerDraft } from "../src/components/orchestrator/kanbanComposerDraft.js";

function assert(condition: unknown, message: string): void { if (!condition) throw new Error(message); }
const values = new Map<string, string>();
const storage = () => ({ getItem: (key: string) => values.get(key) ?? null });
const a = composerDraftKey("project/a", "main");
const b = composerDraftKey("project", "a:main");
const c = composerDraftKey("project/a", "other");
assert(new Set([a, b, c]).size === 3, "project and thread keys must not collide");
values.set(a, JSON.stringify({ message: "draft A", attachments: [{ version_id: "v1" }], unreferencedTasks: ["T1"],
  planDiscussion: { requestEventId: "e1", requestId: "p1", revision: 1, header: "Plan" } }));
values.set(b, JSON.stringify({ message: "draft B", attachments: [], unreferencedTasks: [], planDiscussion: null }));
const loaded = readComposerDraft(storage, a);
assert(loaded.message === "draft A" && loaded.attachments[0]?.version_id === "v1", "reload restores draft and attachments");
assert(loaded.unreferencedTasks[0] === "T1" && loaded.planDiscussion?.requestId === "p1", "reload restores task and Plan reference");
assert(readComposerDraft(storage, b).message === "draft B" && readComposerDraft(storage, c).message === "", "drafts remain isolated by project and thread");
values.set(c, "not-json");
assert(readComposerDraft(storage, c).planDiscussion === null, "corrupt saved data fails closed");
assert(readComposerDraft(() => { throw new Error("storage blocked"); }, c).message === "", "blocked storage must not break the composer");
