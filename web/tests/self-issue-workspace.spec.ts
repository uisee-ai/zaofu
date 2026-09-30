import { test, expect } from "@playwright/test";

const issuePort = Number(process.env.ZF_ISSUE_E2E_PORT || 4176);
test.use({ baseURL: `http://127.0.0.1:${issuePort}` });

test("free-form /issue opens a Draft and keeps assessment user-selected while runtime is live", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  const input = page.locator("textarea.headless-input");
  await input.fill("/issue Page freezes after Save\n\nSteps:\n1. Open the page\n2. Click Save");
  await input.press("Enter");
  await expect(page.getByRole("dialog", { name: "Self-Issue Draft" })).toBeVisible();
  await expect(page.locator('input[value="Page freezes after Save"]')).toBeVisible();
  await expect(page.getByText("Choose whether to run the project-assessment agent.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with project assessment" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with limited report" })).toBeVisible();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-capture"');
  await expect(page.locator("#fixture-calls")).toContainText('"source":"browser_detector"');
  await expect(page.locator("#fixture-calls")).not.toContainText('"action":"self-issue-evaluation-start"');
});

test("long Chinese /issue text is projected as distinct Draft fields", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  const input = page.locator("textarea.headless-input");
  await input.fill("/issue 我在设置页改了通知选项，点击保存后页面一直显示处理中，按钮也点不了。刷新页面还是卡住。我希望保存完成后页面恢复可操作，并显示最新设置。我本来想附控制台截图，但这次没有附件。");
  await input.press("Enter");

  await expect(page.getByLabel("Title")).toHaveValue("保存后页面持续卡死且刷新无效");
  await expect(page.getByLabel("Describe the bug")).toContainText("修改通知选项并保存后");
  await expect(page.getByLabel("To reproduce")).toContainText("点击保存");
  await expect(page.getByLabel("Actual behavior")).toContainText("持续显示处理中");
  await expect(page.getByLabel("Expected behavior")).toContainText("页面恢复可操作");
  await expect(page.getByLabel("Attachment context")).toContainText("未附加文件");
});

test("limited report is explicit and does not dispatch project assessment", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with limited report" }).click();
  await expect(page.locator("#fixture-calls")).not.toContainText('"action":"self-issue-evaluation-start"');
  await expect(page.locator("#fixture-calls")).toContainText('"assessment_decision":"limited_report"');
});

test("Evaluation Lock makes the whole Draft read-only until cancellation", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with project assessment" }).click();

  await expect(page.getByText("Evaluation is running. This Draft is read-only.")).toBeVisible();
  await expect(page.getByLabel("Title")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel evaluation" })).toBeVisible();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-evaluation-start"');

  await page.getByRole("button", { name: "Cancel evaluation" }).click();
  await expect(page.getByText("Evaluation is running. This Draft is read-only.")).toHaveCount(0);
  await expect(page.getByLabel("Title")).toBeEnabled();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-evaluation-cancel"');
});

test("Suggestion dismissal and Conflict reinvestigation update the current projection", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html?scenario=enrichment");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");

  const suggestions = page.getByRole("region", { name: "Evaluation suggestions" });
  await expect(suggestions).toContainText("The Save request remains pending.");
  await suggestions.getByRole("button", { name: "Dismiss" }).click();
  await expect(suggestions).toHaveCount(0);
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-suggestion-dismiss"');

  const conflicts = page.getByRole("region", { name: "Evaluation conflicts" });
  await expect(conflicts).toContainText("Page becomes unresponsive.");
  await conflicts.getByRole("button", { name: "Reinvestigate" }).click();
  await expect(conflicts).toHaveCount(0);
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-conflict-resolve"');
  await expect(page.locator("#fixture-calls")).toContainText('"resolution":"needs_reinvestigation"');
});

test("Evaluation Lock disables Suggestion and Conflict decisions", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html?scenario=enrichment&locked=1");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");

  await expect(page.getByRole("region", { name: "Evaluation suggestions" }).getByRole("button", { name: "Dismiss" })).toBeDisabled();
  await expect(page.getByRole("region", { name: "Evaluation conflicts" }).getByRole("button", { name: "Reinvestigate" })).toBeDisabled();
});

test("Draft lets the user edit the target repository before publishing", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with limited report" }).click();
  const repository = page.getByLabel("Issue repository");
  await expect(repository).toHaveValue("owner/repo");
  await repository.fill("user/project");
  await page.getByRole("button", { name: "Save & preview" }).click();
  await expect(page.locator("#fixture-calls")).toContainText('"project":"user/project"');
});

test("direct-report Draft accepts and removes a local attachment", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  const picker = page.getByLabel("Add Draft attachments");
  await picker.setInputFiles({
    name: "runtime.log",
    mimeType: "text/plain",
    buffer: Buffer.from("runtime log\n"),
  });
  await expect(page.getByText("runtime.log · 13 bytes", { exact: true })).toBeVisible();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-draft-attachment-add"');
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(page.getByText("No files attached.", { exact: true })).toBeVisible();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-draft-attachment-remove"');
});

test("Kanban composer uploads files with plus and pastes images into /issue", async ({ page }) => {
  await page.route("**/api/projects/fixture/kanban-agent/attachments", async (route) => {
    const upload = route.request().postDataJSON();
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ ok: true, attachment: {
      attachment_id: `ka-${upload.filename}`, filename: upload.filename, content_type: upload.content_type,
      byte_count: 20, sha256: "fixture", ref: `artifacts/kanban-agent/attachments/fixture/${upload.filename}`,
    } }) });
  });
  await page.goto("/tests/fixtures/self-issue-workspace/index.html?scenario=portable");
  const composer = page.locator(".headless-composer");
  const input = composer.locator("textarea.headless-input");
  const picker = composer.locator("input.headless-attachment-input");
  const attachmentButton = composer.locator("button.headless-attachment-button");

  await expect(attachmentButton).toBeVisible();
  await picker.setInputFiles({
    name: "runtime.log",
    mimeType: "text/plain",
    buffer: Buffer.from("runtime output\n"),
  });
  await expect(composer.getByText("runtime.log", { exact: true })).toBeVisible();
  await expect(attachmentButton).toBeVisible();
  await expect(attachmentButton).toBeInViewport();
  const fileChooserPromise = page.waitForEvent("filechooser");
  await attachmentButton.click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "second.log",
    mimeType: "text/plain",
    buffer: Buffer.from("second runtime output\n"),
  });
  await expect(composer.getByText("second.log", { exact: true })).toBeVisible();
  await expect(attachmentButton).toBeInViewport();
  await composer.getByRole("button", { name: "Remove runtime.log" }).click();
  await expect(composer.getByText("runtime.log", { exact: true })).toHaveCount(0);

  // Browsers/OS sniffers may report a text attachment as text/html or
  // application/octet-stream. The shared sidecar ingress preserves metadata
  // without routing a newly created Session through the legacy Draft API.
  await picker.setInputFiles({
    name: "wheel-METADATA.txt",
    mimeType: "text/html",
    buffer: Buffer.from("Metadata-Version: 2.4\n"),
  });
  await expect(composer.getByText("wheel-METADATA.txt", { exact: true })).toBeVisible();

  await input.evaluate((node) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array([137, 80, 78, 71])], "pasted-screen.png", {
      type: "image/png",
    }));
    node.dispatchEvent(new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData: transfer,
    }));
  });
  await expect(composer.getByText("pasted-screen.png", { exact: true })).toBeVisible();
  await input.fill("/issue Clicking Save leaves the page frozen");
  await input.press("Enter");

  await expect(page.getByRole("dialog", { name: "Issue Agent session" })).toBeVisible();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-capture"');
  await expect(page.locator("#fixture-calls")).not.toContainText('"action":"self-issue-draft-attachment-add"');
  await expect(page.locator("#fixture-calls")).toContainText('"filename":"wheel-METADATA.txt","content_type":"text/html"');
  await expect(page.locator("#fixture-calls")).toContainText('"filename":"pasted-screen.png"');
  await expect(composer.getByText("pasted-screen.png", { exact: true })).toHaveCount(0);
});

test("ordinary Kanban Agent messages send verified attachment refs", async ({ page }) => {
  await page.route("**/api/projects/fixture/kanban-agent/attachments", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        attachment: {
          attachment_id: "ka-fixture",
          filename: "notes.md",
          content_type: "text/markdown",
          byte_count: 8,
          ref: "artifacts/kanban-agent/attachments/fixture/notes.md",
          sha256: "fixture",
        },
      }),
    });
  });
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  const composer = page.locator(".headless-composer");
  await composer.locator("input.headless-attachment-input").setInputFiles({
    name: "notes.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("evidence"),
  });
  await composer.locator("textarea.headless-input").fill("Inspect this file");
  await composer.locator("textarea.headless-input").press("Enter");

  await expect(page.locator("#fixture-calls")).toContainText('"action":"chat-orchestrator"');
  await expect(page.locator("#fixture-calls")).toContainText('"attachment_id":"ka-fixture"');
  await expect(page.locator(".agent-ref-chip").filter({ hasText: "notes.md" })).toBeVisible();
  await expect(composer.getByText("notes.md", { exact: true })).toHaveCount(0);
});

test("first GitHub publish becomes Connect with a code and never shows Confirm", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with limited report" }).click();
  await page.getByRole("button", { name: "Save & preview" }).click();
  await expect(page.getByRole("button", { name: "Publish", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Confirm this exact preview/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByRole("button", { name: "Publish", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect GitHub" })).toBeVisible();
  await page.getByRole("button", { name: "Connect GitHub" }).click();
  await expect(page.getByText("ABCD-1234", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open GitHub authorization" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect GitHub" })).toHaveCount(0);
  await expect(page.locator("#fixture-calls")).toContainText('"implicit_publish":true');
  await expect(page.locator("#fixture-calls")).not.toContainText('"action":"self-issue-confirm"');
});

test("an already connected GitHub publish becomes View", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html?github=connected");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with limited report" }).click();
  await page.getByRole("button", { name: "Save & preview" }).click();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByRole("link", { name: "Published on GitHub & View" })).toHaveAttribute(
    "href",
    "https://github.com/owner/repo/issues/42",
  );
  await expect(page.getByRole("button", { name: "Publish", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect GitHub" })).toHaveCount(0);
});

test("partial publication keeps the Issue link but exposes label reconciliation", async ({ page }) => {
  await page.goto("/tests/fixtures/self-issue-workspace/index.html?github=partial");
  await page.locator("textarea.headless-input").fill("/issue Save hangs");
  await page.locator("textarea.headless-input").press("Enter");
  await page.getByRole("button", { name: "Continue with limited report" }).click();
  await page.getByRole("button", { name: "Save & preview" }).click();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Issue created; labels are still pending.", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "Published on GitHub & View" })).toHaveAttribute(
    "href",
    "https://github.com/owner/repo/issues/43",
  );
  await expect(page.getByRole("button", { name: "Retry label reconciliation" })).toBeVisible();
  await page.getByRole("button", { name: "Retry label reconciliation" }).click();
  await expect(page.locator("#fixture-calls")).toContainText('"action":"self-issue-recover"');
  await expect(page.getByText("This Issue has been published.", { exact: false })).toBeVisible();
});
