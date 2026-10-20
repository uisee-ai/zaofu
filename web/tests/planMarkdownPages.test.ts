import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import { planMarkdownPages, PLAN_PAGE_LIMIT } from "../src/components/delivery-trace/planMarkdownPages.js";

function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
const parse = (text: string) => fromMarkdown(text, { extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()] });
const examples = ["", "# 标题\n\n**强调**及列表\n\n- 第一项\n- 第二项\n", "\n# CRLF\r\n\r\n段落😀\r\n",
  "# Tokens\n\n" + "😀".repeat(40000), "```" + "a".repeat(30000),
  "```bash\n" + "echo 未闭合\n".repeat(5000), "# List\n\n" + "- 逐条验证权限与状态\n".repeat(5000)];
const table = "# 表格\n\n| 编号 | 行为 |\n| --- | --- |\n" + Array.from({ length: 2400 }, (_, i) => `| R-${i} | 输入与失败结果😀 |\n`).join("") + "\n## 末尾\n结束\n";
const code = "# 代码\n\n```bash\n" + Array.from({ length: 4000 }, (_, i) => `echo 行-${i}😀\n`).join("") + "```\n\n末尾\n";
for (const source of [...examples, table, code]) {
  const pages = planMarkdownPages(source);
  assert(pages.length > 0, "empty documents still have a page");
  assert(pages.map(p => p.source).join("") === source, "exact source survives all boundaries");
  assert(pages.every(p => p.markdown.length <= PLAN_PAGE_LIMIT), "rendered pages remain bounded");
  assert(pages.every(p => !/[\uD800-\uDBFF]$/.test(p.source)), "no split surrogate pairs");
}
const longParagraph = "# Long\n\n" + "很长😀 ".repeat(16000) + "END-OF-PLAN\n";
const last = planMarkdownPages(longParagraph).at(-1)!;
assert(last.source.includes("END-OF-PLAN"), "trailing whitespace must not create a blank final page");
const tablePages = planMarkdownPages(table);
assert(tablePages.length > 1, "long tables are paginated");
let rows = 0;
for (const page of tablePages) {
  for (const node of parse(page.markdown).children) if (node.type === "table") rows += node.children.length - 1;
}
assert(rows === 2400, `all rows remain formatted table rows, got ${rows}`);
let codeText = "";
for (const page of planMarkdownPages(code)) {
  for (const node of parse(page.markdown).children) if (node.type === "code") {
    assert(node.lang === "bash", "fenced language retained"); codeText += node.value;
  }
}
assert((codeText.match(/echo 行-/g) || []).length === 4000, "all code lines stay in fenced code blocks");
assert(planMarkdownPages("# A\n\n正文\n\n## B\n\n正文")[0].title === "A", "navigation keeps the page's first heading");
console.log("planMarkdownPages.test.ts OK");
