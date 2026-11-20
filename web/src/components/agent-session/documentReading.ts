import type { Element, ElementContent, Root, RootContent } from "hast";

const text = (node: RootContent): string => "value" in node ? String(node.value) : "children" in node ? node.children.map(text).join("") : "";
const element = (tagName: string, children: ElementContent[], properties: Element["properties"] = {}): Element => ({ type: "element", tagName, properties, children });
const label = (value: string): ElementContent => ({ type: "text", value });

/** Runs after the existing sanitizer and before URL hardening. Empty named
 * anchors are locations, not links. Keep sanitized IDs and no executable props. */
export function documentAnchors() {
  return (tree: Root) => {
    const visit = (node: Root | RootContent) => {
      if (node.type === "element" && node.tagName === "a" && !node.properties.href
        && (node.properties.id || node.properties.name)
        && node.children.every(child => child.type === "text" && !child.value.trim())) {
        node.tagName = "span";
        node.properties = { id: node.properties.id || node.properties.name, className: ["document-anchor"] };
      }
      if ("children" in node) node.children.forEach(visit);
    };
    visit(tree);
  };
}

/** Only mechanically labelled catalog tables get a row reader. Ordinary
 * product tables keep their original layout; every catalog field remains. */
export function documentCatalogs() {
  return (tree: Root) => {
    const visit = (parent: Root | Element) => {
      parent.children = parent.children.map(node => {
        if (node.type !== "element") return node;
        if (node.tagName !== "table") { visit(node); return node; }
        const head = node.children.find(child => child.type === "element" && child.tagName === "thead") as Element | undefined;
        const body = node.children.find(child => child.type === "element" && child.tagName === "tbody") as Element | undefined;
        const header = head?.children.find(child => child.type === "element" && child.tagName === "tr") as Element | undefined;
        const cells = header?.children.filter((child): child is Element => child.type === "element" && child.tagName === "th") ?? [];
        const keys = cells.map(cell => text(cell).match(/（([a-z_]+)）$/)?.[1] ?? text(cell));
        if (keys.length < 6 || !keys.includes("id") || !keys.every(key => /^[a-z_]+$/.test(key)) || !body) return node;
        const rows = body.children.filter((child): child is Element => child.type === "element" && child.tagName === "tr");
        if (!rows.length || rows.some(row => row.children.filter(child => child.type === "element").length !== keys.length)) return node;
        const entries = rows.map(row => {
          const values = row.children.filter((child): child is Element => child.type === "element");
          // Keep decisions and their raw state visible, including unknown/new
          // states. Never infer approval or blocking from a status string.
          const summaryKeys = ["id", "title", "question", "statement", "decision", "answer", "status", "blocking", "owner", "decision_maker", "disposition"];
          const labels: Record<string, string> = { title: "标题", question: "问题", statement: "说明", decision: "决定", answer: "答复", status: "状态", blocking: "阻塞", owner: "负责人", decision_maker: "决策人", disposition: "处置" };
          const summary = summaryKeys.flatMap(key => {
            const index = keys.indexOf(key);
            const value = index < 0 ? "" : text(values[index]).trim();
            return value ? [key === "id" ? value : `${labels[key]}: ${value}`] : [];
          }).join("\n");
          return element("details", [
            element("summary", [label(summary)]),
            element("dl", values.flatMap((value, index) => [
              element("dt", cells[index].children), element("dd", value.children.length ? value.children : [label("—")]),
            ])),
          ], { className: ["document-catalog-entry"] });
        });
        return element("div", [
          element("p", [label(`共 ${rows.length} 条，展开条目查看全部字段。`)]),
          ...entries,
          element("details", [element("summary", [label("查看原始表格")]), node]),
        ], { className: ["document-catalog"] });
      }) as typeof parent.children;
    };
    visit(tree);
  };
}
