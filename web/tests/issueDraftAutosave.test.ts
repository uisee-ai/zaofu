import { DraftSaveConflict, IssueDraftAutosave, type AutosaveView } from "../src/components/orchestrator/issueDraftAutosave.js";
import type { DraftBackupStore, DraftSnapshot, DraftText, LocalIssueDraft } from "../src/components/orchestrator/selfIssueDraftStore.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

class MemoryStore implements DraftBackupStore {
  readonly values = new Map<string, LocalIssueDraft>();
  async read(scope: string): Promise<LocalIssueDraft[]> {
    return [...this.values.values()].filter((item) => item.scope === scope).sort((a, b) => b.updatedAt - a.updatedAt);
  }
  async put(value: LocalIssueDraft): Promise<void> { this.values.set(value.key, { ...value }); }
  async remove(key: string, expected?: LocalIssueDraft): Promise<void> {
    const current = this.values.get(key);
    if (!expected || (current && current.updatedAt === expected.updatedAt && current.body === expected.body)) this.values.delete(key);
  }
}

const remote = (): DraftSnapshot => ({ title: "Original", body: "body", revision: 1 });
const options = (store: DraftBackupStore, save: (content: DraftText, revision: number) => Promise<DraftSnapshot>, changed: (view: AutosaveView) => void = () => undefined) => ({
  scope: "session:draft", clientId: "tab-a", remote: remote(), store, save, refresh: async () => remote(), changed,
  timing: { local: 0, debounce: 60_000, maxWait: 60_000 },
});

async function testLocalBackupAndSingleWriter(): Promise<void> {
  const store = new MemoryStore();
  const saves: string[] = [];
  const controller = new IssueDraftAutosave(options(store, async (content, revision) => {
    saves.push(content.body);
    return { ...content, revision: revision + 1 };
  }, () => undefined));
  await controller.start();
  controller.edit({ title: "Original", body: "first" });
  await controller.persist();
  assert(store.values.size === 1, "editing should create a local recovery copy");
  await controller.flush();
  assert(saves.join(",") === "first", "flush should make one server write");
  assert(store.values.size === 0, "successful sync should remove the local recovery copy");
  controller.dispose();
}

async function testEditDuringSaveIsNotLost(): Promise<void> {
  const store = new MemoryStore();
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const saves: string[] = [];
  const controller = new IssueDraftAutosave(options(store, async (content, revision) => {
    saves.push(content.body);
    if (saves.length === 1) await gate;
    return { ...content, revision: revision + 1 };
  }, () => undefined));
  await controller.start();
  controller.edit({ title: "Original", body: "first" });
  const pending = controller.flush();
  await Promise.resolve(); // Let the first request capture its payload.
  controller.edit({ title: "Original", body: "second" });
  assert(controller.flush() === pending, "concurrent flush calls must share the same writer");
  release?.();
  await pending;
  await controller.flush();
  assert(saves.join(",") === "first,second", "a later edit must be written after the first request");
  controller.dispose();
}

async function testConflictPreservesLocalCopy(): Promise<void> {
  const store = new MemoryStore();
  const controller = new IssueDraftAutosave(options(store, async () => {
    throw new DraftSaveConflict("revision_conflict");
  }, () => undefined));
  await controller.start();
  controller.edit({ title: "Original", body: "local" });
  try { await controller.flush(); } catch { /* expected */ }
  assert(controller.snapshot().status === "conflict", "revision conflict should be visible to the editor");
  assert(store.values.size === 1, "revision conflict must retain the local recovery copy");
  controller.dispose();
}

async function testRecoveryAndOtherTabRetention(): Promise<void> {
  const store = new MemoryStore();
  const first = new IssueDraftAutosave(options(store, async (content, revision) => ({ ...content, revision: revision + 1 })));
  await first.start();
  first.edit({ title: "Original", body: "unsynced" });
  await first.persist();
  first.dispose();
  await first.persist();
  const second = new IssueDraftAutosave({ ...options(store, async (content, revision) => ({ ...content, revision: revision + 1 })), clientId: "tab-b" });
  await second.start();
  assert(second.snapshot().content.body === "unsynced", "reload must restore unsynced content");
  const old = store.values.get("session:draft:tab-a")!;
  await store.put({ ...old, body: "newer edit in another tab", updatedAt: old.updatedAt + 1 });
  await second.flush();
  assert(store.values.get(old.key)?.body === "newer edit in another tab", "sync must not delete another tab's newer recovery copy");
  second.dispose();
}

async function testStorageAndNetworkFailure(): Promise<void> {
  const unavailable: DraftBackupStore = {
    read: async () => { throw new Error("unavailable"); },
    put: async () => { throw new Error("quota"); },
    remove: async () => undefined,
  };
  const controller = new IssueDraftAutosave(options(unavailable, async () => { throw new Error("network offline"); }));
  await controller.start();
  assert(controller.snapshot().ready && Boolean(controller.snapshot().localError), "unavailable IndexedDB must warn without disabling editing");
  controller.edit({ title: "Original", body: "not saved" });
  let rejected = false;
  try { await controller.flush(); } catch { rejected = true; }
  assert(rejected, "failed flush must prevent callers from publishing");
  assert(controller.snapshot().localError === "quota" && controller.snapshot().status === "offline", "local and remote failure must both be exposed");
  assert(controller.snapshot().content.body === "not saved", "failed save must preserve editor content");
  controller.dispose();
}

async function testPollConflictAndPublicationLock(): Promise<void> {
  const store = new MemoryStore();
  let writes = 0;
  const controller = new IssueDraftAutosave({
    ...options(store, async (content, revision) => { writes++; return { ...content, revision: revision + 1 }; }),
    refresh: async () => ({ ...remote(), revision: 2, locked: true }),
  });
  await controller.start();
  controller.edit({ title: "Original", body: "local edit" });
  controller.observe({ ...remote(), revision: 2, body: "other tab", locked: true });
  assert(controller.snapshot().status === "conflict" && controller.snapshot().content.body === "local edit", "poll must not overwrite dirty content");
  let rejected = false;
  try { await controller.resolve("local"); } catch { rejected = true; }
  assert(rejected && writes === 0, "published draft must reject overwrite");
  await controller.persist();
  assert(store.values.size === 1, "publication conflict must preserve local content");
  controller.dispose();
}

async function testScheduling(): Promise<void> {
  const realSet = globalThis.setTimeout;
  const realClear = globalThis.clearTimeout;
  const timers = new Map<number, { at: number; run: () => void }>();
  let now = 0, next = 0;
  globalThis.setTimeout = ((callback: () => void, delay = 0) => {
    const id = ++next; timers.set(id, { at: now + delay, run: callback }); return id;
  }) as typeof setTimeout;
  globalThis.clearTimeout = ((id: number) => { timers.delete(id); }) as typeof clearTimeout;
  const tick = async (ms: number) => {
    const end = now + ms;
    while (true) {
      const due = [...timers.entries()].filter(([, item]) => item.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].run();
      for (let i = 0; i < 20; i++) await Promise.resolve();
    }
    now = end;
  };
  const store = new MemoryStore();
  const savedAt: number[] = [];
  const controller = new IssueDraftAutosave({
    ...options(store, async (content, revision) => { savedAt.push(now); return { ...content, revision: revision + 1 }; }),
    timing: { local: 300, debounce: 1000, maxWait: 5000 },
  });
  try {
    await controller.start();
    controller.edit({ title: "Original", body: "first" });
    await tick(300);
    assert(store.values.size === 1 && savedAt.length === 0, "local backup must precede server sync");
    await tick(700);
    assert(savedAt.join() === "1000", "idle input must sync after one second");
    for (let i = 0; i < 10; i++) {
      controller.edit({ title: "Original", body: `typing ${i}` });
      await tick(500);
    }
    assert(savedAt.join() === "1000,6000", "continuous input must sync at maxWait, not per keystroke");
    await tick(10000);
    assert(savedAt.length === 2, "unchanged content must not generate new versions");
  } finally {
    controller.dispose();
    globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear;
  }
}

for (const run of [testLocalBackupAndSingleWriter, testEditDuringSaveIsNotLost, testConflictPreservesLocalCopy,
  testRecoveryAndOtherTabRetention, testStorageAndNetworkFailure, testPollConflictAndPublicationLock, testScheduling]) {
  await run();
  console.log(`PASS ${run.name}`);
}
