import type { DraftBackupStore, DraftSnapshot, DraftText, LocalIssueDraft } from "./selfIssueDraftStore.js";

const same = (a: DraftText, b: DraftText) => a.title === b.title && a.body === b.body;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
export class DraftSaveConflict extends Error {}
export interface AutosaveView {
  content: DraftText;
  ready: boolean;
  status: "saved" | "editing" | "local" | "saving" | "offline" | "conflict";
  revision: number;
  localError: string;
  remoteError: string;
  recovery: LocalIssueDraft[];
  remote: DraftSnapshot;
}
interface Options {
  scope: string;
  clientId: string;
  remote: DraftSnapshot;
  store: DraftBackupStore;
  save: (content: DraftText, revision: number) => Promise<DraftSnapshot>;
  refresh: () => Promise<DraftSnapshot>;
  changed: (view: AutosaveView) => void;
  timing?: { local: number; debounce: number; maxWait: number };
}

/** Single server writer and an independent per-tab recovery copy. */
export class IssueDraftAutosave {
  private base: DraftSnapshot;
  private view: AutosaveView;
  private localTimer?: ReturnType<typeof setTimeout>;
  private debounceTimer?: ReturnType<typeof setTimeout>;
  private maxTimer?: ReturnType<typeof setTimeout>;
  private inFlight?: Promise<void>;
  private localQueue: Promise<void> = Promise.resolve();
  private recoveredCopies: LocalIssueDraft[] = [];
  private disposed = false;
  private readonly timing;
  constructor(private options: Options) {
    this.base = { ...options.remote };
    this.timing = options.timing || { local: 300, debounce: 1000, maxWait: 5000 };
    this.view = { content: { title: this.base.title, body: this.base.body }, ready: false,
      status: "saved", revision: this.base.revision, localError: "", remoteError: "", recovery: [], remote: this.base };
  }
  snapshot() { return this.view; }
  private emit(patch: Partial<AutosaveView> = {}) {
    this.view = { ...this.view, ...patch };
    if (!this.disposed) this.options.changed(this.view);
  }
  private dirty() { return !same(this.view.content, this.base); }
  async start() {
    try {
      const backups = (await this.options.store.read(this.options.scope)).filter((item) => !same(item, this.base));
      if (this.disposed) return;
      if (backups.length) {
        const latest = backups[0];
        this.recoveredCopies = backups;
        const unambiguous = backups.every((item) => same(item, latest));
        if (!this.base.locked && unambiguous && same(latest.base, this.base)) {
          this.emit({ content: { title: latest.title, body: latest.body }, ready: true, status: "local" });
          await this.persist();
          this.scheduleRemote();
          return;
        }
        this.emit({ recovery: backups, ready: true, status: "conflict" });
        return;
      }
      this.emit({ ready: true });
    } catch (error) { this.emit({ ready: true, localError: message(error) }); }
  }
  edit(content: DraftText) {
    if (!this.view.ready || this.base.locked || this.view.remote.locked || this.disposed) return;
    this.emit({ content, status: this.view.status === "conflict" ? "conflict" : "editing" });
    if (!this.dirty() && this.view.status !== "conflict") {
      this.clearRemoteTimers(); this.emit({ status: "saved" });
    }
    // Throttled trailing write also protects uninterrupted typing.
    if (!this.localTimer) this.localTimer = setTimeout(() => { this.localTimer = undefined; void this.persist(); }, this.timing.local);
    this.scheduleRemote();
  }
  observe(remote: DraftSnapshot) {
    // A save response is authoritative for its own request. A concurrent poll
    // may arrive just before it; CAS/next poll handles a different tab's write.
    if (this.inFlight) return;
    if (remote.revision < this.base.revision || (remote.revision === this.base.revision && remote.locked === this.base.locked)) return;
    if (same(remote, this.base) && !remote.locked) {
      this.base = remote;
      this.emit({ revision: remote.revision, remote });
      return;
    }
    if (this.dirty() || this.view.status === "conflict") {
      this.clearRemoteTimers();
      this.emit({ status: "conflict", remote, remoteError: remote.locked ? "Draft 已进入发布状态，本机编辑仍保留" : "服务端版本已变化，请比较后选择" });
      void this.persist();
    } else {
      this.base = remote;
      this.emit({ content: { title: remote.title, body: remote.body }, revision: remote.revision, remote });
    }
  }
  private clearRemoteTimers() {
    clearTimeout(this.debounceTimer); clearTimeout(this.maxTimer);
    this.debounceTimer = this.maxTimer = undefined;
  }
  private scheduleRemote() {
    if (this.disposed || !this.dirty() || this.view.status === "conflict" || this.base.locked) return;
    clearTimeout(this.debounceTimer);
    const sync = () => { void this.flush().catch(() => undefined); };
    this.debounceTimer = setTimeout(sync, this.timing.debounce);
    if (!this.maxTimer) this.maxTimer = setTimeout(sync, this.timing.maxWait);
  }
  async persist() {
    clearTimeout(this.localTimer); this.localTimer = undefined;
    this.localQueue = this.localQueue.then(async () => {
      const content = { ...this.view.content };
      const key = `${this.options.scope}:${this.options.clientId}`;
      try {
        if (this.dirty()) {
          await this.options.store.put({ ...content, key, scope: this.options.scope, base: { ...this.base }, updatedAt: Date.now() });
          if (same(content, this.view.content) && this.view.status === "editing") this.emit({ status: "local" });
        } else {
          await this.options.store.remove(key);
          if (this.view.status !== "conflict") {
            for (const old of this.recoveredCopies) await this.options.store.remove(old.key, old);
            this.recoveredCopies = [];
          }
        }
        this.emit({ localError: "" });
      } catch (error) { this.emit({ localError: message(error) }); }
    });
    await this.localQueue;
  }
  flush(): Promise<void> {
    if (this.inFlight) return this.inFlight;
    this.clearRemoteTimers();
    this.inFlight = Promise.resolve().then(async () => {
      if (!this.view.ready) throw new Error("正在恢复本机草稿，请稍后");
      if (this.view.status === "conflict") throw new DraftSaveConflict("请先处理 Draft 版本冲突");
      while (this.dirty() && !this.disposed) {
        if (this.base.locked || this.view.remote.locked) throw new DraftSaveConflict("Draft 已锁定，本机编辑未覆盖服务端");
        const submitted = { ...this.view.content };
        this.emit({ status: "saving", remoteError: "" });
        try {
          const result = await this.options.save(submitted, this.base.revision);
          this.base = result;
          this.emit({ revision: result.revision, remote: result,
            content: same(this.view.content, submitted) ? { title: result.title, body: result.body } : this.view.content,
            status: same(this.view.content, submitted) ? "saved" : "editing" });
          await this.persist();
        } catch (error) {
          const conflict = error instanceof DraftSaveConflict;
          this.clearRemoteTimers();
          this.emit({ status: conflict ? "conflict" : "offline", remoteError: message(error) });
          await this.persist();
          if (conflict) {
            try { this.emit({ remote: await this.options.refresh() }); } catch { /* Keep the known base. */ }
          }
          throw error;
        }
      }
    }).finally(() => { this.inFlight = undefined; });
    return this.inFlight;
  }
  async resolve(choice: "server" | "local", backup?: LocalIssueDraft) {
    const remote = await this.options.refresh();
    if (choice === "local" && remote.locked) throw new DraftSaveConflict("已发布 Draft 不可覆盖；可复制本机内容另存");
    this.base = remote;
    const chosen = choice === "server" ? remote : backup || this.view.content;
    this.emit({ content: { title: chosen.title, body: chosen.body }, remote, revision: remote.revision,
      status: "editing", remoteError: "", recovery: [] });
    await this.persist();
    if (choice === "local") await this.flush();
    else this.emit({ status: "saved" });
  }
  dispose() {
    this.disposed = true;
    this.clearRemoteTimers();
    clearTimeout(this.localTimer);
    void this.persist();
  }
}
