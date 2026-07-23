import { expect, test, type Page } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const bareProjectRoot = process.env.ZF_E2E_BARE_PROJECT_ROOT ?? "";
const existingProjectRoot = process.env.ZF_E2E_EXISTING_PROJECT_ROOT ?? "";
const newProjectRoot = process.env.ZF_E2E_NEW_PROJECT_ROOT ?? "";
const mobileViewport = process.env.ZF_E2E_VIEWPORT === "mobile";

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(
  !actionToken || !bareProjectRoot || !existingProjectRoot || !newProjectRoot,
  "Project Wizard E2E requires isolated host project paths and an action token.",
);

async function openProjectWizard(page: Page) {
  const navigationToggle = page.getByRole("button", { name: "Open navigation" });
  if (await navigationToggle.isVisible()) {
    await navigationToggle.click();
  }
  const addProjectButton = page.getByTitle("Add Project").or(
    page.locator(".board-panel").getByRole("button", { name: "Add Project" }),
  );
  await expect(addProjectButton).toBeVisible();
  await addProjectButton.click();
  const dialog = page.getByRole("dialog", { name: "Workspace Project" });
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

async function expectCleanExistingDraft(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Workspace Project" });
  await expect(dialog.getByRole("button", { name: "Existing" })).toHaveClass(/active/);
  await expect(dialog.getByPlaceholder("/path/to/project")).toHaveValue("");
  await expect(dialog.locator("pre")).toHaveCount(0);
}

test.beforeEach(async ({ page }) => {
  if (mobileViewport) await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript((token) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.themeMode", "light");
  }, actionToken);
});

test("registers an existing ZaoFu project without starting a workflow", async ({ page }) => {
  const intakeRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/workflow-intake")) {
      intakeRequests.push(request.url());
    }
  });

  await page.goto("/?page=project");
  const dialog = await openProjectWizard(page);
  await dialog.getByPlaceholder("/path/to/project").fill(existingProjectRoot);
  await dialog.getByTestId("wizard-inspect-existing").click();
  await expect(dialog.getByTestId("wizard-candidates")).toBeVisible();
  await expect(dialog.getByTestId("wizard-bare-repo")).toHaveCount(0);

  const registered = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/register") && response.request().method() === "POST"
  ));
  await dialog.getByRole("button", { name: "Register" }).click();
  expect((await registered).status()).toBe(200);
  await expect(dialog).toBeHidden();
  expect(intakeRequests).toHaveLength(0);
  await expect(page.getByLabel("Project")).toHaveAttribute("title", existingProjectRoot);

  await openProjectWizard(page);
  await expectCleanExistingDraft(page);
});

test("creates a default multi project without starting an unsupported intake", async ({ page }) => {
  const intakeRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/workflow-intake")) {
      intakeRequests.push(request.url());
    }
  });

  await page.goto("/?page=project");
  const dialog = await openProjectWizard(page);
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(dialog.getByTestId("wizard-kind")).toHaveValue("multi");
  await dialog.getByPlaceholder("/path/to/project").fill(newProjectRoot);
  await dialog.getByTestId("wizard-apply-profile").uncheck();

  const initialized = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/init") && response.request().method() === "POST"
  ));
  await dialog.getByRole("button", { name: "Initialize" }).click();
  expect((await initialized).status()).toBe(201);
  await expect(dialog).toBeHidden();
  expect(intakeRequests).toHaveLength(0);
  await expect(page.getByLabel("Project")).toHaveAttribute("title", newProjectRoot);

  await openProjectWizard(page);
  await expectCleanExistingDraft(page);
});

test("closes after concrete workflow project intake succeeds", async ({ page }) => {
  for (const kind of ["issue", "prd", "refactor"] as const) {
    await page.goto("/?page=project");
    const dialog = await openProjectWizard(page);
    await dialog.getByRole("button", { name: "Create" }).click();
    await dialog.getByTestId("wizard-kind").selectOption(kind);
    const projectRoot = `${newProjectRoot}-${kind}`;
    await dialog.getByPlaceholder("/path/to/project").fill(projectRoot);
    if (kind === "refactor") {
      await dialog.getByTestId("wizard-source-root").fill(existingProjectRoot);
    }
    await dialog.getByTestId("wizard-apply-profile").uncheck();

    const initialized = page.waitForResponse((response) => (
      response.url().endsWith("/api/workspace/projects/init") && response.request().method() === "POST"
    ));
    const intakeCreated = page.waitForResponse((response) => (
      response.url().includes("/workflow-intake") && response.request().method() === "POST"
    ));
    await dialog.getByRole("button", { name: "Initialize" }).click();
    expect((await initialized).status(), `${kind} init`).toBe(201);
    expect((await intakeCreated).status(), `${kind} intake`).toBe(200);
    await expect(dialog, `${kind} dialog`).toBeHidden();
    await expect(page.getByLabel("Project")).toHaveAttribute("title", projectRoot);
  }
});

test("routes a bare Git project through detected preset initialization", async ({ page }) => {
  await page.goto("/?page=project");
  const dialog = await openProjectWizard(page);
  await dialog.getByPlaceholder("/path/to/project").fill(bareProjectRoot);
  await dialog.getByTestId("wizard-inspect-existing").click();
  await expect(dialog.getByTestId("wizard-bare-repo")).toBeVisible();
  await dialog.getByTestId("wizard-bootstrap-init").click();

  await expect(dialog.getByRole("button", { name: "Create" })).toHaveClass(/active/);
  await expect(dialog.getByTestId("wizard-kind")).toHaveValue("");
  await expect(dialog.getByTestId("wizard-preset")).toBeVisible();
  await dialog.getByTestId("wizard-apply-profile").uncheck();

  const initialized = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/projects/init") && response.request().method() === "POST"
  ));
  await dialog.getByRole("button", { name: "Initialize" }).click();
  expect((await initialized).status()).toBe(201);
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("Project")).toHaveAttribute("title", bareProjectRoot);
});
