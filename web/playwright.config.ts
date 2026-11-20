import { defineConfig, devices } from "@playwright/test";

const chromiumExecutablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: "./tests",
  // Long provider journeys have their own body budget and bounded fixture teardown.
  timeout: process.env.ZF_UI_TIMEOUT_MS ? 120_000 : 20_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    baseURL: process.env.ZF_WEB_BASE_URL ?? "http://127.0.0.1:8001",
    trace: process.env.ZF_E2E_TRACE === "off" ? "off" : process.env.ZF_E2E_VIDEO === "on" ? "on" : "retain-on-failure",
    video: process.env.ZF_E2E_VIDEO === "on" ? "on" : "off",
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
