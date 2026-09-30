type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const DEFAULT_TTL_MS = 750;
const cache = new Map<string, CacheEntry<unknown>>();
const inFlight = new Map<string, Promise<unknown>>();
const generations = new Map<string, number>();

export async function cachedGetJson<T>(
  path: string,
  options: { ttlMs?: number; bypassCache?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const ttlMs = options.ttlMs ?? ttlForPath(path);
  const now = Date.now();
  const generation = generations.get(path) ?? 0;
  if (ttlMs > 0) {
    if (!options.bypassCache && !options.signal) {
      const cached = cache.get(path);
      if (cached && cached.expiresAt > now) return cached.value as T;
    }
    // A caller-owned AbortSignal cannot safely share a promise: aborting one
    // consumer would otherwise abort every consumer of the same cache key.
    if (!options.bypassCache && !options.signal) {
      const pending = inFlight.get(path);
      if (pending) return pending as Promise<T>;
    }
  }
  const promise = fetch(path, {
    headers: { Accept: "application/json" },
    signal: options.signal,
  })
    .then(async (response) => {
      if (!response.ok) throw new Error(`${path} returned ${response.status}`);
      return (await response.json()) as T;
    })
    .then((value) => {
      // Signal-owned requests have an independent lifecycle and can race a
      // shared request for the same key. Keep them out of the shared cache so
      // an older response cannot overwrite a newer caller-owned result.
      if (ttlMs > 0 && !options.signal && (generations.get(path) ?? 0) === generation) {
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

export function clearGetCache(prefix = ""): void {
  if (!prefix) {
    for (const key of new Set([...cache.keys(), ...inFlight.keys()])) {
      generations.set(key, (generations.get(key) ?? 0) + 1);
    }
    cache.clear();
    inFlight.clear();
    return;
  }
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key);
      generations.set(key, (generations.get(key) ?? 0) + 1);
    }
  }
  for (const key of inFlight.keys()) {
    if (key.startsWith(prefix)) {
      inFlight.delete(key);
      generations.set(key, (generations.get(key) ?? 0) + 1);
    }
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
