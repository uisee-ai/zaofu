import { defineConfig, devices } from "@playwright/test";
const issuePort = Number(process.env.ZF_ISSUE_E2E_PORT || 4176);
const issueBaseUrl = `http://127.0.0.1:${issuePort}`;
export default defineConfig({
  testDir: "./tests", testMatch: /(?:issue-triage-(controls|actions|loading)|self-issue-workspace|portable-issue-workspace|issue-source-settings)\.spec\.ts/,
  outputDir: "./test-results/issue-workspace/artifacts",
  reporter: [["list"], ["html", { outputFolder: "test-results/issue-workspace/html", open: "never" }], ["json", { outputFile: "test-results/issue-workspace/results.json" }]],
  timeout: 25000, expect: { timeout: 6000 }, workers: 1,
  use: { baseURL: issueBaseUrl, viewport: { width:1536,height:960 }, trace:"retain-on-failure", screenshot:"only-on-failure" },
  projects: [{ name:"chromium",use:{...devices["Desktop Chrome"],viewport:{width:1536,height:960},launchOptions:process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH}:{}} }],
  webServer: { command:`npm run dev -- --host 0.0.0.0 --port ${issuePort} --strictPort`,url:`${issueBaseUrl}/tests/fixtures/issue-workspace/index.html`,reuseExistingServer:false, stdout:"pipe", stderr:"pipe" },
});
