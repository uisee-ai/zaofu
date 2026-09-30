export interface DraftText { title: string; body: string }
export interface DraftSnapshot extends DraftText { revision: number; locked?: boolean }
export interface LocalIssueDraft extends DraftText {
  key: string;
  scope: string;
  base: DraftSnapshot;
  updatedAt: number;
}
export interface DraftBackupStore {
  read(scope: string): Promise<LocalIssueDraft[]>;
  put(value: LocalIssueDraft): Promise<void>;
  remove(key: string, expected?: LocalIssueDraft): Promise<void>;
}

let databasePromise: Promise<IDBDatabase> | undefined;
function openDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("zf-self-issue", 2);
    const timeout = window.setTimeout(() => reject(new Error("本机草稿数据库打开超时")), 3000);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains("drafts")) request.result.createObjectStore("drafts", { keyPath: "key" });
      const store = request.transaction!.objectStore("drafts");
      if (!store.indexNames.contains("scope")) store.createIndex("scope", "scope");
    };
    request.onsuccess = () => {
      window.clearTimeout(timeout);
      request.result.onversionchange = () => { request.result.close(); databasePromise = undefined; };
      resolve(request.result);
    };
    request.onerror = () => { window.clearTimeout(timeout); reject(request.error); };
    request.onblocked = () => { window.clearTimeout(timeout); reject(new Error("本机草稿数据库被其他页面占用")); };
  }).catch((error) => { databasePromise = undefined; throw error; });
  return databasePromise;
}

// Request success is not commit: quota failures can still abort the transaction.
async function transaction<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", mode);
    const request = operation(tx.objectStore("drafts"));
    tx.oncomplete = () => resolve(request.result);
    tx.onabort = tx.onerror = () => reject(tx.error || new Error("本机草稿写入失败"));
  });
}

export const issueDraftBackupStore: DraftBackupStore = {
  async read(scope) {
    const values: unknown[] = await transaction("readonly", (store) => store.index("scope").getAll(scope));
    return values.filter((raw): raw is LocalIssueDraft => {
      if (!raw || typeof raw !== "object") return false;
      const item = raw as LocalIssueDraft;
      return item.scope === scope && typeof item.key === "string"
        && typeof item.title === "string" && typeof item.body === "string"
        && typeof item.updatedAt === "number" && typeof item.base?.revision === "number"
        && typeof item.base.title === "string" && typeof item.base.body === "string";
    }).sort((a, b) => b.updatedAt - a.updatedAt);
  },
  async put(value) { await transaction("readwrite", (store) => store.put(value)); },
  async remove(key, expected) {
    if (!expected) { await transaction("readwrite", (store) => store.delete(key)); return; }
    await transaction("readwrite", (store) => {
      const request = store.get(key);
      request.onsuccess = () => {
        const current = request.result as LocalIssueDraft | undefined;
        // Another tab may have edited this copy since recovery read it.
        if (current && current.updatedAt === expected.updatedAt && current.title === expected.title
          && current.body === expected.body && current.base.revision === expected.base.revision) store.delete(key);
      };
      return request;
    });
  },
};
