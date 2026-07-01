import {
  clampPlainText,
  isPathologicalText,
  longestUnbrokenRun,
  MAX_PLAINTEXT_DISPLAY_LENGTH,
} from "../src/components/agent-session/markdownGuard.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

// --- longestUnbrokenRun ---
assert(longestUnbrokenRun("") === 0, "empty run is 0");
assert(longestUnbrokenRun("ab cde f") === 3, `whitespace resets run, got ${longestUnbrokenRun("ab cde f")}`);
assert(longestUnbrokenRun("a\nbb\tcccc") === 4, "tabs/newlines reset run");

// --- isPathologicalText ---
assert(!isPathologicalText("normal markdown text"), "normal text is fine");
assert(!isPathologicalText("a ".repeat(20_000)), "sub-cap breakable text is fine"); // 40k chars < 50k, longest run 1
assert(isPathologicalText("x".repeat(60_000)), "huge total length is pathological");
assert(isPathologicalText("data:image/png;base64," + "A".repeat(6_000)), "unbroken base64 token is pathological");

// --- clampPlainText ---
const small = clampPlainText("short");
assert(small.shown === "short" && small.hiddenCount === 0, "small text not clamped");
const big = clampPlainText("y".repeat(MAX_PLAINTEXT_DISPLAY_LENGTH + 500));
assert(big.shown.length === MAX_PLAINTEXT_DISPLAY_LENGTH, "clamped to cap");
assert(big.hiddenCount === 500, `reports hidden count, got ${big.hiddenCount}`);

console.log("markdownGuard.test.ts OK");
