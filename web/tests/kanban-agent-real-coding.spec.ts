import { mkdirSync, readFileSync } from "node:fs";

import {
  expect,
  test,
  type APIRequestContext,
  type Dialog,
  type Page,
} from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const projectRoot = process.env.ZF_REAL_CODING_PROJECT_ROOT ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  causation_id?: string | null;
  correlation_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  items?: EventItem[];
  current_seq?: number;
};

type EventDetail = {
  event?: EventItem;
};

type Snapshot = {
  project?: { project_id?: string };
};

test.describe.configure({ mode: "serial", timeout: 300_000 });

async function apiJson<T>(
  request: APIRequestContext,
  path: string,
): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(
    response.ok(),
    `${path}: ${response.status()} ${JSON.stringify(body)}`,
  ).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await apiJson<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id).not.toBe("");
  return id;
}

async function eventCursor(
  request: APIRequestContext,
  id: string,
): Promise<number> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?limit=1`,
  );
  return Number(page.current_seq ?? 0);
}

async function eventsAfter(
  request: APIRequestContext,
  id: string,
  cursor: number,
): Promise<EventItem[]> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?cursor=${cursor}&limit=200`,
  );
  return Array.isArray(page.items) ? page.items : [];
}

async function hydrateEvent(
  request: APIRequestContext,
  id: string,
  event: EventItem,
): Promise<EventItem> {
  const detail = await apiJson<EventDetail>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events/${encodeURIComponent(event.id)}`,
  );
  expect(detail.event).toBeDefined();
  return detail.event as EventItem;
}

function payloadText(event: EventItem): string {
  return JSON.stringify(event.payload ?? {});
}

function completedUserForMarker(
  events: EventItem[],
  marker: string,
): EventItem | undefined {
  const users = events.filter((event) => (
    event.type === "user.message" && payloadText(event).includes(marker)
  ));
  return [...users].reverse().find((user) => events.some((event) => (
    event.type === "kanban.agent.turn.completed"
    && event.correlation_id === user.correlation_id
  )));
}

async function waitForTurn(
  request: APIRequestContext,
  id: string,
  cursor: number,
  marker: string,
): Promise<{
  reply: EventItem;
  completed: EventItem;
  permission: EventItem;
}> {
  let events: EventItem[] = [];
  await expect.poll(async () => {
    events = await eventsAfter(request, id, cursor);
    return Boolean(completedUserForMarker(events, marker));
  }, {
    timeout: 180_000,
    intervals: [250, 500, 1000],
  }).toBeTruthy();

  const user = completedUserForMarker(events, marker);
  expect(user).toBeDefined();
  const replySummary = events.find((event) => (
    event.type === "kanban.agent.reply"
    && event.causation_id === user?.id
  ));
  const completedSummary = events.find((event) => (
    event.type === "kanban.agent.turn.completed"
    && event.correlation_id === user?.correlation_id
  ));
  expect(replySummary).toBeDefined();
  expect(completedSummary).toBeDefined();
  const reply = await hydrateEvent(request, id, replySummary as EventItem);
  const completed = await hydrateEvent(
    request,
    id,
    completedSummary as EventItem,
  );
  const permissionSummary = events.find((event) => (
    event.type === "provider.permission.snapshot.recorded"
    && event.causation_id === reply.id
  ));
  expect(permissionSummary).toBeDefined();
  const permission = await hydrateEvent(
    request,
    id,
    permissionSummary as EventItem,
  );
  return { reply, completed, permission };
}

async function openAgent(page: Page, id: string): Promise<void> {
  await page.addInitScript(({ actionToken }) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "codex-headless");
    window.localStorage.setItem("zf.webActionToken", actionToken);
  }, { actionToken: token });
  await page.goto(`/?project=${encodeURIComponent(id)}`);
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(
    page.getByRole("dialog", { name: "Kanban Agent" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Agent backend: Codex/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("radiogroup", { name: "Kanban Agent permission profile" }),
  ).toHaveCount(0);
}

async function reopenAgent(page: Page): Promise<void> {
  await page.reload();
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  }
  await expect(dialog).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Agent backend: Codex/ }),
  ).toBeVisible();
}

async function capture(page: Page, name: string): Promise<void> {
  if (!evidenceDir) {
    return;
  }
  mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({
    path: `${evidenceDir}/${name}.png`,
    fullPage: true,
  });
}

async function sendCodingTurn(
  page: Page,
  message: string,
  replyMarker: string,
): Promise<void> {
  const input = page.locator("textarea.headless-input");
  await input.fill(message);
  const dialogs: string[] = [];
  const recordUnexpectedDialog = async (dialog: Dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  };
  page.on("dialog", recordUnexpectedDialog);
  await page.getByRole("button", { name: "Send message" }).click();
  await page.waitForTimeout(100);
  page.off("dialog", recordUnexpectedDialog);
  expect(dialogs).toEqual([]);
  const reply = page.locator(".agent-text-part").filter({
    hasText: replyMarker,
  }).last();
  const escalation = page.getByRole("button", {
    name: "Run once with full access",
  });
  await expect.poll(async () => (
    await reply.isVisible().catch(() => false)
  ), {
    timeout: 180_000,
    intervals: [250, 500, 1000],
  }).toBeTruthy();
  await expect(reply).toBeVisible({ timeout: 180_000 });
  await expect(escalation).not.toBeVisible();
}

function expectPermissionMapping(
  turn: Awaited<ReturnType<typeof waitForTurn>>,
): void {
  expect(turn.reply.payload?.permission_profile).toBe("dangerous_full");
  expect(turn.completed.payload?.permission_profile).toBe("dangerous_full");
  expect(turn.permission.payload?.permission_profile).toBe("dangerous_full");
  const snapshot = turn.permission.payload?.snapshot as
    | Record<string, unknown>
    | undefined;
  expect(snapshot?.sandbox_policy).toBe("danger-full-access");
  expect(snapshot?.approval_policy).toBe("never");
}

test("real Codex edits code and resumes the Kanban Agent session", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(projectRoot).not.toBe("");
  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  await openAgent(page, id);

  const initialSource = readFileSync(
    `${projectRoot}/counter.py`,
    "utf-8",
  );
  expect(initialSource).toContain("NotImplementedError");

  const firstMarker = "ZF_REAL_CODING_TURN_ONE_DONE";
  await sendCodingTurn(
    page,
    [
      "ZF_REAL_CODING_TURN_ONE.",
      "Work only in the current temporary project.",
      "Implement running_total in counter.py so test_counter.py passes.",
      "Do not modify tests, zf.yaml, Git history, or .zf state.",
      "Run: python3 -m unittest -q test_counter.py",
      `After the test passes, reply exactly ${firstMarker}.`,
    ].join(" "),
    firstMarker,
  );
  const first = await waitForTurn(
    request,
    id,
    cursor,
    "ZF_REAL_CODING_TURN_ONE",
  );
  const firstSource = readFileSync(`${projectRoot}/counter.py`, "utf-8");
  expect(firstSource).not.toBe(initialSource);
  expect(firstSource).not.toContain("NotImplementedError");

  expect(first.reply.payload?.backend).toBe("codex-headless");
  expect(first.reply.payload?.resumed).toBe(false);
  expectPermissionMapping(first);
  await capture(page, "01-real-coding-turn-one");

  await reopenAgent(page);
  await expect(
    page.getByRole("radiogroup", { name: "Kanban Agent permission profile" }),
  ).toHaveCount(0);

  const secondMarker = "ZF_REAL_CODING_TURN_TWO_DONE";
  const secondCursor = await eventCursor(request, id);
  await sendCodingTurn(
    page,
    [
      "ZF_REAL_CODING_TURN_TWO.",
      "Continue the same coding task in the current project.",
      "Extend running_total with a keyword-only start argument defaulting to 0.",
      "running_total([2, -1, 3], start=10) must return [12, 11, 14],",
      "and empty input must still return an empty list.",
      "Do not modify tests, zf.yaml, Git history, or .zf state.",
      "Run the public test and an inline assertion for the start behavior.",
      `After both pass, reply exactly ${secondMarker}.`,
    ].join(" "),
    secondMarker,
  );
  const second = await waitForTurn(
    request,
    id,
    secondCursor,
    "ZF_REAL_CODING_TURN_TWO",
  );
  const finalSource = readFileSync(`${projectRoot}/counter.py`, "utf-8");
  expect(finalSource).not.toBe(firstSource);
  expect(finalSource).toContain("start");

  expect(second.reply.payload?.backend).toBe("codex-headless");
  expect(second.reply.payload?.resumed).toBe(true);
  expectPermissionMapping(second);
  await capture(page, "02-real-coding-turn-two-resumed");
  const firstSession = String(
    first.reply.payload?.provider_session_id ?? "",
  );
  const secondSession = String(
    second.reply.payload?.provider_session_id ?? "",
  );
  expect(firstSession).not.toBe("");
  expect(secondSession).toBe(firstSession);
});
