import { expect, test, type Page } from "@playwright/test";
import { installUiFixture } from "./ui-alignment.fixture";

const projectId = "ui-review";
const threadId = "kanban:ui-review";
const binding = { project_id: projectId, conversation_id: threadId, thread_key: threadId,
  turn_id: "turn-review", backend: "codex-headless" };
function event(seq: number, type: string, payload: Record<string, unknown>) {
  return { seq, id: `evt-${seq}`, ts: `2026-10-27T08:00:${String(seq).padStart(2, "0")}Z`,
    type, payload: { ...binding, ...payload } };
}
function plan(number: number) {
  return { request_id: `question-${number}`, request_event_id: `evt-${number * 10 + 1}`,
    revision: 1, question_id: `question-${number}`, header: "Confirm requirements",
    question: number === 1 ? "Where should the application live?" : "Which integration environment is available?",
    options: [{ id: "option-1", label: "Use isolated workspace", recommended: true, submit_mode: "continue" },
      { id: "option-2", label: "Provide an existing repository", submit_mode: "continue" }],
    allow_other: true, valid: true, turn_id: binding.turn_id, backend: binding.backend };
}
function questionEvents(number: number) {
  return [event(number * 10, "kanban.agent.reply", { answer: `Review question ${number}`, plan_request: plan(number) }),
    event(number * 10 + 1, "kanban.agent.plan.requested", { plan_request: plan(number) }),
    event(number * 10 + 2, "kanban.agent.turn.completed", { status: "waiting_input" })];
}
async function fixture(page: Page, withTurn: boolean) {
  const result = await installUiFixture(page, { snapshot: { runtime: {
    mode: "live", live: true,
    actions: { mutation_enabled: true, allowed: ["chat-orchestrator", "kanban-plan-apply"] },
    web_session: { mode: "local_trusted", unlocked: true, actions_enabled: true },
    agent_surface: { configured_backend: "codex-headless", permission_profile: "workspace_writer",
      backends: [{ id: "codex-headless", title: "Codex", available: true, default: true }] },
  } } });
  await page.addInitScript(() => localStorage.setItem("zf.operatorBackend", "codex-headless"));
  const state = {
    phase: withTurn ? "running" : "idle", attempt: 1, answers: 0, posts: 0,
    failHistory: 0, failedHistory: 0, permissionDelay: false, failAction: false, failAnswer: false,
    events: withTurn ? [event(1, "user.message", { target: "kanban-agent", runtime_delivery: "headless", message: "Prepare the workflow" }),
      event(2, "kanban.agent.turn.created", { message_event_id: "evt-1" }),
      event(3, "kanban.agent.turn.delta", { control_state: "plan_request_buffering" })] : [],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/webchat/model-catalog")) {
      return route.fulfill({ json: { backend: "codex-headless", models: [], session_defaults: {} } });
    }
    if (path.endsWith("/webchat/runtime")) {
      if (state.permissionDelay) await new Promise((resolve) => setTimeout(resolve, 300));
      const waiting = state.phase === "waiting_input";
      return route.fulfill({ json: { project_id: projectId, turns: state.phase === "idle" ? [] : [{
        ...binding, thread_id: threadId, state: state.phase, current_attempt_id: `attempt-${state.attempt}`,
        pending_interaction_id: waiting ? `interaction-${state.attempt}` : "",
        created_at: "2026-10-27T08:00:00Z", updated_at: `2026-10-27T08:0${state.attempt}:00Z`,
      }], interactions: waiting ? [{ interaction_id: `interaction-${state.attempt}`, turn_id: binding.turn_id,
        kind: "ask_user", revision: 1, status: "pending", request: plan(state.attempt), currentness: {} }] : [],
      capabilities: { controls: ["queue", "cancel"], native_steer: false },
      queue: [], artifacts: [], messages: [], threads: [], control_receipts: [], tool_catalog: { items: [] },
      } });
    }
    if (path.endsWith("/agent-session/history")) {
      if (state.failHistory > 0) {
        state.failHistory -= 1; state.failedHistory += 1;
        return route.fulfill({ status: 503, json: { detail: "Temporary history outage" } });
      }
      return route.fulfill({ json: { items: state.events, has_more: false, projection_state: "ready", projection_lag: 0 } });
    }
    if (path.includes("/interactions/") && path.endsWith("/answer")) {
      state.answers += 1;
      if (state.failAnswer) return route.fulfill({ status: 503, json: { detail: "Answer temporarily unavailable" } });
      const response = route.request().postDataJSON().response.plan_response;
      state.events.push(event(13, "kanban.agent.plan.answered", response));
      state.phase = "running"; state.attempt = 2;
      state.events.push(event(14, "user.message", { target: "kanban-agent", runtime_delivery: "headless",
        message: "Use isolated workspace", request: { plan_response: response } }),
      event(15, "kanban.agent.turn.created", { message_event_id: "evt-14" }));
      return route.fulfill({ json: { ok: true } });
    }
    if (path.endsWith("/actions/chat-orchestrator")) {
      state.posts += 1;
      return route.fulfill({ status: state.failAction ? 503 : 200,
        json: state.failAction ? { ok: false, status: "failed", reason: "Try again" } : { ok: true, status: "completed" } });
    }
    return route.fallback();
  });
  await page.goto("/?project=ui-review");
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.locator("textarea.headless-input")).toBeVisible();
  await expect(page.getByRole("button", { name: /Agent backend: Codex/ })).toBeVisible();
  return { state, ...result };
}

test("recovers questions without SSE, including a resumed turn and a failed history read", async ({ page }) => {
  test.setTimeout(60_000);
  const { state, errors } = await fixture(page, true);
  await expect(page.getByTestId("agent-card-preparing")).toBeVisible();
  // No SSE terminal/reply is delivered: only durable runtime and history change.
  state.events.push(...questionEvents(1)); state.phase = "waiting_input";
  await expect(page.getByRole("heading", { name: plan(1).question, exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("agent-card-preparing")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop generating", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cancel pending response", exact: true })).toBeVisible();
  await expect(page).not.toHaveTitle(/^●/);
  await page.getByRole("radio", { name: /Use isolated workspace/ }).check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect.poll(() => state.answers).toBe(1);
  await expect(page.getByRole("button", { name: "Stop generating", exact: true })).toBeVisible();
  state.events.push(...questionEvents(2)); state.phase = "waiting_input";
  state.failHistory = 1;
  await expect(page.getByRole("heading", { name: plan(2).question, exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Review question 2", { exact: true })).toBeVisible({ timeout: 20_000 });
  expect(state.failedHistory).toBe(1);
  await expect(page.getByTestId("agent-card-preparing")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop generating", exact: true })).toHaveCount(0);
  state.phase = "cancelled";
  await expect(page.getByRole("region", { name: "Ask user question", exact: true })).toHaveCount(0, { timeout: 15_000 });
  await expect(page.locator(".agent-session").getByText("Cancelled", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel pending response", exact: true })).toHaveCount(0);
  await expect(page).not.toHaveTitle(/^●/);
  expect(errors).toEqual([]);
});

test("rapid Enter events send once across permission lookup, and failure releases the lock", async ({ page }) => {
  test.setTimeout(40_000);
  const { state, errors } = await fixture(page, false);
  state.permissionDelay = true; state.failAction = true;
  const input = page.locator("textarea.headless-input");
  await input.fill("Prepare PRD v4 with eight lanes");
  await input.evaluate((element) => {
    for (let i = 0; i < 2; i += 1) element.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  await expect(input).toBeEnabled();
  await expect(page.getByRole("alert")).toContainText(/Try again|503/);
  expect(state.posts).toBe(1);
  state.failAction = false;
  await input.press("Enter");
  await expect.poll(() => state.posts).toBe(2);
  await expect(input).toBeEnabled();
  expect(errors).toEqual([]);
});


test("unsubmitted question options and Other text survive reload and failed submission", async ({ page }) => {
  test.setTimeout(60_000);
  const { state, errors } = await fixture(page, true);
  state.events.push(...questionEvents(1)); state.phase = "waiting_input";
  const card = page.getByRole("region", { name: "Ask user question", exact: true });
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByRole("radio", { name: /Provide an existing repository/ }).check();
  await page.reload();
  if (!await card.isVisible()) await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(card.getByRole("radio", { name: /Provide an existing repository/ })).toBeChecked();
  expect(state.answers).toBe(0);
  await card.getByRole("radio", { name: "Other", exact: true }).check();
  await card.getByRole("textbox").fill("Use the CSV export workspace");
  state.failAnswer = true;
  await card.getByRole("button", { name: "Continue", exact: true }).click();
  await expect.poll(() => state.answers).toBe(1);
  await page.reload();
  if (!await card.isVisible()) await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(card.getByRole("radio", { name: "Other", exact: true })).toBeChecked();
  await expect(card.getByRole("textbox")).toHaveValue("Use the CSV export workspace");
  state.failAnswer = false;
  await card.getByRole("button", { name: "Continue", exact: true }).click();
  await expect.poll(() => state.answers).toBe(2);
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("zf.plan-question-draft:")).length)).toBe(0);
  state.events.push(...questionEvents(2)); state.phase = "waiting_input";
  await expect(page.getByRole("heading", { name: plan(2).question, exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(card.getByRole("radio", { name: "Other", exact: true })).not.toBeChecked();
  expect(errors).toEqual([]);
});
