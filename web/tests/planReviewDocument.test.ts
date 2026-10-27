import { planBlockQuote, planReviewBlocks } from "../src/components/workflow/planReviewDocument.js";

function equal(actual: unknown, expected: unknown) { if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`); }

const paragraph = "玩家😀点击 **加一**，结果为 `1`。";
const body = `\n# 计划😀\r\n\r\n${paragraph}\n\n${paragraph}\n\n- 保留状态\n- 不发网络请求\n\n| A | B |\n| - | - |\n| 😀 | 1 |\n\n[ref]: https://example.com\n`;
const { blocks, definitions } = planReviewBlocks(body);
equal(blocks.length, 5);
equal(definitions, "\n\n[ref]: https://example.com");
for (const block of blocks) {
  const quote = planBlockQuote(body, block.start, block.end)!;
  equal(Array.from(body).slice(quote.start, quote.end).join(""), block.source);
}
const first = planBlockQuote(body, blocks[1].start, blocks[1].end)!;
const second = planBlockQuote(body, blocks[2].start, blocks[2].end)!;
equal(first.text, second.text);
if (second.start <= first.end) throw new Error("repeated paragraphs must retain their own source identity");
equal(Array.from(body).slice(0, second.start).join("") + "replacement" + Array.from(body).slice(second.end).join(""),
  body.slice(0, blocks[2].start) + "replacement" + body.slice(blocks[2].end));
equal(planBlockQuote(body, blocks[1].start, blocks[2].end)?.text, `${paragraph}\n\n${paragraph}`);
equal(planBlockQuote(body, -1, 3), null);
equal(planBlockQuote(body, 2, body.length + 1), null);
equal(planBlockQuote(body, 4, 2), null);
equal(planReviewBlocks("").blocks, []);
console.log("planReviewDocument.test.ts OK");
