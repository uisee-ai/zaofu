// WEB-TOKEN-LINT (docs/design/67 §3.3) — forcing-function:
// styles.css 必须用 --text-* token,不允许裸的绝对 font-size 字面量(px/rem)。
// em 是相对单位(如内联 code 随父字号缩放),豁免。
// 接 P9「signals not scripts」:把「用 token 不用字面值」从约定变机械护栏。
//
// 三刀① 守护(Plane 对齐):--text-10 / --text-11 只允许徽章/计数 chip/轴刻度/
// 图例/标注类选择器使用。allowlist(scripts/text-small-allowlist.json,选择器
// 级清单 + 计数封顶)之外新增 10/11 档 font-size 即 fail——正文/表格/可点击/
// meta 必须 ≥ --text-12(正文 ≥ --text-13)。要新增豁免,先改清单文件并说明理由。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const cssPath = join(here, "..", "src", "styles.css");
const allowlistPath = join(here, "text-small-allowlist.json");
const lines = readFileSync(cssPath, "utf8").split("\n");

// ── 守护 1:裸绝对 font-size 字面量 ──────────────────────────────────────────
const RAW_ABSOLUTE = /font-size:\s*[0-9.]+(px|rem)\b/; // 裸绝对字面量
const USES_TOKEN = /font-size:\s*var\(/;               // 已用 token

const violations = [];
lines.forEach((line, i) => {
  if (RAW_ABSOLUTE.test(line) && !USES_TOKEN.test(line)) {
    violations.push(`  ${i + 1}: ${line.trim()}`);
  }
});

if (violations.length > 0) {
  console.error("WEB-TOKEN-LINT 失败:styles.css 出现裸绝对 font-size(应改用 var(--text-*)):");
  console.error(violations.join("\n"));
  console.error("修复:用 --text-* token 替换;若需新档位,先在 :root 定义。em 相对单位豁免。");
  process.exit(1);
}

// ── 守护 2:--text-10/--text-11 选择器级 allowlist + 计数封顶 ────────────────
// 提取每个 font-size: var(--text-10|11) 的 enclosing selector(回溯到 `{` 行,
// 再吸收以 `,` 结尾的前置选择器行),与清单逐条比对;总次数超清单封顶也 fail。
const SMALL_TOKEN = /font-size:\s*var\(--text-(10|11)\)/;
const found = [];
for (let i = 0; i < lines.length; i++) {
  const m = lines[i].match(SMALL_TOKEN);
  if (!m) continue;
  let j = i;
  while (j >= 0 && !lines[j].includes("{")) j--;
  let selStart = j;
  while (selStart - 1 >= 0 && lines[selStart - 1].trim().endsWith(",")) selStart--;
  const selector = lines
    .slice(selStart, j + 1)
    .join(" ")
    .replace(/\{.*/, "")
    .replace(/\s+/g, " ")
    .trim();
  found.push({ line: i + 1, selector, tier: `text-${m[1]}` });
}

const allowlist = JSON.parse(readFileSync(allowlistPath, "utf8"));
const allowedKeys = new Set(allowlist.map((entry) => `${entry.selector}|${entry.tier}`));
const cap = allowlist.length;

const unlisted = found.filter((hit) => !allowedKeys.has(`${hit.selector}|${hit.tier}`));
if (unlisted.length > 0) {
  console.error("WEB-TOKEN-LINT 失败:--text-10/--text-11 出现在 allowlist 之外的选择器:");
  unlisted.forEach((hit) => console.error(`  ${hit.line}: [${hit.tier}] ${hit.selector}`));
  console.error("规则:10/11 档只留徽章/计数/轴刻度/图例/标注;正文/表格/可点击/meta ≥ 12。");
  console.error("确属徽章类:把选择器加进 scripts/text-small-allowlist.json(带 tier)。");
  process.exit(1);
}

if (found.length > cap) {
  console.error(`WEB-TOKEN-LINT 失败:--text-10/--text-11 总次数 ${found.length} 超过清单封顶 ${cap}。`);
  console.error("同一选择器重复新增小字号也算超标;先收敛或更新清单。");
  process.exit(1);
}

// ── 守护 3:KV 对规范(2026-06-12 ①)——.kv 族块内禁 semibold/bold ────────────
// .kv 值字重封顶 500(medium):.kv / .kv-key / .kv-value 选择器块内出现
// font-weight 600+(字面量或 --font-weight-semibold/bold/heavy token)即 fail。
const KV_SELECTOR = /\.kv(-key|-value)?(?![\w-])/;
const KV_FORBIDDEN = /font-weight:\s*(?:[6-9]\d{2}|var\(--font-weight-(?:semibold|bold|heavy)\))/;

const kvViolations = [];
let kvSelector = null;
for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  if (line.includes("{")) {
    // 与守护 2 同思路:取 { 前的选择器文本(吸收以 , 结尾的前置选择器行)。
    let selStart = i;
    while (selStart - 1 >= 0 && lines[selStart - 1].trim().endsWith(",")) selStart--;
    kvSelector = lines.slice(selStart, i + 1).join(" ").replace(/\{.*/, "").replace(/\s+/g, " ").trim();
  }
  if (kvSelector && KV_SELECTOR.test(kvSelector) && KV_FORBIDDEN.test(line)) {
    kvViolations.push(`  ${i + 1}: [${kvSelector}] ${line.trim()}`);
  }
  if (line.includes("}")) kvSelector = null;
}

if (kvViolations.length > 0) {
  console.error("WEB-TOKEN-LINT 失败:.kv 族(.kv/.kv-key/.kv-value)块内出现 ≥600 字重:");
  kvViolations.forEach((violation) => console.error(violation));
  console.error("KV 规范:value 字重封顶 500(--font-weight-medium);强调预算=颜色必选 + weight≤500。");
  process.exit(1);
}

console.log("WEB-TOKEN-LINT OK:styles.css 无裸绝对 font-size 字面量。");
console.log(`WEB-TOKEN-LINT OK:--text-10/11 共 ${found.length} 处,全部在 allowlist 内(封顶 ${cap})。`);
console.log("WEB-TOKEN-LINT OK:.kv 族块内无 ≥600 字重。");
