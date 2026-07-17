#!/usr/bin/env node
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`ZaoFu Web Visual Product QA
Usage: npm --prefix web run qa:visual
Env: ZF_WEB_URL, ZF_WEB_PROJECT, ZF_WEB_PROJECT_NAME, ZF_VISUAL_QA_OUT,
     ZF_VISUAL_QA_FAIL_ON=P0|P1|P2|none, ZF_VISUAL_QA_PAGES,
     ZF_PLAYWRIGHT_EXECUTABLE_PATH
`);
  process.exit(0);
}

const BASE_URL = process.env.ZF_WEB_URL || "http://127.0.0.1:8001";
let PROJECT_ID = process.env.ZF_WEB_PROJECT || "";
const PROJECT_NAME = process.env.ZF_WEB_PROJECT_NAME || "";
const OUT_DIR = process.env.ZF_VISUAL_QA_OUT
  || `/tmp/zf-visual-product-qa-${new Date().toISOString().replace(/[:.]/g, "-")}`;
const FAIL_ON = process.env.ZF_VISUAL_QA_FAIL_ON || "P1";
const PLAYWRIGHT_EXECUTABLE_PATH = process.env.ZF_PLAYWRIGHT_EXECUTABLE_PATH || "";
const DEFAULT_PAGES = ["project", "inbox", "board", "agents", "automations", "delivery", "behavior-loop", "observability", "channels", "settings"];
const PAGES = (process.env.ZF_VISUAL_QA_PAGES || DEFAULT_PAGES.join(","))
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const VIEWPORTS = [{ name: "desktop", width: 1440, height: 900 }, { name: "mobile", width: 390, height: 844 }];
const SEVERITY_RANK = { P0: 3, P1: 2, P2: 1, info: 0 };
const FAIL_RANK = FAIL_ON === "none" ? Number.POSITIVE_INFINITY : (SEVERITY_RANK[FAIL_ON] ?? SEVERITY_RANK.P1);
const SNAPSHOT_PAGES = new Set([
  "project", "board", "task", "triage", "traces", "runtime", "settings",
  "diagnostics", "observability", "events", "runs", "fanouts",
  "candidates", "workdirs", "skills", "archives",
]);
function pageUrl(page) {
  const url = new URL(BASE_URL);
  if (PROJECT_ID) url.searchParams.set("project", PROJECT_ID);
  url.searchParams.set("page", page);
  return url.toString();
}
async function resolveProjectId() {
  if (PROJECT_ID || !PROJECT_NAME) return;
  const url = new URL("/api/workspace/projects", BASE_URL);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url.toString()} returned ${response.status}`);
  }
  const payload = await response.json();
  const projects = Array.isArray(payload.items)
    ? payload.items
    : Array.isArray(payload.projects)
      ? payload.projects
      : [];
  const namedProjects = projects.filter((project) => (
    project?.name === PROJECT_NAME
    || project?.project_id === PROJECT_NAME
    || (Array.isArray(project?.aliases) && project.aliases.includes(PROJECT_NAME))
  ));
  const activeNamedProject = namedProjects.find((project) => project.project_id === payload.active_project_id);
  const selectedProject = activeNamedProject || namedProjects[0];
  if (!selectedProject?.project_id) {
    throw new Error(`workspace project not found: ${PROJECT_NAME}`);
  }
  PROJECT_ID = selectedProject.project_id;
}
function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
function issue(severity, code, message, detail = {}) {
  return { severity, code, message, detail };
}
function shouldFail(issues) {
  return issues.some((item) => (SEVERITY_RANK[item.severity] ?? 0) >= FAIL_RANK);
}
async function clickReadOnlyTabs(page) {
  const results = [];
  const selector = ".tab-row button, .compact-tabs button, .delivery-main-tab, [role=tab]";
  const visited = new Set();
  for (let attempt = 0; attempt < 32; attempt += 1) {
    const candidates = await page.locator(selector).evaluateAll((nodes) => {
      const occurrences = new Map();
      return nodes.map((node) => {
        const text = String(node.textContent || node.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
        const signature = [
          node.getAttribute("data-testid") || "",
          node.getAttribute("aria-controls") || "",
          node.getAttribute("role") || "",
          node.parentElement?.className || "",
          text,
        ].join("|");
        const occurrence = occurrences.get(signature) || 0;
        occurrences.set(signature, occurrence + 1);
        return {
          key: `${signature}|${occurrence}`,
          text,
          visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
          disabled: node.matches(":disabled, [aria-disabled='true']"),
          ignored: !!node.closest(".dt-feature-selector"),
        };
      });
    }).catch(() => []);
    const candidate = candidates.find((item) => (
      item.visible && !item.disabled && !item.ignored && !visited.has(item.key)
    ));
    if (!candidate) break;
    visited.add(candidate.key);
    try {
      const targetIndex = candidates.findIndex((item) => item.key === candidate.key);
      await page.locator(selector).nth(targetIndex).click({ timeout: 2500 });
      await page.waitForTimeout(350);
      results.push({ key: candidate.key, text: candidate.text, ok: true });
    } catch (error) {
      results.push({ key: candidate.key, text: candidate.text, ok: false, error: String(error).slice(0, 220) });
    }
  }
  return results;
}
async function waitForPageReady(page, pageId) {
  if (!PROJECT_ID) return { required: false, ok: true };
  await page.locator(".route-loading[aria-busy='true']").waitFor({
    state: "hidden",
    timeout: 12000,
  }).catch(() => undefined);
  const required = SNAPSHOT_PAGES.has(pageId);
  if (!required) return { required: false, ok: true };
  try {
    await page.waitForFunction(() => {
      const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
      const topbarText = normalize(document.querySelector(".topbar")?.textContent);
      const bodyText = normalize(document.body?.textContent);
      const snapshotMatch = bodyText.match(/snapshot\s+#(\d+)/i);
      const seq = snapshotMatch ? Number(snapshotMatch[1]) : 0;
      return topbarText.includes("local workspace") || seq > 0;
    }, null, { timeout: 12000 });
    return { required: true, ok: true };
  } catch (error) {
    return {
      error: String(error).slice(0, 500),
      ok: false,
      required,
    };
  }
}
async function collectPageModel(page, pageId) {
  return page.evaluate((input) => {
    const { pageId } = input;
    const visible = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== "none"
        && style.visibility !== "hidden"
        && rect.width > 2
        && rect.height > 2;
    };
    const text = (el) => String(el?.textContent || "").replace(/\s+/g, " ").trim();
    const selectorName = (el) => {
      const className = typeof el.className === "string" && el.className.trim()
        ? `.${el.className.trim().split(/\s+/).slice(0, 3).join(".")}`
        : "";
      return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${className}`;
    };
    const rectOf = (el) => {
      const rect = el.getBoundingClientRect();
      return {
        bottom: Math.round(rect.bottom),
        height: Math.round(rect.height),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
      };
    };
    const hasHorizontalScrollAncestor = (el) => {
      let node = el.parentElement;
      while (node && node !== document.body) {
        const style = getComputedStyle(node);
        const canScroll = /(auto|scroll)/.test(style.overflowX) && node.scrollWidth > node.clientWidth + 3;
        if (canScroll) return true;
        node = node.parentElement;
      }
      return false;
    };
    const isFrameChrome = (el) => (
      el === document.documentElement
      || el === document.body
      || el.id === "root"
      || el.classList.contains("app-shell")
      || el.classList.contains("topbar")
      || el.classList.contains("app-layout")
    );
    const nodeSummary = (el) => ({
      rect: rectOf(el),
      selector: selectorName(el),
      text: text(el).slice(0, 180),
    });
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const horizontalOverflow = document.documentElement.scrollWidth - viewportWidth;
    const horizontallyClipped = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (!visible(el)) continue;
      if (isFrameChrome(el)) continue;
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (style.position === "fixed") continue;
      if (viewportWidth < 720 && el.closest(".rail-section")) continue;
      if ((rect.left < -3 || rect.right > viewportWidth + 3) && !hasHorizontalScrollAncestor(el)) {
        horizontallyClipped.push(nodeSummary(el));
      }
      if (horizontallyClipped.length >= 25) break;
    }
    const textOverflow = [];
    const overflowTargets = [
      "button",
      "a",
      ".badge",
      ".metric-chip",
      ".tab-button",
      ".tab-chip",
      ".delivery-main-tab",
      ".event-meta-chip",
      ".event-type-text",
      ".event-target-pill",
      ".task-flow-node",
      ".project-health-card p",
    ].join(",");
    for (const el of Array.from(document.querySelectorAll(overflowTargets))) {
      if (!visible(el)) continue;
      const style = getComputedStyle(el);
      const label = text(el);
      if (!label || label.length <= 1) continue;
      if (style.overflow === "visible" && style.textOverflow !== "ellipsis") continue;
      if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) {
        textOverflow.push({
          ...nodeSummary(el),
          clientHeight: el.clientHeight,
          clientWidth: el.clientWidth,
          scrollHeight: el.scrollHeight,
          scrollWidth: el.scrollWidth,
        });
      }
      if (textOverflow.length >= 30) break;
    }
    const metricGaps = Array.from(document.querySelectorAll(".metrics-strip-item")).map((node) => {
      const label = node.querySelector("span");
      const value = node.querySelector("strong");
      const labelRect = label?.getBoundingClientRect();
      const valueRect = value?.getBoundingClientRect();
      return {
        gap: labelRect && valueRect ? Math.round(valueRect.left - labelRect.right) : null,
        text: text(node),
      };
    });
    const keyValueGaps = Array.from(document.querySelectorAll(".key-value-grid dt, .detail-grid dt")).slice(0, 80).map((dt) => {
      const dd = dt.nextElementSibling;
      const dtRect = dt.getBoundingClientRect();
      const ddRect = dd?.getBoundingClientRect();
      return {
        gap: ddRect ? Math.round(ddRect.left - dtRect.right) : null,
        key: text(dt),
        value: text(dd).slice(0, 80),
      };
    }).filter((row) => row.gap !== null);
    const deliveryCard = Array.from(document.querySelectorAll(".project-health-card"))
      .map((node) => text(node))
      .find((value) => value.includes("Delivery")) || "";
    const runPulse = text(document.querySelector("[data-testid='overview-pulse-band']"));
    const taskFlow = text(document.querySelector("[data-testid='overview-task-flow-band']"));
    const taskFlowNodeCount = document.querySelectorAll("[data-testid='overview-task-flow-band'] .task-flow-node").length;
    const taskFlowAscii = Array.from(document.querySelectorAll("[data-testid='overview-task-flow-band'] *"))
      .map((node) => text(node))
      .filter((value) => /──|-->|->|▶|◀/.test(value));
    const deliveryTabs = Array.from(document.querySelectorAll(".delivery-main-tab")).map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        clientWidth: node.clientWidth,
        rect: { width: Math.round(rect.width) },
        scrollWidth: node.scrollWidth,
        text: text(node),
      };
    });
    const rawText = text(document.body).slice(0, 20000);
    const topbarText = text(document.querySelector(".topbar"));
    const legacyStaticShell = /KANBAN.*BACKLOG.*IN_PROGRESS.*WORKERS.*EVENT TAIL/is.test(rawText);
    const snapshotLabel = rawText.match(/snapshot\s+#\d+\s*·\s*[^|]+/i)?.[0] || "";
    const snapshotSeq = Number(snapshotLabel.match(/snapshot\s+#(\d+)/i)?.[1] || 0);
    return {
      bodyTextSample: rawText.slice(0, 500),
      deliveryCard,
      deliveryTabs,
      horizontallyClipped,
      horizontalOverflow,
      keyValueGaps,
      legacyStaticShell,
      metricGaps,
      pageId,
      runPulse,
      snapshotLabel,
      snapshotReady: topbarText.includes("local workspace") || snapshotSeq > 0,
      snapshotSeq,
      taskFlow,
      taskFlowAscii,
      taskFlowNodeCount,
      textOverflow,
      title: text(document.querySelector(".board-panel h1, .board-panel h2")),
      topbarText,
      visibleButtonTexts: Array.from(document.querySelectorAll("button")).filter(visible).map(text).filter(Boolean).slice(0, 80),
    };
  }, { pageId });
}
function evaluateModel(model, context) {
  const issues = [];
  const where = `${context.viewport}:${context.pageId}`;
  const add = (severity, code, message, detail = {}) => issues.push(issue(severity, code, `${where}: ${message}`, detail));
  if (model.horizontalOverflow > 3) {
    add("P1", "PAGE_HORIZONTAL_OVERFLOW", `page has ${model.horizontalOverflow}px horizontal overflow`, { overflow: model.horizontalOverflow });
  }
  if (model.legacyStaticShell) {
    add("P1", "LEGACY_STATIC_FALLBACK", "React cockpit was replaced by legacy static Kanban shell", {
      bodyTextSample: model.bodyTextSample,
    });
  }
  if (PROJECT_ID && context.snapshotRequired && !model.snapshotReady) {
    add("P1", "PROJECT_SNAPSHOT_NOT_READY", "target project snapshot did not load before visual QA", {
      expectedProjectId: PROJECT_ID,
      snapshotLabel: model.snapshotLabel,
      topbarText: model.topbarText,
    });
  }
  for (const item of model.horizontallyClipped.slice(0, 5)) {
    add("P1", "VISIBLE_ELEMENT_CLIPPED", "visible element is horizontally outside viewport", item);
  }
  for (const item of model.textOverflow.slice(0, 8)) {
    const shortText = item.text.length <= 40;
    const primary = /tab|button|project-health|task-flow/.test(item.selector);
    if (shortText || primary) {
      add("P1", "PRIMARY_TEXT_TRUNCATED", "primary label/control text is truncated", item);
    }
  }
  const badTokens = [
    { code: "UNDEFINED_TEXT", pattern: /\bundefined\b/i },
    { code: "NULL_TEXT", pattern: /\bnull\b/i },
    { code: "NAN_TEXT", pattern: /\bNaN\b/ },
    { code: "EMPTY_TIMING_ARROW", pattern: /[-—]\s*(?:->|→)\s*[-—]/ },
  ];
  for (const token of badTokens) {
    if (token.pattern.test(model.bodyTextSample)) {
      add("P1", token.code, "page contains technical placeholder text", { sample: model.bodyTextSample.match(token.pattern)?.[0] });
    }
  }
  if (context.pageId === "project") {
    if (/events\/min.*0\.0/i.test(model.runPulse)) {
      add("P1", "RUN_PULSE_ZERO_RATE", "Run Pulse should render idle/no-data instead of events/min 0.0", { runPulse: model.runPulse });
    }
    if (/events\/min.*[▁▂▃▄▅▆▇█]{2,}.*0\.0/i.test(model.runPulse)) {
      add("P1", "RUN_PULSE_ZERO_SPARKLINE", "Run Pulse should not show all-zero sparkline noise", { runPulse: model.runPulse });
    }
    if (/Delivery.*[▁▂▃▄▅▆▇█]{2,}\s*7d/i.test(model.deliveryCard)) {
      add("P1", "DELIVERY_EMPTY_SPARKLINE", "Delivery card should not show empty 7d sparkline", { deliveryCard: model.deliveryCard });
    }
    if (/rework\s*-%/i.test(model.deliveryCard)) {
      add("P1", "DELIVERY_UNKNOWN_REWORK_PERCENT", "Delivery card should omit unknown rework instead of showing -%", { deliveryCard: model.deliveryCard });
    }
    for (const row of model.metricGaps) {
      if (typeof row.gap === "number" && row.gap > 48) {
        add("P1", "OVERVIEW_METRIC_GAP_TOO_WIDE", "Velocity/Quality/Economy key/value gap is too wide", row);
      }
    }
    if (model.taskFlow && model.taskFlowNodeCount < 4) {
      add("P1", "TASK_FLOW_MISSING_NODES", "Task Flow must render at least four workflow nodes", { taskFlow: model.taskFlow, taskFlowNodeCount: model.taskFlowNodeCount });
    }
    if (model.taskFlowAscii.length > 0) {
      add("P1", "TASK_FLOW_ASCII_ROUTE", "Task Flow should use visual edges, not ASCII arrows", { samples: model.taskFlowAscii.slice(0, 5) });
    }
  }
  if (context.pageId === "delivery") {
    for (const tab of model.deliveryTabs) {
      if (tab.scrollWidth > tab.clientWidth + 2 && tab.text.length <= 32) {
        add("P1", "DELIVERY_TAB_TRUNCATED", "Delivery tab label is truncated", tab);
      }
    }
  }
  const keyValueOutliers = model.keyValueGaps
    .filter((row) => typeof row.gap === "number" && row.gap > 96 && row.key.length <= 28)
    .slice(0, 5);
  for (const row of keyValueOutliers) {
    add("P2", "KEY_VALUE_GAP_WIDE", "key/value gap is wide; verify density against design baseline", row);
  }
  return issues;
}
function markdownReport(report) {
  const counts = report.issues.reduce((acc, item) => {
    acc[item.severity] = (acc[item.severity] || 0) + 1;
    return acc;
  }, {});
  const lines = [
    "# Visual Product QA",
    "",
    `- base: \`${report.baseUrl}\``,
    `- project: \`${report.projectId || "-"}\``,
    `- project_name: \`${report.projectName || "-"}\``,
    `- generated_at: \`${report.generatedAt}\``,
    `- out_dir: \`${report.outDir}\``,
    `- pages: \`${report.results.length}\``,
    `- fail_on: \`${report.failOn}\``,
    `- issues: P0=${counts.P0 || 0} P1=${counts.P1 || 0} P2=${counts.P2 || 0}`,
    "",
  ];
  if (!report.issues.length) {
    lines.push("No product QA issues found by current rules.", "");
  } else {
    lines.push("## Issues", "");
    for (const item of report.issues) {
      lines.push(`- **${item.severity} ${item.code}** ${item.message}`);
      const detail = JSON.stringify(item.detail || {});
      if (detail !== "{}") lines.push(`  - detail: \`${detail.slice(0, 500)}\``);
    }
    lines.push("");
  }
  lines.push("## Screenshots", "");
  for (const result of report.results) {
    lines.push(`- ${result.viewport}:${result.pageId} -> \`${result.screenshot}\``);
  }
  lines.push("");
  return `${lines.join("\n")}\n`;
}
async function runPage(browser, viewport, pageId) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(cleanText(msg.text()).slice(0, 300));
  });
  page.on("pageerror", (error) => pageErrors.push(String(error).slice(0, 500)));
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (url.startsWith("data:")) return;
    const error = request.failure()?.errorText || "failed";
    const normalSseAbort = /\/api\/(?:projects\/[^/]+\/)?stream(?:\?|$)/.test(url)
      && /ERR_ABORTED|NS_BINDING_ABORTED|cancelled/i.test(error);
    if (normalSseAbort) return;
    failedRequests.push({ url: url.slice(0, 240), error });
  });
  const url = pageUrl(pageId);
  const result = {
    consoleErrors,
    failedRequests,
    pageErrors,
    pageId,
    url,
    viewport: viewport.name,
  };
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(1800);
    result.snapshotReadyWait = await waitForPageReady(page, pageId);
    result.tabClicks = await clickReadOnlyTabs(page);
    result.model = await collectPageModel(page, pageId);
    const screenshot = join(OUT_DIR, `${viewport.name}-${pageId}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    result.screenshot = screenshot;
  } catch (error) {
    result.error = String(error).slice(0, 1000);
  } finally {
    await context.close();
  }
  return result;
}
async function main() {
  await resolveProjectId();
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: PLAYWRIGHT_EXECUTABLE_PATH } : {}),
  });
  const results = [];
  for (const viewport of VIEWPORTS) {
    for (const pageId of PAGES) {
      results.push(await runPage(browser, viewport, pageId));
    }
  }
  await browser.close();
  const issues = [];
  for (const result of results) {
    const where = `${result.viewport}:${result.pageId}`;
    if (result.error) issues.push(issue("P0", "PAGE_LOAD_ERROR", `${where}: page failed to load`, { error: result.error }));
    for (const error of result.consoleErrors || []) {
      issues.push(issue("P1", "CONSOLE_ERROR", `${where}: console error`, { error }));
    }
    for (const error of result.pageErrors || []) {
      issues.push(issue("P0", "PAGE_ERROR", `${where}: uncaught page error`, { error }));
    }
    for (const failure of result.failedRequests || []) {
      issues.push(issue("P1", "REQUEST_FAILED", `${where}: request failed`, failure));
    }
    for (const tab of (result.tabClicks || []).filter((item) => !item.ok)) {
      issues.push(issue("P1", "TAB_CLICK_FAILED", `${where}: tab click failed`, tab));
    }
    if (result.model) {
      issues.push(...evaluateModel(result.model, {
        pageId: result.pageId,
        snapshotRequired: SNAPSHOT_PAGES.has(result.pageId),
        viewport: result.viewport,
      }));
    }
  }
  const report = {
    baseUrl: BASE_URL,
    failOn: FAIL_ON,
    generatedAt: new Date().toISOString(),
    issues,
    outDir: OUT_DIR,
    pages: PAGES,
    projectId: PROJECT_ID,
    projectName: PROJECT_NAME,
    results,
    viewports: VIEWPORTS,
  };
  const jsonPath = join(OUT_DIR, "visual-product-qa.json");
  const mdPath = join(OUT_DIR, "visual-product-qa.md");
  writeFileSync(jsonPath, JSON.stringify(report, null, 2));
  writeFileSync(mdPath, markdownReport(report));
  const summary = {
    failed: shouldFail(issues),
    issueCounts: issues.reduce((acc, item) => {
      acc[item.severity] = (acc[item.severity] || 0) + 1;
      return acc;
    }, {}),
    jsonPath,
    mdPath,
    outDir: OUT_DIR,
    pages: results.length,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (summary.failed) process.exit(1);
}
main().catch((error) => {
  console.error(error);
  process.exit(1);
});
