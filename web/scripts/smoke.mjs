const baseUrl = process.env.ZF_WEB_BASE_URL ?? "http://127.0.0.1:8001";

async function getText(path) {
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.text();
}

async function getJson(path) {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

const html = await getText("/");
if (!html.includes("<div id=\"root\"></div>") && !html.includes("zaofu")) {
  throw new Error("root HTML does not look like the ZaoFu workbench");
}

const snapshot = await getJson("/api/snapshot");
for (const key of [
  "seq",
  "project",
  "tasks",
  "traces",
  "fanouts",
  "candidates",
  "roles",
  "workdirs",
  "skills",
  "runtime",
]) {
  if (!(key in snapshot)) {
    throw new Error(`/api/snapshot missing ${key}`);
  }
}

console.log(`ZaoFu web smoke passed: ${baseUrl} seq=${snapshot.seq}`);
