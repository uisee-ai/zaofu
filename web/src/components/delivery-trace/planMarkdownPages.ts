import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";

export const PLAN_PAGE_LIMIT = 24_000;

export interface PlanMarkdownPage {
  source: string;
  markdown: string;
  title: string;
  plain: boolean;
}

// Prefer syntax boundaries, while keeping an exact source partition for review
// and download. Repeated table headers/fences exist only in the rendered view.
export function planMarkdownPages(content: string): PlanMarkdownPage[] {
  const tree = fromMarkdown(content, { extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()] });
  const pages: PlanMarkdownPage[] = [];
  let title = "计划正文";
  let cursor = 0;
  const add = (source: string, markdown = source, plain = false) => {
    const last = pages.at(-1);
    if (!plain && last && !last.plain
      && last.markdown.length + markdown.length <= PLAN_PAGE_LIMIT) {
      last.source += source;
      last.markdown += markdown;
    } else {
      pages.push({ source, markdown, title, plain });
    }
  };
  for (const node of tree.children) {
    const start = node.position!.start.offset!;
    const end = node.position!.end.offset!;
    const raw = content.slice(cursor, end);
    if (node.type === "heading") {
      title = content.slice(start, end).split("\n")[0].replace(/^#{1,6}\s*/, "").trim();
    }
    if (raw.length <= PLAN_PAGE_LIMIT) {
      add(raw);
    } else if (node.type === "table" && node.children.length > 1) {
      const firstRow = node.children[1].position!.start.offset!;
      const header = content.slice(start, firstRow);
      if (header.length >= PLAN_PAGE_LIMIT / 2) {
        for (const piece of splitText(raw)) add(piece, piece, true);
        cursor = end;
        continue;
      }
      let offset = cursor;
      let groupEnd = firstRow;
      let prefix = "";
      let limit = Math.max(header.length + 1000, PLAN_PAGE_LIMIT - (pages.at(-1)?.markdown.length || 0));
      for (const row of node.children.slice(1)) {
        const rowEnd = row.position!.end.offset!;
        if (prefix.length + rowEnd - offset > limit && groupEnd > offset) {
          const source = content.slice(offset, groupEnd);
          if (prefix.length + source.length <= PLAN_PAGE_LIMIT) add(source, prefix + source);
          else for (const piece of splitText(source)) add(piece, piece, true);
          offset = groupEnd;
          // source starts with the newline after the preceding row.
          prefix = header.replace(/\r?\n$/, "");
          limit = PLAN_PAGE_LIMIT;
        }
        groupEnd = rowEnd;
      }
      const source = content.slice(offset, end);
      if (prefix.length + source.length <= PLAN_PAGE_LIMIT) add(source, prefix + source);
      else for (const piece of splitText(source)) add(piece, piece, true);
    } else if (node.type === "code" && /^ {0,3}(`{3,}|~{3,})/.test(content.slice(start, end))) {
      const newline = content.indexOf("\n", start);
      const opener = content.slice(start, newline < 0 ? end : newline + 1);
      if (newline < 0 || opener.length > 1000) {
        for (const piece of splitText(raw)) add(piece, piece, true);
        cursor = end;
        continue;
      }
      const fence = opener.trimStart().match(/^(`{3,}|~{3,})/)![0];
      const pieces = splitText(raw, PLAN_PAGE_LIMIT - opener.length - fence.length - 4);
      pieces.forEach((source, index) => {
        const prefix = index ? opener : "";
        const suffix = index < pieces.length - 1 ? `\n${fence}\n` : "";
        add(source, prefix + source + suffix);
      });
    } else {
      // A single enormous paragraph/list/row must not lock the rich renderer.
      // Only that block falls back; other sections retain their formatting.
      for (const piece of splitText(raw)) add(piece, piece, true);
    }
    cursor = end;
  }
  const tail = content.slice(cursor);
  const last = pages.at(-1);
  if (last && last.markdown.length + tail.length <= PLAN_PAGE_LIMIT) {
    last.source += tail;
    last.markdown += tail;
  } else if (tail || !pages.length) {
    for (const piece of splitText(tail)) add(piece);
  }
  return pages;
}

function splitText(text: string, limit = PLAN_PAGE_LIMIT): string[] {
  const chunks: string[] = [];
  let offset = 0;
  while (offset < text.length) {
    let end = Math.min(offset + Math.max(limit, 2), text.length);
    if (end < text.length) {
      const newline = text.lastIndexOf("\n", end - 1);
      if (newline >= offset) end = newline + 1;
      else if (text.charCodeAt(end) >= 0xdc00 && text.charCodeAt(end) <= 0xdfff) end -= 1;
    }
    chunks.push(text.slice(offset, end));
    offset = end;
  }
  return chunks.length ? chunks : [""];
}
