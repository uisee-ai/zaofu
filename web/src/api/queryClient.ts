type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const DEFAULT_TTL_MS = 750;
const cache = new Map<string, CacheEntry<unknown>>();
const inFlight = new Map<string, Promise<unknown>>();

export async function cachedGetJson<T>(path: string, options: { ttlMs?: number; bypassCache?: boolean } = {}): Promise<T> {
  const ttlMs = options.ttlMs ?? ttlForPath(path);
  const now = Date.now();
  if (!options.bypassCache && ttlMs > 0) {
    const cached = cache.get(path);
    if (cached && cached.expiresAt > now) return cached.value as T;
    const pending = inFlight.get(path);
    if (pending) return pending as Promise<T>;
  }
  const promise = fetch(path, { headers: { Accept: "application/json" } })
    .then(async (response) => {
      if (!response.ok) throw new Error(`${path} returned ${response.status}`);
      return (await response.json()) as T;
    })
    .then((value) => {
      if (ttlMs > 0) cache.set(path, { value, expiresAt: Date.now() + ttlMs });
      return value;
    })
    .finally(() => {
      inFlight.delete(path);
    });
  if (!options.bypassCache && ttlMs > 0) inFlight.set(path, promise);
  return promise;
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
