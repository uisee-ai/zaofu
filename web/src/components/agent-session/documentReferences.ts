import { fromMarkdown } from "mdast-util-from-markdown";
import type { RootContent } from "mdast";
import { isPathologicalText } from "./markdownGuard.js";

/** Document scope survives presentation sections/pages. First definition wins,
 * as in CommonMark. Only definitions are copied; source/download stay exact. */
export function documentReferences(chunks: readonly string[]): string {
  const definitions = new Map<string, string>();
  const escape = (value: string) => value.replace(/[&<>"\\\r\n]/g, char => `&#${char.charCodeAt(0)};`);
  const visit = (nodes: RootContent[]) => {
    for (const node of nodes) {
      if (node.type === "definition" && !definitions.has(node.identifier)) {
        const label = node.identifier.replace(/[\\[\]]/g, "\\$&");
        definitions.set(node.identifier, `[${label}]: <${escape(node.url)}>${node.title == null ? "" : ` "${escape(node.title)}"`}`);
      } else if ("children" in node) visit(node.children as RootContent[]);
    }
  };
  // Callers supply syntax-safe pages, excluding plain fallback chunks. Keep
  // the same parser budget when collecting context as when rendering a page.
  for (const content of chunks) {
    if (!isPathologicalText(content)) visit(fromMarkdown(content).children);
  }
  return [...definitions.values()].join("\n");
}
