import { expect, test } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

test("drafts and late attachments stay with their original thread", async ({ page }) => {
  await installUiFixture(page);
  await page.route("**/webchat/runtime**", (route) => route.fulfill({ json: {
    schema_version: "webchat-runtime.v1", project_id: "ui-review", conversation_id: "kanban:ui-review", thread_id: "main",
    capabilities: { actions: [], controls: [], native_steer: false, steer_fallback: "queue", completion_authority: false },
    turns: [], interactions: [], queue: [], threads: [], artifacts: [], messages: [],
    tool_catalog: { schema_version: "webchat-tool-catalog.v1", digest: "fixture", surface: "kanban_agent", credentials_exposed: false, items: [] },
  } }));
  let releaseUpload: (() => void) | undefined;
  const uploadReleased = new Promise<void>((resolve) => { releaseUpload = resolve; });
  await page.route("**/webchat/artifacts", async (route) => {
    await uploadReleased;
    await route.fulfill({ json: { artifact: {
      version_id: "v1", storage_uri: "artifact:v1", digest: "sha256:one", logical_name: "attachments/spec.txt",
    } } });
  });
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  const input = panel.locator("textarea.headless-input");
  await panel.getByRole("button", { name: "Add to message" }).click();
  const search = panel.getByLabel("Search attachments, context and tools");
  await expect(search).toBeFocused();
  await expect(panel.getByRole("button", { name: "Reference selected task" })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Reference plan" })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Reference artifact" })).toBeVisible();
  await expect(panel.getByRole("button", { name: /Conversation/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /Agent profile/ })).toBeVisible();
  await search.fill("artifact");
  await expect(panel.getByRole("button", { name: "Upload file" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Reference artifact" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel.getByRole("button", { name: "Add to message" })).toBeFocused();
  await input.fill("main draft");
  await panel.locator('input[type="file"]').setInputFiles({ name: "spec.txt", mimeType: "text/plain", buffer: Buffer.from("spec") });
  await expect(panel.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "New Kanban Agent chat" }).click();
  await expect(input).toHaveValue("");
  await input.fill("second draft");
  releaseUpload?.();
  await expect(panel.getByText("spec.txt", { exact: true })).toHaveCount(0);
  await panel.locator(".agent-thread-chip").filter({ hasText: "main" }).click();
  await expect(input).toHaveValue("main draft");
  await expect(panel.getByText("spec.txt", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.locator("textarea.headless-input")).toHaveValue("main draft");
  await expect(page.getByText("spec.txt", { exact: true })).toBeVisible();
});

test("Plan reference follows its draft across threads and reload", async ({ page }) => {
  await installUiFixture(page);
  await page.addInitScript(() => {
    const key = "zf.kanbanComposer.v1:ui-review:kanban%3Aui-review";
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({
      message: "Question about plan", attachments: [], unreferencedTasks: [], planDiscussion: {
        requestEventId: "event-1", requestId: "plan-1", revision: 1, header: "Delivery plan",
        questionId: "question-1", question: "Choose a route", options: [], questions: [], allowOther: true, valid: true,
      },
    }));
  });
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await expect(panel.getByText("Delivery plan", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "New Kanban Agent chat" }).click();
  await expect(panel.getByText("Delivery plan", { exact: true })).toHaveCount(0);
  await panel.locator(".agent-thread-chip").filter({ hasText: "main" }).click();
  await expect(panel.getByText("Delivery plan", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByText("Delivery plan", { exact: true })).toBeVisible();
  await expect(page.locator("textarea.headless-input")).toHaveValue("Question about plan");
});

test("draft and delayed upload remain in their original project", async ({ page }) => {
  await installUiFixture(page);
  const projects = ["ui-review", "ui-other"].map((project_id) => ({
    project_id, name: project_id, root: `/workspace/${project_id}`, can_open_board: true,
    lifecycle: { initialized: true, can_open_board: true },
  }));
  await page.route("**/api/workspace/projects", (route) => route.fulfill({ json: {
    items: projects, projects, active_project_id: "ui-review", server_default_project_id: "ui-review",
  } }));
  await page.route(/\/api\/projects\/[^/]+\/snapshot(?:\/(?:light|board|observability))?$/, (route) => {
    const projectId = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({ json: {
      seq: 1, generated_at: "2026-10-26T12:30:00Z", project: { project_id: projectId, name: projectId, root: `/workspace/${projectId}` },
      tasks: [], archive_tasks: [], agents: [], features: [], traces: [], fanouts: [], candidates: [], runs: [], active_runs: [], roles: [], workdirs: [], workers: [],
      skills: { pool: [], bindings: [] }, cost: { total: { usd: 0, input_tokens: 0, output_tokens: 0 }, by_role: {} },
      runtime: { mode: "live", live: true, actions: { mutation_enabled: true, allowed: ["chat-orchestrator"] },
        web_session: { mode: "local_trusted", actions_enabled: true } },
    } });
  });
  let release!: () => void;
  const uploadReady = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/webchat/artifacts", async (route) => {
    await uploadReady;
    await route.fulfill({ json: { artifact: { version_id: "project-v1", storage_uri: "artifact:v1", digest: "sha256:one", logical_name: "attachments/a.txt" } } });
  });
  try {
    await page.goto("/?project=ui-review&page=board");
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
    const panel = page.getByRole("dialog", { name: "Kanban Agent" });
    const input = panel.locator("textarea.headless-input");
    await input.fill("project A draft");
    await panel.locator('input[type="file"]').setInputFiles({ name: "a.txt", mimeType: "text/plain", buffer: Buffer.from("a") });
    await expect(panel.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
    await page.getByLabel("Project", { exact: true }).selectOption("ui-other");
    await expect(input).toHaveValue("");
    await input.fill("project B draft");
    release();
    await expect(panel.getByText("a.txt", { exact: true })).toHaveCount(0);
    await page.getByLabel("Project", { exact: true }).selectOption("ui-review");
    await expect(input).toHaveValue("project A draft");
    await expect(panel.getByText("a.txt", { exact: true })).toBeVisible();
    await page.getByLabel("Project", { exact: true }).selectOption("ui-other");
    await expect(input).toHaveValue("project B draft");
  } finally {
    release();
  }
});

test("partial upload failure preserves successful attachments", async ({ page }) => {
  await installUiFixture(page);
  let count = 0;
  await page.route("**/webchat/artifacts", async (route) => {
    count += 1;
    if (count === 1) {
      await route.fulfill({ json: { ok: true, artifact: {
        version_id: "v1", storage_uri: "artifact:v1", digest: "sha256:one", logical_name: "attachments/first.txt",
      } } });
    } else {
      await route.fulfill({ status: 500, json: { reason: "upload failed" } });
    }
  });
  await page.goto("/?project=ui-review&page=board");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  const panel = page.getByRole("dialog", { name: "Kanban Agent" });
  await panel.locator('input[type="file"]').setInputFiles([
    { name: "first.txt", mimeType: "text/plain", buffer: Buffer.from("one") },
    { name: "second.txt", mimeType: "text/plain", buffer: Buffer.from("two") },
  ]);
  await expect(panel.getByText("first.txt", { exact: true })).toBeVisible();
  await expect(panel.getByRole("alert")).toContainText("upload failed");
});
