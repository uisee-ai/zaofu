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

async function testBypassCacheDoesNotJoinAStaleInFlightRequest(): Promise<void> {
  clearGetCache();
  let calls = 0;
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  globalThis.fetch = (async () => {
    calls += 1;
    await gate;
    return new Response(JSON.stringify({ calls }), { status: 200 });
  }) as typeof fetch;

  const first = cachedGetJson<{ calls: number }>("/api/projects/p/channels/x/conversation", {
    bypassCache: true,
  });
  const second = cachedGetJson<{ calls: number }>("/api/projects/p/channels/x/conversation", {
    bypassCache: true,
  });
  assert(calls === 2, `bypass-cache GETs must be independent, got ${calls} calls`);
  release?.();
  const rows = await Promise.all([first, second]);
  assert(rows.map((row) => row.calls).join(",") === "2,2", "bypass requests did not complete");

  await cachedGetJson<{ calls: number }>("/api/projects/p/channels/x/conversation", {
    bypassCache: true,
  });
  assert(calls === 3, "a later bypass request must not reuse a completed response");
}

async function testInvalidatedInFlightRequestCannotRepopulateCache(): Promise<void> {
  clearGetCache();
  let calls = 0;
  let releaseStale: (() => void) | undefined;
  const staleGate = new Promise<void>((resolve) => {
    releaseStale = resolve;
  });
  globalThis.fetch = (async () => {
    calls += 1;
    const call = calls;
    if (call === 1) await staleGate;
    return new Response(JSON.stringify({ call }), { status: 200 });
  }) as typeof fetch;

  const path = "/api/projects/p/issue-triage";
  const stale = cachedGetJson<{ call: number }>(path, { ttlMs: 2000 });
  clearGetCache("/api/projects/p/issue-triage");
  const fresh = await cachedGetJson<{ call: number }>(path, {
    ttlMs: 2000,
    bypassCache: true,
  });
  assert(fresh.call === 2, "post-action refresh should receive the fresh response");
  releaseStale?.();
  assert((await stale).call === 1, "the original caller should still settle normally");
  const cached = await cachedGetJson<{ call: number }>(path, { ttlMs: 2000 });
  assert(cached.call === 2, "a stale in-flight response repopulated the cleared cache");
  assert(calls === 2, `expected the fresh response to remain cached, got ${calls} calls`);
}

async function testCallerAbortDoesNotCancelOrEvictSharedRequest(): Promise<void> {
  clearGetCache();
  let calls = 0;
  let releaseShared: (() => void) | undefined;
  const sharedGate = new Promise<void>((resolve) => {
    releaseShared = resolve;
  });
  globalThis.fetch = (async (_input, init) => {
    calls += 1;
    if (!init?.signal) {
      await sharedGate;
      return new Response(JSON.stringify({ kind: "shared" }), { status: 200 });
    }
    return await new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        reject(new DOMException("cancelled", "AbortError"));
      }, { once: true });
    });
  }) as typeof fetch;

  const shared = cachedGetJson<{ kind: string }>("/api/projects/p/delivery-traces/F-1", {
    ttlMs: 2000,
  });
  const controller = new AbortController();
  const owned = cachedGetJson<{ kind: string }>("/api/projects/p/delivery-traces/F-1", {
    bypassCache: true,
    signal: controller.signal,
    ttlMs: 2000,
  });
  assert(calls === 2, "a caller-owned signal must not share another consumer's promise");
  controller.abort();
  await owned.catch((error: unknown) => {
    assert(error instanceof DOMException && error.name === "AbortError", "aborted request should reject with AbortError");
  });
  assert(cacheStats().inFlight === 1, "aborting an owned request must keep the shared request registered");
  releaseShared?.();
  const result = await shared;
  assert(result.kind === "shared", "the shared request should still complete");
  assert(cacheStats().inFlight === 0, "completed shared request should clear its registration");
}

async function testCallerOwnedRequestDoesNotReadOrWriteSharedCache(): Promise<void> {
  clearGetCache();
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ calls }), { status: 200 });
  }) as typeof fetch;

  const path = "/api/projects/p/delivery-traces/F-2";
  const seeded = await cachedGetJson<{ calls: number }>(path, { ttlMs: 2000 });
  assert(seeded.calls === 1, "shared request should seed the cache");
  const owned = await cachedGetJson<{ calls: number }>(path, {
    signal: new AbortController().signal,
    ttlMs: 2000,
  });
  assert(owned.calls === 2, "caller-owned request must bypass a shared cached response");
  const cached = await cachedGetJson<{ calls: number }>(path, { ttlMs: 2000 });
  assert(cached.calls === 1, "caller-owned response must not overwrite the shared cache");
  assert(calls === 2, `expected two network requests, got ${calls}`);
}

await testSingleFlightAndTtlCache();
await testPrefixInvalidation();
await testBypassCacheDoesNotJoinAStaleInFlightRequest();
await testInvalidatedInFlightRequestCannotRepopulateCache();
await testCallerAbortDoesNotCancelOrEvictSharedRequest();
await testCallerOwnedRequestDoesNotReadOrWriteSharedCache();
