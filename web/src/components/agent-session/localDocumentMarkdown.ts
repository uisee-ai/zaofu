import type { Nodes } from "mdast";
import { localDocumentLink } from "./previewRegistry.js";

const FILE_LINK_PREFIX = "#zf-local-document=";

/** Preserve the Markdown destination before URI normalization and hardening. */
export function remarkLocalDocuments() {
  return (tree: Nodes) => {
    function visit(node: Nodes) {
      if (node.type === "link" && localDocumentLink(node.url)) {
        // An inert fragment survives the normal sanitizer/hardener. Only the
        // preview-aware renderer consumes it; backend authorization is unchanged.
        node.url = FILE_LINK_PREFIX + encodeURIComponent(node.url);
      }
      if ("children" in node) node.children.forEach(visit);
    }
    visit(tree);
  };
}

export function localDocumentMarkdownRef(href: string): string {
  if (!href.startsWith(FILE_LINK_PREFIX)) return "";
  try {
    const ref = decodeURIComponent(href.slice(FILE_LINK_PREFIX.length));
    return localDocumentLink(ref) ? ref : "";
  } catch {
    return "";
  }
}
