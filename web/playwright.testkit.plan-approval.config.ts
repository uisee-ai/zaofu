import { defineConfig } from "@playwright/test";
import base from "./playwright.testkit.config";

export default defineConfig({
  ...base,
  testMatch: "testkit-plan-approval.spec.ts",
});
