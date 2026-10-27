import { expect, test } from "@playwright/test";

const channelId = "ch-layout-fixture";
const questionText = "Which boundary should the PRD preserve?";
const ts = "2026-09-22T08:00:00Z";

for (const sameResponder of [false, true]) {
  test(`Channel chat preserves content, roles and mobile space (${sameResponder ? "shared" : "separate"} responder)`, async ({ page, request }) => {
    const projectsResponse = await request.get("/api/workspace/projects");
    expect(projectsResponse.ok()).toBeTruthy();
    const projects = await projectsResponse.json();
    const projectId = String(projects.active_project_id);
    expect(projectId).not.toBe("");
    const members = ["product_pm", "arch", "security"].map((member_id) => ({
      member_id, display_name: member_id, member_type: "agent", status: "active", permissions: ["message"],
    }));
    const detail = {
      channel_id: channelId, name: "Layout verification", status: "open", members,
      leader_member_id: "product_pm",
      discussion: { mode: "multi_lens", default_responder_id: sameResponder ? "product_pm" : "arch" },
      messages: [{ message_id: "request", thread_id: "main", role: "user", text: "Review the requirement.", ts }],
      provider_runs: members.map(({ member_id }) => ({
        run_id: `run-${member_id}`, message_id: "request", thread_id: "main",
        target_member_id: member_id, status: "failed", provider: "codex", updated_at: ts,
        parts: member_id === "product_pm"
          ? [{ id: "partial", kind: "text", state: "completed", content: "Partial analysis must remain visible after a failure." }]
          : [
            { id: `error-${member_id}`, kind: "error", state: "failed", content: "Provider unavailable" },
            { id: `tool-${member_id}`, kind: "tool_result", state: "completed", content: "Requirement file read before failure." },
          ],
      })),
      reply_requests: members.map(({ member_id }) => ({
        request_id: `reply-${member_id}`, message_id: "request", thread_id: "main", target_member_id: member_id,
        status: "failed", reason: "Provider unavailable", updated_at: ts,
      })),
      workflow_requests: [], syntheses: [],
      discussion_attention: { main: {
        thread_id: "main", state: "needs_input", execution_state: "blocked", attention_kind: "question",
        active_agent_count: 0, owner_question_count: 1, open_question_count: 1,
        completed_reply_count: 0, failed_reply_count: 3, question_consolidation_status: "open",
      } },
      open_questions: [{ question_id: "q1", thread_id: "main", question: questionText, status: "open", asked_by: "security" }],
      owner_questionnaires: { main: [{
        question_id: "q1", question: questionText, asked_by: "security", source_run_id: "run-security",
        why_it_matters: "Keep the full rationale visible. ".repeat(12),
        options: [{ id: "one", label: "One authority" }, { id: "two", label: "Separate authorities" }],
      }] },
    };
    // Mock only the Channel projection; no actions or provider requests are sent.
    await page.route("**/api/projects/*/channels**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/channels")) {
        await route.fulfill({ json: { channels: [detail] } });
      } else if (path.endsWith(`/${channelId}`) || path.endsWith(`/${channelId}/conversation`)) {
        await route.fulfill({ json: detail });
      } else {
        await route.continue();
      }
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`/?project=${encodeURIComponent(projectId)}&page=channels&channel=${channelId}`);
    await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
    await expect(page.getByTestId("channel-flow-discussion")).toHaveAttribute("aria-current", "step");
    await expect(page.getByTestId("channel-details-tab")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Context", exact: true })).toHaveCount(0);
    const identity = page.locator('.agent-run-block[data-member-id="product_pm"] .channel-message-identity');
    await expect(identity).toContainText("产品负责人");
    await expect(identity).toContainText("主持人");
    if (sameResponder) await expect(identity).toContainText("默认回复");
    await expect(page.locator('.agent-run-block[data-member-id="arch"]')).toHaveCount(0);
    await expect(page.locator('.agent-run-block[data-member-id="product_pm"]')).toContainText("Partial analysis must remain visible after a failure.");
    const question = page.getByTestId("channel-inline-question");
    await expect(question).toContainText(questionText);
    await expect(page.locator('.agent-run-block[data-member-id="security"]')).toContainText(questionText);
    await expect(page.getByTestId("channel-prd-handoff")).toHaveCount(0);
    await page.getByTestId("channel-flow-prd").click();
    await expect(page.getByTestId("channel-create-task-from-prd")).toBeDisabled();
    await page.getByRole("button", { name: "Close drawer" }).click();
    await page.getByTestId("channel-flow-next").click();
    await expect(page.locator(".channel-drawer")).toHaveCount(0);
    await expect(question).toBeInViewport();
    await expect(question.locator("details.channel-question-card")).not.toHaveAttribute("open", "");
    await question.locator("summary").first().click();
    await expect(question.getByRole("heading", { name: questionText })).toHaveCount(1);
    expect(await question.locator("legend").evaluate((node) => getComputedStyle(node).clipPath)).toBe("inset(50%)");

    await page.getByTestId("channel-header-members").click();
    await expect(page.locator(".channel-drawer")).toContainText("Channel Moderator");
    await expect(page.locator(".channel-drawer")).toContainText(sameResponder ? "同一成员" : "默认回复");
    await page.getByTestId("channel-header-members").click();
    await page.getByTestId("channel-header-failures").click();
    await expect(page.locator(".channel-drawer")).toContainText("0 running · 0 waiting");
    await page.locator(".channel-reply-error summary").first().click();
    await expect(page.locator(".channel-drawer")).toContainText("Provider unavailable");
    await expect(page.getByTestId("channel-provider-incident")).toHaveCount(0);
    await expect(page.locator('.channel-attention-card[data-member-id="arch"]')).toBeVisible();
    await page.getByTestId("channel-header-failures").click();

    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 360, height: 740 }]) {
      await page.setViewportSize(viewport);
      await expect(page.getByTestId("channel-flow-nav")).toBeVisible();
      await expect(page.locator(".channel-flow-node-button")).toHaveCount(5);
      await expect(page.getByTestId("channel-flow-status")).toHaveCount(0);
      await expect(page.getByTestId("channel-flow-next")).toHaveText("1");
      await expect(page.getByTestId("channel-flow-next")).toHaveAccessibleName("1 decision");
      await expect(page.locator(".channel-flow-node-meta")).toHaveCount(0);
      await expect(page.getByTestId("channel-flow-nav").getByText(/Captured|Unknown|Not generated|Not created/)).toHaveCount(4);
      await expect(page.getByTestId("channel-flow-discussion")).toHaveAttribute("aria-label", "Discussion · Awaiting input");
      await expect(page.getByTestId("channel-flow-prd")).toHaveAttribute("aria-label", "PRD · Not generated");
      const layout = await page.evaluate(() => {
        const timeline = document.querySelector<HTMLElement>(".channel-timeline")!;
        const composer = document.querySelector<HTMLElement>(".channel-composer")!;
        const description = document.querySelector<HTMLElement>(".channel-inline-question .ask-user-head p")!;
        const node = document.querySelector('.channel-flow-node[data-node="discussion"]')!;
        const marker = node.querySelector(".channel-flow-marker")!.getBoundingClientRect();
        const label = node.querySelector("strong")!.getBoundingClientRect();
        const action = node.querySelector(".channel-flow-stage-status")!.getBoundingClientRect();
        const navigation = document.querySelector(".channel-flow-nav")!.getBoundingClientRect();
        const summaryBounds = document.querySelector(".channel-flow-summary")!.getBoundingClientRect();
        return {
          timelineHeight: timeline.clientHeight,
          composerBottom: composer.getBoundingClientRect().bottom,
          pageWidth: document.documentElement.scrollWidth,
          descriptionHeight: description.clientHeight,
          descriptionScrollHeight: description.scrollHeight,
          toolbarWidth: document.querySelector<HTMLElement>(".channel-header-actions")!.clientWidth,
          toolbarScrollWidth: document.querySelector<HTMLElement>(".channel-header-actions")!.scrollWidth,
          headerHeight: document.querySelector<HTMLElement>(".channel-shell-header")!.clientHeight,
          flowHeight: document.querySelector(".channel-flow")!.getBoundingClientRect().height,
          markerWidth: marker.width,
          headingAlignment: Math.abs(marker.left + marker.width / 2 - label.left - label.width / 2),
          badgeAlignment: Math.abs(label.left + label.width / 2 - action.left - action.width / 2),
          headingGap: label.top - marker.bottom,
          badgeGap: action.top - label.bottom,
          columnAlignment: Math.max(Math.abs(navigation.left - summaryBounds.left), Math.abs(navigation.right - summaryBounds.right)),
          navWidth: navigation.width,
          readableBadges: [...document.querySelectorAll<HTMLElement>(".channel-flow-badge")].every((badge) => badge.scrollWidth <= badge.clientWidth),
        };
      });
      expect(layout.timelineHeight).toBeGreaterThan(220);
      expect(layout.composerBottom).toBeLessThanOrEqual(viewport.height + 1);
      expect(layout.pageWidth).toBeLessThanOrEqual(viewport.width);
      expect(layout.descriptionHeight).toBeGreaterThanOrEqual(layout.descriptionScrollHeight);
      expect(layout.toolbarScrollWidth).toBeLessThanOrEqual(layout.toolbarWidth + 1);
      expect(layout.headerHeight).toBeLessThanOrEqual(104);
      expect(layout.flowHeight).toBeGreaterThanOrEqual(120);
      expect(layout.flowHeight).toBeLessThanOrEqual(220);
      expect(layout.markerWidth).toBe(30);
      expect(layout.headingGap).toBeGreaterThanOrEqual(5);
      expect(layout.headingGap).toBeLessThanOrEqual(10);
      expect(layout.headingAlignment).toBeLessThan(1);
      expect(layout.badgeAlignment).toBeLessThan(1);
      expect(layout.badgeGap).toBeGreaterThanOrEqual(4);
      expect(layout.badgeGap).toBeLessThanOrEqual(12);
      expect(layout.columnAlignment).toBeLessThan(1);
      expect(layout.navWidth).toBeLessThanOrEqual(820);
      expect(layout.readableBadges).toBe(true);
    }
    const baseReply = detail.reply_requests[0];
    detail.reply_requests = [
      { ...baseReply, request_id: "queued", status: "queued" },
      { ...baseReply, request_id: "working", status: "started" },
      { ...baseReply, request_id: "cancelled", status: "cancelled" },
    ];
    await page.reload();
    await page.getByTestId("channel-header-failures").click();
    const activity = page.locator(".channel-drawer");
    await expect(activity).toContainText("1 running · 1 waiting");
    await expect(activity.locator(".channel-attention-card")).toHaveCount(2);
    await expect(activity.locator(".channel-discussion-live-elapsed")).toHaveCount(1);
    await expect(activity).toContainText("正在回复");
    await expect(activity).toContainText("等待运行时调度");
    expect(errors).toEqual([]);
  });
}
