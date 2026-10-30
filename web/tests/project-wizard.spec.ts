import { expect, test, type Page } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const artifactDir = process.env.ZF_E2E_ARTIFACT_DIR ?? "";
const bareProjectRoot = process.env.ZF_E2E_BARE_PROJECT_ROOT ?? "";
const blockedProjectRoot = process.env.ZF_E2E_BLOCKED_PROJECT_ROOT ?? "";
const existingProjectRoot = process.env.ZF_E2E_EXISTING_PROJECT_ROOT ?? "";
const newProjectRoot = process.env.ZF_E2E_NEW_PROJECT_ROOT ?? "";
const mobileViewport = process.env.ZF_E2E_VIEWPORT === "mobile";
const viewportLabel = mobileViewport ? "mobile" : "desktop";

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(
  !actionToken
    || !bareProjectRoot
    || !blockedProjectRoot
    || !existingProjectRoot
    || !newProjectRoot,
  "Add/Open Project E2E requires isolated host project paths and an action token.",
);

async function openProjectDialog(page: Page) {
  const navigationToggle = page.getByRole("button", { name: "Open navigation" });
  if (await navigationToggle.isVisible()) {
    await navigationToggle.click();
  }
  const addProjectButton = page.getByTitle("Add Project").or(
    page.locator(".board-panel").getByRole("button", { name: "Add Project" }),
  );
  await expect(addProjectButton).toBeVisible();
  await addProjectButton.click();
  const dialog = page.getByRole("dialog", { name: "Add/Open Project" });
  await expect(dialog).toBeVisible();

  const bounds = await dialog.boundingBox();
  const viewport = page.viewportSize();
  expect(bounds).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport!.width);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport!.height);
  return dialog;
}

async function inspectPath(page: Page, root: string) {
  const dialog = page.getByRole("dialog", { name: "Add/Open Project" });
  await dialog.getByTestId("project-path-input").fill(root);
  const response = page.waitForResponse((candidate) => (
    candidate.url().endsWith("/api/workspace/projects/validate-path")
      && candidate.request().method() === "POST"
  ));
  await dialog.getByTestId("project-inspect").click();
  expect((await response).status()).toBe(200);
  await expect(dialog.getByTestId("project-admission-result")).toBeVisible();
  return dialog;
}

test.beforeEach(async ({ page }, testInfo) => {
  if (mobileViewport) await page.setViewportSize({ width: 390, height: 844 });
  const browserToken = testInfo.title.includes("untrusted browser") ? "" : actionToken;
  await page.addInitScript((token) => {
    window.localStorage.clear();
    if (token) window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.themeMode", "light");
  }, browserToken);
});

test("authorizes an untrusted browser before inspecting a project path", async ({ page }) => {
  await page.goto("/?page=project");

  const dialog = await openProjectDialog(page);
  await dialog.getByTestId("project-path-input").fill(existingProjectRoot);
  await expect(dialog.getByTestId("project-inspect")).toBeDisabled();
  await expect(dialog.getByTestId("project-admission-access")).toBeVisible();

  await dialog.getByLabel("Web action token").fill(actionToken);
  await dialog.getByTestId("project-admission-authorize").click();
  await expect(dialog.getByTestId("project-inspect")).toBeEnabled();
  await inspectPath(page, existingProjectRoot);

  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/project-authorization-${viewportLabel}.png`,
    });
  }
});

test("registers then opens an existing project without init or workflow intake", async ({ page }) => {
  const mutationRequests: string[] = [];
  const intakeRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    if (
      request.url().endsWith("/api/workspace/projects/register")
      || request.url().endsWith("/api/workspace/projects/init")
    ) {
      mutationRequests.push(request.url());
    }
    if (request.url().includes("/workflow-intake")) intakeRequests.push(request.url());
  });

  await page.goto("/?page=project");
  let dialog = await openProjectDialog(page);
  await expect(dialog.getByRole("button", { name: "Existing" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Create" })).toHaveCount(0);
  await expect(dialog.locator("select")).toHaveCount(0);
  await inspectPath(page, existingProjectRoot);
  await expect(dialog.getByTestId("project-admission-action")).toHaveText("register");
  await expect(dialog.getByTestId("project-admission-metadata")).toHaveCount(0);

  await dialog.getByTestId("project-path-input").fill(`${existingProjectRoot}-changed`);
  await expect(dialog.getByTestId("project-admission-result")).toHaveCount(0);
  await expect(dialog.getByTestId("project-admission-submit")).toHaveCount(0);
  dialog = await inspectPath(page, existingProjectRoot);
  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/project-register-${viewportLabel}.png`,
    });
  }

  const registered = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/register")
      && response.request().method() === "POST"
  ));
  await dialog.getByTestId("project-admission-submit").click();
  expect((await registered).status()).toBe(200);
  await expect(dialog).toBeHidden();
  expect(intakeRequests).toHaveLength(0);
  await expect(page.getByLabel("Project", { exact: true })).toHaveAttribute("title", existingProjectRoot);

  const mutationsBeforeOpen = mutationRequests.length;
  dialog = await openProjectDialog(page);
  await inspectPath(page, existingProjectRoot);
  await expect(dialog.getByTestId("project-admission-action")).toHaveText("open");
  await dialog.getByTestId("project-admission-submit").click();
  await expect(dialog).toBeHidden();
  expect(mutationRequests).toHaveLength(mutationsBeforeOpen);
  expect(intakeRequests).toHaveLength(0);
});

test("initializes an existing bare code repository with the internal default", async ({ page }) => {
  const intakeRequests: string[] = [];
  let initPayload: Record<string, unknown> | null = null;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/workflow-intake")) {
      intakeRequests.push(request.url());
    }
    if (
      request.method() === "POST"
      && request.url().endsWith("/api/workspace/projects/init")
    ) {
      initPayload = request.postDataJSON() as Record<string, unknown>;
    }
  });

  await page.goto("/?page=project");
  const dialog = await openProjectDialog(page);
  await inspectPath(page, bareProjectRoot);
  await expect(dialog.getByTestId("project-admission-action")).toHaveText("initialize_project");
  await expect(dialog.getByTestId("project-admission-submit")).toHaveText(/Initialize & Open/);
  await expect(dialog.getByTestId("project-admission-metadata")).toBeVisible();
  await expect(dialog.getByTestId("project-name-input")).not.toHaveValue("");
  await dialog.getByTestId("project-name-input").fill("oap-bare-project");
  await dialog.getByTestId("project-description-input").fill(
    "Browser-verified durable project context",
  );
  await dialog.getByTestId("project-stack-select").selectOption("python");
  await dialog.getByTestId("project-provider-codex").click();
  const mixedToggle = dialog.getByTestId("project-mixed-enabled");
  const mixedEnabled = await mixedToggle.isEnabled();
  if (mixedEnabled) await mixedToggle.check();
  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/project-bare-${viewportLabel}.png`,
    });
  }

  const initialized = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/init")
      && response.request().method() === "POST"
  ));
  await dialog.getByTestId("project-admission-submit").click();
  const initializedResponse = await initialized;
  expect(initializedResponse.status()).toBe(201);
  const initializedBody = await initializedResponse.json() as {
    instruction_docs?: { profile?: Record<string, unknown> };
    project_metadata?: Record<string, unknown>;
    provider_policy?: Record<string, unknown>;
  };
  expect(initializedBody).toMatchObject({
    kind: "prd", config_generated: "registered_controller",
    controller_id: "zaofu-prd-v5-production-codex",
  });
  await expect(dialog).toBeHidden();
  expect(intakeRequests).toHaveLength(0);
  expect(initPayload).toMatchObject({
    name: "oap-bare-project",
    description: "Browser-verified durable project context",
    stack: "python",
    backend: "codex",
    mixed_enabled: mixedEnabled,
  });
  expect(initializedBody.project_metadata).toEqual({
    name: "oap-bare-project",
    description: "Browser-verified durable project context",
  });
  expect(initializedBody.provider_policy).toMatchObject({
    primary_backend: "codex",
    mixed_enabled: mixedEnabled,
  });
  expect(initializedBody.instruction_docs?.profile).toMatchObject({
    confidence: "declared",
    languages: ["python"],
  });
  await expect(page.getByLabel("Project", { exact: true })).toHaveAttribute("title", bareProjectRoot);
  await expect(page.getByTestId("project-description")).toHaveText(
    "Browser-verified durable project context",
  );
});

test("creates a missing project with the internal default", async ({ page }) => {
  await page.goto("/?page=project");
  const dialog = await openProjectDialog(page);
  await inspectPath(page, newProjectRoot);
  await expect(dialog.getByTestId("project-admission-action")).toHaveText("initialize_project");
  await expect(dialog.getByTestId("project-admission-submit")).toHaveText(/Create Project/);
  await dialog.getByTestId("project-name-input").fill("oap-new-project");
  await dialog.getByTestId("project-description-input").fill("New Project metadata");
  await dialog.getByTestId("project-stack-select").selectOption("go");

  const initialized = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/init")
      && response.request().method() === "POST"
  ));
  await dialog.getByTestId("project-admission-submit").click();
  const initializedResponse = await initialized;
  expect(initializedResponse.status()).toBe(201);
  const initializedBody = await initializedResponse.json() as {
    git_readiness?: {
      created?: boolean;
      head?: string;
      ready?: boolean;
    };
  };
  expect(initializedBody.git_readiness).toMatchObject({
    created: true,
    ready: true,
  });
  expect(initializedBody.git_readiness?.head).toMatch(/^[0-9a-f]{40,64}$/);
  expect(initializedBody).toMatchObject({
    kind: "prd", config_generated: "registered_controller",
    controller_id: "zaofu-prd-v5-production-codex",
  });
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("Project", { exact: true })).toHaveAttribute("title", newProjectRoot);
  await page.reload();
  await expect(page.getByLabel("Project", { exact: true })).toHaveAttribute("title", newProjectRoot);
});

test("blocks an invalid project without register or init mutation", async ({ page }) => {
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST"
      && (
        request.url().endsWith("/api/workspace/projects/register")
        || request.url().endsWith("/api/workspace/projects/init")
      )
    ) {
      mutations.push(request.url());
    }
  });

  await page.goto("/?page=project");
  const dialog = await openProjectDialog(page);
  await inspectPath(page, blockedProjectRoot);
  await expect(dialog.getByTestId("project-admission-action")).toHaveText("blocked");
  await expect(dialog.getByTestId("project-admission-metadata")).toHaveCount(0);
  await expect(dialog.getByTestId("project-admission-submit")).toHaveCount(0);
  await expect(dialog.getByText(/zf\.yaml is invalid/)).toBeVisible();
  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/project-blocked-${viewportLabel}.png`,
    });
  }
  expect(mutations).toHaveLength(0);
});
