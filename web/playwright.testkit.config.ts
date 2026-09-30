import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "testkit-truth-chain.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  outputDir: "/zf-run/browser-artifacts",
  reporter: [["json", { outputFile: "/zf-run/playwright.json" }]],
  use: {
    baseURL: process.env.ZF_WEB_BASE_URL,
    viewport: { width: 1440, height: 1000 },
    trace: "on",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH,
    },
  },
});
