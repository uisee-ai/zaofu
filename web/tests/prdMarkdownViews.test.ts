import { prdMarkdownView } from "../src/components/channel/prdMarkdownViews.js";

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
console.log("prdMarkdownViews.test.ts OK");
