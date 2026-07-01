import {
  formatOutputStats,
  formatToolDuration,
  getOutputPreview,
  prettyPrintIfJson,
} from "../src/components/agent-session/toolOutput.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

// --- getOutputPreview ---
const short = getOutputPreview("a\nb\nc");
assert(!short.isTruncated, "short output not truncated");
assert(short.text === "a\nb\nc", "short output returned verbatim");

const longByLines = getOutputPreview(Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n"));
assert(longByLines.isTruncated, "200 lines truncates");
assert(longByLines.shownLineCount === 80, `shows 80 lines, got ${longByLines.shownLineCount}`);
assert(longByLines.hiddenLineCount === 120, `hides 120 lines, got ${longByLines.hiddenLineCount}`);

const expanded = getOutputPreview(Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n"), true);
assert(!expanded.isTruncated, "expanded returns full text");
assert(expanded.shownLineCount === 200, "expanded shows all lines");

const longByChars = getOutputPreview("x".repeat(20_000));
assert(longByChars.isTruncated, "20k chars truncates");
assert(longByChars.shownCharCount <= 12_000, `char cap respected, got ${longByChars.shownCharCount}`);

// --- formatOutputStats ---
assert(formatOutputStats(short).includes("3 lines"), "stats counts lines");
assert(formatOutputStats(getOutputPreview("only")).includes("1 line"), "singular line");
assert(formatOutputStats(longByLines).includes("hidden"), "truncated stats mention hidden");

// --- prettyPrintIfJson ---
assert(prettyPrintIfJson('{"a":1}').includes("\n"), "compact JSON gets indented");
assert(prettyPrintIfJson("not json") === "not json", "non-JSON returned verbatim");
assert(prettyPrintIfJson("hello {world}") === "hello {world}", "prose with brace not mangled");

// --- formatToolDuration ---
assert(formatToolDuration(0.25) === "250ms", `sub-second ms, got ${formatToolDuration(0.25)}`);
assert(formatToolDuration(3.2) === "3.2s", `under 10s one decimal, got ${formatToolDuration(3.2)}`);
assert(formatToolDuration(42) === "42s", `under a minute whole, got ${formatToolDuration(42)}`);
assert(formatToolDuration(90) === "1m 30s", `minutes, got ${formatToolDuration(90)}`);
assert(formatToolDuration(3661) === "1h 1m", `hours, got ${formatToolDuration(3661)}`);
assert(formatToolDuration(-5) === "0ms", "negative clamps");

console.log("agentToolOutput.test.ts OK");
