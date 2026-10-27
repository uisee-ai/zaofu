import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";

export interface PlanQuote { text: string; start: number; end: number }
export interface PlanBlock { start: number; end: number; source: string }

export function planReviewBlocks(body: string) {
  const nodes = fromMarkdown(body, { extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()] }).children;
  const blocks: PlanBlock[] = [];
  const definitions: string[] = [];
  for (const node of nodes) {
    const start = node.position!.start.offset!;
    const end = node.position!.end.offset!;
    const source = body.slice(start, end);
    if (node.type === "definition") definitions.push(source);
    else blocks.push({ start, end, source });
  }
  return { blocks, definitions: definitions.length ? `\n\n${definitions.join("\n\n")}` : "" };
}

// AST/DOM positions use UTF-16; the server contract uses Unicode code points.
export function planBlockQuote(body: string, start: number, end: number): PlanQuote | null {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > body.length) return null;
  const text = body.slice(start, end);
  return text.trim() ? { text, start: Array.from(body.slice(0, start)).length, end: Array.from(body.slice(0, end)).length } : null;
}

export function selectedPlanBlocks(selection: Selection | null, root: HTMLElement | null, body: string): PlanQuote | null {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1 || !root) return null;
  const range = selection.getRangeAt(0);
  const block = (node: Node) => (node.nodeType === 1 ? node as Element : node.parentElement)?.closest<HTMLElement>("[data-plan-block]");
  const first = block(range.startContainer), last = block(range.endContainer);
  if (!first || !last || !root.contains(first) || !root.contains(last) || !selection.toString().trim()) return null;
  return planBlockQuote(body, Number(first.dataset.sourceStart), Number(last.dataset.sourceEnd));
}
