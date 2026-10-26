type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const DEFAULT_TTL_MS = 750;
const cache = new Map<string, CacheEntry<unknown>>();
const inFlight = new Map<string, Promise<unknown>>();

export async function cachedGetJson<T>(
  path: string,
  options: { ttlMs?: number; bypassCache?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const ttlMs = options.ttlMs ?? ttlForPath(path);
  const now = Date.now();
  if (ttlMs > 0) {
    if (!options.bypassCache && !options.signal) {
      const cached = cache.get(path);
      if (cached && cached.expiresAt > now) return cached.value as T;
    }
    // A caller-owned AbortSignal cannot safely share a promise: aborting one
    // consumer would otherwise abort every consumer of the same cache key.
    if (!options.signal) {
      const pending = inFlight.get(path);
      if (pending) return pending as Promise<T>;
    }
  }
  const promise = fetchReadyJson<T>(path, options.signal)
    .then((value) => {
      // Signal-owned requests have an independent lifecycle and can race a
      // shared request for the same key. Keep them out of the shared cache so
      // an older response cannot overwrite a newer caller-owned result.
      if (ttlMs > 0 && !options.signal) {
        cache.set(path, { value, expiresAt: Date.now() + ttlMs });
      }
      return value;
    })
    .finally(() => {
      if (inFlight.get(path) === promise) inFlight.delete(path);
    });
  if (ttlMs > 0 && !options.signal) inFlight.set(path, promise);
  return promise;
}

async function fetchReadyJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const deadline = Date.now() + 180_000;
  while (true) {
    const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    const value = await response.json();
    if (response.status !== 202 || value?.read_projection?.state !== "building") return value as T;
    if (Date.now() >= deadline) throw new Error("首次数据准备仍未完成，请稍后刷新。");
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
      const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, 1000);
      if (signal?.aborted) abort();
      else signal?.addEventListener("abort", abort, { once: true });
    });
  }
}

export function clearGetCache(prefix = ""): void {
  if (!prefix) {
    cache.clear();
    return;
  }
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}

export function cacheStats(): { cached: number; inFlight: number } {
  return { cached: cache.size, inFlight: inFlight.size };
}

function ttlForPath(path: string): number {
  if (path.includes("/events?")) return 500;
  if (path.includes("/agent-session/history?")) return 500;
  if (path.includes("/operator/output?")) return 0;
  if (path.includes("/stream")) return 0;
  if (path.includes("/web/perf/summary")) return 0;
  if (path.endsWith("/snapshot") || path.endsWith("/snapshot/light")) return 1000;
  if (path.includes("/channels")) return 1000;
  if (path.includes("/delivery-features")) return 1500;
  if (path.includes("/operator/inbox")) return 1500;
  if (path === "/api/workspace/projects") return 2000;
  return DEFAULT_TTL_MS;
}
