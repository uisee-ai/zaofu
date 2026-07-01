import { cachedGetJson, cacheStats, clearGetCache } from "../src/api/queryClient.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

async function testSingleFlightAndTtlCache(): Promise<void> {
  clearGetCache();
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ calls }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  const [first, second] = await Promise.all([
    cachedGetJson<{ calls: number }>("/api/snapshot", { ttlMs: 2000 }),
    cachedGetJson<{ calls: number }>("/api/snapshot", { ttlMs: 2000 }),
  ]);

  assert(calls === 1, `expected one network call for concurrent GETs, got ${calls}`);
  assert(first.calls === 1 && second.calls === 1, "single-flight returned inconsistent payloads");
  assert(cacheStats().cached === 1, "expected one cached response");
  assert(cacheStats().inFlight === 0, "in-flight request was not cleared");

  const cached = await cachedGetJson<{ calls: number }>("/api/snapshot", { ttlMs: 2000 });
  assert(calls === 1, `expected TTL cache hit, got ${calls} calls`);
  assert(cached.calls === 1, "TTL cache did not return original payload");
}

async function testPrefixInvalidation(): Promise<void> {
  clearGetCache();
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ calls }), { status: 200 });
  }) as typeof fetch;

  await cachedGetJson<{ calls: number }>("/api/projects/proj-a/snapshot", { ttlMs: 2000 });
  await cachedGetJson<{ calls: number }>("/api/projects/proj-b/snapshot", { ttlMs: 2000 });
  assert(cacheStats().cached === 2, "expected two project cache entries");

  clearGetCache("/api/projects/proj-a");
  assert(cacheStats().cached === 1, "expected prefix invalidation to keep unrelated project cache");

  await cachedGetJson<{ calls: number }>("/api/projects/proj-a/snapshot", { ttlMs: 2000 });
  assert(calls === 3, `expected proj-a refetch after invalidation, got ${calls} calls`);
}

await testSingleFlightAndTtlCache();
await testPrefixInvalidation();
