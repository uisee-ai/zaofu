import { defineConfig, devices } from "@playwright/test";

const chromiumExecutablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: "./tests",
  // Isolated Issue UI tests start Vite through playwright.issue.config.ts.
  testIgnore: ["issue-triage-controls.spec.ts", "issue-triage-actions.spec.ts", "issue-triage-loading.spec.ts", "settings-workspace.spec.ts", "issue-source-settings.spec.ts"],
  timeout: 20_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    baseURL: process.env.ZF_WEB_BASE_URL ?? "http://127.0.0.1:8001",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: chromiumExecutablePath
          ? { executablePath: chromiumExecutablePath }
          : undefined,
      },
    },
  ],
});
