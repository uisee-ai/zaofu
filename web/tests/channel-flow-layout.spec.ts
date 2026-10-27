import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

const channelId = "ch-flow-ui-fixture";
const ts = "2026-09-22T14:00:00Z";
function fixture() {
  return {
    channel_id: channelId, name: "需求讨论到交付", status: "open", seq: 1,
    members: [{ member_id: "product_pm", role: "product_pm", member_type: "agent", status: "active" }],
    leader_member_id: "product_pm", discussion: { mode: "multi_lens", default_responder_id: "product_pm" },
    messages: [{ message_id: "m1", role: "user", member_id: "operator", thread_id: "main", text: "Analyze the PRD and preserve approvals.", ts,
      refs: { attachments: [{ name: "prd.md", type: "text/markdown" }] } }],
    provider_runs: [{ run_id: "r1", message_id: "m1", target_member_id: "product_pm", thread_id: "main", status: "completed", updated_at: ts,
      parts: [{ id: "response", kind: "text", state: "completed", content: "A complete analysis." }] }],
    member_invitation_proposals: [] as Record<string, unknown>[],
    cross_reviews: [] as Record<string, unknown>[],
    synthesis_requests: [] as Record<string, unknown>[], workflow_requests: [] as Record<string, unknown>[], syntheses: [] as Record<string, unknown>[],
    result_receipts: [] as Record<string, unknown>[], consensus: {} as Record<string, unknown>,
    open_questions: [{ question_id: "q1", thread_id: "main", status: "open", question: "Who approves execution?", asked_by: "product_pm", source_run_id: "r1", answer: "", resolution: "", resolved_by: "" }],
    owner_questionnaires: { main: [{ question_id: "q1", question: "Who approves execution?", asked_by: "product_pm", source_run_id: "r1",
      why_it_matters: "Task creation and execution have independent approvals.", options: [{ id: "owner", label: "Owner approval" }] }] },
    discussion_attention: { main: { thread_id: "main", state: "needs_input", attention_kind: "question", execution_state: "blocked", active_agent_count: 0,
      owner_question_count: 1, open_question_count: 1, completed_reply_count: 1, failed_reply_count: 0, question_consolidation_status: "open" } },
  };
}

async function setup(page: Page, request: APIRequestContext, detail: ReturnType<typeof fixture>, diagnosticMode: "data" | "error" | "empty" = "data") {
  const projects = await (await request.get("/api/workspace/projects")).json();
  const state = { diagnosticCalls: 0, mode: diagnosticMode, writes: [] as string[], payloads: [] as Record<string, unknown>[], unexpected: [] as string[], errors: [] as string[], hold: false, release: () => {},
    previewMode: "data", markdown: "# Canonical PRD\n\n## Acceptance\n\n| Criterion | Result |\n|---|---|\n| Full body | Required |\n\n**Last paragraph preserved.**",
    questionOk: true, allowInvitations: false, allowPlanning: false, allowMessages: false, planningOk: true, planningPayload: {} as Record<string, unknown>, runs: [] as Record<string, unknown>[],
    plans: [] as Record<string, unknown>[], deliveryError: false, fileCalls: 0, allowReports: false, allowSynthesis: false, allowConsensus: false, readiness: undefined as Record<string, unknown> | undefined,
    ownerReports: [] as Record<string, unknown>[], allowEvaluation: false, allowCancel: false, cancelOk: true, synthesisError: "",
    evaluationRows: [] as Array<Record<string, unknown>>,
    sources: undefined as Record<string, unknown> | undefined, requirementCoverage: [] as Array<Record<string, unknown>> };
  page.on("pageerror", (error) => state.errors.push(error.message));
  // Intercept every write, including unexpected actions. These tests cannot
  // launch a real provider or mutate the real project even on a regression.
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() !== "GET") {
      state.writes.push(path);
      if (path.endsWith(`/${channelId}/attachments`) && state.allowMessages) {
        const name = new URL(req.url()).searchParams.get("name");
        await route.fulfill({ json: { attachment: { name, ref: `channels/${channelId}/attachments/${name}`, sha256: "fixture-attachment", size: 7, type: "text/plain" } } });
      } else if (path.endsWith("/actions/agent-session-cancel") && state.allowCancel) {
        const payload = req.postDataJSON().payload;
        state.payloads.push(payload);
        if (!state.cancelOk) {
          await route.fulfill({ status: 503, json: { ok: false, reason: "Provider cancellation failed" } });
          return;
        }
        const run = detail.provider_runs.find((item) => item.run_id === payload.run_id)!;
        run.status = "cancelled";
        detail.seq += 1;
        await route.fulfill({ json: { ok: true, status: "interrupted", process_found: true, process_terminated: true } });
      } else if (path.endsWith("/actions/channel-question-resolve")) {
        const payload = req.postDataJSON().payload;
        state.payloads.push(payload);
        if (!state.questionOk) { await route.fulfill({ status: 409, json: { ok: false, reason: "Decision was not accepted; retry after refresh." } }); return; }
        Object.assign(detail.open_questions[0], { status: "resolved", answer: payload.answer, resolution: payload.resolution, resolved_by: "Owner" });
        Object.assign(Object.values(detail.discussion_attention)[0], { owner_question_count: 0, open_question_count: 0, state: "ready", attention_kind: "none" });
        detail.seq += 1;
        await route.fulfill({ json: { ok: true, action: "channel-question-resolve", status: "accepted" } });
      } else if (path.endsWith("/actions/channel-consensus-confirm") && state.allowConsensus) {
        state.payloads.push(req.postDataJSON().payload);
        await route.fulfill({ status: 202, json: { ok: true, status: "confirmed" } });
      } else if (path.endsWith("/actions/channel.synthesis.request") && state.allowSynthesis) {
        state.payloads.push(req.postDataJSON().payload);
        if (state.synthesisError) {
          detail.synthesis_requests.push({ thread_id: "main", status: "blocked", reason: state.synthesisError });
          await route.fulfill({ status: 409, json: { ok: false, status: "synthesis_blocked", reason: state.synthesisError } });
          return;
        }
        if (req.postDataJSON().payload.source_file) {
          detail.synthesis_requests.push({ thread_id: "main", request_id: "retry", status: "proposed" });
          detail.syntheses.push({ thread_id: "main", prd_revision: 1, artifact_ref: "channels/prd.json", artifact_digest: "published-digest" });
          detail.consensus.main = { artifact_ref: "channels/prd.json", artifact_digest: "published-digest", prd_revision: 1, status: "proposed" };
          detail.seq += 1;
        }
        await route.fulfill({ status: 202, json: { ok: true, status: "requested", request_id: "new-synthesis" } });
      } else if (path.endsWith("/actions/channel.owner_report.request") && state.allowReports) {
        state.ownerReports.push({ report_id: "report-current", status: "generated", summary: "Current report without conversation change" });
        await route.fulfill({ status: 202, json: { ok: true, action: "channel-owner-report", status: "generated" } });
      } else if (path.endsWith("/actions/channel-prd-evaluate") && state.allowEvaluation) {
        const payload = req.postDataJSON().payload;
        state.payloads.push(payload);
        state.evaluationRows.push({ role: payload.role, status: "requested", verdict: "",
          request_id: `eval-${payload.role}`, findings: [] });
        await route.fulfill({ status: 202, json: { ok: true, action: "channel-prd-evaluate", status: "requested" } });
      } else if (path.endsWith("/actions/channel-invite-member") && state.allowInvitations) {
        const payload = req.postDataJSON().payload;
        state.payloads.push(payload);
        const proposal = detail.member_invitation_proposals.find((row) => row.proposal_id === payload.proposal_id)!;
        proposal.status = payload.decision;
        detail.seq += 1;
        await route.fulfill({ json: { ok: true, status: "admitted" } });
      } else if (path.endsWith("/actions/channel-post-message") && state.allowMessages) {
        const payload = req.postDataJSON().payload;
        state.payloads.push(payload);
        detail.messages.push({ ...detail.messages[0], message_id: payload.client_message_id || "followup",
          thread_id: payload.thread_id, text: payload.text });
        detail.seq += 1;
        await route.fulfill({ json: { ok: true, status: "posted", message_id: payload.client_message_id || "followup" } });
      } else if (path.endsWith("/actions/chat-orchestrator") && state.allowPlanning) {
        state.planningPayload = req.postDataJSON().payload;
        await route.fulfill({ json: { ok: state.planningOk, action: "chat-orchestrator", status: state.planningOk ? "accepted" : "forbidden", reason: state.planningOk ? "" : "PRD changed. Refresh and review." } });
      } else {
        state.unexpected.push(path);
        await route.fulfill({ status: 400, json: { ok: false, reason: "Unexpected test action blocked" } });
      }
    } else if (path === "/api/web-session") {
      await route.fulfill({ json: { actions_enabled: true, unlocked: true } });
    } else if (path.endsWith("/channels")) {
      await route.fulfill({ json: { channels: [detail] } });
    } else if (path.endsWith(`/${channelId}/file-preview`)) {
      state.fileCalls += 1;
      await route.fulfill(state.previewMode === "error" ? { status: 409, json: { detail: "file changed" } } : { json: {
        name: "generated.md", ref: "outputs/generated.md", format: "markdown", content: state.markdown, sha256: "file-digest", byte_count: state.markdown.length,
      } });
    } else if (path.endsWith(`/${channelId}/prd-preview`)) {
      const prd = detail.syntheses.at(-1)!;
      await route.fulfill(state.previewMode === "error" ? { status: 409, json: { detail: "digest mismatch" } } : { json: {
        channel_id: channelId, thread_id: prd.thread_id, revision: prd.prd_revision, artifact_ref: prd.artifact_ref,
        artifact_digest: prd.artifact_digest, title: "Canonical PRD", markdown: state.markdown, byte_count: state.markdown.length, readiness: state.readiness,
        sources: state.sources, requirement_coverage: state.requirementCoverage,
      } });
    } else if (path.endsWith(`/${channelId}/delivery`)) {
      const prd = detail.syntheses.at(-1)!;
      await route.fulfill(state.deliveryError ? { status: 503, json: { detail: "delivery unavailable" } }
        : { json: { revision: prd.prd_revision, artifact_digest: prd.artifact_digest, plans: state.plans, runs: state.runs, planning: {} } });
    } else if (path.endsWith(`/${channelId}/prd-evaluation`)) {
      const prd = detail.syntheses.at(-1)!;
      await route.fulfill({ json: { revision: prd.prd_revision, artifact_digest: prd.artifact_digest,
        evaluations: state.evaluationRows } });
    } else if (path.endsWith(`/${channelId}/conversation`)) {
      await route.fulfill({ json: detail });
    } else if (path.endsWith(`/${channelId}`)) {
      state.diagnosticCalls += 1;
      if (state.hold) await new Promise<void>((resolve) => { state.release = resolve; });
      if (state.mode === "error") await route.fulfill({ status: 503, json: { error: "diagnostics unavailable" } });
      else await route.fulfill({ json: { ...detail, owner_reports: state.ownerReports, context_packs: state.mode === "empty" ? [] : [{ context_id: "ctx-first-open", thread_id: "main", summary: "Loaded directly from diagnostics" }], state_updates: [] } });
    } else await route.continue();
  });
  const url = `/?project=${encodeURIComponent(String(projects.active_project_id))}&page=channels&channel=${channelId}`;
  await page.goto(url);
  await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
  return state;
}

test("Channel Cancel targets the public provider identity and refreshes the stopped run", async ({ page, request }) => {
  const detail = fixture();
  Object.assign(detail.provider_runs[0], { status: "streaming", provider: "codex-headless", provider_thread_id: "native-thread-public" });
  const state = await setup(page, request, detail);
  state.allowCancel = true;
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
  expect(state.payloads[0]).toMatchObject({ run_id: "r1", thread_id: "native-thread-public", conversation_id: channelId });
  expect(state.unexpected).toEqual([]);
  await page.reload();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});

test("Channel cancellation failures remain actionable without claiming the run stopped", async ({ page, request }) => {
  const detail = fixture();
  Object.assign(detail.provider_runs[0], { status: "streaming", provider: "codex-headless", provider_thread_id: "native-thread-public" });
  const state = await setup(page, request, detail);
  state.allowCancel = true;
  state.cancelOk = false;
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByTestId("channel-control-error")).toContainText("Provider cancellation failed");
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  expect(detail.provider_runs[0].status).toBe("streaming");
});

test("Owner report refreshes diagnostics without a conversation revision or page reload", async ({ page, request }) => {
  const state = await setup(page, request, fixture());
  state.allowReports = true;
  await page.getByTitle("More", { exact: true }).click();
  await page.getByRole("button", { name: "Reports", exact: true }).click();
  await expect(page.locator(".channel-drawer")).toContainText("0 reports");
  await expect.poll(() => state.diagnosticCalls).toBe(1);
  await page.getByRole("button", { name: "Report", exact: true }).click();
  await expect(page.locator(".channel-drawer")).toContainText("1 reports");
  await expect(page.locator(".channel-drawer")).toContainText("report-current");
  expect(state.unexpected).toEqual([]);
});

test("rejected source context remains visible when no provider reply was created", async ({ page, request }) => {
  const detail = fixture();
  Object.assign(detail.discussion_attention.main, { state: "blocked", execution_state: "blocked", reason: "context_pack_rejected",
    owner_question_count: 0, context_rejections: [{ context_pack_id: "ctx-too-large", target_member_id: "product_pm", reason: "semantic_source_budget_exceeded:121844>120000" }] });
  await setup(page, request, detail);
  await page.getByTestId("channel-header-failures").click();
  await expect(page.getByTestId("channel-context-failures")).toContainText("来源上下文未通过检查");
  await page.getByTestId("channel-context-failures").getByText("查看原因").click();
  await expect(page.getByTestId("channel-context-failures")).toContainText("121844>120000");
  await expect(page.getByText("All requested agent replies have settled.", { exact: true })).toHaveCount(0);
});

test("message file Preview loads full Markdown, source and download without Process or writes", async ({ page, request, context }) => {
  const detail = fixture();
  Object.assign(detail.messages[0].refs.attachments[0], { name: "generated.md", path: "outputs/generated.md" });
  const state = await setup(page, request, detail);
  expect(state.fileCalls).toBe(0);
  await page.getByRole("button", { name: "Preview generated.md", exact: true }).click();
  const preview = page.getByTestId("channel-file-preview");
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(preview.getByRole("table")).toContainText("Full body");
  await expect(preview).toContainText("Last paragraph preserved.");
  await page.screenshot({ path: "/tmp/zf-channel-file-preview-desktop-20260923.png" });
  await expect(preview.getByRole("button", { name: "Process", exact: true })).toHaveCount(0);
  await preview.getByRole("button", { name: "Source", exact: true }).click();
  await expect(preview.getByTestId("channel-file-source")).toHaveText(state.markdown);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await preview.getByRole("button", { name: "Copy file" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(state.markdown);
  const downloaded = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download file" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("generated.md");
  const stream = await download.createReadStream();
  let body = "";
  for await (const chunk of stream!) body += chunk.toString();
  expect(body).toBe(state.markdown);
  await preview.getByRole("button", { name: "Preview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(preview.getByRole("button", { name: "Close preview" })).toBeInViewport();
  await page.screenshot({ path: "/tmp/zf-channel-file-preview-mobile-20260923.png" });
  await preview.getByRole("button", { name: "Close preview" }).click();
  state.previewMode = "error";
  await page.getByRole("button", { name: "Preview generated.md", exact: true }).click();
  await expect(preview.getByRole("alert")).toContainText("未使用摘要替代正文");
  await expect(preview.getByTestId("channel-file-source")).toHaveCount(0);
  state.previewMode = "data";
  await preview.getByRole("button", { name: "Retry" }).click();
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("all owner questions stay inline and collapsed drafts survive reopening", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions = Array.from({ length: 8 }, (_, i) => ({ ...detail.open_questions[0], question_id: `q${i}`, question: `Decision ${i}` }));
  detail.owner_questionnaires.main = detail.open_questions.map((question) => ({ ...detail.owner_questionnaires.main[0], ...question }));
  detail.discussion_attention.main.owner_question_count = 8;
  const state = await setup(page, request, detail);
  const cards = page.locator(".channel-question-card");
  await expect(cards).toHaveCount(8);
  await expect(page.getByTestId("channel-decision-queue")).toContainText("8 pending");
  const timeline = page.locator(".channel-timeline");
  await expect.poll(async () => timeline.evaluate((node) => node.scrollTop < node.scrollHeight - node.clientHeight - 4)).toBe(true);
  await page.getByTestId("channel-decision-queue").getByRole("button", { name: /Decision 0/ }).click();
  await expect(page.locator('[data-testid="channel-inline-question"], [data-testid="channel-unassigned-question-tray"]').first()).toBeInViewport();
  await expect(page.locator(".channel-question-card[open]")).toHaveCount(0);
  await expect(page.getByTestId("channel-flow-next")).toHaveText("8");
  await expect(page.getByTestId("channel-flow-next")).toHaveAccessibleName("8 decisions");
  await cards.first().locator("summary").click();
  await cards.first().getByLabel("Owner approval").check();
  await cards.first().locator("summary").click();
  await cards.first().locator("summary").click();
  await expect(cards.first().getByLabel("Owner approval")).toBeChecked();
  await expect(page.locator('.agent-run-block[data-member-id="product_pm"] .channel-question-card')).toHaveCount(8);
  expect(state.writes).toEqual([]);
});

test("flow nodes are read-only; cold Diagnostics loads, retries and distinguishes empty", async ({ page, request }) => {
  const detail = fixture();
  const state = await setup(page, request, detail, "error");
  await page.getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("button", { name: "诊断", exact: true }).click();
  await expect(page.getByTestId("channel-diagnostics-error")).toBeVisible();
  const failedCalls = state.diagnosticCalls;
  // The error is stable, not an effect/callback retry loop.
  await page.getByTestId("channel-diagnostics-error").getByRole("button", { name: "Retry" }).focus();
  expect(state.diagnosticCalls).toBe(failedCalls);
  state.mode = "data";
  state.hold = true;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("channel-diagnostics-loading")).toBeVisible();
  state.hold = false;
  state.release();
  await expect(page.getByTestId("channel-diagnostics-loaded")).toContainText("1 packs");
  await expect(page.locator(".channel-drawer")).toContainText("ctx-first-open");
  await page.getByRole("button", { name: "Close drawer" }).click();
  for (const node of ["requirement", "discussion", "prd", "task", "workflow"]) {
    await page.getByTestId(`channel-flow-${node}`).click();
    await expect(page.locator(".channel-drawer")).toBeVisible();
    if (node === "requirement") {
      await expect(page.locator(".channel-drawer")).toContainText("prd.md");
      await expect(page.locator(".channel-flow-preview")).toContainText("Brief · Captured");
    }
    if (node === "prd") await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
    await page.getByRole("button", { name: "Close drawer" }).click();
  }
  expect(state.writes).toEqual([]);
  state.mode = "empty";
  await page.reload();
  await page.getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("button", { name: "诊断", exact: true }).click();
  await expect(page.getByTestId("channel-diagnostics-loaded")).toContainText("No context packs built.");
  expect(state.errors).toEqual([]);
});

test("decision sheet traps focus on mobile and routes an unavailable source to diagnostics", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions[0].source_run_id = "missing-source-run";
  detail.owner_questionnaires.main[0].source_run_id = "missing-source-run";
  const state = await setup(page, request, detail);
  await page.setViewportSize({ width: 390, height: 844 });
  const queue = page.getByTestId("channel-decision-queue");
  const trigger = queue.getByRole("button", { name: /Decision queue/ });
  await trigger.click();
  await expect(queue.locator('[role="dialog"]')).toBeVisible();
  await expect(queue).toContainText("Review question · source unavailable");
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await trigger.click();
  await queue.getByRole("button", { name: /Who approves execution/ }).click();
  await expect(page.getByTestId("channel-question-source-diagnostics")).toContainText("Source reply unavailable");
  await expect(page.getByTestId("channel-question-source-diagnostics").getByTestId("ask-user-submit")).toBeVisible();
  expect(state.writes).toEqual([]);
});

test("source remains available before question cards auto-open during another run", async ({ page, request }) => {
  const detail = fixture();
  detail.discussion_attention.main.active_agent_count = 1;
  detail.provider_runs.push({ ...detail.provider_runs[0], run_id: "r2", status: "running", parts: [] });
  const state = await setup(page, request, detail);
  const queue = page.getByTestId("channel-decision-queue");
  await expect(queue).toContainText("Open source question");
  await expect(page.locator('.channel-question-card[data-question-id="q1"]')).toHaveCount(0);
  await queue.getByRole("button", { name: /Who approves execution/ }).click();
  await expect(page.locator('.channel-question-card[data-question-id="q1"]')).toBeVisible();
  await expect(page.getByTestId("channel-question-source-diagnostics")).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test("empty channel keeps the composer primary without a placeholder PRD card", async ({ page, request }) => {
  const detail = fixture();
  detail.messages = [];
  detail.provider_runs = [];
  detail.open_questions = [];
  Object.assign(detail.discussion_attention.main, { state: "idle", attention_kind: "none", owner_question_count: 0, open_question_count: 0 });
  const state = await setup(page, request, detail);
  await expect(page.getByTestId("channel-prd-handoff")).toHaveCount(0);
  await expect(page.getByTestId("channel-flow-requirement")).toHaveAttribute("aria-current", "step");
  await expect(page.locator(".channel-composer")).toBeInViewport();
  expect(state.writes).toEqual([]);
});

test("inline answer enters collapsed decision history with original source after reload", async ({ page, request }) => {
  const detail = fixture();
  // Real Kanban-created groups use an origin thread that chat presents as main.
  const sourceThread = "kanban:fixture-project";
  detail.messages[0].thread_id = sourceThread;
  detail.provider_runs[0].thread_id = sourceThread;
  detail.open_questions[0].thread_id = sourceThread;
  detail.discussion_attention.main.thread_id = sourceThread;
  detail.discussion_attention = { [sourceThread]: detail.discussion_attention.main } as typeof detail.discussion_attention;
  detail.owner_questionnaires = { [sourceThread]: detail.owner_questionnaires.main } as typeof detail.owner_questionnaires;
  const state = await setup(page, request, detail);
  await expect(page.getByTestId("channel-flow-requirement")).toHaveAttribute("aria-label", "Brief · Captured");
  await page.getByTestId("channel-flow-next").click();
  await expect(page.locator(".channel-drawer")).toHaveCount(0);
  const question = page.getByTestId("channel-inline-question");
  await question.locator("summary").first().click();
  await question.getByLabel("Owner approval").check();
  await question.getByTestId("ask-user-submit").click();
  const history = page.getByTestId("channel-decision-history");
  await expect(history).toContainText("决定与澄清 · 1项");
  await expect(history).not.toHaveAttribute("open");
  await history.locator("summary").first().click();
  const group = history.getByTestId("channel-decision-group");
  await group.locator("summary").first().click();
  await expect(group.locator(".channel-decision-answer")).toHaveText("Owner approval");
  const record = group.getByTestId("channel-decision-record");
  await record.locator("summary").click();
  await expect(record.getByLabel("原始记录 q1")).toContainText('"source_run_id": "r1"');
  await expect(question).toHaveCount(0);
  await expect(page.getByTestId("channel-flow-next")).toHaveCount(0);
  await page.reload();
  await expect(history).toContainText("决定与澄清 · 1项");
  await expect(history).not.toHaveAttribute("open");
  await history.locator("summary").first().click();
  await expect(group).toContainText("Owner approval");
  expect(state.writes).toHaveLength(1);
  expect(state.payloads[0].thread_id).toBe(sourceThread);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("decision history folds repeated scoped answers and keeps every member source on narrow screens", async ({ page, request }) => {
  const detail = fixture();
  const question = detail.open_questions[0];
  const receipt = (id: string, answer: string, kind = "owner_decision", resolution = "answered") => ({
    ...question, question_id: id, question: `Original question ${id}`, asked_by: `member-${id}`, source_run_id: "",
    answer, kind, resolution, status: "resolved", resolved_by: "Owner", resolved_event_id: `evt-${id}`,
  });
  detail.open_questions = [
    ...Array.from({ length: 4 }, (_, i) => receipt(`fact-${i}`, `Source finding ${i}`, "fact", "evidence")),
    ...Array.from({ length: 5 }, (_, i) => receipt(`test-${i}`, `本次隔离验收的测试决定：测试口径 ${i}`)),
    receipt("test-repeat", "本次隔离验收的测试决定：测试口径 0"),
    ...Array.from({ length: 5 }, (_, i) => receipt(`stage-${i}`, "本轮阶段澄清（当前要求）：仅验收文档。")),
  ];
  detail.owner_questionnaires = { main: [] };
  Object.assign(detail.discussion_attention.main, { owner_question_count: 0, open_question_count: 0, state: "ready", attention_kind: "none" });
  const state = await setup(page, request, detail);
  const history = page.getByTestId("channel-decision-history");
  await expect(history).toHaveCount(1);
  await expect(history).toContainText("决定与澄清 · 10项");
  await expect(history).toContainText("15 条原始记录");
  await expect(history).not.toHaveAttribute("open");
  await expect(page.getByTestId("channel-decision-group")).toHaveCount(0);
  await expect(page.getByText("已记录决定", { exact: false })).toHaveCount(0);
  const summary = history.locator("summary").first();
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(history.getByRole("heading", { name: "事实核证 · 4" })).toBeVisible();
  await expect(history.getByRole("heading", { name: "测试假设 · 5" })).toBeVisible();
  await expect(history.getByRole("heading", { name: "阶段澄清 · 1" })).toBeVisible();
  const group = history.locator('[data-decision-category="clarification"]').getByTestId("channel-decision-group");
  await group.locator("summary").first().click();
  await expect(group).toContainText("5 条原始记录");
  await expect(group.getByTestId("channel-decision-record")).toHaveCount(5);
  const source = group.getByTestId("channel-decision-record").last();
  await source.locator("summary").click();
  await expect(source.getByLabel("原始记录 stage-4")).toBeVisible();
  await expect(source).toContainText("evt-stage-4");
  await expect(source).toContainText("原记录未绑定具体回复");
  await page.setViewportSize({ width: 390, height: 844 });
  await source.scrollIntoViewIfNeeded();
  expect(await history.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  await summary.click();
  await expect(page.locator(".channel-composer")).toBeInViewport();
  await page.reload();
  await expect(history).toContainText("决定与澄清 · 10项");
  await expect(history).not.toHaveAttribute("open");
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("PRD shows actual version and bound Tasks; a new draft cannot inherit earlier execution", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions = [];
  Object.assign(detail.discussion_attention.main, { owner_question_count: 0, open_question_count: 0, state: "ready", attention_kind: "none" });
  const prd = { thread_id: "main", artifact_ref: "artifacts/prd.md", artifact_digest: "current", prd_revision: 2, event_id: "synthesis", readiness_verdict: "ready", summary: "Current PRD summary", open_questions: [] };
  detail.syntheses = [prd];
  detail.consensus = { main: { ...prd, reached_event_id: "consensus" } };
  detail.result_receipts = [
    { ...prd, receipt_id: "task-current", receipt_kind: "task_created", revision: 2, task_id: "T-CURRENT", status: "created" },
    { ...prd, receipt_id: "task-old", receipt_kind: "task_created", revision: 1, task_id: "T-OLD", status: "created" },
    { thread_id: "main", receipt_id: "result-current", receipt_kind: "workflow_terminal", task_id: "T-CURRENT", workflow_run_id: "run-current", artifact_digest: "result", status: "completed" },
  ];
  const state = await setup(page, request, detail);
  await expect(page.getByTestId("channel-prd-handoff")).toContainText("v2 · Ready");
  await expect(page.getByTestId("channel-prd-handoff")).toContainText("Current PRD summary");
  await page.getByTestId("channel-flow-task").click();
  await expect(page.locator(".channel-flow-preview")).toContainText("T-CURRENT");
  await expect(page.locator(".channel-flow-preview")).not.toContainText("T-OLD");
  await expect(page.getByRole("link", { name: "T-CURRENT" })).toHaveAttribute("href", /page=task.*task=T-CURRENT/);
  await expect(page.getByTestId("channel-create-task-from-prd")).toHaveCount(0);
  await page.getByRole("button", { name: "Close drawer" }).click();
  await page.getByTestId("channel-flow-workflow").click();
  await expect(page.locator(".channel-flow-preview")).toContainText("run-current");
  expect(state.writes).toEqual([]);
  detail.syntheses.push({ ...prd, artifact_digest: "next", artifact_ref: "next-prd.json", prd_revision: 3, readiness_verdict: "unassessed" });
  detail.consensus = { main: { ...prd, reached_event_id: "" } };
  detail.seq += 1;
  await page.reload();
  await expect(page.getByTestId("channel-prd-handoff")).toContainText("v3 · Draft");
  await expect(page.getByTestId("channel-flow-task")).toHaveAttribute("aria-label", "Tasks · Not created");
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Confirm", exact: true })).toHaveCount(0);
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

function readyFixture() {
  const detail = fixture();
  detail.open_questions = [];
  detail.owner_questionnaires = {} as typeof detail.owner_questionnaires;
  Object.assign(detail.discussion_attention.main, { owner_question_count: 0, open_question_count: 0, state: "ready", attention_kind: "none" });
  const prd = { thread_id: "main", artifact_ref: "channels/ch-flow-ui-fixture/prd/r2.json", artifact_digest: "current", prd_revision: 2,
    event_id: "synthesis", readiness_verdict: "ready", summary: "Only a summary, never substitute it for the document.", open_questions: [] };
  detail.syntheses = [prd];
  detail.consensus = { main: { ...prd, reached_event_id: "consensus" } };
  return detail;
}

async function expectCenteredConnectors(page: Page) {
  const connectors = page.locator(".channel-flow-connector");
  await expect(connectors).toHaveCount(4);
  const geometry = await connectors.evaluateAll((elements) => elements.map((element) => {
    const left = element.previousElementSibling!, right = element.nextElementSibling!;
    const leftEdge = Math.max(...[...left.children].map((child) => child.getBoundingClientRect().right));
    const rightEdge = Math.min(...[...right.children].map((child) => child.getBoundingClientRect().left));
    const line = element.querySelector(".channel-flow-line")!.getBoundingClientRect();
    const marker = right.querySelector(".channel-flow-marker")!.getBoundingClientRect();
    return { before: line.left - leftEdge, after: rightEdge - line.right, width: line.width,
      verticalOffset: Math.abs(line.top + line.height / 2 - marker.top - marker.height / 2),
      decorative: element.getAttribute("aria-hidden") === "true" };
  }));
  for (const line of geometry) {
    expect(Math.abs(line.before - line.after)).toBeLessThan(1);
    expect(line.before).toBeGreaterThanOrEqual(9);
    expect(line.after).toBeGreaterThanOrEqual(9);
    expect(line.width).toBeGreaterThanOrEqual(24);
    expect(line.verticalOffset).toBeLessThan(1);
    expect(line.decorative).toBe(true);
  }
}

test("question badge and PRD node open their own content without a duplicate Preview button", async ({ page, request }) => {
  const detail = fixture();
  detail.syntheses = readyFixture().syntheses;
  const state = await setup(page, request, detail);
  const discussion = page.locator('.channel-flow-node[data-node="discussion"]');
  await expect(page.getByTestId("channel-flow-nav").getByRole("button", { name: "Preview PRD", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("channel-flow-discussion")).toHaveAttribute("aria-current", "step");
  await expectCenteredConnectors(page);
  await discussion.getByRole("button", { name: "1 decision", exact: true }).click();
  await expect(page.getByTestId("channel-inline-question")).toBeInViewport();
  await expect(page.locator(".channel-drawer")).toHaveCount(0);
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-prd-preview").getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  await expect(page.getByTestId("channel-flow-discussion")).toHaveAttribute("aria-current", "step");
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

for (const node of ["task", "workflow"] as const) {
  for (const status of ["awaiting_approval", "needs_revision"]) {
  test(`${node} ${status} badge opens Kanban Agent without executing`, async ({ page, request }) => {
    const detail = readyFixture();
    if (node === "workflow") detail.result_receipts = [{ ...detail.syntheses[0], receipt_kind: "task_created", revision: 2, task_id: "T-CURRENT" }];
    const state = await setup(page, request, detail);
    state.plans = [{ subject_type: node === "task" ? "task_create" : "task_workflow", task_id: "T-CURRENT", status }];
    await page.reload();
    const label = status === "needs_revision" ? "Needs revision" : "Awaiting approval";
    const stage = page.locator(`.channel-flow-node[data-node="${node}"]`);
    const badge = stage.getByRole("button", { name: "Open plan", exact: true });
    await expect(page.getByTestId(`channel-flow-${node}`)).toHaveAttribute("aria-label", `${node === "task" ? "Tasks" : "Workflow"} · ${label}`);
    await expect(badge).toHaveText(label);
    await expect(stage).toHaveAttribute("data-tone", "waiting");
    for (const width of [390, 360, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await expect.poll(() => stage.evaluate((element) => {
        const rect = element.getBoundingClientRect(), nav = element.closest("nav")!.getBoundingClientRect();
        return rect.left >= nav.left && rect.right <= nav.right;
      })).toBe(true);
      expect((await page.getByRole("region", { name: "Delivery progress" }).boundingBox())!.height).toBeLessThanOrEqual(240);
      await expectCenteredConnectors(page);
    }
    await badge.click();
    await expect(page.locator(".agent-page-shell")).toBeVisible();
    await expect(page.getByTestId(`channel-flow-${node}`)).toHaveAttribute("aria-current", "step");
    expect(state.writes).toEqual([]);
    expect(state.errors).toEqual([]);
  });
  }
}

test("Awaiting input stays visible if no concrete question has arrived yet", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions = [];
  detail.owner_questionnaires = {} as typeof detail.owner_questionnaires;
  Object.assign(detail.discussion_attention.main, { owner_question_count: 0, open_question_count: 0 });
  const state = await setup(page, request, detail);
  await expect(page.locator('.channel-flow-node[data-node="discussion"] .channel-flow-badge')).toHaveText("Awaiting input");
  await expect(page.getByTestId("channel-flow-next")).toHaveCount(0);
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("delivery read errors remain visible without a permanent Next step row", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.deliveryError = true;
  await page.reload();
  await expect(page.getByRole("region", { name: "Delivery progress" }).getByRole("status")).toContainText("Delivery status unavailable");
  await expect(page.getByTestId("channel-flow-status")).toHaveCount(0);
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-prd-preview").getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("PRD source view exposes bound inputs and returns to chat for correction", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.sources = { status: "ready", items: [{ kind: "owner_input", message_id: "msg-input", digest: "abc", name: "", content: "必须支持撤销。", available: true }] };
  state.requirementCoverage = [{ id: "REQ-1", status: "covered", section: "Acceptance", source_refs: ["msg-input"] }];
  await page.getByTestId("channel-flow-prd").click();
  const inputs = page.getByTestId("channel-prd-inputs");
  await inputs.locator("summary").first().click();
  await inputs.getByText("讨论消息 · msg-input").click();
  await expect(inputs).toContainText("必须支持撤销。");
  await inputs.getByText("Agent 需求映射 · 1").click();
  await expect(inputs).toContainText("REQ-1 · covered");
  await inputs.getByRole("button", { name: "在讨论中补充或修正" }).click();
  await expect(page.getByTestId("channel-prd-preview")).toHaveCount(0);
  await expect(page.locator(".channel-composer-input")).toBeFocused();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("verified Markdown preview supports Source and Process uses only the existing planning action", async ({ page, request }) => {
  const detail = readyFixture();
  const state = await setup(page, request, detail);
  state.allowPlanning = true;
  await expect(page.getByTestId("channel-flow-prd")).toHaveAttribute("aria-label", "PRD · Ready");
  await page.getByTestId("channel-flow-prd").click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(preview.getByRole("table")).toContainText("Full body");
  await expect(preview).toContainText("Last paragraph preserved.");
  await preview.getByRole("button", { name: "Source", exact: true }).click();
  await expect(page.getByTestId("channel-prd-source")).toHaveText(state.markdown);
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeEnabled();
  for (const width of [1440, 390, 360]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByTestId("channel-create-task-from-prd")).toBeInViewport();
    expect(await page.evaluate(() => {
      const drawer = document.querySelector(".channel-document-drawer")!.getBoundingClientRect();
      const shell = document.querySelector(".channel-shell")!.getBoundingClientRect();
      return Math.abs(drawer.bottom - shell.bottom);
    })).toBeLessThan(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => {
      const process = document.querySelector('[data-testid="channel-create-task-from-prd"]')!.getBoundingClientRect();
      const fab = document.querySelector(".agent-fab")?.getBoundingClientRect();
      return Boolean(fab && process.left < fab.right && process.right > fab.left && process.top < fab.bottom && process.bottom > fab.top);
    })).toBe(false);
  }
  await preview.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await page.screenshot({ path: "/tmp/zf-channel-document-preview-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: "/tmp/zf-channel-document-preview-desktop.png" });
  const downloadPromise = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download PRD" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("PRD-v2.md");
  expect(state.writes).toEqual([]);
  await page.getByTestId("channel-create-task-from-prd").click();
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]).toMatch(/\/actions\/chat-orchestrator$/);
  expect(state.planningPayload.source).toBe("web-channel-workflow-plan");
  expect(state.planningPayload.workflow_context).toMatchObject({ channel_id: channelId, thread_id: "main", prd_revision: 2, source_digest: "current" });
  expect(state.planningPayload.message).toContain("不要直接启动 workflow");
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("current PRD preview starts isolated advisory review with exact version", async ({ page, request }) => {
  const detail = readyFixture();
  const state = await setup(page, request, detail);
  state.allowEvaluation = true;
  await page.getByTestId("channel-flow-prd").click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(preview.getByTestId("channel-prd-evaluation")).toContainText("全文评估 · 未开始");
  await preview.getByTestId("channel-prd-evaluate-full").click();
  await expect(preview.getByTestId("channel-prd-evaluation")).toContainText("全文评估 · requested");
  const prd = detail.syntheses.at(-1)!;
  const payload = state.payloads.at(-1)!;
  expect(payload).toMatchObject({ channel_id: channelId, thread_id: prd.thread_id,
    prd_revision: prd.prd_revision, artifact_ref: prd.artifact_ref,
    artifact_digest: prd.artifact_digest, role: "full", retry: false });
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("completed PRD review can be rerun on the same version", async ({ page, request }) => {
  const detail = readyFixture();
  const state = await setup(page, request, detail);
  state.allowEvaluation = true;
  state.evaluationRows.push({ role: "full", status: "completed", verdict: "PASS" });
  await page.getByTestId("channel-flow-prd").click();
  const retry = page.getByTestId("channel-prd-evaluate-full");
  await expect(retry).toHaveText("重新评估");
  await retry.click();
  expect(state.payloads.at(-1)).toMatchObject({ role: "full", retry: true });
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("full and focused findings expose readable text and source locations", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.evaluationRows.push({ role: "full", status: "completed", verdict: "FAIL",
    feedback: { status: "reviewed", review: { ref: "artifacts/triage.json", sha256: "triage" } }, findings: [
    { severity: "high", criterion_id: "C45", finding: "机器可读交接合同缺少 readiness",
      source_location: "SOURCE:交接", target_location: "PRD:合同" },
  ] }, { role: "focused", status: "completed", verdict: "INDETERMINATE", findings: [
    { severity: "medium", criterion_ids: ["C46", "C106"], reason: "缺少可核实签字证据" },
  ] });
  await page.getByTestId("channel-flow-prd").click();
  const evaluation = page.getByTestId("channel-prd-evaluation");
  await expect(evaluation.getByTestId("channel-evaluation-feedback")).toContainText("已记录逐项处置");
  await expect(evaluation).toContainText("completed / FAIL");
  await expect(evaluation.getByText("发现 1 项", { exact: true })).toHaveCount(2);
  for (const summary of await evaluation.getByText("发现 1 项", { exact: true }).all()) await summary.click();
  const findings = evaluation.getByTestId("channel-evaluation-finding");
  await expect(findings).toHaveCount(2);
  await expect(findings.first().locator("p").first()).toBeVisible();
  await expect(findings.first()).toContainText("high · C45");
  await expect(findings.first()).toContainText("机器可读交接合同缺少 readiness");
  await expect(findings.first()).toContainText("来源：SOURCE:交接");
  await expect(findings.first()).toContainText("目标：PRD:合同");
  await expect(findings.last()).toContainText("缺少可核实签字证据");
  await expect(findings.last()).toContainText("medium · C46, C106");
  await expect(findings.first().locator("pre")).toBeHidden();
  await findings.first().getByText("原始数据", { exact: true }).click();
  await expect(findings.first().locator("pre")).toContainText('"finding"');
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("preview failures and read-only sessions cannot Process; retry loads the full document", async ({ page, request }) => {
  const detail = readyFixture();
  const state = await setup(page, request, detail);
  state.previewMode = "error";
  await page.getByTestId("channel-flow-prd").click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(preview.getByRole("alert")).toContainText("Preview unavailable");
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  await expect(preview).not.toContainText("Only a summary");
  state.previewMode = "data";
  await preview.getByRole("button", { name: "Retry" }).click();
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeEnabled();
  await page.route("**/api/web-session", (route) => route.fulfill({ json: { mode: "read_only", unlocked: false, actions_enabled: false } }));
  await page.reload();
  await page.getByTestId("channel-flow-prd").click();
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  await expect(preview).toContainText("session is read-only");
  expect(state.writes).toEqual([]);
});

test("large PRDs expose every source page without silently truncating the body", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.markdown = "# Large PRD\n\n" + "Content with spaces.\n".repeat(12_000) + "FINAL-ACCEPTANCE-LINE";
  await page.getByTestId("channel-flow-prd").click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(preview).toContainText("本节内容过长");
  const pages = Math.ceil(state.markdown.length / 50_000);
  for (let index = 1; index < pages; index++) await preview.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByTestId("channel-prd-source")).toContainText("FINAL-ACCEPTANCE-LINE");
  await expect(preview).not.toContainText("more characters not shown");
  expect(state.writes).toEqual([]);
});

test("long PRD Markdown stays readable in split view and fullscreen with intact pages and download", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.markdown = "# Long readable PRD\n\n" + Array.from({ length: 8 }, (_, index) =>
    `## Chapter ${index}\n\n${"Readable requirements. ".repeat(400)}\n\n| Rule | Result |\n|---|---|\n| Rule ${index} | Required |\n\n\`\`\`text\n# Heading inside code\n\`\`\`\n\n`).join("");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByTestId("channel-prd-handoff").getByRole("button", { name: "Preview", exact: true }).click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(preview.getByRole("heading", { name: "Long readable PRD" })).toBeVisible();
  await expect(preview.locator(".channel-prd-file-title")).toContainText("PRD-v2.md");
  await expect(preview.getByRole("group", { name: "Document view" }).getByRole("button", { name: "Preview", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(preview.getByRole("table").first()).toContainText("Rule 0");
  await expect(preview.getByTestId("channel-prd-source")).toHaveCount(0);
  const split = (await preview.boundingBox())!;
  const discussion = (await page.locator(".channel-timeline").boundingBox())!;
  expect(split.x).toBeGreaterThanOrEqual(discussion.x + discussion.width - 2);
  await preview.getByRole("button", { name: "Fullscreen PRD", exact: true }).click();
  await expect(preview).toHaveAttribute("aria-modal", "true");
  await expect.poll(async () => Math.round((await preview.boundingBox())!.width)).toBe(1440);
  await expect.poll(async () => Math.round((await preview.boundingBox())!.height)).toBe(900);
  await preview.getByRole("button", { name: "Next", exact: true }).click();
  await expect(preview.getByRole("heading", { name: "Chapter 7" })).toBeAttached();
  const body = preview.locator(".channel-document-body");
  await body.evaluate((node) => { node.scrollTop = 320; });
  await page.keyboard.press("Escape");
  await expect(preview).toHaveAttribute("aria-modal", "false");
  await expect(preview.getByRole("navigation", { name: "PRD pages" })).toContainText("2 / 2");
  expect(await body.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expect(preview.getByRole("button", { name: "Fullscreen PRD", exact: true })).toBeFocused();
  await preview.getByRole("button", { name: "Previous", exact: true }).click();
  await expect.poll(() => body.evaluate((node) => node.scrollTop)).toBe(0);
  await preview.getByRole("button", { name: "Source", exact: true }).click();
  let source = await preview.getByTestId("channel-prd-source").textContent();
  await preview.getByRole("button", { name: "Next", exact: true }).click();
  source += (await preview.getByTestId("channel-prd-source").textContent())!;
  expect(source).toBe(state.markdown);
  await preview.getByRole("button", { name: "Preview", exact: true }).click();
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 });
    await preview.getByRole("button", { name: "Fullscreen PRD", exact: true }).click();
    await expect(preview.getByRole("button", { name: "Exit PRD fullscreen", exact: true })).toBeInViewport();
    await expect(preview.getByRole("button", { name: "Download PRD" })).toBeInViewport();
    await expect(preview.getByRole("navigation", { name: "PRD pages" })).toBeInViewport();
    expect(Math.round((await preview.boundingBox())!.width)).toBe(width);
    await preview.getByRole("button", { name: "Process", exact: true }).focus();
    await page.keyboard.press("Tab");
    // Native dialogs may visit browser chrome between the last and first control;
    // background page controls must remain inert throughout the modal session.
    await page.keyboard.press("Tab");
    await expect(preview.getByRole("button", { name: "Preview", exact: true })).toBeFocused();
    await page.getByTestId("channel-flow-prd").evaluate((node) => (node as HTMLElement).focus());
    await expect(preview.getByRole("button", { name: "Preview", exact: true })).toBeFocused();
    await preview.getByRole("button", { name: "Exit PRD fullscreen", exact: true }).click();
  }
  const downloaded = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download PRD" }).click();
  const stream = await (await downloaded).createReadStream();
  let downloadedBody = "";
  for await (const chunk of stream!) downloadedBody += chunk.toString();
  expect(downloadedBody).toBe(state.markdown);
  await preview.getByRole("button", { name: "Fullscreen PRD", exact: true }).click();
  await preview.getByRole("button", { name: "Close drawer" }).click();
  await expect(preview).toHaveCount(0);
  await page.getByTestId("channel-flow-prd").click();
  await expect(preview).toHaveAttribute("aria-modal", "false");
  await expect(preview.getByRole("heading", { name: "Long readable PRD" })).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("rejected Process keeps the verified document and never claims planning succeeded", async ({ page, request }) => {
  const state = await setup(page, request, readyFixture());
  state.allowPlanning = true;
  state.planningOk = false;
  await page.getByTestId("channel-flow-prd").click();
  const preview = page.getByTestId("channel-prd-preview");
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeEnabled();
  await page.getByTestId("channel-create-task-from-prd").click();
  await expect(preview.getByRole("alert")).toContainText("PRD changed");
  await expect(preview).not.toContainText("Preview unavailable");
  await expect(preview.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(page.getByTestId("channel-create-task-from-prd")).toHaveText("Process");
  expect(state.writes).toHaveLength(1);
});

test("connected stage navigation exposes statuses and keeps the current mobile stage reachable", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const detail = readyFixture();
  detail.result_receipts = [{ ...detail.syntheses[0], receipt_kind: "task_created", revision: 2, task_id: "T-CURRENT" }];
  detail.workflow_requests = [{ thread_id: "main", task_id: "T-CURRENT", status: "requested" }];
  const state = await setup(page, request, detail);
  const navigation = page.getByTestId("channel-flow-nav");
  const workflow = page.getByTestId("channel-flow-workflow");
  await expect(navigation.locator(".channel-flow-badge")).toHaveCount(0);
  expect(await navigation.locator(".channel-flow-node-button").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("title"))))
    .toEqual(["Brief · Captured", "Discussion · Converged", "PRD · Ready", "Tasks · Created", "Workflow · Unknown"]);
  for (const width of [390, 1440, 360]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(async () => workflow.evaluate((node) => {
      const stage = node.closest(".channel-flow-node")!.getBoundingClientRect();
      const nav = node.closest("nav")!.getBoundingClientRect();
      return stage.left >= nav.left && stage.right <= nav.right;
    })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page.getByTestId("channel-flow-status")).toHaveCount(0);
    await expect(navigation.getByText("Next step", { exact: true })).toHaveCount(0);
    await expectCenteredConnectors(page);
  }
  await workflow.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByTestId("channel-flow-task")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator(".channel-drawer")).toBeVisible();
  await expect(workflow).toHaveAttribute("aria-current", "step");
  await page.getByRole("button", { name: "Close drawer" }).click();
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-prd-preview").getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(workflow).toHaveAttribute("aria-current", "step");
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("Workflow progress consumes registered delivery evidence and never upgrades an unverified request", async ({ page, request }) => {
  const detail = readyFixture();
  detail.result_receipts = [{ ...detail.syntheses[0], receipt_kind: "task_created", revision: 2, task_id: "T-CURRENT" }];
  detail.workflow_requests = [{ thread_id: "main", task_id: "T-CURRENT", status: "requested" }];
  const state = await setup(page, request, detail);
  await expect(page.getByTestId("channel-flow-workflow")).toHaveAttribute("aria-label", "Workflow · Unknown");
  for (const status of ["queued", "running", "paused", "failed", "cancelled", "completed"]) {
    state.runs = [{ task_id: "T-CURRENT", workflow_run_id: "run-current", status, event_id: `evt-${status}`, reason: "" }];
    await page.reload();
    await expect(page.getByTestId("channel-flow-workflow")).toHaveAttribute("aria-label", "Workflow · " + status[0].toUpperCase() + status.slice(1));
    const badge = page.locator('.channel-flow-node[data-node="workflow"] .channel-flow-badge');
    if (["failed", "paused"].includes(status)) {
      await expect(badge).toHaveText(status[0].toUpperCase() + status.slice(1));
      expect(await badge.evaluate((element) => getComputedStyle(element).color === getComputedStyle(element.closest("nav")!).color)).toBe(false);
    } else await expect(badge).toHaveCount(0);
    await expect(page.getByTestId("channel-flow-workflow")).toHaveAttribute("aria-current", "step");
    await page.getByTestId("channel-flow-workflow").click();
    await expect(page.locator(".channel-flow-preview")).toContainText("Workflow · " + status[0].toUpperCase() + status.slice(1));
    await page.getByRole("button", { name: "Close drawer" }).click();
  }
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("actual origin thread, draft and attachment survive switching and reload", async ({ page, request }) => {
  const detail = fixture();
  const origin = "kanban:origin-project";
  detail.messages[0].thread_id = origin;
  detail.provider_runs[0].thread_id = origin;
  detail.messages.push({ ...detail.messages[0], message_id: "other-message", thread_id: "other", text: "Separate conversation" });
  Object.assign(detail, { open_questions: [], owner_questionnaires: {}, discussion_attention: {},
    discussions: { [origin]: { state: "active", roster: ["product_pm"] } } });
  const state = await setup(page, request, detail);
  state.allowMessages = true;
  const editor = page.locator(".channel-composer-input");
  const navigation = page.getByTestId("agent-thread-navigation");
  await editor.fill("Origin draft");
  await expect(page.getByLabel("Mark as original requirement source")).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles({ name: "origin-only.txt", mimeType: "text/plain", buffer: Buffer.from("fixture") });
  await expect(page.locator(".channel-composer")).toContainText("已上传");
  await navigation.getByRole("button", { name: "other", exact: true }).click();
  await expect(editor).toHaveText("");
  await expect(page.locator(".channel-composer")).not.toContainText("origin-only.txt");
  await expect(page.getByLabel("Mark as original requirement source")).toHaveCount(0);
  await editor.fill("Other draft");
  await page.reload();
  await expect(editor).toHaveText("Other draft");
  await expect(navigation.getByRole("button", { name: "other", exact: true })).toHaveAttribute("aria-selected", "true");
  await navigation.getByRole("button", { name: "需求讨论到交付", exact: true }).click();
  await expect(editor).toHaveText("Origin draft");
  await expect(page.locator(".channel-composer")).toContainText("origin-only.txt");
  await expect(page.getByLabel("Mark as original requirement source")).toHaveCount(0);
  await editor.fill("Follow up in original discussion");
  await editor.press("Enter");
  await expect.poll(() => state.payloads.length).toBe(1);
  expect(state.payloads[0].thread_id).toBe(origin);
  expect(state.payloads[0].refs?.evaluation_source_document).toBeUndefined();
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});


test("failed Owner decision keeps the answer for retry and shows a persisted receipt on success", async ({ page, request }) => {
  const state = await setup(page, request, fixture());
  state.questionOk = false;
  const card = page.locator(".channel-question-card").first();
  await card.locator("summary").click();
  await card.getByLabel("Owner approval").check();
  await card.getByTestId("ask-user-submit").click();
  await expect(page.getByTestId("channel-control-error")).toContainText("Decision was not accepted");
  await expect(card.getByLabel("Owner approval")).toBeChecked();
  const history = page.getByTestId("channel-decision-history");
  await expect(history).toHaveCount(0);
  state.questionOk = true;
  await card.getByTestId("ask-user-submit").click();
  await expect(history).toContainText("决定与澄清 · 1项");
  await expect(history).not.toHaveAttribute("open");
  await history.locator("summary").first().click();
  await expect(history).toContainText("Owner approval");
  await page.reload();
  await expect(history).not.toHaveAttribute("open");
  await history.locator("summary").first().click();
  await expect(history).toContainText("Owner approval");
  expect(state.payloads).toHaveLength(2);
  expect(state.payloads.every((row) => row.thread_id === "main")).toBe(true);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("chair invitation is reviewed in its source thread and remains decided after refresh", async ({ page, request }) => {
  const detail = fixture();
  detail.member_invitation_proposals = [{ proposal_id: "invite-qa", member_id: "qa", profile_id: "qa-audit", channel_role: "qa_analyst",
    thread_id: "main", status: "pending", reason: "缺少独立验收和并发反例审查", required_reviewer: true, source_refs: ["PRD#库存"] }];
  const state = await setup(page, request, detail);
  state.allowInvitations = true;
  const invite = page.getByTestId("channel-member-invitations");
  await expect(invite).toContainText("缺少独立验收");
  await invite.getByRole("button", { name: "批准并参评" }).click();
  await expect(invite).toContainText("已批准并纳入讨论");
  await page.reload();
  await expect(invite).toContainText("已批准并纳入讨论");
  expect(state.payloads).toEqual([{ channel_id: channelId, thread_id: "main", member_id: "qa", proposal_id: "invite-qa", decision: "approved" }]);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});


test("completed transport does not hide rejected or pending formal review", async ({ page, request }) => {
  const detail = fixture();
  detail.cross_reviews = [
    { request_id: "rejected", thread_id: "main", target_member_id: "security", status: "rejected", reason: "cross_review_needs_owner_requires_question_and_recommendation" },
    { request_id: "pending", thread_id: "main", target_member_id: "arch", status: "pending" },
    { request_id: "done", thread_id: "main", target_member_id: "qa", status: "completed", outcome: "resolved" },
    { request_id: "other", thread_id: "other", target_member_id: "hidden", status: "rejected" },
  ];
  await setup(page, request, detail);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const reviews = page.getByTestId("channel-review-attention");
  await expect(reviews).toContainText("正式审查尚未通过 · 2");
  await reviews.getByText("查看原因").click();
  await expect(reviews).toContainText("cross_review_needs_owner_requires_question_and_recommendation");
  await expect(reviews).not.toContainText("hidden");
  await expect(page.getByText("All requested agent replies have settled.", { exact: true })).toHaveCount(0);
});

test("PRD preview reports current synthesis without suggesting another trigger", async ({ page, request }) => {
  const detail = fixture();
  Object.assign(detail.discussion_attention.main, { state: "running", execution_state: "running", kernel_phase: "phase3_synthesis", owner_question_count: 0 });
  await setup(page, request, detail);
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-prd-preview")).toContainText("正在生成完整 PRD");
  await expect(page.getByTestId("channel-prd-preview")).toContainText("Generating");
  await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
});


test("exhausted synthesis retry keeps the source thread and lets backend select the synthesizer", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions = [];
  detail.owner_questionnaires.main = [];
  Object.assign(detail.discussion_attention.main, { state: "blocked", execution_state: "blocked", kernel_phase: "phase3_synthesis",
    owner_question_count: 0, open_question_count: 0, failed_reply_count: 1, can_synthesize: false, can_restart: false });
  const state = await setup(page, request, detail);
  state.allowSynthesis = true;
  await page.getByTitle("More", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Synthesize", exact: true })).toBeDisabled();
  Object.assign(detail.discussion_attention.main, { can_synthesize: true });
  await page.reload();
  await page.getByTitle("More", { exact: true }).click();
  await page.getByRole("button", { name: "Synthesize", exact: true }).click();
  await expect.poll(() => state.payloads.length).toBe(1);
  expect(state.payloads[0].thread_id).toBe("main");
  expect(state.payloads[0].target_member_id).toBeUndefined();
  expect(state.writes.every(path => path.endsWith("/actions/channel.synthesis.request"))).toBe(true);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});


for (const mode of ["explicit", "missing", "ready"] as const) {
  test(`PRD risk confirmation requires explicit version-bound acceptance: ${mode}`, async ({ page, request }) => {
    const detail = readyFixture();
    const prd = detail.syntheses[0];
    const verdict = mode === "ready" ? "ready" : "needs_multi_lens";
    Object.assign(prd, { readiness_verdict: verdict, readiness_ref: "readiness.json", readiness_digest: "risk-digest" });
    detail.consensus = { main: { ...prd, reached_event_id: "", human_confirmed: false } };
    const state = await setup(page, request, detail);
    state.allowConsensus = true;
    if (mode !== "missing") state.readiness = { ref: "readiness.json", digest: "risk-digest", verdict,
      implementation_start: false, reason: "Runner not available", gaps: ["Browser runner missing"],
      risks: Array.from({length:25}, (_,i) => `Risk ${i}`) };
    await page.getByTestId("channel-flow-prd").click();
    const panel = page.getByTestId("channel-consensus-control");
    if (mode === "ready") {
      await panel.getByRole("button", { name: "Confirm", exact: true }).click();
      await expect.poll(() => state.payloads.length).toBe(1);
      expect(state.payloads[0].accept_readiness_risk).toBeUndefined();
    } else if (mode === "missing") {
      await expect(panel.getByRole("alert")).toContainText("风险版本不一致");
      await expect(panel.getByRole("button", { name: "接受风险并确认", exact: true })).toBeDisabled();
      expect(state.writes).toEqual([]);
    } else {
      await expect(panel).toContainText("Risk 24");
      const confirm = panel.getByRole("button", { name: "接受风险并确认", exact: true });
      await expect(confirm).toBeDisabled();
      await panel.getByRole("checkbox").check();
      await confirm.click();
      await expect.poll(() => state.payloads.length).toBe(1);
      expect(state.payloads[0].accept_readiness_risk).toBe(true);
      expect(state.payloads[0].artifact_digest).toBe(prd.artifact_digest);
      expect(state.payloads[0].prd_revision).toBe(prd.prd_revision);
    }
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

for (const choice of ["planning", "execution", "missing", "mismatched"] as const) {
  test(`ready planning-only PRD keeps execution consent optional and version-bound: ${choice}`, async ({ page, request }) => {
    const detail = readyFixture();
    const prd = detail.syntheses[0];
    Object.assign(prd, { readiness_verdict: "ready", implementation_start: false,
      readiness_ref: "planning-readiness.json", readiness_digest: "planning-risk-digest" });
    detail.consensus = { main: { ...prd, reached_event_id: "", human_confirmed: false } };
    const state = await setup(page, request, detail);
    state.allowConsensus = true;
    if (choice !== "missing") state.readiness = { ref: "planning-readiness.json", digest: "planning-risk-digest",
      verdict: "ready", implementation_start: choice === "mismatched", reason: "Document ready; execution has not been permitted.",
      gaps: [], risks: ["Stop this Run through controlled actions; a cancelled label alone does not prove process exit."] };
    await page.getByTestId("channel-flow-prd").click();
    const panel = page.getByTestId("channel-consensus-control");
    await expect(panel).toContainText("当前版本仅允许规划");
    if (choice === "missing" || choice === "mismatched") {
      await expect(panel.getByRole("alert")).toContainText("风险版本不一致");
      await expect(panel.getByRole("checkbox")).toHaveCount(0);
    } else {
      await expect(panel).toContainText("a cancelled label alone does not prove process exit");
      await expect(panel.getByRole("checkbox")).not.toBeChecked();
      await expect(panel.getByRole("button", { name: "Confirm", exact: true })).toBeEnabled();
      if (choice === "execution") await panel.getByRole("checkbox").check();
    }
    await panel.getByRole("button", { name: choice === "execution" ? "接受风险并确认" : "Confirm", exact: true }).click();
    await expect.poll(() => state.payloads.length).toBe(1);
    if (choice === "execution") expect(state.payloads[0].accept_readiness_risk).toBe(true);
    else expect(state.payloads[0].accept_readiness_risk).toBeUndefined();
    expect(state.payloads[0].artifact_digest).toBe(prd.artifact_digest);
    expect(state.payloads[0].prd_revision).toBe(prd.prd_revision);
    expect(state.writes).toHaveLength(1);
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

test("published PRD shows review while reviewers run and a new synthesis still shows generation", async ({ page, request }) => {
  const detail = readyFixture();
  const prd = detail.syntheses.at(-1)!;
  detail.consensus = { main: { ...prd, status: "proposed", reached_event_id: "" } };
  const requests = [{ thread_id: "main", status: "proposed" }];
  Object.assign(detail, { synthesis_requests: requests });
  Object.assign(detail.discussion_attention.main, { state: "running", execution_state: "running", kernel_phase: "phase3_synthesis", owner_question_count: 0 });
  await setup(page, request, detail);
  await expect(page.getByTestId("channel-flow-prd")).toHaveAttribute("aria-label", "PRD · In review");
  await page.getByTestId("channel-flow-prd").click();
  await expect(page.getByTestId("channel-prd-preview")).toContainText("In review");
  await expect(page.getByTestId("channel-prd-preview")).not.toContainText("Generating");
  requests.push({ thread_id: "main", status: "requested" });
  detail.seq += 1;
  await page.reload();
  await expect(page.getByTestId("channel-flow-prd")).toHaveAttribute("aria-label", "PRD · Generating");
});


test("local Markdown delivery previews and explicitly publishes a draft without granting Process", async ({ page, request }) => {
  const detail = fixture();
  detail.open_questions = [];
  detail.owner_questionnaires.main = [];
  Object.assign(detail.discussion_attention.main, { state: "blocked", owner_question_count: 0, open_question_count: 0, failed_reply_count: 1 });
  detail.provider_runs[0].parts[0].content = "Saved [完整 PRD r2](/home/project/PRD.r2.md).";
  const state = await setup(page, request, detail);
  state.allowSynthesis = true;
  const initialUrl = page.url();
  await page.getByRole("button", { name: "完整 PRD r2", exact: true }).click();
  const file = page.getByTestId("channel-file-preview");
  await expect(file.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  expect(page.url()).toBe(initialUrl);
  expect(state.writes).toEqual([]);
  await file.getByRole("button", { name: "生成正式 PRD 草案" }).click();
  await expect.poll(() => state.payloads.length).toBe(1);
  expect(state.payloads[0].source_file).toEqual({ ref: "outputs/generated.md", sha256: "file-digest" });
  const prd = page.getByTestId("channel-prd-preview");
  await expect(prd).toContainText("In review");
  await expect(prd.getByRole("heading", { name: "Canonical PRD" })).toBeVisible();
  await expect(prd.getByRole("button", { name: "Process", exact: true })).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("blocked PRD generation keeps its reason after reload without enabling Process", async ({ page, request }) => {
  const detail = fixture();
  detail.synthesis_requests = [{ thread_id: "main", request_id: "blocked", status: "blocked", reason: "semantic_source_budget_exceeded:342979>120000" }];
  const state = await setup(page, request, detail);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt) await page.reload();
    await page.getByTestId("channel-flow-prd").click();
    await expect(page.getByTestId("channel-prd-generation-error")).toContainText("342979>120000");
    await expect(page.getByTestId("channel-prd-generation-error")).toContainText("重新打开原文件");
    await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
  }
  expect(state.writes).toEqual([]);
});

test("rejected file publication retains preview and retries after the cause is resolved", async ({ page, request }) => {
  const detail = fixture();
  Object.assign(detail.discussion_attention.main, { owner_question_count: 0, open_question_count: 0 });
  detail.open_questions = [];
  detail.owner_questionnaires.main = [];
  detail.provider_runs[0].parts[0].content = "Saved [完整 PRD r2](/home/project/PRD.r2.md).";
  const state = await setup(page, request, detail);
  state.allowSynthesis = true;
  state.synthesisError = "semantic_source_budget_exceeded:342979>120000";
  await page.getByRole("button", { name: "完整 PRD r2", exact: true }).click();
  const file = page.getByTestId("channel-file-preview");
  await file.getByRole("button", { name: "生成正式 PRD 草案" }).click();
  await expect(file.getByRole("alert")).toContainText(state.synthesisError);
  await expect(file.getByRole("button", { name: "生成正式 PRD 草案" })).toBeEnabled();
  expect(detail.syntheses).toHaveLength(0);
  state.synthesisError = "";
  await file.getByRole("button", { name: "生成正式 PRD 草案" }).click();
  await expect(page.getByTestId("channel-prd-preview")).toContainText("In review");
  await expect(page.getByRole("button", { name: "Process", exact: true })).toBeDisabled();
  expect(state.payloads).toHaveLength(2);
  expect(state.payloads[0].source_file).toEqual(state.payloads[1].source_file);
});

test("pipeline summary follows inspected stages while execution and approvals keep their identity", async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const detail = readyFixture();
  detail.result_receipts = [{ ...detail.syntheses[0], receipt_kind: "task_created", revision: 2, task_id: "T-CURRENT" }];
  detail.workflow_requests = [{ thread_id: "main", task_id: "T-CURRENT", status: "running" }];
  const state = await setup(page, request, detail);
  const summary = page.getByTestId("channel-flow-summary");
  const workflow = page.getByTestId("channel-flow-workflow");
  await expect(summary).toContainText("Running");
  await expect(workflow).toHaveAttribute("aria-current", "step");
  await page.getByTestId("channel-flow-prd").click();
  await expect(summary).toContainText("Revision 2");
  await expect(summary).toContainText("Current: Workflow");
  await expect(page.getByTestId("channel-flow-prd")).toHaveAttribute("aria-pressed", "true");
  await expect(workflow).toHaveAttribute("aria-current", "step");
  await expect(page.locator('.channel-flow-connector[data-complete="true"]')).toHaveCount(3);
  await page.getByRole("button", { name: "Close drawer" }).click();
  await expect(summary).toContainText("Running");
  await page.screenshot({ path: testInfo.outputPath("channel-desktop.png") });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.screenshot({ path: testInfo.outputPath("channel-dark.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(workflow).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("channel-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  Object.assign(detail, { open_questions: fixture().open_questions, owner_questionnaires: fixture().owner_questionnaires, discussion_attention: fixture().discussion_attention });
  await page.reload();
  await expect(page.getByTestId("channel-flow-discussion")).toHaveAttribute("aria-current", "step");
  await expect(summary).toContainText("1 decision needs your input");
  await summary.getByRole("button", { name: "Review decisions" }).click();
  await expect(page.getByTestId("channel-inline-question")).toBeInViewport();
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});
