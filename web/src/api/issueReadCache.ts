/** Bounded, memory-only last-good snapshots. Reads always revalidate. */
export class IssueReadCache {
  private values = new Map<string, { value: unknown; expiresAt: number }>();
  private pending = new Map<string, Promise<unknown>>();
  private generation = 0;

  constructor(private ttlMs = 300_000, private capacity = 100) {}

  peek<T>(key: string): T | null {
    const entry = this.values.get(key);
    if (!entry) return null;
    if (entry.expiresAt <= Date.now()) {
      this.values.delete(key);
      return null;
    }
    return entry.value as T;
  }

  load<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
    const existing = this.pending.get(key);
    if (existing) return existing as Promise<T>;
    const generation = this.generation;
    const request = Promise.resolve().then(fetcher).then(value => {
      if (generation === this.generation) {
        this.values.delete(key);
        this.values.set(key, { value, expiresAt: Date.now() + this.ttlMs });
        while (this.values.size > this.capacity) this.values.delete(this.values.keys().next().value!);
      }
      return value;
    }).finally(() => {
      if (this.pending.get(key) === request) this.pending.delete(key);
    });
    this.pending.set(key, request);
    return request;
  }

  /**
   * Drop one snapshot and start a fresh read.  Detail views use this when an
   * operator explicitly opens or refreshes an Issue: a previously selected
   * Issue must not keep showing an old workflow projection after it enters a
   * queue in another view or project.
   */
  reload<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
    this.generation++;
    this.values.delete(key);
    this.pending.delete(key);
    return this.load(key, fetcher);
  }

  clear(): void {
    this.generation++;
    this.values.clear();
    this.pending.clear();
  }
}
