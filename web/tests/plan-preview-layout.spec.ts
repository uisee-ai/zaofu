import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = [
  "../src/styles/00-tokens-base.css",
  "../src/styles/11-delivery.css",
  "../src/styles/12-workflow.css",
].map((path) => readFileSync(resolve(here, path), "utf8")).join("\n");

function planPreviewMarkup() {
  return `
    <html data-theme="dark">
      <head>
        <style>${css}</style>
        <style>
          body { margin: 0; background: var(--bg); color: var(--text); }
          .agent-markdown h1 { margin-top: 0; }
        </style>
      </head>
      <body>
        <section class="panel plan-approval-panel">
          <div class="plan-approval-list">
            <article class="plan-approval-card">
              <div class="plan-approval-card-main">
                <div><strong>Plan Ready</strong><span class="mono">evt-plan-1</span></div>
                <span class="badge badge-warn">pending</span>
              </div>
              <dl class="plan-approval-meta">
                <div><dt>stage</dt><dd>writer</dd></div>
                <div><dt>tasks</dt><dd>12</dd></div>
                <div><dt>pdd</dt><dd>PDD-1</dd></div>
                <div><dt>trace</dt><dd>trace-super-long-value-for-ellipsis</dd></div>
              </dl>
              <div class="plan-approval-actions">
                <button class="delivery-action-button">Preview</button>
                <button class="delivery-action-button">Approve</button>
                <input value="missing assembly verification details" />
                <button class="delivery-action-button">Reject</button>
                <button class="delivery-action-button">Repair</button>
              </div>
            </article>
          </div>
        </section>
        <div class="plan-preview-overlay" role="dialog" aria-label="Plan preview">
          <div class="plan-preview-toolbar">
            <div><strong>Plan Preview</strong><span class="mono">evt-plan-1</span></div>
            <div class="plan-preview-toolbar-actions">
              <button class="delivery-action-button">Approve</button>
              <input value="missing assembly verification details" />
              <button class="delivery-action-button">Reject</button>
              <button class="delivery-action-button">Repair</button>
              <button class="icon-button">x</button>
            </div>
          </div>
          <div class="plan-preview-body">
            <main class="plan-preview-markdown-pane">
              <div class="agent-markdown plan-preview-markdown">
                <h1>Plan Ready</h1>
                <p>${"Review ".repeat(200)}</p>
              </div>
            </main>
            <aside class="plan-preview-context">
              <dl class="plan-approval-meta">
                <div><dt>status</dt><dd>pending</dd></div>
                <div><dt>stage</dt><dd>writer</dd></div>
                <div><dt>tasks</dt><dd>12</dd></div>
                <div><dt>trace</dt><dd>trace-1</dd></div>
              </dl>
              <pre class="delivery-raw-block">{"digest_ref":"artifacts/plan-digest/evt-plan-1.md"}</pre>
            </aside>
          </div>
        </div>
      </body>
    </html>
  `;
}

test.describe("plan preview layout", () => {
  for (const size of [
    { name: "desktop", width: 1280, height: 760 },
    { name: "mobile", width: 390, height: 760 },
  ]) {
    test(`${size.name} keeps preview scrollable and non-overlapping`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.setContent(planPreviewMarkup());

      const toolbar = page.locator(".plan-preview-toolbar");
      const body = page.locator(".plan-preview-body");
      const markdown = page.locator(".plan-preview-markdown-pane");
      const context = page.locator(".plan-preview-context");
      await expect(toolbar).toBeVisible();
      await expect(markdown).toBeVisible();
      await expect(context).toBeVisible();

      const boxes = await page.evaluate(() => {
        const rect = (selector: string) => {
          const el = document.querySelector(selector) as HTMLElement;
          const r = el.getBoundingClientRect();
          return {
            bottom: r.bottom,
            height: r.height,
            left: r.left,
            right: r.right,
            scrollHeight: el.scrollHeight,
            top: r.top,
            width: r.width,
          };
        };
        return {
          body: rect(".plan-preview-body"),
          context: rect(".plan-preview-context"),
          markdown: rect(".plan-preview-markdown-pane"),
          toolbar: rect(".plan-preview-toolbar"),
          toolbarInput: rect(".plan-preview-toolbar-actions input"),
        };
      });

      expect(boxes.toolbar.bottom).toBeLessThanOrEqual(boxes.body.top + 1);
      expect(boxes.body.bottom).toBeLessThanOrEqual(size.height + 1);
      expect(boxes.markdown.height).toBeGreaterThan(120);
      expect(boxes.toolbarInput.width).toBeGreaterThan(120);
      if (size.name === "desktop") {
        expect(boxes.markdown.right).toBeLessThanOrEqual(boxes.context.left + 1);
      } else {
        expect(boxes.markdown.bottom).toBeLessThanOrEqual(boxes.context.top + 1);
        expect(boxes.context.height).toBeLessThanOrEqual(size.height * 0.36);
      }
    });
  }

  test("mobile contract health table wraps inside the panel", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 760 });
    await page.setContent(`
      <html data-theme="dark">
        <head>
          <style>${css}</style>
          <style>body { margin: 0; background: var(--bg); color: var(--text); }</style>
        </head>
        <body>
          <section class="panel plan-approval-panel">
            <details class="plan-contract-health" open>
              <summary>Contract health: 4/12</summary>
              <table>
                <thead>
                  <tr><th>task</th><th>status</th><th>source</th><th>rework</th><th>signals</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td>TASK-029275</td>
                    <td>backlog</td>
                    <td>source_anchor_degraded</td>
                    <td>0</td>
                    <td>source_anchor_degraded, task_contract_needs_review</td>
                  </tr>
                </tbody>
              </table>
            </details>
          </section>
        </body>
      </html>
    `);

    const metrics = await page.locator(".plan-contract-health table").evaluate((el) => {
      const rect = el.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth;
      const cells = Array.from(el.querySelectorAll("th,td")).map((cell) => {
        const cellRect = cell.getBoundingClientRect();
        return {
          right: cellRect.right,
          scrollWidth: (cell as HTMLElement).scrollWidth,
          width: cellRect.width,
        };
      });
      return { cells, right: rect.right, viewportWidth, width: rect.width };
    });

    expect(metrics.right).toBeLessThanOrEqual(metrics.viewportWidth + 1);
    expect(metrics.width).toBeLessThanOrEqual(metrics.viewportWidth);
    for (const cell of metrics.cells) {
      expect(cell.right).toBeLessThanOrEqual(metrics.viewportWidth + 1);
      expect(cell.scrollWidth).toBeLessThanOrEqual(cell.width + 2);
    }
  });
});
