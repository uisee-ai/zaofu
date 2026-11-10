import { prdMarkdownView } from "../src/components/channel/prdMarkdownViews.js";
import mixed from "./fixtures/prd205/reading.json" with { type: "json" };

function assert(condition: unknown, message = "assertion failed"): void {
  if (!condition) throw new Error(message);
}

const markdown = [
  "# Product\n",
  "\n",
  "## 01. Overview\nProduct outcome.\n",
  "\n",
  "## 06. Functional Requirements\nFR behavior.\n",
  "\n",
  "## 07. Business Rules, Permissions, and State\nState boundary.\n",
  "\n",
  "## 08. Non-functional Requirements\nLatency target.\n",
  "\n",
  "## 10. Dependencies, Assumptions, Risks, and Open Questions\nOpen decision.\n",
].join("");

const index = {
  product: { label: "产品经理", sections: ["01. Overview"] },
  architecture: { label: "架构师", sections: ["07. Business Rules, Permissions, and State", "08. Non-functional Requirements"] },
  developer: { label: "资深开发", sections: ["06. Functional Requirements", "10. Dependencies, Assumptions, Risks, and Open Questions"] },
};

const product = prdMarkdownView(markdown, "product", index);
assert(product.includes("Product outcome."));
assert(!product.includes("FR behavior."));
assert(product.startsWith("# Product"));

const architecture = prdMarkdownView(markdown, "architecture", index);
assert(architecture.includes("State boundary.") && architecture.includes("Latency target."));
assert(!architecture.includes("FR behavior."));

const developer = prdMarkdownView(markdown, "developer", index);
assert(developer.includes("FR behavior.") && developer.includes("Open decision."));
assert(!developer.includes("Product outcome."));

assert(prdMarkdownView(markdown, "product") === markdown, "missing server index keeps the full document");

// The backend API tests validate this same fixture, including its exact index.
for (const role of ["product", "architecture", "developer"] as const) {
  const rendered = prdMarkdownView(mixed.markdown, role, mixed.read_views);
  assert(rendered.startsWith("# 库存设计（合成夹具）"));
  assert(rendered.includes("D-1：重试语义未决，仅阻塞重试实现。"));
  assert(rendered.includes("并发扣减需验证。"));
  assert(rendered.includes("Revision r3；候选；NOT_RUN。"));
  assert(rendered.includes("预留记录归属库存服务。") === (role !== "product"));
}
assert(prdMarkdownView(mixed.markdown, "product", mixed.read_views).includes("库存不足时拒绝，库存保持不变。"));
console.log("prdMarkdownViews.test.ts OK");
