import base from "./playwright.config";

// Real API startup + lazy reader loading are part of this functional suite.
// These waits are not a product latency acceptance threshold.
export default {
  ...base,
  testMatch: ["plan-human-review.spec.ts", "plan-preview-content.spec.ts", "plan-preview-layout.spec.ts", "plan-preview-navigation.spec.ts", "plan-preview-agent.spec.ts", "plan-preview-profile.spec.ts"],
  workers: 1,
  timeout: 120000,
  expect: { ...base.expect, timeout: 60000 },
};
