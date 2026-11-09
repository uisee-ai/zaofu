import { fromMarkdown } from "mdast-util-from-markdown";

export function planDocumentSections(content: string) {
  const tree = fromMarkdown(content);
  const starts: Array<{ start: number; title: string; anchor: string; level: number }> = [];
  let previousEnd = 0;
  for (const node of tree.children) {
    if (node.type === "heading") {
      const start = node.position!.start.offset!;
      const title = content.slice(start, node.position!.end.offset!).replace(/^#{1,6}\s*/, "").replace(/\s+#+\s*$/, "");
      const explicit = content.slice(previousEnd, start).match(/<a\s+id=["']([^"']+)["']\s*>\s*<\/a>\s*$/);
      starts.push({ start, title, anchor: explicit?.[1] || `section-${starts.length + 1}`, level: node.depth });
      previousEnd = node.position!.end.offset!;
    } else if (node.type !== "html" && !(node.type === "paragraph" && /^\s*<a\s+id=/.test(content.slice(node.position!.start.offset!, node.position!.end.offset!)))) {
      previousEnd = node.position!.end.offset!;
    }
  }
  if (!starts.length || starts[0].start > 0) starts.unshift({ start: 0, title: "文档开头", anchor: "document-start", level: 1 });
  return starts.map((section, i) => ({ ...section, content: content.slice(section.start, starts[i + 1]?.start ?? content.length), key: `${section.anchor}-${i}` }));
}
