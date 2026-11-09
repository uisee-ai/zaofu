import { getWorkspaceProjects, initWorkspaceProject } from "../src/api/client.js";
import { clearGetCache } from "../src/api/queryClient.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

async function testInitializationRefreshesRegistry(): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  let registered = false;
  let reads = 0;
  clearGetCache();
  Date.now = () => 1000; // Keep the initial registry response inside its TTL.
  Object.defineProperty(globalThis, "window", {
    configurable: true, value: { localStorage: { getItem: () => "" } },
  });
  globalThis.fetch = async (_input, init) => {
    if (init?.method === "POST") {
      registered = true;
      return Response.json({ ok: true }, { status: 201 });
    }
    reads += 1;
    return Response.json({ items: registered ? [{ project_id: "new-project" }] : [] });
  };
  try {
    assert((await getWorkspaceProjects()).items.length === 0, "initial registry is empty");
    await getWorkspaceProjects();
    assert(reads === 1, "initial registry response should be cached");
    await initWorkspaceProject({ root: "/workspace/new-project" });
    const refreshed = await getWorkspaceProjects();
    assert(refreshed.items[0]?.project_id === "new-project", "new project must be selectable immediately after init");
    assert(reads === 2, "successful initialization must refresh the registry before TTL expiry");
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
    clearGetCache();
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
}

await testInitializationRefreshesRegistry();
console.log("projectInitCache tests passed");
